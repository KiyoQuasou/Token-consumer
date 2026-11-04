# Wheel & Tire Size Guide AI 🚗

A local AI assistant that helps pick the right wheel and tire size for popular cars in
Thailand (Honda City, Toyota Yaris Ativ, Isuzu D-Max). It reads tire size charts and car
spec sheets (PDF) and answers questions **with sources** (file + page).

## Demo

![demo](screenshots/demo.png)

Example questions:
- What tire size does the Honda City RS use?
- Can I put 265/65R18 tires on an Isuzu D-Max? How much does the diameter change?
- What does 185/55R16 87H mean?

## How it works

```
PDF files ──► split into chunks (with file + page) ──► nomic-embed-text ──► index.json
                                                                              │
Question ──► nomic-embed-text ──► find closest chunks (top-k) ◄───────────────┘
                                        │
                                        ▼
                     qwen3:1.7b answers using ONLY those chunks
                     and cites them as [1], [2] → shown as clickable sources
```

1. **Indexing:** each PDF is split page by page into small chunks. Each chunk is turned
   into a vector (embedding) with `nomic-embed-text`.
2. **Search:** the question is turned into a vector too, and the most similar chunks are found
   (cosine similarity). "More searches" mode asks the model for 2 extra queries.
3. **Answer:** the chunks are numbered and given to `qwen3:1.7b`, which must cite them.
4. **Sources:** the app lists file + page for every chunk. Click to open the PDF at that page.

## Run it yourself

You need [Ollama](https://ollama.com) and Python 3.

```bash
ollama pull qwen3:1.7b
ollama pull nomic-embed-text

git clone <this repo url>
cd <repo folder>
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000, upload the PDFs from the `docs/` folder, and ask a question.

## Settings for hard questions

| Setting | How | Default |
|---|---|---|
| Ollama timeout | `OLLAMA_TIMEOUT` env var | 600 s |
| Chunks per search | `TOP_K` env var or the box in the UI | 6 |
| More searches | "More searches" checkbox | off |
| Thinking mode | "Thinking mode" checkbox | off |
| Context size | `NUM_CTX` env var | 8192 |

Example: `OLLAMA_TIMEOUT=1200 TOP_K=10 python app.py`

## Files

| File | What it is |
|---|---|
| `app.py` | Backend: upload, index, search, ask, serve PDFs |
| `static/index.html` | Web page (chat, file list, PDF viewer) |
| `docs/` | Our source PDFs (tire charts, car specs, papers) |
| `screenshots/` | Demo pictures for this README |
| `requirements.txt` | Python packages |

## Sources used
- Continental Technical Databook (ETRTO tire data)
- Gołębiewski (2015), Impact of Outer Diameter and Energy Efficiency Class of Tyre on Fuel Consumption, Journal of KONES
- Niajalili & Askari (2025), Effect of wheel rim material and tire size on fuel consumption, Journal of Energy Conversion
- NHTSA, Be TireWise tire safety guide
- Official spec pages: Honda Thailand, Toyota Thailand, Isuzu Thailand

## Team
- Jettawat Kaweenatthayanon 6858016056
- Phophat Philaphandeth 6858081756
- Ayyaphat Suwanphuk 6858116056
