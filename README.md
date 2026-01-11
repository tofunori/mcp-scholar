# mcp-scholar

MCP server for searching scientific papers across multiple academic databases.

## Features

- **Multi-source search**: OpenAlex, Semantic Scholar, Scopus, SciX/NASA ADS
- **Automatic deduplication** and metadata merging across sources
- **Rate limiting** with adaptive backoff per source
- **Citations and references** retrieval
- **Similar papers** recommendations via Semantic Scholar API

## Supported Sources

| Source | Documents | Coverage | API Key |
|--------|-----------|----------|---------|
| [OpenAlex](https://openalex.org/) | 250M+ | All disciplines | Email only |
| [Semantic Scholar](https://www.semanticscholar.org/) | 200M+ | CS, Biomedical, General | Optional |
| [Scopus](https://www.scopus.com/) | 90M+ | Peer-reviewed journals | Required |
| [SciX/NASA ADS](https://scixplorer.org/) | 30M+ | Astrophysics, Earth science, Planetary | Required |

## Installation

Requires Python 3.13+ and [uv](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/tofunori/mcp-scholar.git
cd mcp-scholar
uv sync
```

## Configuration

Create a `.env` file or set environment variables:

```bash
# Required: OpenAlex polite pool (your email)
OPENALEX_MAILTO=your.email@example.com

# Optional: Scopus API key
# Get one at https://dev.elsevier.com/
SCOPUS_API_KEY=your_scopus_key

# Optional: SciX/NASA ADS API key
# Get one at https://ui.adsabs.harvard.edu/user/settings/token
SCIX_API_KEY=your_scix_token
```

Semantic Scholar works without an API key (rate limited to 1 req/sec).

## Claude Code Setup

### Option 1: HTTP/SSE Server (Recommended)

Run as a shared HTTP server to avoid duplicating processes across Claude sessions.

**1. Start the HTTP server:**
```bash
uv run python -m src.server_http
# Server runs on http://127.0.0.1:8323/mcp
```

**2. Configure Claude Code** (`~/.claude.json`):
```json
{
  "mcpServers": {
    "scholar": {
      "type": "sse",
      "url": "http://127.0.0.1:8323/mcp"
    }
  }
}
```

**3. (Optional) Run as systemd service** (Linux):
```bash
# Create ~/.config/systemd/user/scholar-mcp.service
[Unit]
Description=Scholar MCP HTTP Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/mcp-scholar
Environment="OPENALEX_MAILTO=your.email@example.com"
Environment="SCOPUS_API_KEY=your_scopus_key"
Environment="SCIX_API_KEY=your_scix_token"
ExecStart=/path/to/uv run python -m src.server_http
Restart=on-failure

[Install]
WantedBy=default.target

# Enable and start
systemctl --user daemon-reload
systemctl --user enable --now scholar-mcp
```

### Option 2: Stdio (per-session)

Add to your `~/.claude.json` (Windows: `C:\Users\<user>\.claude.json`):

```json
{
  "mcpServers": {
    "scholar": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "/path/to/mcp-scholar", "run", "python", "-m", "src.server"],
      "env": {
        "OPENALEX_MAILTO": "your.email@example.com",
        "SCOPUS_API_KEY": "your_scopus_key",
        "SCIX_API_KEY": "your_scix_token"
      }
    }
  }
}
```

## Available Tools

| Tool | Description |
|------|-------------|
| `search_papers` | Search papers by keywords across all configured sources |
| `get_paper` | Get paper details by DOI, OpenAlex ID, S2 ID, Scopus EID, or ADS bibcode |
| `get_citations` | Get papers citing a given paper |
| `get_references` | Get references (bibliography) of a paper |
| `get_similar_papers` | Find similar papers (Semantic Scholar recommendations) |
| `get_api_status` | Check API configuration and quotas |

## Usage Examples

```python
# Search across all sources
search_papers("glacier albedo remote sensing", limit=10)

# Search specific sources
search_papers("MERRA-2 reanalysis", sources=["scix", "openalex"])

# Filter by year
search_papers("black carbon snow", year_min=2020, year_max=2024)

# Get paper by DOI
get_paper("10.1175/JCLI-D-16-0758.1")

# Get citations
get_citations("10.1175/JCLI-D-16-0758.1", limit=50)

# Find similar papers
get_similar_papers("10.1038/s41586-021-03426-z")
```

## Rate Limits

| Source | Rate Limit | Daily Limit |
|--------|------------|-------------|
| OpenAlex | 10 req/sec | Unlimited |
| Semantic Scholar | 1 req/sec (no key) | Unlimited |
| Scopus | 2 req/sec | ~20,000/week |
| SciX/NASA ADS | 5 req/sec | 5,000/day |

## License

MIT
