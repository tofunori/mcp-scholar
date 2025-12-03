"""Driver Scopus pour la recherche d'articles."""

from typing import Optional

from ..models import Paper, Author, PaperSource
from ..rate_limiting import RateLimiter, RateLimitConfig
from .base import BaseSource, SourceError


class ScopusSource(BaseSource):
    """Source Scopus pour les articles scientifiques."""

    SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
    ABSTRACT_URL = "https://api.elsevier.com/content/abstract"

    def __init__(self, api_key: str, limiter: Optional[RateLimiter] = None):
        if limiter is None:
            limiter = RateLimiter(
                "scopus",
                RateLimitConfig(
                    requests_per_second=2.0,  # Conservateur
                    daily_limit=None,
                    burst_size=1,
                    retry_after_429=120.0,  # Scopus peut etre strict
                )
            )
        super().__init__(limiter)
        self.api_key = api_key

    def _headers(self) -> dict:
        """Headers pour les requetes Scopus."""
        return {
            "X-ELS-APIKey": self.api_key,
            "Accept": "application/json",
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
        # Construire la requete Scopus
        scopus_query = f"TITLE-ABS-KEY({query})"

        if year_min and year_max:
            scopus_query += f" AND PUBYEAR > {year_min - 1} AND PUBYEAR < {year_max + 1}"
        elif year_min:
            scopus_query += f" AND PUBYEAR > {year_min - 1}"
        elif year_max:
            scopus_query += f" AND PUBYEAR < {year_max + 1}"

        params = {
            "query": scopus_query,
            "count": min(limit, 25),  # Scopus limite a 25 par page sans pagination
            "view": "COMPLETE",
        }

        try:
            response = await self._request(
                "GET",
                self.SEARCH_URL,
                params=params,
                headers=self._headers(),
            )
            data = response.json()

            search_results = data.get("search-results", {})
            entries = search_results.get("entry", [])

            papers = []
            for entry in entries:
                # Verifier si c'est une erreur
                if entry.get("error"):
                    continue
                papers.append(self._parse_entry(entry))

            return papers

        except SourceError:
            return []

    async def get_by_id(self, paper_id: str) -> Optional[Paper]:
        """Recupere un article par DOI ou Scopus ID."""
        # Determiner le type d'ID
        if paper_id.startswith("10."):
            url = f"{self.ABSTRACT_URL}/doi/{paper_id}"
        elif paper_id.startswith("SCOPUS_ID:"):
            scopus_id = paper_id.replace("SCOPUS_ID:", "")
            url = f"{self.ABSTRACT_URL}/scopus_id/{scopus_id}"
        else:
            # Essayer comme DOI
            url = f"{self.ABSTRACT_URL}/doi/{paper_id}"

        params = {"view": "FULL"}

        try:
            response = await self._request(
                "GET",
                url,
                params=params,
                headers=self._headers(),
            )
            data = response.json()

            # La reponse est dans abstracts-retrieval-response
            abstract_data = data.get("abstracts-retrieval-response", {})
            if not abstract_data:
                return None

            return self._parse_abstract_response(abstract_data)

        except SourceError:
            return None

    async def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Recupere les articles citant cet article."""
        # Scopus utilise le Scopus ID pour les citations
        # On doit d'abord obtenir l'article pour avoir son EID
        paper = await self.get_by_id(paper_id)
        if not paper or not paper.scopus_eid:
            return []

        # Rechercher les articles qui citent cet EID
        scopus_query = f"REFEID({paper.scopus_eid})"

        params = {
            "query": scopus_query,
            "count": min(limit, 25),
            "view": "COMPLETE",
        }

        try:
            response = await self._request(
                "GET",
                self.SEARCH_URL,
                params=params,
                headers=self._headers(),
            )
            data = response.json()

            search_results = data.get("search-results", {})
            entries = search_results.get("entry", [])

            papers = []
            for entry in entries:
                if entry.get("error"):
                    continue
                papers.append(self._parse_entry(entry))

            return papers

        except SourceError:
            return []

    async def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Recupere les references de cet article."""
        # Scopus ne fournit pas facilement les references via l'API de base
        # On retourne une liste vide pour l'instant
        # Note: Cela necessite l'API Abstract Retrieval avec view=REF
        return []

    def _parse_entry(self, entry: dict) -> Paper:
        """Parse une entree de recherche Scopus."""
        # Extraire le DOI
        doi = entry.get("prism:doi")

        # Extraire l'EID (identifiant Scopus)
        scopus_eid = entry.get("eid")

        # Auteurs
        authors = self._parse_authors_from_entry(entry)

        return Paper(
            scopus_eid=scopus_eid,
            doi=doi,
            title=entry.get("dc:title", ""),
            year=self._extract_year(entry.get("prism:coverDate")),
            publication_date=entry.get("prism:coverDate"),
            abstract=entry.get("dc:description"),
            journal=entry.get("prism:publicationName"),
            volume=entry.get("prism:volume"),
            issue=entry.get("prism:issueIdentifier"),
            pages=entry.get("prism:pageRange"),
            citation_count=self._safe_int(entry.get("citedby-count")),
            is_open_access=entry.get("openaccess") == "1",
            authors=authors,
            publication_types=[entry.get("subtypeDescription")] if entry.get("subtypeDescription") else [],
            sources=[PaperSource.SCOPUS],
            primary_source=PaperSource.SCOPUS,
            raw_data={"scopus": entry},
        )

    def _parse_abstract_response(self, data: dict) -> Paper:
        """Parse une reponse Abstract Retrieval Scopus."""
        coredata = data.get("coredata", {})

        # Auteurs
        authors_data = data.get("authors", {})
        author_list = authors_data.get("author", [])
        authors = self._parse_authors_from_abstract(author_list)

        # DOI
        doi = coredata.get("prism:doi")

        # EID
        scopus_eid = coredata.get("eid")

        return Paper(
            scopus_eid=scopus_eid,
            doi=doi,
            title=coredata.get("dc:title", ""),
            year=self._extract_year(coredata.get("prism:coverDate")),
            publication_date=coredata.get("prism:coverDate"),
            abstract=coredata.get("dc:description"),
            journal=coredata.get("prism:publicationName"),
            publisher=coredata.get("dc:publisher"),
            volume=coredata.get("prism:volume"),
            issue=coredata.get("prism:issueIdentifier"),
            pages=coredata.get("prism:pageRange"),
            citation_count=self._safe_int(coredata.get("citedby-count")),
            is_open_access=coredata.get("openaccess") == "1",
            authors=authors,
            sources=[PaperSource.SCOPUS],
            primary_source=PaperSource.SCOPUS,
            raw_data={"scopus": data},
        )

    def _parse_authors_from_entry(self, entry: dict) -> list[Author]:
        """Parse les auteurs depuis une entree de recherche."""
        authors = []

        # Le champ dc:creator contient le premier auteur
        creator = entry.get("dc:creator")
        if creator:
            authors.append(Author(name=creator))

        # Le champ author contient plus de details si disponible
        author_data = entry.get("author", [])
        if author_data:
            authors = []  # Reset si on a des donnees plus completes
            if not isinstance(author_data, list):
                author_data = [author_data]

            for auth in author_data:
                name = auth.get("authname", auth.get("given-name", ""))
                if not name and auth.get("surname"):
                    name = auth.get("surname")
                    if auth.get("given-name"):
                        name = f"{auth['given-name']} {name}"

                if name:
                    authors.append(Author(
                        name=name,
                        scopus_author_id=auth.get("authid"),
                    ))

        return authors

    def _parse_authors_from_abstract(self, author_list: list) -> list[Author]:
        """Parse les auteurs depuis une reponse Abstract Retrieval."""
        authors = []

        if not isinstance(author_list, list):
            author_list = [author_list] if author_list else []

        for auth in author_list:
            name = ""
            if auth.get("ce:indexed-name"):
                name = auth["ce:indexed-name"]
            elif auth.get("ce:surname"):
                name = auth["ce:surname"]
                if auth.get("ce:given-name"):
                    name = f"{auth['ce:given-name']} {name}"

            if name:
                # Affiliations
                affiliations = []
                affil = auth.get("affiliation", [])
                if not isinstance(affil, list):
                    affil = [affil] if affil else []
                for aff in affil:
                    if aff and aff.get("affilname"):
                        affiliations.append(aff["affilname"])

                authors.append(Author(
                    name=name,
                    scopus_author_id=auth.get("@auid"),
                    affiliations=affiliations,
                ))

        return authors

    def _extract_year(self, date_str: Optional[str]) -> Optional[int]:
        """Extrait l'annee d'une date Scopus."""
        if not date_str:
            return None
        try:
            return int(date_str[:4])
        except (ValueError, IndexError):
            return None

    def _safe_int(self, value) -> Optional[int]:
        """Convertit une valeur en int de maniere securisee."""
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
