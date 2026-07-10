@echo off
setlocal
cd /d %~dp0

if not exist .venv (
  echo Creando entorno virtual...
  python -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --disable-pip-version-check -r requirements.txt
python server.py

pause
