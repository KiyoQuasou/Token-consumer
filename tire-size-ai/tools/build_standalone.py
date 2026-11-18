"""
Builds tire-size-ai.html: the no-Python version of the chatbot in one file.
It takes the page design from static/index.html, swaps the server calls for direct Ollama calls
(tools/standalone.js), and embeds the PDFs from docs/ plus their page text.

Run:  python tools/build_standalone.py
"""
import base64
import hashlib
import json
import re
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
head = page[:page.index("<script>\nconst $")]          # markup + styles + panel script

# swap the "Index" section for "Setup" (Ollama address, models, timeout)
setup = '''<span class="num">03</span>Setup
            <svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></button>
          <div class="acc-body">
            <div class="stats">
              <div class="stat"><b id="stDocs">0</b><span>documents ready</span></div>
              <div class="stat"><b id="stChunks">0</b><span>text chunks</span></div>
            </div>
            <div class="fields">
              <label for="cfgUrl">Ollama address</label><input id="cfgUrl" value="http://localhost:11434" spellcheck="false">
              <label for="cfgChat">Chat model</label><input id="cfgChat" value="qwen3:1.7b" spellcheck="false">
              <label for="cfgEmbed">Embedding model</label><input id="cfgEmbed" value="nomic-embed-text" spellcheck="false">
              <label for="cfgTimeout">Timeout (seconds)</label><input id="cfgTimeout" type="number" min="30" value="600">
            </div>
            <button class="ghost" id="testBtn" type="button">Test connection</button>
            <p class="note" id="connNote"></p>
          </div>
        </section>'''
head = re.sub(r'<span class="num">03</span>Index.*?</section>', setup, head, count=1, flags=re.S)
head = head.replace("  @media (max-width: 1100px)", """  .fields { display: grid; gap: 4px; margin-top: 12px; font-size: 12.5px; color: var(--muted); }
  .fields input { border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px; font-size: 13.5px; color: var(--ink); margin-bottom: 6px; width: 100%; }
  .fields input:focus { outline: none; border-color: var(--slate); }
  #testBtn { margin-top: 4px; }
  .note { font-size: 12.5px; color: var(--muted); margin: 8px 2px 0; white-space: pre-line; }
  @media (max-width: 1100px)""", 1)
head = head.replace("<title>Tire Size AI</title>", '<title>Tire Size AI</title>\n<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>', 1)

man = json.loads((ROOT / "docs" / "sources.json").read_text(encoding="utf-8"))
data = json.loads((ROOT / "tire_data.json").read_text(encoding="utf-8"))
docs, external = [], []
for name, meta in man.items():
    if meta.get("download"):
        external.append({"name": name, "title": meta["title"], "category": meta["category"], "url": meta["download"]})
        continue
    raw = (ROOT / "docs" / name).read_bytes()
    pages = [(p.extract_text() or "") for p in PdfReader(str(ROOT / "docs" / name)).pages]
    docs.append({"name": name, "title": meta["title"], "category": meta["category"], "sources": meta.get("sources", []),
                 "size": len(raw), "version": hashlib.sha1(raw).hexdigest()[:12], "pages": pages,
                 "b64": base64.b64encode(raw).decode()})
bundle = json.dumps({"docs": docs, "external": external,
                     "data": {k: data[k] for k in ("CARS", "LOAD", "SPEED")}}, ensure_ascii=False).replace("</", "<\\/")
script = (ROOT / "tools" / "standalone.js").read_text(encoding="utf-8")
out = head + '<script type="application/json" id="bundle">' + bundle + "</script>\n<script>\n" + script + "\n</script>\n</body>\n</html>\n"
(ROOT / "tire-size-ai.html").write_text(out, encoding="utf-8")
print(f"wrote tire-size-ai.html ({len(docs)} documents built in, {len(out) // 1024} KB)")
