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

    # Infos supplementaires
    homepage: Optional[str] = None
    sources: list[str] = field(default_factory=list)

    # Publications recentes (pour get_author)
    recent_papers: list = field(default_factory=list)

    def get_display_name(self) -> str:
        """Retourne le nom d'affichage."""
        return self.name

    def get_primary_id(self) -> Optional[str]:
        """Retourne l'identifiant principal (priorite: ORCID > OpenAlex > S2 > Scopus)."""
        if self.orcid:
            return f"ORCID:{self.orcid}"
        if self.openalex_id:
            return self.openalex_id
        if self.s2_author_id:
            return self.s2_author_id
        if self.scopus_author_id:
            return self.scopus_author_id
        return None

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
            "homepage": self.homepage,
            "sources": self.sources,
        }
