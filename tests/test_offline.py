"""Offline tests: every HTTP call goes through an httpx.MockTransport.

The OpenAlex and Crossref fixtures are real API responses for the query
"black carbon glacier albedo" (trimmed), captured 2026-09-29.
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from src.rate_limiting import reset_limiters
from src.server import format_api_status, format_paper_not_found
from src.services import Orchestrator
from src.sources import OpenAlexSource, SemanticScholarSource

FIXTURES = Path(__file__).parent / "fixtures"
QUERY = "black carbon glacier albedo"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fresh_limiters():
    # Limiters hold an asyncio.Lock; each test runs its own event loop.
    reset_limiters()
    yield
    reset_limiters()


@pytest.fixture
def mock_http(monkeypatch):
    """Route every httpx.AsyncClient through `handler`; records requests."""
    state = {"handler": None, "requests": []}

    def dispatch(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        return state["handler"](request)

    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(dispatch)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    return state


def make_orchestrator(**keys) -> Orchestrator:
    defaults = dict(
        openalex_mailto="test@example.org",
        s2_api_key="",
        scopus_api_key="",
        scix_api_key="",
        core_api_key="",
        openalex_api_key="",
    )
    defaults.update(keys)
    orch = Orchestrator(**defaults)
    # Orchestrator falls back to the process config for empty values;
    # force the test values so the environment cannot leak in.
    for name, value in defaults.items():
        setattr(orch, name, value)
    return orch


# --- OpenAlex request parameters -------------------------------------------


def test_openalex_sends_api_key_and_caps_per_page(mock_http):
    mock_http["handler"] = lambda req: httpx.Response(200, json={"results": []})

    async def run():
        async with OpenAlexSource("me@example.org", api_key="KEY") as src:
            await src.search(QUERY, limit=500)

    asyncio.run(run())
    params = mock_http["requests"][0].url.params
    assert params["api_key"] == "KEY"
    assert int(params["per-page"]) == 100


def test_openalex_without_key_or_mailto_sends_neither(mock_http):
    mock_http["handler"] = lambda req: httpx.Response(200, json={"results": []})

    async def run():
        async with OpenAlexSource() as src:
            await src.search(QUERY, limit=5)

    asyncio.run(run())
    params = mock_http["requests"][0].url.params
    assert "api_key" not in params
    assert "mailto" not in params


# --- Search pipeline on real payloads -------------------------------------


def test_search_merges_real_openalex_and_crossref_results(mock_http):
    openalex = load("openalex_search.json")
    crossref = load("crossref_search.json")

    def handler(req):
        if req.url.host == "api.openalex.org":
            return httpx.Response(200, json=openalex)
        if req.url.host == "api.crossref.org":
            return httpx.Response(200, json=crossref)
        raise AssertionError(f"unexpected host {req.url.host}")

    mock_http["handler"] = handler
    orch = make_orchestrator()
    papers, meta = asyncio.run(
        orch.search(QUERY, sources=["openalex", "crossref"], limit=5)
    )

    assert meta["errors"] == []
    assert meta["results_per_source"] == {"openalex": 3, "crossref": 5}
    # 10.5194/tc-9-1385-2015 is returned by both sources and must be merged.
    assert meta["duplicates_removed"] == 1
    merged = [p for p in papers if p.doi and p.doi.lower() == "10.5194/tc-9-1385-2015"]
    assert len(merged) == 1
    assert {s.value for s in merged[0].sources} == {"openalex", "crossref"}
    assert len(papers) == 7


# --- Errors are reported, not disguised as "not found" --------------------


def test_get_paper_reports_outage_instead_of_not_found(mock_http):
    mock_http["handler"] = lambda req: httpx.Response(503)
    orch = make_orchestrator()

    paper, meta = asyncio.run(orch.get_paper("10.5194/tc-9-1385-2015"))

    assert paper is None
    assert len(meta["errors"]) == len(meta["sources_queried"])
    text = format_paper_not_found("10.5194/tc-9-1385-2015", meta)
    assert text.startswith("Verification impossible")


def test_get_paper_genuine_404_is_not_an_error(mock_http):
    def handler(req):
        if req.url.host == "www.ebi.ac.uk":
            return httpx.Response(200, json={"resultList": {"result": []}})
        return httpx.Response(404)

    mock_http["handler"] = handler
    orch = make_orchestrator()

    paper, meta = asyncio.run(orch.get_paper("10.9999/does-not-exist"))

    assert paper is None
    assert meta["errors"] == []
    assert format_paper_not_found("10.9999/does-not-exist", meta).startswith(
        "Article non trouve"
    )


def test_citations_and_references_report_source_errors(mock_http):
    mock_http["handler"] = lambda req: httpx.Response(403)
    orch = make_orchestrator()

    _, cit_meta = asyncio.run(orch.get_citations("10.5194/tc-9-1385-2015"))
    _, ref_meta = asyncio.run(orch.get_references("10.5194/tc-9-1385-2015"))

    assert {e.split(":")[0] for e in cit_meta["errors"]} == {"openalex", "semantic_scholar"}
    assert {e.split(":")[0] for e in ref_meta["errors"]} == {
        "openalex",
        "semantic_scholar",
        "crossref",
    }


def test_author_search_reports_source_errors(mock_http):
    mock_http["handler"] = lambda req: httpx.Response(500)
    orch = make_orchestrator()

    authors, meta = asyncio.run(orch.get_author("Mark Flanner"))

    assert authors == []
    assert len(meta["errors"]) == 2


# --- Rate limiting --------------------------------------------------------


def test_sources_share_one_limiter_per_api():
    assert OpenAlexSource().limiter is OpenAlexSource(api_key="x").limiter


# --- Semantic Scholar list endpoints ----------------------------------------


def test_s2_citations_and_references_do_not_request_tldr(mock_http):
    # S2 answers 400 "Unrecognized or unsupported fields: [tldr]" on these.
    mock_http["handler"] = lambda req: httpx.Response(200, json={"data": []})

    async def run():
        async with SemanticScholarSource() as src:
            await src.get_citations("DOI:10.5194/tc-9-1385-2015", limit=5)
            await src.get_references("DOI:10.5194/tc-9-1385-2015", limit=5)

    asyncio.run(run())
    assert len(mock_http["requests"]) == 2
    for req in mock_http["requests"]:
        assert "tldr" not in req.url.params["fields"]


# --- get_api_status really calls each source -------------------------------


def test_api_status_reports_refused_key_as_error(mock_http):
    def handler(req):
        if req.url.host == "api.elsevier.com":
            return httpx.Response(401)
        if req.url.host == "api.openalex.org":
            return httpx.Response(200, json=load("openalex_search.json"))
        if req.url.host == "api.crossref.org":
            return httpx.Response(200, json=load("crossref_search.json"))
        if req.url.host == "www.ebi.ac.uk":
            return httpx.Response(200, json={"resultList": {"result": []}})
        return httpx.Response(200, json={"data": []})

    mock_http["handler"] = handler
    orch = make_orchestrator(scopus_api_key="EXPIRED")

    text = asyncio.run(format_api_status(orch))

    assert "- **scopus**: ERREUR" in text
    assert "401" in text
    assert "- **openalex**: OK" in text
    assert "- **scix**: Non configure" in text
