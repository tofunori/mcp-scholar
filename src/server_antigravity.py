#!/usr/bin/env python3
"""
Wrapper for scholar MCP server to fix Windows CRLF issues with Google Antigravity.
Must be run as: python -m src.server_antigravity
"""
import sys
import os
import io
import warnings

# CRITICAL: Suppress ALL warnings first
warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

# CRITICAL: Fix Windows CRLF issue BEFORE any other imports
if sys.platform == "win32":
    sys.stdout.reconfigure(newline="\n")
    sys.stdin.reconfigure(newline="\n")

# CRITICAL: Redirect stdout during imports
_real_stdout = sys.stdout
sys.stdout = io.StringIO()

# Set environment to suppress logging BEFORE config loads
os.environ["LOG_LEVEL"] = "CRITICAL"
os.environ["PYTHONUNBUFFERED"] = "1"

# CRITICAL: Disable ALL logging before any imports
import logging

# Completely disable logging
logging.disable(logging.CRITICAL)

# Create a null handler that does nothing
class NullHandler(logging.Handler):
    def emit(self, record):
        pass

# Replace root logger
logging.root.handlers = [NullHandler()]
logging.root.setLevel(logging.CRITICAL)

# Import the server (with stdout captured)
import asyncio
from .server import main, server, logger

# Suppress the server's logger specifically
logger.handlers = [NullHandler()]
logger.setLevel(logging.CRITICAL)
logger.propagate = False

# Restore stdout for MCP communication
sys.stdout = _real_stdout

if __name__ == "__main__":
    asyncio.run(main())
