# mcp-scholar

MCP server for searching scientific papers across multiple academic databases.

## Features

- Multi-source search: OpenAlex, Semantic Scholar, Scopus
- Automatic deduplication and metadata merging
- Rate limiting with adaptive backoff
- Citations and references retrieval
- Similar papers recommendations (via SPECTER embeddings)

## Installation

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/tofunori/mcp-scholar.git
cd mcp-scholar
uv sync
```

## Configuration

Create a `.env` file (or set environment variables):

```bash
# Required for OpenAlex polite pool (your email)
OPENALEX_MAILTO=your.email@example.com

# Optional: Scopus API key (get one at https://dev.elsevier.com/)
SCOPUS_API_KEY=your_scopus_key
```

Semantic Scholar works without an API key (1 req/sec rate limit).

## Claude Code Setup

Add to your `~/.claude.json`:

```json
{
  "mcpServers": {
    "scholar": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "/path/to/mcp-scholar", "run", "python", "-m", "src.server"],
      "env": {
        "OPENALEX_MAILTO": "your.email@example.com",
        "SCOPUS_API_KEY": "your_scopus_key"
      }
    }
  }
}
```

## Available Tools

| Tool | Description |
|------|-------------|
| `search_papers` | Search papers by keywords across all sources |
| `get_paper` | Get paper details by DOI, OpenAlex ID, S2 ID, or Scopus EID |
| `get_citations` | Get papers citing a given paper |
| `get_references` | Get references (bibliography) of a paper |
| `get_similar_papers` | Find similar papers using SPECTER embeddings |
| `get_api_status` | Check API configuration and status |

## Usage Examples

```
# Search for papers
search_papers("glacier albedo remote sensing", limit=10)

# Get paper by DOI
get_paper("10.1038/s41586-021-03426-z")

# Get citations
get_citations("10.1038/s41586-021-03426-z", limit=50)
```

## License

MIT
