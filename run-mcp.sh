#!/bin/bash
set -euo pipefail
cd /volume1/Services/mcp/scholar
set -a; source .env; set +a
exec .venv/bin/python -m src.server
