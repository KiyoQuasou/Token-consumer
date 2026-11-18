"""
Tire Size AI: a local RAG chatbot for wheel and tire sizes of popular cars in Thailand.

Runs on your own computer with Ollama:
  - qwen3:1.7b        writes the answers
  - nomic-embed-text  turns text into vectors for search

On start it indexes every PDF in docs/ (the document collection), then serves the web app.
Every answer comes with numbered references (file + page) you can click to open the PDF.

Run in a terminal:
    ollama pull qwen3:1.7b
    ollama pull nomic-embed-text
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000
"""
import json
import os
import re
import threading
import time
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
TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "600"))   # seconds; hard questions on a small model can be slow
TOP_K = int(os.getenv("TOP_K", "6"))                # chunks retrieved per search
NUM_CTX = int(os.getenv("NUM_CTX", "8192"))         # context window (Ollama's default 2048 is too small for RAG)
PORT = int(os.getenv("PORT", "5000"))
CHUNK_SIZE, CHUNK_OVERLAP = 900, 150

BASE = Path(__file__).resolve().parent
DOCS_DIR = BASE / "docs"
INDEX_FILE = BASE / "index.json"
MANIFEST_FILE = DOCS_DIR / "sources.json"
DATA = json.loads((BASE / "tire_data.json").read_text(encoding="utf-8"))
DOCS_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder="static")
lock = threading.Lock()
STATUS = {"state": "idle", "file": "", "done": 0, "total": 0, "error": ""}


# ---------------- manifest: titles, categories and source links for each document ----------------
def manifest():
    try:
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def doc_title(name):
    return manifest().get(name, {}).get("title", name)


# ---------------- index (JSON file + numpy) ----------------
def load_index():
    if INDEX_FILE.exists():
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return {"files": {}, "chunks": []}


INDEX = load_index()
MATRIX = None  # cached numpy matrix of all chunk vectors


def save_index():
    global MATRIX
    INDEX_FILE.write_text(json.dumps(INDEX), encoding="utf-8")
    MATRIX = None


def embed(texts):
    r = requests.post(f"{OLLAMA_URL}/api/embed", json={"model": EMBED_MODEL, "input": texts}, timeout=TIMEOUT)
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
    new = []
    for page_no, page in enumerate(PdfReader(str(path)).pages, start=1):
        for piece in chunk_text(page.extract_text() or ""):
            if len(piece) > 40:
                new.append({"file": path.name, "page": page_no, "text": piece})
    for i in range(0, len(new), 32):
        batch = new[i:i + 32]
        for c, v in zip(batch, embed(["search_document: " + c["text"] for c in batch])):
            c["vec"] = v
    with lock:
        INDEX["chunks"] = [c for c in INDEX["chunks"] if c["file"] != path.name] + new
        INDEX["files"][path.name] = {"mtime": path.stat().st_mtime, "pages": len(PdfReader(str(path)).pages)}
        save_index()
    return len(new)


def needs_index(path: Path):
    info = INDEX["files"].get(path.name)
    return not info or abs(info["mtime"] - path.stat().st_mtime) > 1


def index_missing():
    """Index every PDF in docs/ that is new or changed. Runs in the background."""
    todo = [p for p in sorted(DOCS_DIR.glob("*.pdf")) if needs_index(p)]
    with lock:
        gone = [n for n in INDEX["files"] if not (DOCS_DIR / n).exists()]
        if gone:
            INDEX["chunks"] = [c for c in INDEX["chunks"] if c["file"] not in gone]
            for n in gone:
                INDEX["files"].pop(n, None)
            save_index()
    if not todo:
        STATUS.update(state="ready", file="", error="")
        print(f"Index ready: {len(INDEX['files'])} documents, {len(INDEX['chunks'])} chunks")
        return
    STATUS.update(state="indexing", done=0, total=len(todo), error="")
    for i, p in enumerate(todo, 1):
        STATUS.update(file=p.name, done=i - 1)
        print(f"  indexing [{i}/{len(todo)}] {p.name} ...", end=" ", flush=True)
        try:
            print(f"{index_pdf(p)} chunks")
        except requests.exceptions.ConnectionError:
            msg = f"Can't reach Ollama at {OLLAMA_URL}. Start Ollama, then click 'Index new files'."
            print("\n  ✕ " + msg)
            STATUS.update(state="error", error=msg)
            return
        except Exception as e:
            print(f"failed: {e}")
            STATUS.update(error=f"{p.name}: {e}")
    STATUS.update(state="ready", file="", done=len(todo))
    print(f"Index ready: {len(INDEX['files'])} documents, {len(INDEX['chunks'])} chunks")


