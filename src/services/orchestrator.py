"""Orchestrateur de requetes multi-sources."""

import asyncio
import logging
from typing import Optional

from ..config import config
from ..models import Paper, Author
from ..sources import OpenAlexSource, SemanticScholarSource, ScopusSource, SciXSource
from .deduplicator import Deduplicator

logger = logging.getLogger(__name__)


class Orchestrator:
    """Orchestre les requetes paralleles sur plusieurs sources."""

    def __init__(
        self,
        openalex_mailto: Optional[str] = None,
        s2_api_key: Optional[str] = None,
        scopus_api_key: Optional[str] = None,
        scix_api_key: Optional[str] = None,
    ):
        self.openalex_mailto = openalex_mailto or config.openalex_mailto
        self.s2_api_key = s2_api_key or config.s2_api_key
        self.scopus_api_key = scopus_api_key or config.scopus_api_key
        self.scix_api_key = scix_api_key or config.scix_api_key

        self.deduplicator = Deduplicator(
            title_threshold=config.title_similarity_threshold
        )

        # Sources disponibles
        self._sources_config = {
            "openalex": self.openalex_mailto,
            "semantic_scholar": True,  # Toujours disponible
            "scopus": bool(self.scopus_api_key),
            "scix": bool(self.scix_api_key),
        }

    def get_available_sources(self) -> list[str]:
        """Retourne la liste des sources disponibles."""
        return [
            name for name, available in self._sources_config.items()
            if available
        ]

    async def search(
        self,
        query: str,
        sources: Optional[list[str]] = None,
        limit: int = 10,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        deduplicate: bool = True,
    ) -> tuple[list[Paper], dict]:
        """
        Recherche d'articles sur plusieurs sources en parallele.

        Args:
            query: Requete de recherche
            sources: Liste des sources a utiliser (defaut: toutes)
            limit: Nombre max d'articles par source
            year_min: Annee minimum
            year_max: Annee maximum
            deduplicate: Dedupliquer les resultats

        Returns:
            Tuple (articles, metadata)
        """
        if sources is None:
            sources = self.get_available_sources()

        # Filtrer les sources invalides
        sources = [s for s in sources if s in self._sources_config]

        if not sources:
            return [], {"error": "Aucune source disponible"}

        # Lancer les requetes en parallele
        tasks = []
        source_names = []

        for source in sources:
            if source == "openalex" and self.openalex_mailto:
                tasks.append(self._search_openalex(query, limit, year_min, year_max))
                source_names.append("openalex")

            elif source == "semantic_scholar":
                tasks.append(self._search_s2(query, limit, year_min, year_max))
                source_names.append("semantic_scholar")

            elif source == "scopus" and self.scopus_api_key:
                tasks.append(self._search_scopus(query, limit, year_min, year_max))
                source_names.append("scopus")

            elif source == "scix" and self.scix_api_key:
                tasks.append(self._search_scix(query, limit, year_min, year_max))
                source_names.append("scix")

        # Executer en parallele
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collecter les resultats
        all_papers = []
        metadata = {
            "sources_queried": source_names,
            "results_per_source": {},
            "errors": [],
        }

        for source_name, result in zip(source_names, results):
            if isinstance(result, Exception):
                logger.warning(f"Erreur {source_name}: {result}")
                metadata["errors"].append(f"{source_name}: {str(result)}")
                metadata["results_per_source"][source_name] = 0
            else:
                all_papers.extend(result)
                metadata["results_per_source"][source_name] = len(result)

        # Dedupliquer
        if deduplicate and all_papers:
            papers, duplicates_removed = self.deduplicator.deduplicate(all_papers)
            metadata["total_before_dedup"] = len(all_papers)
            metadata["duplicates_removed"] = duplicates_removed
        else:
            papers = all_papers

        metadata["total_results"] = len(papers)

        return papers, metadata

    async def get_paper(self, paper_id: str) -> Optional[Paper]:
        """Recupere un article par ID (DOI, S2 ID, etc.)."""
        # Essayer les sources dans l'ordre
        tasks = []

        if self.openalex_mailto:
            tasks.append(("openalex", self._get_openalex(paper_id)))

        tasks.append(("semantic_scholar", self._get_s2(paper_id)))

        if self.scopus_api_key:
            tasks.append(("scopus", self._get_scopus(paper_id)))

        if self.scix_api_key:
            tasks.append(("scix", self._get_scix(paper_id)))

        # Executer en parallele
        results = await asyncio.gather(
            *[t[1] for t in tasks],
            return_exceptions=True
        )

        # Collecter les resultats valides
        papers = []
        for (source_name, _), result in zip(tasks, results):
            if isinstance(result, Paper):
                papers.append(result)
            elif isinstance(result, Exception):
                logger.debug(f"Erreur {source_name} pour {paper_id}: {result}")

        if not papers:
            return None

        # Fusionner si plusieurs sources ont trouve l'article
        if len(papers) > 1:
            papers, _ = self.deduplicator.deduplicate(papers)

        return papers[0] if papers else None

    async def get_citations(
        self,
        paper_id: str,
        sources: Optional[list[str]] = None,
        limit: int = 100,
    ) -> tuple[list[Paper], dict]:
        """Recupere les articles citant un article donne."""
        if sources is None:
            sources = self.get_available_sources()

        tasks = []
        source_names = []

        for source in sources:
            if source == "openalex" and self.openalex_mailto:
                tasks.append(self._get_citations_openalex(paper_id, limit))
                source_names.append("openalex")

            elif source == "semantic_scholar":
                tasks.append(self._get_citations_s2(paper_id, limit))
                source_names.append("semantic_scholar")

            elif source == "scopus" and self.scopus_api_key:
                tasks.append(self._get_citations_scopus(paper_id, limit))
                source_names.append("scopus")

            elif source == "scix" and self.scix_api_key:
                tasks.append(self._get_citations_scix(paper_id, limit))
                source_names.append("scix")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_papers = []
        metadata = {"sources_queried": source_names, "results_per_source": {}}

        for source_name, result in zip(source_names, results):
            if isinstance(result, list):
                all_papers.extend(result)
                metadata["results_per_source"][source_name] = len(result)
            else:
                logger.warning(f"Erreur citations {source_name}: {result}")
                metadata["results_per_source"][source_name] = 0

        papers, duplicates = self.deduplicator.deduplicate(all_papers)
        metadata["total_results"] = len(papers)
        metadata["duplicates_removed"] = duplicates

        return papers, metadata

    async def get_references(
        self,
        paper_id: str,
        sources: Optional[list[str]] = None,
        limit: int = 100,
    ) -> tuple[list[Paper], dict]:
        """Recupere les references d'un article."""
        if sources is None:
            sources = self.get_available_sources()

        tasks = []
        source_names = []

        for source in sources:
            if source == "openalex" and self.openalex_mailto:
                tasks.append(self._get_references_openalex(paper_id, limit))
                source_names.append("openalex")

            elif source == "semantic_scholar":
                tasks.append(self._get_references_s2(paper_id, limit))
                source_names.append("semantic_scholar")

            elif source == "scix" and self.scix_api_key:
                tasks.append(self._get_references_scix(paper_id, limit))
                source_names.append("scix")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_papers = []
        metadata = {"sources_queried": source_names, "results_per_source": {}}

        for source_name, result in zip(source_names, results):
            if isinstance(result, list):
                all_papers.extend(result)
                metadata["results_per_source"][source_name] = len(result)
            else:
                logger.warning(f"Erreur references {source_name}: {result}")
                metadata["results_per_source"][source_name] = 0

        papers, duplicates = self.deduplicator.deduplicate(all_papers)
        metadata["total_results"] = len(papers)
        metadata["duplicates_removed"] = duplicates

        return papers, metadata

    async def get_similar_papers(
        self,
        paper_id: str,
        limit: int = 10,
    ) -> list[Paper]:
        """Recupere des articles similaires via S2 SPECTER."""
        async with SemanticScholarSource(self.s2_api_key) as source:
            return await source.get_recommendations([paper_id], limit=limit)

    # --- Methodes privees pour chaque source ---

    async def _search_openalex(
        self, query: str, limit: int, year_min: Optional[int], year_max: Optional[int]
    ) -> list[Paper]:
        async with OpenAlexSource(self.openalex_mailto) as source:
            return await source.search(query, limit, year_min, year_max)

    async def _search_s2(
        self, query: str, limit: int, year_min: Optional[int], year_max: Optional[int]
    ) -> list[Paper]:
        async with SemanticScholarSource(self.s2_api_key) as source:
            return await source.search(query, limit, year_min, year_max)

    async def _search_scopus(
        self, query: str, limit: int, year_min: Optional[int], year_max: Optional[int]
    ) -> list[Paper]:
        async with ScopusSource(self.scopus_api_key) as source:
            return await source.search(query, limit, year_min, year_max)

    async def _get_openalex(self, paper_id: str) -> Optional[Paper]:
        async with OpenAlexSource(self.openalex_mailto) as source:
            return await source.get_by_id(paper_id)

    async def _get_s2(self, paper_id: str) -> Optional[Paper]:
        async with SemanticScholarSource(self.s2_api_key) as source:
            return await source.get_by_id(paper_id)

    async def _get_scopus(self, paper_id: str) -> Optional[Paper]:
        async with ScopusSource(self.scopus_api_key) as source:
            return await source.get_by_id(paper_id)

    async def _get_citations_openalex(self, paper_id: str, limit: int) -> list[Paper]:
        async with OpenAlexSource(self.openalex_mailto) as source:
            return await source.get_citations(paper_id, limit)

    async def _get_citations_s2(self, paper_id: str, limit: int) -> list[Paper]:
        async with SemanticScholarSource(self.s2_api_key) as source:
            return await source.get_citations(paper_id, limit)

    async def _get_citations_scopus(self, paper_id: str, limit: int) -> list[Paper]:
        async with ScopusSource(self.scopus_api_key) as source:
            return await source.get_citations(paper_id, limit)

    async def _get_references_openalex(self, paper_id: str, limit: int) -> list[Paper]:
        async with OpenAlexSource(self.openalex_mailto) as source:
            return await source.get_references(paper_id, limit)

    async def _get_references_s2(self, paper_id: str, limit: int) -> list[Paper]:
        async with SemanticScholarSource(self.s2_api_key) as source:
            return await source.get_references(paper_id, limit)

    # --- Methodes privees SciX ---

    async def _search_scix(
        self, query: str, limit: int, year_min: Optional[int], year_max: Optional[int]
    ) -> list[Paper]:
        async with SciXSource(self.scix_api_key) as source:
            return await source.search(query, limit, year_min, year_max)

    async def _get_scix(self, paper_id: str) -> Optional[Paper]:
        async with SciXSource(self.scix_api_key) as source:
            return await source.get_by_id(paper_id)

    async def _get_citations_scix(self, paper_id: str, limit: int) -> list[Paper]:
        async with SciXSource(self.scix_api_key) as source:
            return await source.get_citations(paper_id, limit)

    async def _get_references_scix(self, paper_id: str, limit: int) -> list[Paper]:
        async with SciXSource(self.scix_api_key) as source:
            return await source.get_references(paper_id, limit)

    # --- Methodes Auteur ---

    async def get_author(
        self,
        author_query: str,
        limit: int = 10,
    ) -> tuple[list[Author], dict]:
        """
        Recherche un auteur par nom ou ID.

        Detecte automatiquement le type d'input:
        - Nom d'auteur (recherche)
        - OpenAlex ID (A...)
        - Semantic Scholar ID (numerique)
        - ORCID (0000-...)
        - Scopus Author ID

        Args:
            author_query: Nom ou ID de l'auteur
            limit: Nombre max de resultats pour la recherche par nom

        Returns:
            Tuple (liste auteurs, metadata)
        """
        metadata = {
            "query": author_query,
            "query_type": "unknown",
            "sources_queried": [],
            "results_per_source": {},
        }

        # Detecter le type d'input
        if self._is_author_id(author_query):
            # Lookup par ID
            metadata["query_type"] = "id_lookup"
            return await self._get_author_by_id(author_query, metadata)
        else:
            # Recherche par nom
            metadata["query_type"] = "name_search"
            return await self._search_authors_by_name(author_query, limit, metadata)

    def _is_author_id(self, query: str) -> bool:
        """Detecte si la query est un ID d'auteur."""
        # OpenAlex author ID
        if query.startswith("A") and len(query) > 5 and query[1:].isdigit():
            return True
        # ORCID
        if query.startswith("0000-") or query.startswith("https://orcid.org/"):
            return True
        # Semantic Scholar ID (numerique pur)
        if query.isdigit() and len(query) > 5:
            return True
        # Scopus Author ID (numerique)
        if query.isdigit() and len(query) >= 10:
            return True
        return False

    async def _get_author_by_id(
        self,
        author_id: str,
        metadata: dict,
    ) -> tuple[list[Author], dict]:
        """Recupere un auteur par ID depuis plusieurs sources."""
        tasks = []
        source_names = []

        if self.openalex_mailto:
            tasks.append(self._get_author_openalex(author_id))
            source_names.append("openalex")

        tasks.append(self._get_author_s2(author_id))
        source_names.append("semantic_scholar")

        if self.scopus_api_key:
            tasks.append(self._get_author_scopus(author_id))
            source_names.append("scopus")

        metadata["sources_queried"] = source_names

        results = await asyncio.gather(*tasks, return_exceptions=True)

        authors = []
        for source_name, result in zip(source_names, results):
            if isinstance(result, Author):
                authors.append(result)
                metadata["results_per_source"][source_name] = 1
            else:
                if isinstance(result, Exception):
                    logger.debug(f"Erreur {source_name} pour {author_id}: {result}")
                metadata["results_per_source"][source_name] = 0

        # Fusionner les resultats si meme auteur trouve sur plusieurs sources
        if len(authors) > 1:
            authors = [self._merge_authors(authors)]

        metadata["total_results"] = len(authors)
        return authors, metadata

    async def _search_authors_by_name(
        self,
        name: str,
        limit: int,
        metadata: dict,
    ) -> tuple[list[Author], dict]:
        """Recherche des auteurs par nom."""
        tasks = []
        source_names = []

        if self.openalex_mailto:
            tasks.append(self._search_authors_openalex(name, limit))
            source_names.append("openalex")

        tasks.append(self._search_authors_s2(name, limit))
        source_names.append("semantic_scholar")

        metadata["sources_queried"] = source_names

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_authors = []
        for source_name, result in zip(source_names, results):
            if isinstance(result, list):
                all_authors.extend(result)
                metadata["results_per_source"][source_name] = len(result)
            else:
                if isinstance(result, Exception):
                    logger.warning(f"Erreur recherche auteurs {source_name}: {result}")
                metadata["results_per_source"][source_name] = 0

        # Dedupliquer par ORCID
        authors = self._deduplicate_authors(all_authors)
        metadata["total_results"] = len(authors)
        metadata["duplicates_removed"] = len(all_authors) - len(authors)

        return authors[:limit], metadata

    def _deduplicate_authors(self, authors: list[Author]) -> list[Author]:
        """Deduplique les auteurs par ORCID."""
        seen_orcids = set()
        unique = []

        for author in authors:
            if author.orcid:
                if author.orcid in seen_orcids:
                    # Fusionner avec l'existant
                    for i, existing in enumerate(unique):
                        if existing.orcid == author.orcid:
                            unique[i] = self._merge_two_authors(existing, author)
                            break
                    continue
                seen_orcids.add(author.orcid)
            unique.append(author)

        return unique

    def _merge_authors(self, authors: list[Author]) -> Author:
        """Fusionne plusieurs profils du meme auteur."""
        if not authors:
            return Author(name="Unknown")
        if len(authors) == 1:
            return authors[0]

        result = authors[0]
        for author in authors[1:]:
            result = self._merge_two_authors(result, author)
        return result

    def _merge_two_authors(self, a1: Author, a2: Author) -> Author:
        """Fusionne deux profils d'auteur."""
        return Author(
            name=a1.name or a2.name,
            openalex_id=a1.openalex_id or a2.openalex_id,
            s2_author_id=a1.s2_author_id or a2.s2_author_id,
            scopus_author_id=a1.scopus_author_id or a2.scopus_author_id,
            orcid=a1.orcid or a2.orcid,
            affiliations=list(set(a1.affiliations + a2.affiliations))[:5],
            paper_count=max(a1.paper_count or 0, a2.paper_count or 0) or None,
            citation_count=max(a1.citation_count or 0, a2.citation_count or 0) or None,
            h_index=max(a1.h_index or 0, a2.h_index or 0) or None,
            homepage=a1.homepage or a2.homepage,
            sources=list(set(a1.sources + a2.sources)),
        )

    # --- Methodes privees auteur ---

    async def _get_author_openalex(self, author_id: str) -> Optional[Author]:
        async with OpenAlexSource(self.openalex_mailto) as source:
            return await source.get_author(author_id)

    async def _get_author_s2(self, author_id: str) -> Optional[Author]:
        async with SemanticScholarSource(self.s2_api_key) as source:
            return await source.get_author(author_id)

    async def _get_author_scopus(self, author_id: str) -> Optional[Author]:
        async with ScopusSource(self.scopus_api_key) as source:
            return await source.get_author(author_id)

    async def _search_authors_openalex(self, name: str, limit: int) -> list[Author]:
        async with OpenAlexSource(self.openalex_mailto) as source:
            return await source.search_authors(name, limit)

    async def _search_authors_s2(self, name: str, limit: int) -> list[Author]:
        async with SemanticScholarSource(self.s2_api_key) as source:
            return await source.search_authors(name, limit)
