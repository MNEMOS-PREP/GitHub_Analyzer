@echo off
REM ============================================================
REM  M03 - GitHub Analyzer, on its own (PORTS.txt: API 8103).
REM
REM  Creates .venv on first run, then serves the /v1 API.
REM  Repository code is read, never executed. The analysis uses no model; the
REM  optional PR helper does, and only when LLM_API_KEY is set.
REM
REM  Usage:  start_github.bat         opens the server in its own tab and returns
REM                                   (the platform shell calls it this way)
REM          start_github.bat run     runs the server in this window
REM ============================================================

cd /d "%~dp0"
if /i "%~1"=="run" goto run

REM The shell waits for this file to return, then looks for port 8103, so the server goes in a tab of its own.
where wt >nul 2>&1
if not errorlevel 1 (
  wt -w ai-interviewer new-tab --title "M03 - GitHub Analyzer" cmd /k ""%~f0" run"
  exit /b 0
)
start "M03 - GitHub Analyzer" cmd /k ""%~f0" run"
exit /b 0

:run
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv || exit /b 1
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || exit /b 1
)
title M03 - GitHub Analyzer
".venv\Scripts\python.exe" -m uvicorn --factory gh_analyzer.agents_app:create_app --host 127.0.0.1 --port 8103
