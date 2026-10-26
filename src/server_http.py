#!/usr/bin/env python3
"""
Serveur HTTP/SSE pour scholar-mcp (partage entre sessions).
Port: 8323

Utilise fastmcp standalone (pas mcp.server.fastmcp) pour supporter host/port.
"""

import os
import logging
from typing import Optional

# Use standalone fastmcp (supports host/port in run())
from fastmcp import FastMCP
from starlette.responses import PlainTextResponse

from .config import config
from .hosted import HostedSettings, KeyStore
from .hosted.oauth import build_auth_provider, current_identity
from .hosted.web import register_routes
from .services import Orchestrator
from .server import (
    format_search_results,
    format_paper_details,
    format_paper_not_found,
    format_citation_results,
    format_similar_results,
    format_api_status,
    format_author_results,
    _safe_int,
)

# Configuration du logging
logging.basicConfig(
    level=getattr(logging, config.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# httpx logs full request URLs at INFO, including OpenAlex's api_key
# query parameter; keep it quiet so keys never reach the service logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Hosted multi-user mode (SCHOLAR_AUTH=google|github): sign-in required,
# and each user's tool calls run with the API keys they saved on /keys.
hosted = HostedSettings.from_env()
keystore: Optional[KeyStore] = None

if hosted is None:
    mcp = FastMCP("scholar-mcp")
else:
    keystore = KeyStore(hosted.data_dir / "keys.db", hosted.derive_key("keystore"))
    mcp = FastMCP("scholar-mcp", auth=build_auth_provider(hosted))
    register_routes(mcp, hosted, keystore)



@mcp.custom_route("/health", methods=["GET"])
async def health(request) -> PlainTextResponse:
    """Liveness probe for Docker and the tunnel; no auth, no upstream calls."""
    return PlainTextResponse("ok")


# Orchestrateur global (mode personnel uniquement)
orchestrator: Optional[Orchestrator] = None


def get_orchestrator() -> Orchestrator:
    """Retourne l'orchestrateur, le cree si necessaire.

    In hosted mode this builds one from the caller's own keys and never
    falls back to the keys in the server's environment.
    """
    global orchestrator
    if hosted is not None:
        identity = current_identity(hosted)
        if identity is None:
            raise PermissionError("Sign-in required")
        return Orchestrator(
            openalex_mailto=hosted.contact_email or None,
            use_config_keys=False,
            **keystore.get(identity.user_id),
        )
    if orchestrator is None:
        orchestrator = Orchestrator()
    return orchestrator


@mcp.tool()
async def search_papers(
    query: str,
    sources: list = None,
    limit: int = 10,
    year_min: int = None,
    year_max: int = None,
) -> str:
    """
    Recherche d'articles scientifiques sur plusieurs sources (OpenAlex, Semantic Scholar, Scopus, SciX/NASA ADS).
    Retourne les articles dedupliques avec metadonnees fusionnees.
    """
    orch = get_orchestrator()
    papers, metadata = await orch.search(
        query=query,
        sources=sources,
        limit=_safe_int(limit) or 10,
        year_min=_safe_int(year_min),
        year_max=_safe_int(year_max),
    )
    return format_search_results(papers, metadata)


@mcp.tool()
async def get_paper(paper_id: str) -> str:
    """
    Recupere les details complets d'un article par son identifiant.
    Accepte DOI, OpenAlex ID, S2 Paper ID, ou Scopus EID.
    """
    orch = get_orchestrator()
    paper, metadata = await orch.get_paper(paper_id)
    if paper:
        return format_paper_details(paper)
    else:
        return format_paper_not_found(paper_id, metadata)


@mcp.tool()
async def get_citations(paper_id: str, limit: int = 50) -> str:
    """
    Recupere les articles qui citent un article donne.
    Utile pour explorer l'impact et les travaux subsequents.
    """
    orch = get_orchestrator()
    papers, metadata = await orch.get_citations(
        paper_id=paper_id,
        limit=_safe_int(limit) or 50,
    )
    return format_citation_results(papers, metadata, "citant")


@mcp.tool()
async def get_references(paper_id: str, limit: int = 50) -> str:
    """
    Recupere la bibliographie d'un article (articles cites).
    Utile pour explorer les travaux anterieurs.
    """
    orch = get_orchestrator()
    papers, metadata = await orch.get_references(
        paper_id=paper_id,
        limit=_safe_int(limit) or 50,
    )
    return format_citation_results(papers, metadata, "cites")


@mcp.tool()
async def get_similar_papers(paper_id: str, limit: int = 10) -> str:
    """
    Trouve des articles similaires en utilisant les embeddings SPECTER de Semantic Scholar.
    Ideal pour decouvrir des travaux connexes.
    """
    orch = get_orchestrator()
    papers = await orch.get_similar_papers(
        paper_id=paper_id,
        limit=_safe_int(limit) or 10,
    )
    return format_similar_results(papers)


@mcp.tool()
async def get_api_status() -> str:
    """Affiche le statut des APIs configurees et leurs quotas."""
    orch = get_orchestrator()
    status = format_api_status(orch)
    if hosted is not None:
        status += (
            "\n\nCes sources utilisent vos propres cles API. "
            f"Ajoutez-les ou modifiez-les sur {hosted.base_url}/keys"
        )
    return status


@mcp.tool()
async def get_author(query: str, limit: int = 10) -> str:
    """
    Recherche un auteur par nom ou recupere son profil par ID.
    Accepte: nom d'auteur (recherche), OpenAlex ID (A...), Semantic Scholar ID, ORCID (0000-...), ou Scopus Author ID.
    Retourne le profil avec metriques (h-index, citations, publications).
    """
    orch = get_orchestrator()
    authors, metadata = await orch.get_author(
        author_query=query,
        limit=_safe_int(limit) or 10,
    )
    return format_author_results(authors, metadata)


def main():
    """Point d'entree du serveur HTTP/SSE."""
    # 0.0.0.0 inside a container; keep the 127.0.0.1 default on a desktop.
    host = os.getenv("SCHOLAR_HOST", "127.0.0.1")
    port = int(os.getenv("SCHOLAR_PORT", "8323"))
    print("=" * 60)
    print("SCHOLAR MCP HTTP SERVER")
    print("=" * 60)
    print(f"Starting on http://{host}:{port}/mcp")
    if hosted is not None:
        print(f"Hosted mode: {hosted.provider} sign-in, site at {hosted.base_url}/")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    logger.info("Demarrage scholar-mcp en mode Streamable HTTP...")
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
