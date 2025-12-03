"""Deduplication d'articles multi-sources."""

from difflib import SequenceMatcher
from typing import Optional

from ..models import Paper


class Deduplicator:
    """Deduplication hierarchique multi-niveaux pour les articles."""

    def __init__(self, title_threshold: float = 0.85):
        self.title_threshold = title_threshold

    def deduplicate(self, papers: list[Paper]) -> tuple[list[Paper], int]:
        """
        Deduplique une liste d'articles.

        Strategie hierarchique:
        1. DOI exact match (priorite maximale)
        2. S2 Corpus ID match
        3. OpenAlex ID match
        4. Titre + Annee (fuzzy, seuil 85%)

        Returns:
            Tuple (articles dedupliques, nombre de doublons supprimes)
        """
        if not papers:
            return [], 0

        groups: dict[str, list[Paper]] = {}

        for paper in papers:
            key = self._get_dedup_key(paper, groups)
            if key not in groups:
                groups[key] = []
            groups[key].append(paper)

        # Fusionner chaque groupe
        from .merger import MetadataMerger
        merger = MetadataMerger()

        merged = [merger.merge(group) for group in groups.values()]
        duplicates_removed = len(papers) - len(merged)

        return merged, duplicates_removed

    def _get_dedup_key(self, paper: Paper, existing: dict[str, list[Paper]]) -> str:
        """Determine la cle de deduplication pour un article."""

        # Niveau 1: DOI (priorite maximale)
        if paper.doi:
            doi_normalized = paper.doi.lower().strip()
            doi_key = f"doi:{doi_normalized}"

            # Verifier si un article existant a ce DOI
            for key, group in existing.items():
                for p in group:
                    if p.doi and p.doi.lower().strip() == doi_normalized:
                        return key

            return doi_key

        # Niveau 2: S2 Corpus ID
        if paper.s2_corpus_id:
            s2_key = f"s2:{paper.s2_corpus_id}"
            if s2_key in existing:
                return s2_key

            # Verifier si un article existant a ce S2 ID
            for key, group in existing.items():
                for p in group:
                    if p.s2_corpus_id == paper.s2_corpus_id:
                        return key

            return s2_key

        # Niveau 3: OpenAlex ID
        if paper.openalex_id:
            oa_key = f"oa:{paper.openalex_id}"
            if oa_key in existing:
                return oa_key

            # Verifier si un article existant a ce OpenAlex ID
            for key, group in existing.items():
                for p in group:
                    if p.openalex_id == paper.openalex_id:
                        return key

            return oa_key

        # Niveau 4: Titre + Annee (fuzzy)
        for key, group in existing.items():
            for p in group:
                if self._is_title_match(paper, p):
                    return key

        # Nouvelle entree
        return paper.get_canonical_id()

    def _is_title_match(self, p1: Paper, p2: Paper) -> bool:
        """Verifie si deux articles ont des titres similaires."""
        if not p1.title or not p2.title:
            return False

        # Annee doit correspondre si disponible
        if p1.year and p2.year and abs(p1.year - p2.year) > 1:
            return False

        # Normaliser les titres
        title1 = p1._normalize_title()
        title2 = p2._normalize_title()

        if not title1 or not title2:
            return False

        ratio = SequenceMatcher(None, title1, title2).ratio()
        return ratio >= self.title_threshold

    def find_duplicates(self, papers: list[Paper]) -> list[list[Paper]]:
        """
        Trouve les groupes de doublons sans les fusionner.

        Utile pour le debugging ou l'inspection manuelle.
        """
        groups: dict[str, list[Paper]] = {}

        for paper in papers:
            key = self._get_dedup_key(paper, groups)
            if key not in groups:
                groups[key] = []
            groups[key].append(paper)

        # Retourner seulement les groupes avec plus d'un article
        return [group for group in groups.values() if len(group) > 1]
