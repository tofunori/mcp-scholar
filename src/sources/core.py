"""Driver CORE (core.ac.uk) pour la recherche d'articles Open Access."""

import asyncio
from typing import Optional

from ..models import Paper, Author, PaperSource
from ..rate_limiting import RateLimiter, RateLimitConfig, get_limiter
from .base import BaseSource, SourceError


class CORESource(BaseSource):
    """Source CORE pour les articles Open Access.

    CORE agregue des millions d'articles Open Access depuis
    des repositories institutionnels et editeurs.

    API Documentation: https://api.core.ac.uk/docs/v3
    Rate limit: 25 req/min pour compte personnel gratuit.
    """

    BASE_URL = "https://api.core.ac.uk/v3"

    # Le backend Azure Search de CORE renvoie par intermittence un HTTP 500
    # dont le corps enveloppe un "503 ... not enough resources ... reaching
    # the limits". C'est transitoire et sans rapport avec l'auth : on reessaie.
    SEARCH_MAX_ATTEMPTS = 4          # 1 tentative + 3 retries
    SEARCH_RETRY_BACKOFF = 1.5       # secondes (plancher entre tentatives)
    # Statuts consideres comme transitoires cote CORE (5xx + 429).
    _TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

    def __init__(self, api_key: str, limiter: Optional[RateLimiter] = None):
        if limiter is None:
            limiter = get_limiter(
                "core",
                RateLimitConfig(
                    requests_per_second=0.15,  # ~9 req/min (CORE plafonne a 10/min)
                    daily_limit=10_000,
                    burst_size=3,
                )
            )
        super().__init__(limiter)
        self.api_key = api_key

    def _default_headers(self) -> dict:
        """Headers par defaut avec authentification."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    async def search(
        self,
        query: str,
        limit: int = 10,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        **kwargs,
    ) -> list[Paper]:
        """Recherche d'articles par mots-cles.

        Args:
            query: Requete de recherche
            limit: Nombre max de resultats
            year_min: Annee minimum de publication
            year_max: Annee maximum de publication

        Returns:
            Liste de Papers
        """
        # Construire la requete avec filtres de date
        q = query
        if year_min is not None or year_max is not None:
            year_filter = ""
            if year_min is not None:
                year_filter += f" yearPublished>={year_min}"
            if year_max is not None:
                year_filter += f" yearPublished<={year_max}"
            q = f"({query}) AND ({year_filter.strip()})"

        # CORE v3 search = POST JSON. Le backend Azure Search renvoie par
        # intermittence un HTTP 500 enveloppant un "503 not enough resources"
        # (erreur de capacite, PAS d'auth -- on recoit aussi des 200). On
        # reessaie quelques fois avec un court backoff pour absorber ces 5xx
        # transitoires. La boucle est bornee et ne leve jamais : echec final
        # => [] (comme les autres chemins de cette source).
        #
        # Note de design : le retry est scope a CORE ici. La boucle de retry
        # partagee de base._request n'est PAS modifiee (elle ne reessaie que
        # sur 429), donc les 6 autres sources ne sont pas affectees.
        for attempt in range(self.SEARCH_MAX_ATTEMPTS):
            try:
                response = await self._request(
                    "POST",
                    f"{self.BASE_URL}/search/works",
                    headers=self._default_headers(),
                    json={"q": q, "limit": min(limit, 100)},
                )
            except SourceError as exc:
                # Ne reessayer que sur un statut transitoire (5xx / 429).
                # Une vraie erreur (401 auth, 400 requete) n'est pas reessayee.
                if exc.status_code not in self._TRANSIENT_STATUS:
                    return []
                if attempt + 1 >= self.SEARCH_MAX_ATTEMPTS:
                    break
                # Backoff : honorer x-ratelimit-retry-after si present (le
                # limiter l'aura deja arme via report_429 pour un 429), sinon
                # un court delai fixe. acquire() au prochain tour respecte le
                # budget de debit, donc on ne depasse pas le rate limit.
                await asyncio.sleep(self.SEARCH_RETRY_BACKOFF)
                continue

            data = response.json()
            papers = []
            for result in data.get("results", []):
                paper = self._parse_work(result)
                if paper:
                    papers.append(paper)
            return papers

        # Tous les essais ont echoue sur un 5xx transitoire : best-effort [].
        return []

    async def get_by_id(self, paper_id: str) -> Optional[Paper]:
        """Recupere un article par CORE ID.

        Args:
            paper_id: CORE ID (numerique) ou DOI

        Returns:
            Paper ou None si non trouve
        """
        # Si c'est un DOI, chercher par DOI
        if paper_id.startswith("10."):
            return await self._get_by_doi(paper_id)

        # Sinon, c'est un CORE ID
        try:
            response = await self._request(
                "GET",
                f"{self.BASE_URL}/works/{paper_id}",
                headers=self._default_headers(),
            )
            data = response.json()
            return self._parse_work(data)
        except SourceError as exc:
            if not exc.is_not_found:
                raise
            return None

    async def _get_by_doi(self, doi: str) -> Optional[Paper]:
        """Recherche un article par DOI."""
        params = {
            "q": f'doi:"{doi}"',
            "limit": 1,
        }

        try:
            response = await self._request(
                "GET",
                f"{self.BASE_URL}/search/works/",
                headers=self._default_headers(),
                params=params,
            )
            data = response.json()
            results = data.get("results", [])
            if results:
                return self._parse_work(results[0])
        except SourceError as exc:
            if not exc.is_not_found:
                raise
            pass
        return None

    async def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """CORE ne supporte pas la recherche de citations.

        Returns:
            Liste vide
        """
        return []

    async def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """CORE ne supporte pas la recherche de references.

        Returns:
            Liste vide
        """
        return []

    def _parse_work(self, work: dict) -> Optional[Paper]:
        """Convertit un work CORE en Paper.

        Args:
            work: Donnees brutes CORE

        Returns:
            Paper ou None si donnees invalides
        """
        if not work:
            return None

        title = work.get("title")
        if not title:
            return None

        # Extraire le CORE ID
        core_id = str(work.get("id")) if work.get("id") else None

        # Extraire le DOI
        doi = work.get("doi")
        if doi:
            # Nettoyer le DOI si c'est une URL
            doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")

        # Extraire l'annee
        year = work.get("yearPublished")
        if year:
            try:
                year = int(year)
            except (ValueError, TypeError):
                year = None

        # Parser les auteurs
        authors = self._parse_authors(work.get("authors", []))

        # Open Access info
        is_oa = True  # CORE est Open Access par definition
        download_url = work.get("downloadUrl")

        # Source/journal info
        journal = None
        publisher = work.get("publisher")
        if work.get("journals"):
            journals = work.get("journals", [])
            if journals:
                journal = journals[0].get("title")

        # Abstract
        abstract = work.get("abstract")
        if abstract:
            # Limiter la taille de l'abstract
            abstract = abstract[:5000] if len(abstract) > 5000 else abstract

        return Paper(
            core_id=core_id,
            doi=doi,
            title=title,
            year=year,
            publication_date=work.get("publishedDate"),
            abstract=abstract,
            citation_count=work.get("citationCount"),
            reference_count=len(work.get("references", [])) if work.get("references") else None,
            is_open_access=is_oa,
            open_access_url=download_url,
            pdf_url=download_url,
            authors=authors,
            journal=journal,
            publisher=publisher,
            fields_of_study=work.get("fieldOfStudy", []) if work.get("fieldOfStudy") else [],
            publication_types=[work.get("documentType")] if work.get("documentType") else [],
            sources=[PaperSource.CORE],
            primary_source=PaperSource.CORE,
            raw_data={"core": work},
        )

    def _parse_authors(self, authors_data: list) -> list[Author]:
        """Parse les auteurs depuis les donnees CORE.

        Args:
            authors_data: Liste d'auteurs CORE

        Returns:
            Liste d'Authors
        """
        authors = []
        for author_data in authors_data or []:
            if isinstance(author_data, str):
                # Format simple: juste le nom
                authors.append(Author(name=author_data))
            elif isinstance(author_data, dict):
                name = author_data.get("name")
                if name:
                    authors.append(Author(
                        name=name,
                        orcid=author_data.get("orcid"),
                    ))
        return authors
