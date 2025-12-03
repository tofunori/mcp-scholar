"""Modele Author - Representation d'un auteur."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Author:
    """Representation d'un auteur d'article scientifique."""

    name: str

    # Identifiants externes
    openalex_id: Optional[str] = None
    s2_author_id: Optional[str] = None
    scopus_author_id: Optional[str] = None
    orcid: Optional[str] = None

    # Affiliations
    affiliations: list[str] = field(default_factory=list)

    # Metriques (optionnelles)
    paper_count: Optional[int] = None
    citation_count: Optional[int] = None
    h_index: Optional[int] = None

    def to_dict(self) -> dict:
        """Convertit l'auteur en dictionnaire."""
        return {
            "name": self.name,
            "openalex_id": self.openalex_id,
            "s2_author_id": self.s2_author_id,
            "scopus_author_id": self.scopus_author_id,
            "orcid": self.orcid,
            "affiliations": self.affiliations,
            "paper_count": self.paper_count,
            "citation_count": self.citation_count,
            "h_index": self.h_index,
        }
