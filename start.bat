@echo off
REM Tire Size AI: one-step start for Windows.
REM Usage: double-click start.bat, or run it in a terminal.
cd /d "%~dp0"
echo == Tire Size AI ==

where py >nul 2>nul
if %errorlevel% neq 0 (
  echo Python is not installed. Get it from https://www.python.org/downloads/ and run this again.
  pause
  exit /b 1
)

where ollama >nul 2>nul
if %errorlevel% neq 0 (
  echo Ollama is not installed. Get it from https://ollama.com/download, open it, and run this again.
  pause
  exit /b 1
)

ollama list | findstr /b "qwen3:1.7b" >nul || (echo -- downloading qwen3:1.7b & ollama pull qwen3:1.7b)
ollama list | findstr /b "nomic-embed-text" >nul || (echo -- downloading nomic-embed-text & ollama pull nomic-embed-text)

if not exist .venv (
  echo -- creating .venv
  py -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

python download_docs.py || echo (could not download the extra PDFs, the app still works)

start "" cmd /c "timeout /t 3 >nul & start http://localhost:5000"
python app.py
pause
