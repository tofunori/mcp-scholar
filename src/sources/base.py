"""Classe abstraite pour les sources d'articles."""

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
import httpx

# Borne superieure raisonnable pour un Retry-After (secondes)
MAX_RETRY_AFTER = 120.0
DEFAULT_RETRY_AFTER = 60.0


def parse_retry_after(value: Optional[str]) -> float:
    """Interprete un en-tete Retry-After (RFC 7231) de facon robuste.

    Deux formes sont permises:
      - delay-seconds: un entier/flottant ("120")
      - HTTP-date: une date ("Fri, 31 Dec 1999 23:59:59 GMT")

    Retourne un nombre de secondes, borne a [0, MAX_RETRY_AFTER].
    En cas d'echec de parsing, retourne DEFAULT_RETRY_AFTER.
    """
    if value is None:
        seconds = DEFAULT_RETRY_AFTER
    else:
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            try:
                retry_dt = parsedate_to_datetime(value)
                if retry_dt is None:
                    raise ValueError("date non interpretable")
                # parsedate_to_datetime peut retourner un datetime naif
                if retry_dt.tzinfo is None:
                    retry_dt = retry_dt.replace(tzinfo=timezone.utc)
                seconds = (retry_dt - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError):
                seconds = DEFAULT_RETRY_AFTER

    # Borner a [0, MAX_RETRY_AFTER]
    if seconds < 0:
        seconds = 0.0
    if seconds > MAX_RETRY_AFTER:
        seconds = MAX_RETRY_AFTER
    return seconds

from ..models import Paper, Author
from ..rate_limiting import RateLimiter


class SourceError(Exception):
    """Erreur lors de l'acces a une source.

    `status_code` est renseigne quand l'erreur provient d'une reponse HTTP
    (4xx/5xx). Il vaut None pour les erreurs reseau/transport. Cet attribut
    permet aux sources (ex. CORE) de detecter un 5xx transitoire et de
    reessayer, sans modifier la boucle de retry partagee de `_request`.
    """

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


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
        """Execute une requete avec rate limiting.

        Sur un 429, attend le delai Retry-After (durci) et reessaie UNE fois.
        Un second 429 leve une SourceError.
        """
        try:
            for attempt in range(2):  # tentative initiale + 1 retry
                await self.limiter.acquire()

                response = await self.client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json,
                )

                if response.status_code == 429:
                    retry_after = parse_retry_after(
                        response.headers.get("Retry-After")
                    )
                    self.limiter.report_429(retry_after)
                    if attempt == 0:
                        # Premier 429: report_429() a arme le backoff du limiter;
                        # acquire() l'honorera au prochain tour (une seule attente,
                        # pas de double sleep). On reessaie une seule fois.
                        continue
                    # Deuxieme 429: on abandonne.
                    raise SourceError(f"429 Too Many Requests: {url}")

                response.raise_for_status()
                self.limiter.report_success()
                return response

        except httpx.HTTPStatusError as e:
            raise SourceError(
                f"HTTP error {e.response.status_code}: {url}",
                status_code=e.response.status_code,
            )
        except httpx.RequestError as e:
            raise SourceError(f"Request error: {e}")