def start_indexing():
    if STATUS["state"] != "indexing":
        threading.Thread(target=index_missing, daemon=True).start()


def search(query, k, boost_file=None):
    """Vector search. If the question names a car, that car's spec sheet gets a small boost
    (the spec sheets look alike, so a small embedding model can mix up car names)."""
    global MATRIX
    with lock:
        chunks = INDEX["chunks"]
        if not chunks:
            return []
        if MATRIX is None:
            MATRIX = np.array([c["vec"] for c in chunks])
        m = MATRIX
    q = np.array(embed(["search_query: " + query])[0])
    scores = m @ q / (np.linalg.norm(m, axis=1) * np.linalg.norm(q) + 1e-9)
    if boost_file:
        scores = scores + np.array([0.15 if c["file"] == boost_file else 0.0 for c in chunks])
    return [(chunks[i], float(scores[i])) for i in np.argsort(-scores)[:k]]


# ---------------- tire calculator: exact math the small model can rely on ----------------
SIZE_RE = re.compile(r"(\d{3})\s*/\s*(\d{2})\s*Z?R?\s*-?\s*(\d{2})(?:\s*(\d{2,3})\s*([A-Z])\b)?", re.I)


def parse_sizes(text):
    return [{"w": int(m[1]), "ar": int(m[2]), "rim": int(m[3]), "li": int(m[4]) if m[4] else None,
             "sr": m[5].upper() if m[5] else None, "raw": f"{m[1]}/{m[2]}R{m[3]}"} for m in SIZE_RE.finditer(text)]


def diameter(t):
    return t["rim"] * 25.4 + 2 * t["w"] * t["ar"] / 100


def find_car(text):
    t, best, length = text.lower(), None, 0
    for c in DATA["CARS"]:
        for k in c["keys"]:
            if k in t and len(k) > length:
                best, length = c, len(k)
    return best


def verdict(d):
    a = abs(d)
    return "good fit (within ±3%)" if a <= 3 else "risky (3–5% off)" if a <= 5 else "not recommended (more than 5% off)"


def calculator(question):
    """Returns a short text with exact numbers, or None if the question has no tire size."""
    sizes, car = parse_sizes(question), find_car(question)
    load, speed = DATA["LOAD"], DATA["SPEED"]
    if not sizes:
        return None
    lines = []
    if car:
        new = sizes[0]
        oems = [dict(s, t=parse_sizes(s["tire"])[0]) for s in car["sizes"]]
        oem = next((s for s in oems if s["t"]["rim"] == new["rim"]), None) or min(oems, key=lambda s: abs(diameter(s["t"]) - diameter(new)))
        df, dn = diameter(oem["t"]), diameter(new)
        d = (dn - df) / df * 100
        lines.append(f"{car['name']} factory tire on the {oem['label']} version: {oem['tire']}"
                     f"{' ' + str(oem['li']) + oem['sr'] if oem.get('li') else ''} on {oem['rim']}, diameter {df:.1f} mm.")
        lines.append(f"New tire {new['raw']}: diameter {dn:.1f} mm. Change: {d:+.2f}%, which is {verdict(d)}.")
        lines.append(f"When the speedometer shows 100 km/h, the real speed is {100 * dn / df:.1f} km/h. Ride height changes by {(dn - df) / 2:+.1f} mm.")
        if new["rim"] != oem["t"]["rim"]:
            lines.append(f"It needs a {new['rim']}-inch wheel with bolt pattern {car['pcd']} and center bore {car['cb']}.")
        if oem.get("li"):
            lines.append(f"The new tire's load index must be at least {oem['li']} ({load[str(oem['li'])]} kg) and its speed rating at least {oem['sr']} ({speed[oem['sr']]} km/h).")
        if new["li"] and oem.get("li") and new["li"] < oem["li"]:
            lines.append(f"Warning: load index {new['li']} is LOWER than the factory {oem['li']}. Do not use it.")
    elif len(sizes) >= 2:
        a, b = sizes[0], sizes[1]
        da, db = diameter(a), diameter(b)
        d = (db - da) / da * 100
        lines.append(f"{a['raw']} diameter {da:.1f} mm; {b['raw']} diameter {db:.1f} mm. Change: {d:+.2f}%, which is {verdict(d)}.")
        lines.append(f"With {b['raw']} instead of {a['raw']}, the real speed at 100 km/h on the speedometer is {100 * db / da:.1f} km/h.")
    else:
        t = sizes[0]
        lines.append(f"{t['raw']}: width {t['w']} mm, sidewall {t['w'] * t['ar'] / 100:.1f} mm ({t['ar']}% of the width), "
                     f"radial, fits a {t['rim']}-inch wheel, overall diameter {diameter(t):.1f} mm.")
        if t["li"] and str(t["li"]) in load:
            lines.append(f"Load index {t['li']} = up to {load[str(t['li'])]} kg per tire.")
        if t["sr"] and t["sr"] in speed:
            lines.append(f"Speed rating {t['sr']} = up to {speed[t['sr']]} km/h.")
    return "Calculated by the app: " + " ".join(lines)


