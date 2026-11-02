@echo off
REM ============================================================
REM  M03 - GitHub Analyzer, on its own (PORTS.txt: API 8103).
REM
REM  Creates .venv on first run, then serves the /v1 API.
REM  Repository code is read, never executed. No model is used.
REM
REM  Usage:  start_github.bat
REM ============================================================

cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv || exit /b 1
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || exit /b 1
)
title M03 - GitHub Analyzer
".venv\Scripts\python.exe" -m uvicorn --factory gh_analyzer.api:create_app --host 127.0.0.1 --port 8103
