"""
Downloads the public research / standards PDFs listed in docs/sources.json
(the entries that have a "download" link) into the docs/ folder.

Run:  python download_docs.py
Then restart app.py (or click "Index new files" in the app) to add them to the search index.
"""
import json
from pathlib import Path

import requests

DOCS = Path(__file__).resolve().parent / "docs"
manifest = json.loads((DOCS / "sources.json").read_text(encoding="utf-8"))

for name, meta in manifest.items():
    url = meta.get("download")
    if not url:
        continue
    path = DOCS / name
    if path.exists() and path.stat().st_size > 1000:
        print(f"✓ already have {name}")
        continue
    print(f"↓ {name}\n  from {url}")
    try:
        r = requests.get(url, timeout=120, headers={"User-Agent": "TireSizeAI-student-project"})
        r.raise_for_status()
        if not r.content.startswith(b"%PDF"):
            raise ValueError("the link did not return a PDF")
        path.write_bytes(r.content)
        print(f"  saved ({len(r.content) / 1e6:.1f} MB)")
    except Exception as e:
        print(f"  ✕ could not download: {e}\n    Open the link in your browser, save the PDF as docs/{name}, then restart app.py.")
