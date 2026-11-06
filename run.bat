@echo off
rem Activates the venv and starts the server (just double-click this file)
cd /d "%~dp0"
if not exist venv\Scripts\activate.bat (
  echo venv folder not found. Run first:  py -3.12 -m venv venv
  echo then:  venv\Scripts\activate   and   pip install -r requirements.txt
  pause
  exit /b 1
)
call venv\Scripts\activate.bat
uvicorn main:app --host 127.0.0.1 --port 8000
pause
