#!/usr/bin/env bash
# Tire Size AI: one-step start for Mac / Linux.
# Usage:  bash start.sh
set -e
cd "$(dirname "$0")"

echo "== Tire Size AI =="

# 1) Python
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Get it from https://www.python.org/downloads/ and run this again."
  exit 1
fi

# 2) Ollama + models
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is not installed. Get it from https://ollama.com/download, open it, and run this again."
  exit 1
fi
for model in qwen3:1.7b nomic-embed-text; do
  if ! ollama list 2>/dev/null | grep -q "^${model%%:*}"; then
    echo "-- downloading model $model (first time only)"
    ollama pull "$model"
  fi
done

# 3) Python packages in a local virtual environment (.venv)
if [ ! -d .venv ]; then
  echo "-- creating .venv"
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

# 4) Extra research PDFs (optional, skipped if offline)
python download_docs.py || echo "(could not download the extra PDFs, the app still works)"

# 5) Run
( sleep 3; python -m webbrowser "http://localhost:${PORT:-5000}" >/dev/null 2>&1 ) &
python app.py
