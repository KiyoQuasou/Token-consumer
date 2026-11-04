"""
Personal AI assistant with RAG over your PDF files.
Runs on local Ollama: qwen3:1.7b (chat) + nomic-embed-text (embeddings).

Task 1: every answer names its sources (file + page), with [n] citations.
Task 2: uploaded files are clickable and open in the browser (at the right page).
Bonus:  longer timeouts + "more searches" (bigger top-k, multi-query search).

Run:
    pip install flask requests pypdf numpy
    python app.py
    open http://localhost:5000
"""
import json
import os
import re
from pathlib import Path

import numpy as np
import requests
from flask import Flask, abort, jsonify, request, send_from_directory
from pypdf import PdfReader
from werkzeug.utils import secure_filename

# ---------------- settings (change here or with environment variables) ----------------
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen3:1.7b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "600"))   # seconds; hard questions can be slow
TOP_K = int(os.getenv("TOP_K", "6"))                # how many chunks to retrieve
NUM_CTX = int(os.getenv("NUM_CTX", "8192"))         # model context window (default 2048 is too small for RAG)
CHUNK_SIZE, CHUNK_OVERLAP = 900, 150

BASE = Path(__file__).parent
DOCS_DIR = BASE / "docs"
INDEX_FILE = BASE / "index.json"
DOCS_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder="static")

# ---------------- index (simple JSON file + numpy) ----------------
def load_index():
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return []

def save_index(chunks):
    INDEX_FILE.write_text(json.dumps(chunks), encoding="utf-8")

INDEX = load_index()


def embed(texts):
    r = requests.post(f"{OLLAMA_URL}/api/embed",
                      json={"model": EMBED_MODEL, "input": texts}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["embeddings"]


def chunk_text(text):
    text = re.sub(r"\s+", " ", text).strip()
    out, start = [], 0
    while start < len(text):
        out.append(text[start:start + CHUNK_SIZE])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return out


def index_pdf(path: Path):
    """Split the PDF page by page, so every chunk knows its file AND page number."""
    global INDEX
    INDEX = [c for c in INDEX if c["file"] != path.name]   # re-upload replaces old version
    new = []
    for page_no, page in enumerate(PdfReader(str(path)).pages, start=1):
        for piece in chunk_text(page.extract_text() or ""):
            if len(piece) > 40:
                new.append({"file": path.name, "page": page_no, "text": piece})
    for i in range(0, len(new), 32):                         # embed in batches
        batch = new[i:i + 32]
        vecs = embed(["search_document: " + c["text"] for c in batch])
        for c, v in zip(batch, vecs):
            c["vec"] = v
    INDEX += new
    save_index(INDEX)
    return len(new)


def search(query, k):
    if not INDEX:
        return []
    q = np.array(embed(["search_query: " + query])[0])
    m = np.array([c["vec"] for c in INDEX])
    scores = m @ q / (np.linalg.norm(m, axis=1) * np.linalg.norm(q) + 1e-9)
    best = np.argsort(-scores)[:k]
    return [(int(i), float(scores[i])) for i in best]


# ---------------- LLM ----------------
def chat(messages, think=False):
    r = requests.post(f"{OLLAMA_URL}/api/chat", json={
        "model": CHAT_MODEL,
        "messages": messages,
        "stream": False,
        "think": think,                       # qwen3 "thinking" mode: slower but smarter
        "keep_alive": "30m",                  # keep model loaded between questions
        "options": {"num_ctx": NUM_CTX, "temperature": 0.2},
    }, timeout=TIMEOUT)
    r.raise_for_status()
    text = r.json()["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def extra_queries(question):
    """'More searches': ask the model for 2 other ways to phrase the question."""
    try:
        out = chat([{"role": "user", "content":
                     "Write 2 short search queries (one per line, no numbering) that would help "
                     f"find information to answer this question:\n{question}"}])
        return [l.strip("-• ").strip() for l in out.splitlines() if l.strip()][:2]
    except Exception:
        return []


SYSTEM = """You are a helpful assistant. Answer using ONLY the numbered sources below.
After every fact, cite the source number in square brackets, like [1] or [2][3].
If the sources do not contain the answer, say you could not find it in the documents.
Do not invent sources."""


# ---------------- routes ----------------
@app.get("/")
def home():
    return send_from_directory("static", "index.html")


@app.post("/upload")
def upload():
    results = []
    for f in request.files.getlist("files"):
        name = secure_filename(f.filename or "")
        if not name.lower().endswith(".pdf"):
            results.append({"file": f.filename, "error": "not a PDF"})
            continue
        path = DOCS_DIR / name
        f.save(path)
        try:
            results.append({"file": name, "chunks": index_pdf(path)})
        except Exception as e:
            results.append({"file": name, "error": str(e)})
    return jsonify(results)


@app.get("/files")
def list_files():                                   # Task 2: list of uploaded files
    return jsonify(sorted(p.name for p in DOCS_DIR.glob("*.pdf")))


@app.get("/files/<path:name>")
def open_file(name):                                # Task 2: click to open the PDF
    name = secure_filename(name)
    if not (DOCS_DIR / name).exists():
        abort(404)
    return send_from_directory(DOCS_DIR, name, mimetype="application/pdf")


@app.delete("/files/<path:name>")
def delete_file(name):
    global INDEX
    name = secure_filename(name)
    (DOCS_DIR / name).unlink(missing_ok=True)
    INDEX = [c for c in INDEX if c["file"] != name]
    save_index(INDEX)
    return jsonify(ok=True)


@app.post("/ask")
def ask():
    data = request.get_json(force=True)
    question = (data.get("question") or "").strip()
    k = int(data.get("top_k") or TOP_K)
    if not question:
        return jsonify(error="empty question"), 400
    if not INDEX:
        return jsonify(answer="Please upload some PDF files first.", sources=[])

    # 1) search (optionally with several queries) and merge results
    queries = [question] + (extra_queries(question) if data.get("multi_query") else [])
    best = {}
    for q in queries:
        for i, s in search(q, k):
            best[i] = max(s, best.get(i, -1))
    hits = sorted(best.items(), key=lambda x: -x[1])[:k]

    # 2) number the sources so the model can cite them  (Task 1)
    sources, context = [], []
    for n, (i, score) in enumerate(hits, start=1):
        c = INDEX[i]
        sources.append({"n": n, "file": c["file"], "page": c["page"],
                        "score": round(score, 3), "snippet": c["text"][:300],
                        "url": f"/files/{c['file']}#page={c['page']}"})
        context.append(f"[{n}] (from {c['file']}, page {c['page']})\n{c['text']}")

    # 3) ask the model
    try:
        answer = chat([
            {"role": "system", "content": SYSTEM + "\n\nSOURCES:\n\n" + "\n\n".join(context)},
            {"role": "user", "content": question},
        ], think=bool(data.get("think")))
    except requests.exceptions.Timeout:
        return jsonify(error=f"Ollama took longer than {TIMEOUT}s. Raise OLLAMA_TIMEOUT."), 504
    except Exception as e:
        return jsonify(error=f"Ollama error: {e}"), 502

    # mark which sources the model actually cited (the rest are still shown as "also retrieved")
    cited = {int(x) for x in re.findall(r"\[(\d+)\]", answer)}
    for s in sources:
        s["cited"] = s["n"] in cited
    return jsonify(answer=answer, sources=sources, queries=queries)


if __name__ == "__main__":
    print(f"Chat model: {CHAT_MODEL} | timeout {TIMEOUT}s | top-k {TOP_K} | {len(INDEX)} chunks loaded")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
