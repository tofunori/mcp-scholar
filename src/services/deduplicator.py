"""Deduplication d'articles multi-sources."""

import re
from difflib import SequenceMatcher
from typing import Optional

from ..models import Paper


def normalize_doi(doi: Optional[str]) -> str:
    """Normalise un DOI pour la deduplication.

    - minuscules + trim
    - retire un prefixe URL eventuel (https://doi.org/, doi:)
    - effondre les variantes preprint EGU/Copernicus vers une forme finale:
        * retire les suffixes de commentaire referee/auteur/court:
          -rcN, -acN, -scN, -ccN (ex. tc-2020-87-rc1 -> tc-2020-87)
        * retire le prefixe de depot 'egusphere-' (ex. egusphere-2023-1 -> 2023-1)

    Note: la forme preprint (ex. tc-2020-87) et la forme publiee
    (ex. tc-14-3249-2020) restent des chaines DOI distinctes; leur fusion
    repose sur le repli titre-fuzzy (titre + auteurs/annee).
    """
    if not doi:
        return ""
    d = doi.lower().strip()
    # Retirer prefixes URL / scheme
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:", "", d)
    d = d.strip()
    # Variantes preprint EGU/Copernicus: limiter STRICTEMENT au registrant
    # 10.5194/ pour ne pas mutiler un DOI publie qui finirait par -ccN/-scN
    # chez un autre editeur, ni fabriquer une cle 10.xxxx/<annee>-<n> inexistante.
    if d.startswith("10.5194/"):
        # Retirer le prefixe de depot egusphere des preprints
        d = re.sub(r"^10\.5194/egusphere-", "10.5194/", d)
        # Retirer les suffixes de commentaire referee/auteur/short/community
        # (un ou plusieurs, ex. -rc1, -rc2, -ac1, -sc1, -cc1)
        d = re.sub(r"(?:-(?:rc|ac|sc|cc)\d+)+$", "", d)
    return d


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
            doi_normalized = normalize_doi(paper.doi)
            doi_key = f"doi:{doi_normalized}"

            # Verifier si un article existant a ce DOI (normalise: les variantes
            # preprint EGU -rc/-ac/-sc/-cc et egusphere- s'effondrent ici)
            for key, group in existing.items():
                for p in group:
                    if p.doi and normalize_doi(p.doi) == doi_normalized:
                        return key

            # Repli preprint<->publie: un DOI distinct peut tout de meme
            # designer le meme article (ex. preprint tc-2020-87 vs publie
            # tc-14-3249-2020). Garde STRICT pour eviter de sur-fusionner des
            # articles distincts (ex. Part I / Part II du meme auteur, meme
            # annee): on EXIGE un chevauchement de noms d'auteurs ET une tres
            # forte similarite de titre (>=0.95, plus severe que le seuil fuzzy).
            for key, group in existing.items():
                for p in group:
                    if self._authors_overlap(paper, p) and self._is_title_match(
                        paper, p, threshold=0.95
                    ):
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

    def _is_title_match(
        self, p1: Paper, p2: Paper, threshold: Optional[float] = None
    ) -> bool:
        """Verifie si deux articles ont des titres similaires.

        threshold: seuil de similarite a utiliser (defaut: self.title_threshold).
        Un appelant peut exiger un seuil plus severe (ex. fusion cross-DOI).
        """
        if threshold is None:
            threshold = self.title_threshold
        if not p1.title or not p2.title:
            return False

        # Annee doit correspondre si disponible
        # S'assurer que les annees sont des entiers avant comparaison
        try:
            year1 = int(p1.year) if p1.year else None
            year2 = int(p2.year) if p2.year else None
            if year1 and year2 and abs(year1 - year2) > 1:
                return False
        except (ValueError, TypeError):
            pass  # Si conversion echoue, ignorer le filtre d'annee

        # Normaliser les titres
        title1 = p1._normalize_title()
        title2 = p2._normalize_title()

        if not title1 or not title2:
            return False

        ratio = SequenceMatcher(None, title1, title2).ratio()
        return ratio >= threshold

    @staticmethod
    def _author_surnames(paper: Paper) -> set[str]:
        """Extrait l'ensemble des noms de famille (minuscules) d'un article."""
        surnames: set[str] = set()
        for a in getattr(paper, "authors", None) or []:
            name = getattr(a, "name", None) or ""
            name = name.strip()
            if not name:
                continue
            # "Nom, Prenom" -> Nom ; sinon dernier token de "Prenom Nom"
            if "," in name:
                surname = name.split(",", 1)[0]
            else:
                surname = name.split()[-1]
            surname = surname.strip().lower()
            if surname:
                surnames.add(surname)
        return surnames

    def _authors_overlap(self, p1: Paper, p2: Paper) -> bool:
        """Vrai si les deux articles partagent au moins un nom de famille."""
        s1 = self._author_surnames(p1)
        s2 = self._author_surnames(p2)
        if not s1 or not s2:
            return False
        return bool(s1 & s2)

    @staticmethod
    def _same_year(p1: Paper, p2: Paper) -> bool:
        """Vrai si les deux articles ont la meme annee (entiere)."""
        try:
            y1 = int(p1.year) if p1.year else None
            y2 = int(p2.year) if p2.year else None
        except (ValueError, TypeError):
            return False
        return y1 is not None and y2 is not None and y1 == y2

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
