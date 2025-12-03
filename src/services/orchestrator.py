"""Orchestrateur de requetes multi-sources."""

import asyncio
import logging
from typing import Optional

from ..config import config
from ..models import Paper
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
