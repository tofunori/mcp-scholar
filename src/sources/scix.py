"""Driver SciX/NASA ADS pour la recherche d'articles scientifiques."""

from typing import Optional

from ..models import Paper, Author, PaperSource
from ..rate_limiting import RateLimiter, RateLimitConfig
from .base import BaseSource, SourceError


class SciXSource(BaseSource):
    """Source SciX/NASA ADS pour les articles scientifiques.

    Couvre Earth science, planetary science, astrophysics, heliophysics.
    API documentation: https://github.com/adsabs/adsabs-dev-api
    """

    BASE_URL = "https://api.adsabs.harvard.edu/v1"

    # Champs a recuperer
    PAPER_FIELDS = [
        "bibcode",
        "title",
        "abstract",
        "author",
        "year",
        "doi",
        "citation_count",
        "reference",
        "pub",
        "volume",
        "page",
        "arxiv_class",
        "identifier",
        "property",
        "esources",
    ]

    def __init__(self, api_key: str, limiter: Optional[RateLimiter] = None):
        """Initialise la source SciX.

        Args:
            api_key: Token API ADS (requis)
            limiter: Rate limiter optionnel
        """
        if not api_key:
            raise ValueError("SciX API key is required")

        self.api_key = api_key

        # Rate limit: 5000 req/jour = ~0.06 req/sec, mais on peut burst
        if limiter is None:
            limiter = RateLimiter(
                "scix",
                RateLimitConfig(
                    requests_per_second=5.0,  # Burst OK
                    daily_limit=5000,
                    burst_size=10,
                )
            )
        super().__init__(limiter)

    def _get_headers(self) -> dict:
        """Retourne les headers d'authentification."""
        return {"Authorization": f"Bearer {self.api_key}"}

    async def search(
        self,
        query: str,
        limit: int = 10,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        **kwargs,
    ) -> list[Paper]:
        """Recherche d'articles par mots-cles."""
        # Construire la requete ADS
        q = query

        # Filtres d'annee
        if year_min and year_max:
            q += f" year:[{year_min} TO {year_max}]"
        elif year_min:
            q += f" year:[{year_min} TO *]"
        elif year_max:
            q += f" year:[* TO {year_max}]"

        params = {
            "q": q,
            "rows": min(limit, 2000),  # Max ADS = 2000
            "fl": ",".join(self.PAPER_FIELDS),
            "sort": "citation_count desc",
        }

        response = await self._request(
            "GET",
            f"{self.BASE_URL}/search/query",
            headers=self._get_headers(),
            params=params,
        )
        data = response.json()

        papers = []
        for doc in data.get("response", {}).get("docs", []):
            paper = self._parse_paper(doc)
            if paper:
                papers.append(paper)

        return papers

    async def get_by_id(self, paper_id: str) -> Optional[Paper]:
        """Recupere un article par ID (bibcode, DOI, arXiv)."""
        # Construire la requete selon le type d'ID
        if paper_id.startswith("10."):
            q = f'doi:"{paper_id}"'
        elif paper_id.startswith("arXiv:") or paper_id.startswith("arxiv:"):
            arxiv_id = paper_id.split(":")[-1]
            q = f'arxiv:"{arxiv_id}"'
        else:
            # Assume bibcode
            q = f'bibcode:"{paper_id}"'

        params = {
            "q": q,
            "rows": 1,
            "fl": ",".join(self.PAPER_FIELDS),
        }

        try:
            response = await self._request(
                "GET",
                f"{self.BASE_URL}/search/query",
                headers=self._get_headers(),
                params=params,
            )
            data = response.json()

            docs = data.get("response", {}).get("docs", [])
            if docs:
                return self._parse_paper(docs[0])
            return None
        except SourceError:
            return None

    async def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Recupere les articles citant cet article."""
        # D'abord trouver le bibcode
        paper = await self.get_by_id(paper_id)
        if not paper or not paper.scix_bibcode:
            return []

        # Requete citations
        params = {
            "q": f'citations(bibcode:"{paper.scix_bibcode}")',
            "rows": min(limit, 2000),
            "fl": ",".join(self.PAPER_FIELDS),
            "sort": "citation_count desc",
        }

        try:
            response = await self._request(
                "GET",
                f"{self.BASE_URL}/search/query",
                headers=self._get_headers(),
                params=params,
            )
            data = response.json()

            papers = []
            for doc in data.get("response", {}).get("docs", []):
                p = self._parse_paper(doc)
                if p:
                    papers.append(p)
            return papers
        except SourceError:
            return []

    async def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Recupere les references de cet article."""
        # D'abord trouver le bibcode
        paper = await self.get_by_id(paper_id)
        if not paper or not paper.scix_bibcode:
            return []

        # Requete references
        params = {
            "q": f'references(bibcode:"{paper.scix_bibcode}")',
            "rows": min(limit, 2000),
            "fl": ",".join(self.PAPER_FIELDS),
            "sort": "citation_count desc",
        }

        try:
            response = await self._request(
                "GET",
                f"{self.BASE_URL}/search/query",
                headers=self._get_headers(),
                params=params,
            )
            data = response.json()

            papers = []
            for doc in data.get("response", {}).get("docs", []):
                p = self._parse_paper(doc)
                if p:
                    papers.append(p)
            return papers
        except SourceError:
            return []

    def _parse_paper(self, data: dict) -> Optional[Paper]:
        """Convertit un document ADS en Paper."""
        title = data.get("title")
        if isinstance(title, list):
            title = title[0] if title else None

        if not title:
            return None

        # Auteurs
        authors = []
        author_names = data.get("author", [])
        for name in author_names[:20]:  # Limiter a 20 auteurs
            authors.append(Author(name=name))

        # DOI
        doi = None
        dois = data.get("doi", [])
        if dois:
            doi = dois[0] if isinstance(dois, list) else dois

        # ArXiv
        arxiv_id = None
        identifiers = data.get("identifier", [])
        for ident in identifiers:
            if ident.startswith("arXiv:"):
                arxiv_id = ident.replace("arXiv:", "")
                break

        # Open Access
        is_oa = False
        pdf_url = None
        properties = data.get("property", [])
        esources = data.get("esources", [])

        if "OPENACCESS" in properties:
            is_oa = True
        if "PUB_PDF" in esources or "EPRINT_PDF" in esources:
            is_oa = True
            # Construire URL PDF si possible
            bibcode = data.get("bibcode")
            if bibcode and "EPRINT_PDF" in esources:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None

        return Paper(
            title=title,
            authors=authors,
            year=data.get("year"),
            abstract=data.get("abstract"),
            doi=doi,
            arxiv_id=arxiv_id,
            scix_bibcode=data.get("bibcode"),
            citation_count=data.get("citation_count"),
            reference_count=len(data.get("reference", [])) if data.get("reference") else None,
            journal=data.get("pub"),
            volume=data.get("volume"),
            pages=data.get("page", [None])[0] if data.get("page") else None,
            is_open_access=is_oa,
            pdf_url=pdf_url,
            sources=[PaperSource.SCIX],
        )
