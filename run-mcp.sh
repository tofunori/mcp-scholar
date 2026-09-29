#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a
exec .venv/bin/python -m src.server
