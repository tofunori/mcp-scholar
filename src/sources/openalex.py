"""Driver OpenAlex pour la recherche d'articles."""

from typing import Optional

from ..models import Paper, Author, PaperSource
from ..rate_limiting import RateLimiter, RateLimitConfig
from .base import BaseSource, SourceError


class OpenAlexSource(BaseSource):
    """Source OpenAlex pour les articles scientifiques."""

    BASE_URL = "https://api.openalex.org"

    # Champs a recuperer (optimisation)
    WORK_FIELDS = (
        "id,doi,title,publication_year,publication_date,"
        "abstract_inverted_index,cited_by_count,authorships,"
        "primary_location,open_access,concepts,type,referenced_works"
    )

    def __init__(self, mailto: str, limiter: Optional[RateLimiter] = None):
        if limiter is None:
            limiter = RateLimiter(
                "openalex",
                RateLimitConfig(
                    requests_per_second=10.0,  # Polite pool
                    daily_limit=100_000,
                    burst_size=5,
                )
            )
        super().__init__(limiter)
        self.mailto = mailto

    def _default_params(self) -> dict:
        """Parametres par defaut pour toutes les requetes."""
        return {
            "mailto": self.mailto,
            "per-page": 200,  # Max pour performance
        }

    async def search(
        self,
        query: str,
        limit: int = 10,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        fields_of_study: Optional[list[str]] = None,
    ) -> list[Paper]:
        """Recherche d'articles par mots-cles."""
        params = self._default_params()
        params["search"] = query
        params["per-page"] = min(limit, 200)
        params["select"] = self.WORK_FIELDS

        # Construire les filtres
        filters = []
        if year_min:
            filters.append(f"publication_year:>{year_min - 1}")
        if year_max:
            filters.append(f"publication_year:<{year_max + 1}")
        if filters:
            params["filter"] = ",".join(filters)

        response = await self._request("GET", f"{self.BASE_URL}/works", params=params)
        data = response.json()

        return [self._parse_work(work) for work in data.get("results", [])]

    async def get_by_id(self, paper_id: str) -> Optional[Paper]:
        """Recupere un article par DOI ou OpenAlex ID."""
        # Normaliser l'ID
        if paper_id.startswith("10."):
            url = f"{self.BASE_URL}/works/https://doi.org/{paper_id}"
        elif paper_id.startswith("W"):
            url = f"{self.BASE_URL}/works/{paper_id}"
        else:
            url = f"{self.BASE_URL}/works/{paper_id}"

        params = self._default_params()
        params["select"] = self.WORK_FIELDS

        try:
            response = await self._request("GET", url, params=params)
            data = response.json()
            return self._parse_work(data)
        except SourceError:
            return None

    async def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Recupere les articles citant cet article."""
        # D'abord, obtenir l'article pour avoir cited_by_api_url
        paper = await self.get_by_id(paper_id)
        if not paper or not paper.openalex_id:
            return []

        params = self._default_params()
        params["filter"] = f"cites:{paper.openalex_id}"
        params["per-page"] = min(limit, 200)
        params["select"] = self.WORK_FIELDS

        response = await self._request("GET", f"{self.BASE_URL}/works", params=params)
        data = response.json()

        return [self._parse_work(work) for work in data.get("results", [])]

    async def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Recupere les references de cet article."""
        paper = await self.get_by_id(paper_id)
        if not paper:
            return []

        # OpenAlex retourne les referenced_works IDs dans le paper
        raw_data = paper.raw_data.get("openalex", {})
        ref_ids = raw_data.get("referenced_works", [])

        if not ref_ids:
            return []

        # Batch lookup (max 50 IDs par requete)
        ref_ids = ref_ids[:min(limit, 50)]
        # Extraire juste les IDs (W...)
        short_ids = [rid.replace("https://openalex.org/", "") for rid in ref_ids]

        params = self._default_params()
        params["filter"] = f"openalex_id:{'|'.join(short_ids)}"
        params["per-page"] = len(short_ids)
        params["select"] = self.WORK_FIELDS

        response = await self._request("GET", f"{self.BASE_URL}/works", params=params)
        data = response.json()

        return [self._parse_work(work) for work in data.get("results", [])]

    def _parse_work(self, work: dict) -> Paper:
        """Convertit un work OpenAlex en Paper."""
        # Extraire l'ID court
        openalex_id = work.get("id", "").replace("https://openalex.org/", "")

        # Extraire le DOI
        doi = work.get("doi")
        if doi:
            doi = doi.replace("https://doi.org/", "")

        # Reconstruire l'abstract depuis inverted_index
        abstract = self._reconstruct_abstract(work.get("abstract_inverted_index"))

        # Parser les auteurs
        authors = self._parse_authors(work.get("authorships", []))

        # Open access
        oa_info = work.get("open_access", {})
        primary_location = work.get("primary_location", {}) or {}
        source = primary_location.get("source", {}) or {}

        return Paper(
            openalex_id=openalex_id,
            doi=doi,
            title=work.get("title", ""),
            year=work.get("publication_year"),
            publication_date=work.get("publication_date"),
            abstract=abstract,
            citation_count=work.get("cited_by_count"),
            reference_count=len(work.get("referenced_works", [])),
            is_open_access=oa_info.get("is_oa", False),
            open_access_url=oa_info.get("oa_url"),
            authors=authors,
            journal=source.get("display_name"),
            publisher=source.get("host_organization_name"),
            fields_of_study=[
                c.get("display_name") for c in work.get("concepts", [])[:5]
                if c.get("display_name")
            ],
            publication_types=[work.get("type")] if work.get("type") else [],
            sources=[PaperSource.OPENALEX],
            primary_source=PaperSource.OPENALEX,
            raw_data={"openalex": work},
        )

    def _reconstruct_abstract(self, inverted_index: Optional[dict]) -> Optional[str]:
        """Reconstruit l'abstract depuis l'index inverse OpenAlex."""
        if not inverted_index:
            return None

        # L'index inverse mappe mot -> positions
        # On doit reconstruire le texte original
        words: list[tuple[int, str]] = []
        for word, positions in inverted_index.items():
            for pos in positions:
                words.append((pos, word))

        words.sort(key=lambda x: x[0])
        return " ".join(word for _, word in words)

    def _parse_authors(self, authorships: list[dict]) -> list[Author]:
        """Parse les auteurs depuis les authorships OpenAlex."""
        authors = []
        for authorship in authorships:
            author_data = authorship.get("author", {})
            if not author_data:
                continue

            affiliations = []
            for inst in authorship.get("institutions", []):
                if inst and inst.get("display_name"):
                    affiliations.append(inst["display_name"])

            author_id = author_data.get("id", "").replace("https://openalex.org/", "")

            authors.append(Author(
                name=author_data.get("display_name", "Unknown"),
                openalex_id=author_id if author_id else None,
                orcid=author_data.get("orcid"),
                affiliations=affiliations,
            ))

        return authors
