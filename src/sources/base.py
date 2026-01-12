"""Classe abstraite pour les sources d'articles."""

from abc import ABC, abstractmethod
from typing import Optional
import httpx

from ..models import Paper, Author
from ..rate_limiting import RateLimiter


class SourceError(Exception):
    """Erreur lors de l'acces a une source."""
    pass


class BaseSource(ABC):
    """Classe abstraite pour les sources d'articles scientifiques."""

    def __init__(self, limiter: RateLimiter):
        self.limiter = limiter
        self.client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *args):
        if self.client:
            await self.client.aclose()

    @abstractmethod
    async def search(self, query: str, limit: int = 10, **kwargs) -> list[Paper]:
        """Recherche d'articles par mots-cles."""
        pass

    @abstractmethod
    async def get_by_id(self, paper_id: str) -> Optional[Paper]:
        """Recupere un article par son identifiant."""
        pass

    @abstractmethod
    async def get_citations(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Recupere les articles citant cet article."""
        pass

    @abstractmethod
    async def get_references(self, paper_id: str, limit: int = 100) -> list[Paper]:
        """Recupere les references de cet article."""
        pass

    async def get_author(self, author_id: str) -> Optional[Author]:
        """Recupere un auteur par ID ou recherche par nom.

        Accepte: nom d'auteur, OpenAlex ID (A...), S2 ID, ORCID, Scopus ID.
        Implementation par defaut retourne None (non supportee).
        """
        return None

    async def search_authors(self, query: str, limit: int = 10) -> list[Author]:
        """Recherche d'auteurs par nom.

        Implementation par defaut retourne liste vide.
        """
        return []

    async def _request(
        self,
        method: str,
        url: str,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
    ) -> httpx.Response:
        """Execute une requete avec rate limiting."""
        await self.limiter.acquire()

        try:
            response = await self.client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
            )

            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 60))
                self.limiter.report_429(retry_after)
                raise SourceError(f"429 Too Many Requests: {url}")

            response.raise_for_status()
            self.limiter.report_success()
            return response

        except httpx.HTTPStatusError as e:
            raise SourceError(f"HTTP error {e.response.status_code}: {url}")
        except httpx.RequestError as e:
            raise SourceError(f"Request error: {e}")
