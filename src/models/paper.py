"""Modele Paper - Representation unifiee d'un article scientifique."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import re

from .author import Author


class PaperSource(str, Enum):
    """Sources de donnees pour les articles."""
    OPENALEX = "openalex"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    SCOPUS = "scopus"
    SCIX = "scix"


@dataclass
class Paper:
    """Representation unifiee d'un article scientifique multi-sources."""

    # Identifiants (cles de deduplication)
    doi: Optional[str] = None
    openalex_id: Optional[str] = None
    s2_paper_id: Optional[str] = None
    s2_corpus_id: Optional[int] = None
    scopus_eid: Optional[str] = None
    scix_bibcode: Optional[str] = None
    arxiv_id: Optional[str] = None
    pmid: Optional[str] = None

    # Metadonnees essentielles
    title: str = ""
    authors: list[Author] = field(default_factory=list)
    year: Optional[int] = None
    publication_date: Optional[str] = None

    # Contenu
    abstract: Optional[str] = None

    # Publication
    journal: Optional[str] = None
    venue: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    publisher: Optional[str] = None

    # Metriques
    citation_count: Optional[int] = None
    reference_count: Optional[int] = None
    influential_citation_count: Optional[int] = None  # Semantic Scholar

    # Classification
    fields_of_study: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    publication_types: list[str] = field(default_factory=list)

    # Acces ouvert
    is_open_access: bool = False
    open_access_url: Optional[str] = None
    pdf_url: Optional[str] = None

    # Embeddings (Semantic Scholar SPECTER)
    embedding: Optional[list[float]] = None
    embedding_model: Optional[str] = None

    # Resume automatique (S2)
    tldr: Optional[str] = None

    # Provenance
    sources: list[PaperSource] = field(default_factory=list)
    primary_source: Optional[PaperSource] = None
    raw_data: dict[str, dict] = field(default_factory=dict)

    # Metadonnees internes
    acquired_at: str = field(default_factory=lambda: datetime.now().isoformat())
    confidence_score: float = 1.0

    def get_canonical_id(self) -> str:
        """Retourne l'identifiant canonique pour deduplication."""
        if self.doi:
            return f"doi:{self.doi.lower()}"
        if self.s2_corpus_id:
            return f"s2:{self.s2_corpus_id}"
        if self.openalex_id:
            return f"oa:{self.openalex_id}"
        if self.scopus_eid:
            return f"scopus:{self.scopus_eid}"
        if self.scix_bibcode:
            return f"scix:{self.scix_bibcode}"
        # Fallback: hash du titre normalise + annee
        return f"title:{self._normalize_title()}:{self.year or 0}"

    def _normalize_title(self) -> str:
        """Normalise le titre pour comparaison."""
        title = self.title.lower().strip()
        title = re.sub(r'[^\w\s]', '', title)
        return title[:100]

    def get_display_authors(self, max_authors: int = 3) -> str:
        """Retourne une chaine d'auteurs pour affichage."""
        if not self.authors:
            return "Unknown"
        names = [a.name for a in self.authors[:max_authors]]
        if len(self.authors) > max_authors:
            names.append("et al.")
        return ", ".join(names)

    def to_dict(self) -> dict:
        """Convertit le paper en dictionnaire."""
        return {
            "doi": self.doi,
            "openalex_id": self.openalex_id,
            "s2_paper_id": self.s2_paper_id,
            "s2_corpus_id": self.s2_corpus_id,
            "scopus_eid": self.scopus_eid,
            "scix_bibcode": self.scix_bibcode,
            "arxiv_id": self.arxiv_id,
            "pmid": self.pmid,
            "title": self.title,
            "authors": [a.to_dict() for a in self.authors],
            "year": self.year,
            "publication_date": self.publication_date,
            "abstract": self.abstract,
            "journal": self.journal,
            "venue": self.venue,
            "volume": self.volume,
            "issue": self.issue,
            "pages": self.pages,
            "publisher": self.publisher,
            "citation_count": self.citation_count,
            "reference_count": self.reference_count,
            "influential_citation_count": self.influential_citation_count,
            "fields_of_study": self.fields_of_study,
            "keywords": self.keywords,
            "publication_types": self.publication_types,
            "is_open_access": self.is_open_access,
            "open_access_url": self.open_access_url,
            "pdf_url": self.pdf_url,
            "tldr": self.tldr,
            "sources": [s.value for s in self.sources],
            "primary_source": self.primary_source.value if self.primary_source else None,
        }

    def __repr__(self) -> str:
        return f"Paper(title='{self.title[:50]}...', doi={self.doi}, year={self.year})"
