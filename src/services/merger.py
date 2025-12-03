"""Fusion de metadonnees d'articles multi-sources."""

from typing import Optional

from ..models import Paper, PaperSource


class MetadataMerger:
    """Fusionne les metadonnees de plusieurs sources pour un meme article."""

    # Priorite des sources par champ
    # La premiere source listee est preferee
    SOURCE_PRIORITY: dict[str, list[PaperSource]] = {
        "abstract": [
            PaperSource.SEMANTIC_SCHOLAR,
            PaperSource.SCOPUS,
            PaperSource.OPENALEX,
        ],
        "citation_count": [
            PaperSource.OPENALEX,  # Plus complet
            PaperSource.SEMANTIC_SCHOLAR,
            PaperSource.SCOPUS,
        ],
        "authors": [
            PaperSource.OPENALEX,  # Meilleure desambiguation
            PaperSource.SCOPUS,
            PaperSource.SEMANTIC_SCHOLAR,
        ],
        "tldr": [
            PaperSource.SEMANTIC_SCHOLAR,  # Seul a le fournir
        ],
        "influential_citation_count": [
            PaperSource.SEMANTIC_SCHOLAR,  # Seul a le fournir
        ],
        "fields_of_study": [
            PaperSource.OPENALEX,
            PaperSource.SEMANTIC_SCHOLAR,
        ],
        "open_access_url": [
            PaperSource.OPENALEX,
            PaperSource.SEMANTIC_SCHOLAR,
        ],
        "pdf_url": [
            PaperSource.SEMANTIC_SCHOLAR,
            PaperSource.OPENALEX,
        ],
        "journal": [
            PaperSource.SCOPUS,
            PaperSource.OPENALEX,
            PaperSource.SEMANTIC_SCHOLAR,
        ],
        "publisher": [
            PaperSource.SCOPUS,
            PaperSource.OPENALEX,
        ],
    }

    def merge(self, papers: list[Paper]) -> Paper:
        """Fusionne une liste d'articles dupliques en un seul."""
        if len(papers) == 1:
            return papers[0]

        # Article de base: celui avec le plus de donnees
        base = max(papers, key=lambda p: self._completeness_score(p))

        # Fusionner les identifiants (prendre tous les IDs disponibles)
        for p in papers:
            if p.doi and not base.doi:
                base.doi = p.doi
            if p.openalex_id and not base.openalex_id:
                base.openalex_id = p.openalex_id
            if p.s2_paper_id and not base.s2_paper_id:
                base.s2_paper_id = p.s2_paper_id
            if p.s2_corpus_id and not base.s2_corpus_id:
                base.s2_corpus_id = p.s2_corpus_id
            if p.scopus_eid and not base.scopus_eid:
                base.scopus_eid = p.scopus_eid
            if p.arxiv_id and not base.arxiv_id:
                base.arxiv_id = p.arxiv_id
            if p.pmid and not base.pmid:
                base.pmid = p.pmid

        # Fusionner selon priorite des sources
        for field, priority in self.SOURCE_PRIORITY.items():
            best_value = self._get_best_value(papers, field, priority)
            if best_value is not None:
                setattr(base, field, best_value)

        # Fusionner les listes (keywords, fields_of_study)
        all_keywords = set()
        all_fields = set()
        all_types = set()

        for p in papers:
            all_keywords.update(p.keywords or [])
            all_fields.update(p.fields_of_study or [])
            all_types.update(p.publication_types or [])

        base.keywords = list(all_keywords)
        base.fields_of_study = list(all_fields)
        base.publication_types = list(all_types)

        # Enregistrer toutes les sources
        all_sources = set()
        for p in papers:
            all_sources.update(p.sources)
        base.sources = list(all_sources)

        # Conserver les donnees brutes de toutes les sources
        for p in papers:
            base.raw_data.update(p.raw_data)

        # Calculer le score de confiance
        base.confidence_score = min(1.0, len(papers) * 0.3 + 0.4)

        return base

    def _completeness_score(self, paper: Paper) -> int:
        """Calcule un score de completude des donnees."""
        score = 0

        if paper.title:
            score += 10
        if paper.abstract:
            score += 20
        if paper.authors:
            score += len(paper.authors) * 2
        if paper.doi:
            score += 15
        if paper.year:
            score += 5
        if paper.citation_count is not None:
            score += 5
        if paper.tldr:
            score += 5
        if paper.fields_of_study:
            score += 3
        if paper.is_open_access:
            score += 2
        if paper.pdf_url:
            score += 5

        return score

    def _get_best_value(
        self,
        papers: list[Paper],
        field: str,
        priority: list[PaperSource],
    ) -> Optional[any]:
        """Obtient la meilleure valeur selon la priorite des sources."""
        # D'abord, chercher selon la priorite
        for source in priority:
            for paper in papers:
                if source in paper.sources:
                    value = getattr(paper, field, None)
                    if value:
                        return value

        # Fallback: premiere valeur non-nulle
        for paper in papers:
            value = getattr(paper, field, None)
            if value:
                return value

        return None
