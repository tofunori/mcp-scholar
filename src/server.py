"""Serveur MCP Scholar - Recherche d'articles scientifiques multi-sources."""

import logging
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .config import config
from .services import Orchestrator

# Configuration du logging
logging.basicConfig(
    level=getattr(logging, config.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Creer le serveur MCP
server = Server("scholar-mcp")

# Orchestrateur global
orchestrator: Optional[Orchestrator] = None


def get_orchestrator() -> Orchestrator:
    """Retourne l'orchestrateur, le cree si necessaire."""
    global orchestrator
    if orchestrator is None:
        orchestrator = Orchestrator()
    return orchestrator


def _safe_int(value) -> int | None:
    """Convertit une valeur en int de maniere securisee."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None



@server.list_tools()
async def list_tools() -> list[Tool]:
    """Liste les outils disponibles."""
    return [
        Tool(
            name="search_papers",
            description=(
                "Recherche d'articles scientifiques sur plusieurs sources "
                "(OpenAlex, Semantic Scholar, Scopus, SciX/NASA ADS). "
                "Retourne les articles dedupliques avec metadonnees fusionnees."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Requete de recherche (mots-cles)",
                    },
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Sources a interroger: openalex, semantic_scholar, scopus, scix. "
                            "Par defaut: toutes les sources configurees."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max d'articles par source (defaut: 10)",
                        "default": 10,
                    },
                    "year_min": {
                        "type": "integer",
                        "description": "Annee minimum de publication",
                    },
                    "year_max": {
                        "type": "integer",
                        "description": "Annee maximum de publication",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_paper",
            description=(
                "Recupere les details complets d'un article par son identifiant. "
                "Accepte DOI, OpenAlex ID, S2 Paper ID, ou Scopus EID."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "string",
                        "description": (
                            "Identifiant de l'article: DOI (10.xxxx/...), "
                            "OpenAlex ID (W...), S2 Paper ID, ou Scopus EID"
                        ),
                    },
                },
                "required": ["paper_id"],
            },
        ),
        Tool(
            name="get_citations",
            description=(
                "Recupere les articles qui citent un article donne. "
                "Utile pour explorer l'impact et les travaux subsequents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "string",
                        "description": "Identifiant de l'article (DOI, S2 ID, etc.)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max de citations (defaut: 50)",
                        "default": 50,
                    },
                },
                "required": ["paper_id"],
            },
        ),
        Tool(
            name="get_references",
            description=(
                "Recupere la bibliographie d'un article (articles cites). "
                "Utile pour explorer les travaux anterieurs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "string",
                        "description": "Identifiant de l'article (DOI, S2 ID, etc.)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max de references (defaut: 50)",
                        "default": 50,
                    },
                },
                "required": ["paper_id"],
            },
        ),
        Tool(
            name="get_similar_papers",
            description=(
                "Trouve des articles similaires en utilisant les embeddings SPECTER "
                "de Semantic Scholar. Ideal pour decouvrir des travaux connexes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "string",
                        "description": "Identifiant de l'article seed (DOI ou S2 ID)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre de recommandations (defaut: 10)",
                        "default": 10,
                    },
                },
                "required": ["paper_id"],
            },
        ),
        Tool(
            name="get_api_status",
            description=(
                "Affiche le statut des APIs configurees et leurs quotas."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_author",
            description=(
                "Recherche un auteur par nom ou recupere son profil par ID. "
                "Accepte: nom d'auteur (recherche), OpenAlex ID (A...), "
                "Semantic Scholar ID, ORCID (0000-...), ou Scopus Author ID. "
                "Retourne le profil avec metriques (h-index, citations, publications)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Nom de l'auteur (recherche) ou identifiant "
                            "(OpenAlex ID, S2 ID, ORCID, Scopus ID)"
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max de resultats pour la recherche par nom (defaut: 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute un outil MCP."""
    orch = get_orchestrator()

    try:
        if name == "search_papers":
            papers, metadata = await orch.search(
                query=arguments["query"],
                sources=arguments.get("sources"),
                limit=_safe_int(arguments.get("limit")) or 10,
                year_min=_safe_int(arguments.get("year_min")),
                year_max=_safe_int(arguments.get("year_max")),
            )
            return [TextContent(
                type="text",
                text=format_search_results(papers, metadata),
            )]

        elif name == "get_paper":
            paper = await orch.get_paper(arguments["paper_id"])
            if paper:
                return [TextContent(
                    type="text",
                    text=format_paper_details(paper),
                )]
            else:
                return [TextContent(
                    type="text",
                    text=f"Article non trouve: {arguments['paper_id']}",
                )]

        elif name == "get_citations":
            papers, metadata = await orch.get_citations(
                paper_id=arguments["paper_id"],
                limit=_safe_int(arguments.get("limit")) or 50,
            )
            return [TextContent(
                type="text",
                text=format_citation_results(papers, metadata, "citant"),
            )]

        elif name == "get_references":
            papers, metadata = await orch.get_references(
                paper_id=arguments["paper_id"],
                limit=_safe_int(arguments.get("limit")) or 50,
            )
            return [TextContent(
                type="text",
                text=format_citation_results(papers, metadata, "cites"),
            )]

        elif name == "get_similar_papers":
            papers = await orch.get_similar_papers(
                paper_id=arguments["paper_id"],
                limit=_safe_int(arguments.get("limit")) or 10,
            )
            return [TextContent(
                type="text",
                text=format_similar_results(papers),
            )]

        elif name == "get_api_status":
            return [TextContent(
                type="text",
                text=format_api_status(orch),
            )]

        elif name == "get_author":
            authors, metadata = await orch.get_author(
                author_query=arguments["query"],
                limit=_safe_int(arguments.get("limit")) or 10,
            )
            return [TextContent(
                type="text",
                text=format_author_results(authors, metadata),
            )]

        else:
            return [TextContent(
                type="text",
                text=f"Outil inconnu: {name}",
            )]

    except Exception as e:
        logger.exception(f"Erreur lors de l'execution de {name}")
        return [TextContent(
            type="text",
            text=f"Erreur: {str(e)}",
        )]


def format_search_results(papers: list, metadata: dict) -> str:
    """Formate les resultats de recherche."""
    lines = [
        f"## Resultats de recherche",
        f"",
        f"Sources interrogees: {', '.join(metadata.get('sources_queried', []))}",
        f"Total: {metadata.get('total_results', 0)} articles",
    ]

    if metadata.get("duplicates_removed"):
        lines.append(f"Doublons supprimes: {metadata['duplicates_removed']}")

    if metadata.get("errors"):
        lines.append(f"Erreurs: {', '.join(metadata['errors'])}")

    lines.append("")

    for i, paper in enumerate(papers, 1):
        lines.append(f"### {i}. {paper.title}")
        lines.append(f"- **Auteurs**: {paper.get_display_authors()}")
        lines.append(f"- **Annee**: {paper.year or 'N/A'}")

        if paper.doi:
            lines.append(f"- **DOI**: {paper.doi}")

        if paper.citation_count is not None:
            lines.append(f"- **Citations**: {paper.citation_count}")

        if paper.journal:
            lines.append(f"- **Journal**: {paper.journal}")

        if paper.is_open_access:
            lines.append(f"- **Open Access**: Oui")
            if paper.pdf_url:
                lines.append(f"- **PDF**: {paper.pdf_url}")

        sources_str = ", ".join(s.value for s in paper.sources)
        lines.append(f"- **Sources**: {sources_str}")

        if paper.abstract:
            abstract = paper.abstract[:300] + "..." if len(paper.abstract) > 300 else paper.abstract
            lines.append(f"- **Abstract**: {abstract}")

        lines.append("")

    return "\n".join(lines)


def format_paper_details(paper) -> str:
    """Formate les details d'un article."""
    lines = [
        f"## {paper.title}",
        f"",
        f"### Metadonnees",
        f"- **Auteurs**: {paper.get_display_authors()}",
        f"- **Annee**: {paper.year or 'N/A'}",
    ]

    if paper.doi:
        lines.append(f"- **DOI**: {paper.doi}")
    if paper.journal:
        lines.append(f"- **Journal**: {paper.journal}")
    if paper.publisher:
        lines.append(f"- **Editeur**: {paper.publisher}")
    if paper.volume:
        lines.append(f"- **Volume**: {paper.volume}")
    if paper.pages:
        lines.append(f"- **Pages**: {paper.pages}")

    lines.append("")
    lines.append("### Metriques")

    if paper.citation_count is not None:
        lines.append(f"- **Citations**: {paper.citation_count}")
    if paper.reference_count is not None:
        lines.append(f"- **References**: {paper.reference_count}")
    if paper.influential_citation_count is not None:
        lines.append(f"- **Citations influentes**: {paper.influential_citation_count}")

    if paper.is_open_access:
        lines.append("")
        lines.append("### Acces ouvert")
        lines.append("- **Statut**: Open Access")
        if paper.open_access_url:
            lines.append(f"- **URL OA**: {paper.open_access_url}")
        if paper.pdf_url:
            lines.append(f"- **PDF**: {paper.pdf_url}")

    if paper.fields_of_study:
        lines.append("")
        lines.append("### Domaines")
        lines.append(f"- {', '.join(paper.fields_of_study[:10])}")

    if paper.tldr:
        lines.append("")
        lines.append("### Resume (TLDR)")
        lines.append(paper.tldr)

    if paper.abstract:
        lines.append("")
        lines.append("### Abstract")
        lines.append(paper.abstract)

    lines.append("")
    lines.append("### Identifiants")
    if paper.doi:
        lines.append(f"- **DOI**: {paper.doi}")
    if paper.openalex_id:
        lines.append(f"- **OpenAlex**: {paper.openalex_id}")
    if paper.s2_paper_id:
        lines.append(f"- **S2 Paper ID**: {paper.s2_paper_id}")
    if paper.scopus_eid:
        lines.append(f"- **Scopus EID**: {paper.scopus_eid}")
    if paper.arxiv_id:
        lines.append(f"- **ArXiv**: {paper.arxiv_id}")

    sources_str = ", ".join(s.value for s in paper.sources)
    lines.append(f"- **Sources**: {sources_str}")

    return "\n".join(lines)


def format_citation_results(papers: list, metadata: dict, direction: str) -> str:
    """Formate les resultats de citations."""
    lines = [
        f"## Articles {direction}",
        f"",
        f"Total: {metadata.get('total_results', 0)} articles",
    ]

    if metadata.get("duplicates_removed"):
        lines.append(f"Doublons supprimes: {metadata['duplicates_removed']}")

    lines.append("")

    for i, paper in enumerate(papers[:20], 1):  # Limiter l'affichage
        lines.append(f"{i}. **{paper.title}** ({paper.year or 'N/A'})")
        lines.append(f"   - {paper.get_display_authors()}")
        if paper.doi:
            lines.append(f"   - DOI: {paper.doi}")
        if paper.citation_count is not None:
            lines.append(f"   - Citations: {paper.citation_count}")
        lines.append("")

    if len(papers) > 20:
        lines.append(f"... et {len(papers) - 20} autres articles")

    return "\n".join(lines)


def format_similar_results(papers: list) -> str:
    """Formate les resultats de similarite."""
    lines = [
        f"## Articles similaires (SPECTER)",
        f"",
        f"Total: {len(papers)} recommandations",
        "",
    ]

    for i, paper in enumerate(papers, 1):
        lines.append(f"{i}. **{paper.title}** ({paper.year or 'N/A'})")
        lines.append(f"   - {paper.get_display_authors()}")
        if paper.doi:
            lines.append(f"   - DOI: {paper.doi}")
        if paper.citation_count is not None:
            lines.append(f"   - Citations: {paper.citation_count}")
        if paper.tldr:
            lines.append(f"   - TLDR: {paper.tldr[:150]}...")
        lines.append("")

    return "\n".join(lines)


def format_api_status(orch: Orchestrator) -> str:
    """Formate le statut des APIs."""
    lines = [
        "## Statut des APIs",
        "",
    ]

    sources = orch.get_available_sources()

    for source in ["openalex", "semantic_scholar", "scopus", "scix"]:
        status = "OK" if source in sources else "Non configure"
        lines.append(f"- **{source}**: {status}")

    lines.append("")
    lines.append("### Configuration")
    lines.append(f"- OpenAlex mailto: {bool(orch.openalex_mailto)}")
    lines.append(f"- S2 API key: {bool(orch.s2_api_key)}")
    lines.append(f"- Scopus API key: {bool(orch.scopus_api_key)}")
    lines.append(f"- SciX API key: {bool(orch.scix_api_key)}")

    return "\n".join(lines)


def format_author_results(authors: list, metadata: dict) -> str:
    """Formate les resultats de recherche d'auteurs."""
    query_type = metadata.get("query_type", "unknown")

    if query_type == "id_lookup":
        title = "## Profil auteur"
    else:
        title = "## Resultats de recherche d'auteurs"

    lines = [
        title,
        "",
        f"Requete: {metadata.get('query', '')}",
        f"Type: {'Recherche par ID' if query_type == 'id_lookup' else 'Recherche par nom'}",
        f"Sources: {', '.join(metadata.get('sources_queried', []))}",
        f"Total: {metadata.get('total_results', 0)} auteur(s)",
    ]

    if metadata.get("duplicates_removed"):
        lines.append(f"Doublons supprimes: {metadata['duplicates_removed']}")

    lines.append("")

    for i, author in enumerate(authors, 1):
        lines.append(f"### {i}. {author.name}")

        # Identifiants
        ids = []
        if author.orcid:
            ids.append(f"ORCID: {author.orcid}")
        if author.openalex_id:
            ids.append(f"OpenAlex: {author.openalex_id}")
        if author.s2_author_id:
            ids.append(f"S2: {author.s2_author_id}")
        if author.scopus_author_id:
            ids.append(f"Scopus: {author.scopus_author_id}")
        if ids:
            lines.append(f"- **IDs**: {', '.join(ids)}")

        # Affiliations
        if author.affiliations:
            lines.append(f"- **Affiliations**: {', '.join(author.affiliations[:3])}")

        # Metriques
        metrics = []
        if author.h_index is not None:
            metrics.append(f"h-index: {author.h_index}")
        if author.citation_count is not None:
            metrics.append(f"Citations: {author.citation_count:,}")
        if author.paper_count is not None:
            metrics.append(f"Publications: {author.paper_count:,}")
        if metrics:
            lines.append(f"- **Metriques**: {', '.join(metrics)}")

        # Homepage
        if author.homepage:
            lines.append(f"- **Homepage**: {author.homepage}")

        # Sources
        if author.sources:
            lines.append(f"- **Sources**: {', '.join(author.sources)}")

        lines.append("")

    if not authors:
        lines.append("Aucun auteur trouve.")

    return "\n".join(lines)


async def main():
    """Point d'entree principal."""
    logger.info("Demarrage du serveur Scholar MCP...")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
