"""Rate limiter adaptatif pour les APIs."""

import asyncio
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class RateLimitConfig:
    """Configuration du rate limiting par source."""
    requests_per_second: float
    daily_limit: Optional[int] = None
    burst_size: int = 1
    retry_after_429: float = 60.0


class RateLimiter:
    """Rate limiter adaptatif avec token bucket."""

    def __init__(self, name: str, config: RateLimitConfig):
        self.name = name
        self.config = config

        # Token bucket
        self.tokens = float(config.burst_size)
        self.last_update = time.monotonic()
        self.lock = asyncio.Lock()

        # Suivi quotidien
        self.daily_count = 0
        self.daily_reset = time.time()

        # Backoff adaptatif
        self.consecutive_429s = 0
        self.backoff_until: Optional[float] = None

    async def acquire(self) -> None:
        """Attend jusqu'a ce qu'une requete soit autorisee."""
        async with self.lock:
            # Verifier backoff
            if self.backoff_until and time.time() < self.backoff_until:
                wait_time = self.backoff_until - time.time()
                await asyncio.sleep(wait_time)

            # Verifier limite quotidienne
            self._check_daily_reset()
            if self.config.daily_limit and self.daily_count >= self.config.daily_limit:
                raise RateLimitExceeded(
                    f"Limite quotidienne atteinte pour {self.name}: "
                    f"{self.daily_count}/{self.config.daily_limit}"
                )

            # Token bucket
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(
                float(self.config.burst_size),
                self.tokens + elapsed * self.config.requests_per_second
            )
            self.last_update = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.config.requests_per_second
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1

            self.daily_count += 1

    def report_429(self, retry_after: Optional[float] = None) -> None:
        """Signale une erreur 429 pour ajuster le rate limit."""
        self.consecutive_429s += 1
        backoff = retry_after or (self.config.retry_after_429 * (2 ** self.consecutive_429s))
        self.backoff_until = time.time() + min(backoff, 300)  # Max 5 min

    def report_success(self) -> None:
        """Signale une requete reussie."""
        self.consecutive_429s = 0
        self.backoff_until = None

    def _check_daily_reset(self) -> None:
        """Reset le compteur quotidien si necessaire."""
        now = time.time()
        if now - self.daily_reset > 86400:  # 24h
            self.daily_count = 0
            self.daily_reset = now

    def get_status(self) -> dict:
        """Retourne le statut actuel."""
        return {
            "name": self.name,
            "tokens_available": round(self.tokens, 2),
            "daily_count": self.daily_count,
            "daily_limit": self.config.daily_limit,
            "in_backoff": self.backoff_until is not None,
            "requests_per_second": self.config.requests_per_second,
        }


class RateLimitExceeded(Exception):
    """Exception levee quand la limite de requetes est atteinte."""
    pass
