"""Driver Semantic Scholar pour la recherche d'articles."""

from typing import Optional

from ..models import Paper, Author, PaperSource
from ..rate_limiting import RateLimiter, RateLimitConfig
from .base import BaseSource, SourceError


class SemanticScholarSource(BaseSource):
    """Source Semantic Scholar pour les articles scientifiques.

    Utilise l'API publique sans cle (rate limit: 1 req/sec).
    """

    BASE_URL = "https://api.semanticscholar.org/graph/v1"
    RECOMMENDATIONS_URL = "https://api.semanticscholar.org/recommendations/v1"

    # Champs a recuperer
    PAPER_FIELDS = (
        "paperId,corpusId,externalIds,title,abstract,year,venue,"
        "citationCount,referenceCount,influentialCitationCount,"
        "authors,fieldsOfStudy,isOpenAccess,openAccessPdf,tldr"
    )

    def __init__(
        self, api_key: Optional[str] = None, limiter: Optional[RateLimiter] = None
    ):
        # Rate limit: 1 req/sec (API publique sans cle)
        if limiter is None:
            limiter = RateLimiter(
                "semantic_scholar",
                RateLimitConfig(
                    requests_per_second=1.0,
                    daily_limit=None,
                    burst_size=1,
                ),
            )
        super().__init__(limiter)

    def _normalize_id(self, paper_id: str) -> str:
        """Normalise un ID pour l'API S2."""
        if paper_id.startswith("10."):
            return f"DOI:{paper_id}"
        elif paper_id.startswith("arXiv:") or paper_id.startswith("arxiv:"):
            return f"ARXIV:{paper_id.split(':')[1]}"
        return paper_id

    async def search(
        self,
        query: str,
        limit: int = 10,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        fields_of_study: Optional[list[str]] = None,
    ) -> list[Paper]:
        """Recherche d'articles par mots-cles."""
        params = {
            "query": query,
            "limit": min(limit, 100),  # Max S2
            "fields": self.PAPER_FIELDS,
        }

        # Filtres optionnels
        if year_min or year_max:
            year_filter = ""
            if year_min:
                year_filter = f"{year_min}-"
            if year_max:
                year_filter += str(year_max)
            elif year_min:
                year_filter += "2099"  # Pas de max
            params["year"] = year_filter

        if fields_of_study:
            params["fieldsOfStudy"] = ",".join(fields_of_study)

        response = await self._request(
            "GET",
            f"{self.BASE_URL}/paper/search",
            params=params,
        )
        data = response.json()

        return [self._parse_paper(p) for p in data.get("data", [])]

    async def get_by_id(self, paper_id: str) -> Optional[Paper]:
        """Recupere un article par ID (DOI, S2 ID, ArXiv, etc.)."""
        paper_id = self._normalize_id(paper_id)
        params = {"fields": self.PAPER_FIELDS}

        try:
            response = await self._request(
                "GET",
                f"{self.BASE_URL}/paper/{paper_id}",
                params=params,
            )
            data = response.json()
            return self._parse_paper(data)
        except SourceError:
            return None

    async def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Recupere les articles citant cet article."""
        # Normaliser l'ID
        if paper_id.startswith("10."):
            paper_id = f"DOI:{paper_id}"

        params = {
            "fields": self.PAPER_FIELDS,
            "limit": min(limit, 1000),
        }

        try:
            response = await self._request(
                "GET",
                f"{self.BASE_URL}/paper/{paper_id}/citations",
                params=params,
            )
            data = response.json()

            papers = []
            for item in data.get("data", []):
                citing_paper = item.get("citingPaper")
                if citing_paper:
                    papers.append(self._parse_paper(citing_paper))
            return papers

        except SourceError:
            return []

    async def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Recupere les references de cet article."""
        # Normaliser l'ID
        if paper_id.startswith("10."):
            paper_id = f"DOI:{paper_id}"

        params = {
            "fields": self.PAPER_FIELDS,
            "limit": min(limit, 1000),
        }

        try:
            response = await self._request(
                "GET",
                f"{self.BASE_URL}/paper/{paper_id}/references",
                params=params,
            )
            data = response.json()

            papers = []
            for item in data.get("data", []):
                cited_paper = item.get("citedPaper")
                if cited_paper:
                    papers.append(self._parse_paper(cited_paper))
            return papers

        except SourceError:
            return []

    async def get_recommendations(
        self,
        positive_ids: list[str],
        negative_ids: Optional[list[str]] = None,
        limit: int = 100,
    ) -> list[Paper]:
        """Recommandations basees sur SPECTER embeddings."""
        # Normaliser les IDs
        normalized_positive = [self._normalize_id(pid) for pid in positive_ids]
        normalized_negative = [self._normalize_id(pid) for pid in (negative_ids or [])]

        payload = {
            "positivePaperIds": normalized_positive,
            "negativePaperIds": normalized_negative,
        }

        params = {
            "limit": min(limit, 500),
            "fields": self.PAPER_FIELDS,
        }

        try:
            response = await self._request(
                "POST",
                f"{self.RECOMMENDATIONS_URL}/papers/",
                json=payload,
                params=params,
            )
            data = response.json()

            return [self._parse_paper(p) for p in data.get("recommendedPapers", [])]

        except SourceError:
            return []

    def _parse_paper(self, data: dict) -> Paper:
        """Convertit un paper S2 en Paper."""
        external_ids = data.get("externalIds", {}) or {}

        # TLDR
        tldr_data = data.get("tldr")
        tldr = tldr_data.get("text") if tldr_data else None

        # Open access PDF
        oa_pdf = data.get("openAccessPdf")
        pdf_url = oa_pdf.get("url") if oa_pdf else None

        # Auteurs
        authors = self._parse_authors(data.get("authors", []))

        # Fields of study
        fields = []
        for field in data.get("fieldsOfStudy") or []:
            if isinstance(field, str):
                fields.append(field)
            elif isinstance(field, dict):
                fields.append(field.get("category", ""))

        return Paper(
            s2_paper_id=data.get("paperId"),
            s2_corpus_id=data.get("corpusId"),
            doi=external_ids.get("DOI"),
            arxiv_id=external_ids.get("ArXiv"),
            pmid=external_ids.get("PubMed"),
            title=data.get("title", ""),
            abstract=data.get("abstract"),
            year=data.get("year"),
            venue=data.get("venue"),
            citation_count=data.get("citationCount"),
            reference_count=data.get("referenceCount"),
            influential_citation_count=data.get("influentialCitationCount"),
            is_open_access=data.get("isOpenAccess", False),
            pdf_url=pdf_url,
            tldr=tldr,
            authors=authors,
            fields_of_study=fields,
            sources=[PaperSource.SEMANTIC_SCHOLAR],
            primary_source=PaperSource.SEMANTIC_SCHOLAR,
            raw_data={"semantic_scholar": data},
        )

    def _parse_authors(self, authors_data: list[dict]) -> list[Author]:
        """Parse les auteurs depuis S2."""
        authors = []
        for author_data in authors_data:
            if not author_data:
                continue

            authors.append(
                Author(
                    name=author_data.get("name", "Unknown"),
                    s2_author_id=author_data.get("authorId"),
                )
            )

        return authors

    # --- Methodes Auteur ---

    AUTHOR_FIELDS = (
        "authorId,name,url,affiliations,homepage,paperCount,"
        "citationCount,hIndex,externalIds"
    )

    async def search_authors(self, query: str, limit: int = 10) -> list[Author]:
        """Recherche d'auteurs par nom."""
        params = {
            "query": query,
            "limit": min(limit, 100),
            "fields": self.AUTHOR_FIELDS,
        }

        try:
            response = await self._request(
                "GET",
                f"{self.BASE_URL}/author/search",
                params=params,
            )
            data = response.json()
            return [self._parse_author_full(a) for a in data.get("data", [])]
        except SourceError:
            return []

    async def get_author(self, author_id: str) -> Optional[Author]:
        """Recupere un auteur par ID (S2 ID, ORCID)."""
        # Normaliser ORCID
        if author_id.startswith("0000-"):
            author_id = f"ORCID:{author_id}"
        elif author_id.startswith("https://orcid.org/"):
            author_id = f"ORCID:{author_id.replace('https://orcid.org/', '')}"

        params = {"fields": self.AUTHOR_FIELDS}

        try:
            response = await self._request(
                "GET",
                f"{self.BASE_URL}/author/{author_id}",
                params=params,
            )
            data = response.json()
            return self._parse_author_full(data)
        except SourceError:
            return None

    def _parse_author_full(self, data: dict) -> Author:
        """Convertit un auteur S2 en Author (version complete)."""
        external_ids = data.get("externalIds", {}) or {}
        orcid = external_ids.get("ORCID")

        # Affiliations
        affiliations = []
        for aff in data.get("affiliations") or []:
            if isinstance(aff, str):
                affiliations.append(aff)
            elif isinstance(aff, dict) and aff.get("name"):
                affiliations.append(aff["name"])

        return Author(
            name=data.get("name", "Unknown"),
            s2_author_id=data.get("authorId"),
            orcid=orcid,
            affiliations=affiliations,
            paper_count=data.get("paperCount"),
            citation_count=data.get("citationCount"),
            h_index=data.get("hIndex"),
            homepage=data.get("homepage"),
            sources=["semantic_scholar"],
        )
