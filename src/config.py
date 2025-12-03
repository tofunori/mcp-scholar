"""Configuration du serveur MCP Scholar."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    """Configuration centralisee pour le MCP Scholar."""

    # API Keys
    s2_api_key: str
    scopus_api_key: str
    scix_api_key: str

    # OpenAlex (polite pool - email seulement)
    openalex_mailto: str

    # Chemins
    data_dir: Path = Path("./data")

    # Cache
    cache_ttl: int = 3600  # 1 heure
    cache_max_size: int = 1000

    # Deduplication
    title_similarity_threshold: float = 0.85

    # Logging
    log_level: str = "INFO"

    def validate(self) -> None:
        """Valide la configuration."""
        if not self.openalex_mailto:
            raise ValueError("OPENALEX_MAILTO requis pour le polite pool")
        if not self.scopus_api_key:
            raise ValueError("SCOPUS_API_KEY requis")
        # S2 API key optionnelle mais recommandee

        self.data_dir.mkdir(exist_ok=True)


def load_config() -> Config:
    """Charge la configuration depuis les variables d'environnement."""
    config = Config(
        s2_api_key=os.getenv("S2_API_KEY", ""),
        scopus_api_key=os.getenv("SCOPUS_API_KEY", ""),
        scix_api_key=os.getenv("SCIX_API_KEY", ""),
        openalex_mailto=os.getenv("OPENALEX_MAILTO", ""),
        data_dir=Path(os.getenv("DATA_DIR", "./data")),
        cache_ttl=int(os.getenv("CACHE_TTL", "3600")),
        cache_max_size=int(os.getenv("CACHE_MAX_SIZE", "1000")),
        title_similarity_threshold=float(os.getenv("TITLE_SIMILARITY_THRESHOLD", "0.85")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
    return config


# Instance globale
config = load_config()
