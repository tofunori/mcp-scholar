"""Driver Crossref pour la recherche d'articles via metadonnees DOI."""

from typing import Optional

from ..models import Paper, Author, PaperSource
from ..rate_limiting import RateLimiter, RateLimitConfig
from .base import BaseSource, SourceError


class CrossrefSource(BaseSource):
    """Source Crossref pour les metadonnees DOI.

    Crossref est le registre officiel des DOI pour les publications
    scientifiques. Pas de cle API requise, mais un email est recommande
    pour le "polite pool" (meilleur rate limit).

    API Documentation: https://api.crossref.org/swagger-ui/index.html
    Rate limit: ~50 req/sec avec polite pool (email dans User-Agent)
    """

    BASE_URL = "https://api.crossref.org"

    def __init__(self, mailto: str, limiter: Optional[RateLimiter] = None):
        if limiter is None:
            limiter = RateLimiter(
                "crossref",
                RateLimitConfig(
                    requests_per_second=10.0,  # Conservateur pour polite pool
                    daily_limit=100_000,
                    burst_size=10,
                )
            )
        super().__init__(limiter)
        self.mailto = mailto

    def _default_headers(self) -> dict:
        """Headers par defaut avec User-Agent polite."""
        return {
            "User-Agent": f"Scholar-MCP/1.0 (mailto:{self.mailto})",
            "Accept": "application/json",
        }

    def _default_params(self) -> dict:
        """Parametres par defaut pour le polite pool."""
        return {
            "mailto": self.mailto,
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
        params = self._default_params()
        params["query"] = query
        params["rows"] = min(limit, 100)  # Crossref max 1000, mais on limite

        # Filtres de date
        filters = []
        if year_min is not None:
            filters.append(f"from-pub-date:{year_min}")
        if year_max is not None:
            filters.append(f"until-pub-date:{year_max}")
        if filters:
            params["filter"] = ",".join(filters)

        response = await self._request(
            "GET",
            f"{self.BASE_URL}/works",
            headers=self._default_headers(),
            params=params,
        )
        data = response.json()

        papers = []
        for item in data.get("message", {}).get("items", []):
            paper = self._parse_work(item)
            if paper:
                papers.append(paper)

        return papers

    async def get_by_id(self, paper_id: str) -> Optional[Paper]:
        """Recupere un article par DOI.

        Args:
            paper_id: DOI de l'article

        Returns:
            Paper ou None si non trouve
        """
        # Normaliser le DOI
        doi = paper_id
        if doi.startswith("https://doi.org/"):
            doi = doi.replace("https://doi.org/", "")
        elif doi.startswith("http://doi.org/"):
            doi = doi.replace("http://doi.org/", "")

        try:
            response = await self._request(
                "GET",
                f"{self.BASE_URL}/works/{doi}",
                headers=self._default_headers(),
                params=self._default_params(),
            )
            data = response.json()
            return self._parse_work(data.get("message", {}))
        except SourceError:
            return None

    async def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Crossref ne supporte pas directement la recherche de citations.

        Returns:
            Liste vide
        """
        return []

    async def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Recupere les references d'un article via les metadonnees.

        Crossref inclut les references dans les metadonnees de l'article,
        mais avec des informations limitees (pas toujours de DOI).

        Args:
            paper_id: DOI de l'article
            limit: Nombre max de references

        Returns:
            Liste de Papers (informations partielles)
        """
        paper = await self.get_by_id(paper_id)
        if not paper:
            return []

        # Extraire les references du raw_data
        raw = paper.raw_data.get("crossref", {})
        references = raw.get("reference", [])

        papers = []
        for ref in references[:limit]:
            ref_paper = self._parse_reference(ref)
            if ref_paper:
                papers.append(ref_paper)

        return papers

    def _parse_work(self, work: dict) -> Optional[Paper]:
        """Convertit un work Crossref en Paper.

        Args:
            work: Donnees brutes Crossref

        Returns:
            Paper ou None si donnees invalides
        """
        if not work:
            return None

        # Extraire le titre (liste dans Crossref)
        titles = work.get("title", [])
        title = titles[0] if titles else None
        if not title:
            return None

        # DOI
        doi = work.get("DOI")

        # Date de publication
        year = None
        pub_date = None
        if work.get("published"):
            date_parts = work["published"].get("date-parts", [[]])
            if date_parts and date_parts[0]:
                year = date_parts[0][0] if len(date_parts[0]) > 0 else None
                if len(date_parts[0]) >= 3:
                    pub_date = f"{date_parts[0][0]}-{date_parts[0][1]:02d}-{date_parts[0][2]:02d}"

        # Auteurs
        authors = self._parse_authors(work.get("author", []))

        # Journal/Container
        container = work.get("container-title", [])
        journal = container[0] if container else None

        # Open Access
        is_oa = False
        oa_url = None
        if work.get("link"):
            for link in work["link"]:
                if link.get("content-type") == "application/pdf":
                    oa_url = link.get("URL")
                    break
                elif link.get("content-type") == "unspecified":
                    oa_url = link.get("URL")

        # Abstract (pas toujours disponible)
        abstract = work.get("abstract")
        if abstract:
            # Nettoyer les balises JATS
            import re
            abstract = re.sub(r"<[^>]+>", "", abstract)
            abstract = abstract[:5000] if len(abstract) > 5000 else abstract

        return Paper(
            doi=doi,
            title=title,
            year=year,
            publication_date=pub_date,
            abstract=abstract,
            citation_count=work.get("is-referenced-by-count"),
            reference_count=work.get("references-count"),
            is_open_access=is_oa,
            open_access_url=oa_url,
            authors=authors,
            journal=journal,
            publisher=work.get("publisher"),
            volume=work.get("volume"),
            issue=work.get("issue"),
            pages=work.get("page"),
            publication_types=[work.get("type")] if work.get("type") else [],
            sources=[PaperSource.CROSSREF],
            primary_source=PaperSource.CROSSREF,
            raw_data={"crossref": work},
        )

    def _parse_reference(self, ref: dict) -> Optional[Paper]:
        """Convertit une reference Crossref en Paper.

        Les references Crossref sont souvent incompletes.

        Args:
            ref: Donnees de reference Crossref

        Returns:
            Paper avec informations partielles
        """
        # Essayer d'extraire un titre
        title = ref.get("article-title") or ref.get("volume-title") or ref.get("unstructured")
        if not title:
            return None

        # DOI de la reference (pas toujours present)
        doi = ref.get("DOI")

        # Auteur (souvent juste le premier)
        authors = []
        if ref.get("author"):
            authors.append(Author(name=ref["author"]))

        # Annee
        year = ref.get("year")
        if year:
            try:
                year = int(year)
            except (ValueError, TypeError):
                year = None

        return Paper(
            doi=doi,
            title=title[:500] if len(title) > 500 else title,  # Limiter pour unstructured
            year=year,
            authors=authors,
            journal=ref.get("journal-title"),
            volume=ref.get("volume"),
            pages=ref.get("first-page"),
            sources=[PaperSource.CROSSREF],
            primary_source=PaperSource.CROSSREF,
            raw_data={"crossref_reference": ref},
        )

    def _parse_authors(self, authors_data: list) -> list[Author]:
        """Parse les auteurs depuis les donnees Crossref.

        Args:
            authors_data: Liste d'auteurs Crossref

        Returns:
            Liste d'Authors
        """
        authors = []
        for author_data in authors_data or []:
            # Construire le nom complet
            given = author_data.get("given", "")
            family = author_data.get("family", "")

            if family:
                name = f"{given} {family}".strip() if given else family
            else:
                name = author_data.get("name", "Unknown")

            # ORCID
            orcid = author_data.get("ORCID")
            if orcid:
                orcid = orcid.replace("http://orcid.org/", "").replace("https://orcid.org/", "")

            # Affiliation
            affiliations = []
            for aff in author_data.get("affiliation", []):
                if aff.get("name"):
                    affiliations.append(aff["name"])

            authors.append(Author(
                name=name,
                orcid=orcid,
                affiliations=affiliations,
            ))

        return authors
