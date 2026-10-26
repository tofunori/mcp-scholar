"""Per-user API key storage for the hosted server (SQLite, Fernet-encrypted)."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

# Keys a user can store, in the order the /keys page shows them. The names
# match the Orchestrator keyword arguments.
KEY_FIELDS = {
    "openalex_api_key": {
        "label": "OpenAlex",
        "help_url": "https://openalex.org/settings/api",
        "note": "Gratuite. Fortement recommandée : sans clé, OpenAlex limite vite les requêtes.",
    },
    "s2_api_key": {
        "label": "Semantic Scholar",
        "help_url": "https://www.semanticscholar.org/product/api#api-key-form",
        "note": "Gratuite, sur demande. Sans clé, Semantic Scholar fonctionne quand même, plus lentement.",
    },
    "scix_api_key": {
        "label": "NASA ADS / SciX",
        "help_url": "https://ui.adsabs.harvard.edu/user/settings/token",
        "note": "Gratuite. Utile en sciences de la Terre et planétaires.",
    },
    "core_api_key": {
        "label": "CORE",
        "help_url": "https://core.ac.uk/services/api",
        "note": "Gratuite. Articles en libre accès.",
    },
    "scopus_api_key": {
        "label": "Scopus (Elsevier)",
        "help_url": "https://dev.elsevier.com/",
        "note": "Nécessite un accès institutionnel; peut ne pas fonctionner hors du réseau de l'université.",
    },
}

MAX_KEY_LENGTH = 256


def clean_key(value: Optional[str]) -> str:
    """Strip whitespace; reject values that cannot be an API key."""
    value = (value or "").strip()
    if len(value) > MAX_KEY_LENGTH or any(c.isspace() or not c.isprintable() for c in value):
        raise ValueError("invalid key")
    return value


class KeyStore:
    """user_id -> {key name: value}, encrypted at rest."""

    def __init__(self, path: Path, encryption_key: bytes):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(encryption_key)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS user_keys ("
                " user_id TEXT PRIMARY KEY,"
                " display TEXT NOT NULL,"
                " keys BLOB NOT NULL,"
                " updated_at INTEGER NOT NULL)"
            )

    @contextmanager
    def _connect(self):
        """One short-lived connection per operation, committed on success."""
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, user_id: str) -> dict[str, str]:
        with self._connect() as db:
            row = db.execute(
                "SELECT keys FROM user_keys WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return {}
        try:
            stored = json.loads(self._fernet.decrypt(row[0]))
        except InvalidToken:
            # SCHOLAR_SECRET_KEY changed: the old keys are unreadable.
            return {}
        return {name: value for name, value in stored.items() if name in KEY_FIELDS and value}

    def set(self, user_id: str, display: str, keys: dict[str, str]) -> None:
        keys = {name: value for name, value in keys.items() if name in KEY_FIELDS and value}
        if not keys:
            self.delete(user_id)
            return
        blob = self._fernet.encrypt(json.dumps(keys).encode())
        with self._connect() as db:
            db.execute(
                "INSERT INTO user_keys (user_id, display, keys, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET"
                " display = excluded.display, keys = excluded.keys,"
                " updated_at = excluded.updated_at",
                (user_id, display, blob, int(time.time())),
            )

    def delete(self, user_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM user_keys WHERE user_id = ?", (user_id,))

    def users(self) -> list[tuple[str, str, int]]:
        """(user_id, display, updated_at) for every user with stored keys."""
        with self._connect() as db:
            return db.execute(
                "SELECT user_id, display, updated_at FROM user_keys ORDER BY display"
            ).fetchall()
