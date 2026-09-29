@echo off
rem API keys are read from .env (see .env.example); never hardcode them here.
set PYTHONUNBUFFERED=1
set LOG_LEVEL=CRITICAL
cd /d "%~dp0"
uv run --env-file .env python -m src.server_antigravity