# ---------------- LLM ----------------
def chat(messages, think=False):
    r = requests.post(f"{OLLAMA_URL}/api/chat", json={
        "model": CHAT_MODEL, "messages": messages, "stream": False, "think": think,
        "keep_alive": "30m", "options": {"num_ctx": NUM_CTX, "temperature": 0.2},
    }, timeout=TIMEOUT)
    r.raise_for_status()
    return re.sub(r"<think>.*?</think>", "", r.json()["message"]["content"], flags=re.S).strip()


def extra_queries(question):
    """'More searches': ask the model for 2 other ways to phrase the question."""
    try:
        out = chat([{"role": "user", "content": "Write 2 short search queries (one per line, no numbering) "
                                                 f"that would help find information to answer this question:\n{question}"}])
        return [l.strip("-• ").strip() for l in out.splitlines() if l.strip()][:2]
    except Exception:
        return []


SYSTEM = """You are Tire Size AI, a helpful assistant for wheel and tire sizes of cars in Thailand.
Answer in simple English using ONLY the numbered sources below.
After every fact, cite the source number in square brackets, like [1] or [2][3].
If a source says "Calculated by the app", trust its numbers exactly.
If the sources do not contain the answer, say you could not find it in the documents.
Do not invent sources or numbers."""


# ---------------- routes ----------------
@app.get("/")
def home():
    return send_from_directory("static", "index.html")


@app.get("/api/files")
def files_info():
    """Every document in the collection, with its status: ready, indexing, waiting or missing."""
    man = manifest()
    names = sorted(set(man) | {p.name for p in DOCS_DIR.glob("*.pdf")})
    counts = {}
    for c in INDEX["chunks"]:
        counts[c["file"]] = counts.get(c["file"], 0) + 1
    out = []
    for n in names:
        meta, path = man.get(n, {}), DOCS_DIR / n
        if not path.exists():
            status = "missing"
        elif STATUS["state"] == "indexing" and STATUS["file"] == n:
            status = "indexing"
        elif needs_index(path):
            status = "waiting"
        else:
            status = "ready"
        out.append({"name": n, "title": meta.get("title", n), "category": meta.get("category", "Uploaded"),
                    "sources": meta.get("sources", []), "download": meta.get("download"), "status": status,
                    "size": path.stat().st_size if path.exists() else 0,
                    "pages": INDEX["files"].get(n, {}).get("pages", 0), "chunks": counts.get(n, 0)})
    return jsonify(files=out, status=STATUS, model=CHAT_MODEL, embed=EMBED_MODEL,
                   total_chunks=len(INDEX["chunks"]), top_k=TOP_K)


@app.post("/api/reindex")
def reindex():
    start_indexing()
    return jsonify(ok=True)


@app.post("/upload")
def upload():
    results = []
    for f in request.files.getlist("files"):
        name = secure_filename(f.filename or "")
        if not name.lower().endswith(".pdf"):
            results.append({"file": f.filename, "error": "not a PDF"})
            continue
        f.save(DOCS_DIR / name)
        try:
            results.append({"file": name, "chunks": index_pdf(DOCS_DIR / name)})
        except requests.exceptions.ConnectionError:
            results.append({"file": name, "error": f"saved, but can't reach Ollama at {OLLAMA_URL} to index it"})
        except Exception as e:
            results.append({"file": name, "error": str(e)})
    return jsonify(results)


