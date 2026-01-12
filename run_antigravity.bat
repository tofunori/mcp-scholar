@echo off
set PYTHONUNBUFFERED=1
set OPENALEX_MAILTO=tofunori@gmail.com
set SCOPUS_API_KEY=80f185f74ee4298bdb1d8271db5dc227
set SCIX_API_KEY=WtjoL5CE9M4eXycSuNCBnJ453nxJRNlBUQW5Uja3
set LOG_LEVEL=CRITICAL
cd /d "D:\Claude Code\scholar-mcp"
uv run python -m src.server_antigravity
