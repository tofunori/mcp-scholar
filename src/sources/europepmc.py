"""Driver Europe PMC pour la recherche d'articles biomedicaux et sciences de la Terre.

Europe PMC (https://europepmc.org) agrege PubMed/MEDLINE, PMC, Agricola,
preprints (bioRxiv/EGUsphere), brevets et theses. API REST gratuite, sans cle,
un email recommande pour le contact poli.

API: https://europepmc.org/RestfulWebService
Endpoint search: GET /search?query=...&format=json&resultType=core
"""

import re
from typing import Optional

from ..models import Paper, Author, PaperSource
from ..rate_limiting import RateLimiter, RateLimitConfig
from .base import BaseSource


class EuropePMCSource(BaseSource):
    """Source Europe PMC (REST). Sans cle API, forte couverture earth-science/biomed.

    resultType=core renvoie les abstracts + metadonnees completes.
    """

    BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"

    def __init__(self, email: Optional[str] = None, limiter: Optional[RateLimiter] = None):
        if limiter is None:
            limiter = RateLimiter(
                "europe_pmc",
                RateLimitConfig(
                    requests_per_second=8.0,
                    daily_limit=None,
                    burst_size=8,
                ),
            )
        super().__init__(limiter)
        self.email = email or ""

    def _default_params(self) -> dict:
        params = {"format": "json", "resultType": "core"}
        if self.email:
            params["email"] = self.email
        return params

    @staticmethod
    def _year_clause(year_min: Optional[int], year_max: Optional[int]) -> str:
        """Construit une clause PUB_YEAR a ajouter a la requete."""
        if year_min is None and year_max is None:
            return ""
        lo = year_min if year_min is not None else 1800
        hi = year_max if year_max is not None else 3000
        return f" AND (PUB_YEAR:[{lo} TO {hi}])"

    async def search(
        self,
        query: str,
        limit: int = 10,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        **kwargs,
    ) -> list[Paper]:
        """Recherche d'articles par mots-cles (tri par pertinence par defaut)."""
        params = self._default_params()
        params["query"] = f"{query}{self._year_clause(year_min, year_max)}"
        params["pageSize"] = min(limit, 100)

        response = await self._request(
            "GET",
            f"{self.BASE_URL}/search",
            params=params,
        )
        data = response.json()

        papers = []
        for result in data.get("resultList", {}).get("result", []):
            paper = self._parse_result(result)
            if paper:
                papers.append(paper)
        return papers

    async def get_by_id(self, paper_id: str) -> Optional[Paper]:
        """Recupere un article par DOI ou identifiant externe (PMID/PMCID)."""
        params = self._default_params()
        pid = paper_id.strip()
        if "/" in pid:  # ressemble a un DOI
            params["query"] = f'DOI:"{pid}"'
        elif pid.upper().startswith("PMC"):
            params["query"] = f"PMCID:{pid}"
        else:
            params["query"] = f"EXT_ID:{pid}"
        params["pageSize"] = 1

        response = await self._request(
            "GET",
            f"{self.BASE_URL}/search",
            params=params,
        )
        data = response.json()
        results = data.get("resultList", {}).get("result", [])
        if not results:
            return None
        return self._parse_result(results[0])

    async def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Non cable dans l'orchestrateur (source orientee recherche).

        Europe PMC expose /{source}/{id}/citations, mais la resolution
        source+id depuis un DOI necessiterait un appel additionnel; renvoie []
        (les citations proviennent d'OpenAlex/S2/SciX). A implementer au besoin.
        """
        return []

    async def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Non cable dans l'orchestrateur (voir get_citations)."""
        return []

    def _parse_result(self, r: dict) -> Optional[Paper]:
        """Convertit un resultat Europe PMC (resultType=core) en Paper."""
        if not r:
            return None

        title = r.get("title")
        if title:
            title = title.strip().rstrip(".")
        if not title:
            return None

        # Annee
        year = None
        if r.get("pubYear"):
            try:
                year = int(r["pubYear"])
            except (TypeError, ValueError):
                year = None

        # Abstract (peut contenir des balises) -> texte brut
        abstract = r.get("abstractText")
        if abstract:
            abstract = re.sub(r"<[^>]+>", "", abstract)
            abstract = abstract[:5000] if len(abstract) > 5000 else abstract

        # Journal / volume / issue
        journal_info = r.get("journalInfo") or {}
        journal = (journal_info.get("journal") or {}).get("title")

        # Acces ouvert + lien PDF
        is_oa = r.get("isOpenAccess") == "Y"
        oa_url = None
        full_text = (r.get("fullTextUrlList") or {}).get("fullTextUrl", [])
        for ft in full_text:
            if ft.get("documentStyle") == "pdf" and ft.get("url"):
                oa_url = ft["url"]
                break
        if oa_url is None and full_text:
            oa_url = full_text[0].get("url")

        # Mots-cles + types
        keywords = (r.get("keywordList") or {}).get("keyword", []) or []
        pub_types = (r.get("pubTypeList") or {}).get("pubType", []) or []

        return Paper(
            doi=r.get("doi"),
            pmid=r.get("pmid"),
            pmcid=r.get("pmcid"),
            europepmc_id=r.get("id"),
            title=title,
            year=year,
            publication_date=r.get("firstPublicationDate"),
            abstract=abstract,
            citation_count=r.get("citedByCount"),
            is_open_access=is_oa,
            open_access_url=oa_url,
            authors=self._parse_authors(r),
            journal=journal,
            volume=journal_info.get("volume"),
            issue=journal_info.get("issue"),
            pages=r.get("pageInfo"),
            keywords=[k for k in keywords if k],
            publication_types=[t for t in pub_types if t],
            sources=[PaperSource.EUROPE_PMC],
            primary_source=PaperSource.EUROPE_PMC,
            raw_data={"europe_pmc": r},
        )

    def _parse_authors(self, r: dict) -> list[Author]:
        """Parse les auteurs depuis authorList (repli sur authorString)."""
        authors: list[Author] = []
        author_list = (r.get("authorList") or {}).get("author", [])
        for a in author_list:
            name = a.get("fullName")
            if not name:
                first = a.get("firstName") or ""
                last = a.get("lastName") or ""
                name = f"{first} {last}".strip()
            if not name:
                continue
            orcid = (a.get("authorId") or {}).get("value") if (
                (a.get("authorId") or {}).get("type") == "ORCID"
            ) else None
            authors.append(Author(name=name, orcid=orcid))

        # Repli: authorString si authorList absent
        if not authors and r.get("authorString"):
            for nm in r["authorString"].split(","):
                nm = nm.strip().rstrip(".")
                if nm:
                    authors.append(Author(name=nm))
        return authors