@app.get("/files/<path:name>")
def open_file(name):
    name = secure_filename(name)
    if not (DOCS_DIR / name).exists():
        abort(404)
    return send_from_directory(DOCS_DIR, name, mimetype="application/pdf")


@app.delete("/files/<path:name>")
def delete_file(name):
    name = secure_filename(name)
    (DOCS_DIR / name).unlink(missing_ok=True)
    with lock:
        INDEX["chunks"] = [c for c in INDEX["chunks"] if c["file"] != name]
        INDEX["files"].pop(name, None)
        save_index()
    return jsonify(ok=True)


@app.post("/ask")
def ask():
    data = request.get_json(force=True)
    question = (data.get("question") or "").strip()
    k = int(data.get("top_k") or TOP_K)
    if not question:
        return jsonify(error="Type a question first."), 400
    if not INDEX["chunks"]:
        return jsonify(error="No documents are indexed yet. Make sure Ollama is running, then click 'Index new files'."), 400

    t0 = time.time()
    try:
        # 1) search the documents (optionally with extra queries) and merge
        queries = [question] + (extra_queries(question) if data.get("multi_query") else [])
        car = find_car(question)
        boost = (re.sub(r"[^a-z0-9]+", "-", car["name"].lower()).strip("-") + "-specs.pdf") if car else None
        best = {}
        for q in queries:
            for c, s in search(q, k, boost):
                key = (c["file"], c["page"], c["text"][:40])
                if key not in best or best[key][1] < s:
                    best[key] = (c, s)
        hits = sorted(best.values(), key=lambda x: -x[1])[:k]

        # 2) number the references; the calculator (if any) goes first
        refs, context = [], []
        calc = calculator(question)
        if calc:
            refs.append({"n": 1, "kind": "calculator", "file": None, "page": None, "title": "Tire calculator (exact math by the app)",
                         "score": 1.0, "snippet": calc, "url": None})
            context.append(f"[1] (Tire calculator)\n{calc}")
        for c, s in hits:
            n = len(refs) + 1
            refs.append({"n": n, "kind": "document", "file": c["file"], "page": c["page"], "title": doc_title(c["file"]),
                         "score": round(s, 3), "snippet": c["text"][:320], "url": f"/files/{c['file']}#page={c['page']}"})
            context.append(f"[{n}] (from {c['file']}, page {c['page']})\n{c['text']}")

        # 3) ask the model
        answer = chat([{"role": "system", "content": SYSTEM + "\n\nSOURCES:\n\n" + "\n\n".join(context)},
                       {"role": "user", "content": question}], think=bool(data.get("think")))
    except requests.exceptions.Timeout:
        return jsonify(error=f"Ollama took longer than {TIMEOUT}s. Start the app with a bigger OLLAMA_TIMEOUT."), 504
    except requests.exceptions.ConnectionError:
        return jsonify(error=f"Can't reach Ollama at {OLLAMA_URL}. Is Ollama running?"), 502
    except Exception as e:
        return jsonify(error=f"Ollama error: {e}"), 502

    # mark which references the model cited; ALL references are always returned and shown
    cited = {int(x) for x in re.findall(r"\[(\d+)\]", answer)}
    for r in refs:
        r["cited"] = r["n"] in cited
    return jsonify(answer=answer, sources=refs, queries=queries, seconds=round(time.time() - t0, 1))


def check_ollama():
    try:
        tags = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).json()
        names = [m["name"] for m in tags.get("models", [])]
        for m in (CHAT_MODEL, EMBED_MODEL):
            if not any(n == m or n.startswith(m + ":") for n in names):
                print(f"  ! Model '{m}' is not installed. Run:  ollama pull {m}")
        print(f"  Ollama OK at {OLLAMA_URL}")
    except Exception:
        print(f"  ! Can't reach Ollama at {OLLAMA_URL}. Start Ollama first (the app will still open).")


if __name__ == "__main__":
    print("Tire Size AI")
    print(f"  chat model {CHAT_MODEL} · embeddings {EMBED_MODEL} · timeout {TIMEOUT}s · top-k {TOP_K}")
    check_ollama()
    start_indexing()
    print(f"  Open http://localhost:{PORT} in your browser  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
