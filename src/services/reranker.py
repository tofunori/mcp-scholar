"""BM25 relevance reranker for scholar search results.

Reranks a list of Paper objects against a query using BM25 (Okapi),
reusing ragdoc's scientific-domain AdvancedTokenizer when available.

Design goals:
- Import-safe: if rank_bm25 cannot be imported, rerank() degrades to a
  simple truncation and never raises.
- Robust: if the AdvancedTokenizer cannot be constructed (e.g. missing
  nltk data), fall back to a simple regex tokenizer.
- Graceful: empty corpus or all-zero BM25 scores leave order unchanged.
"""

import logging
import re
from typing import Optional

from ..models import Paper

logger = logging.getLogger(__name__)

# Import rank_bm25 defensively so the module is always importable.
try:
    from rank_bm25 import BM25Okapi
    _BM25_AVAILABLE = True
except Exception as exc:  # pragma: no cover - depends on env
    BM25Okapi = None  # type: ignore
    _BM25_AVAILABLE = False
    logger.warning("rank_bm25 unavailable, reranking disabled: %s", exc)


def reranker_available() -> bool:
    """True si le reranking BM25 est reellement actif (rank_bm25 importe).

    Permet a l'appelant de renseigner un flag de metadata honnete: si BM25
    est absent, rerank() degrade en no-op et le classement n'a PAS eu lieu.
    """
    return _BM25_AVAILABLE


def _simple_tokenize(text: str) -> list[str]:
    """Fallback tokenizer: lowercase alphanumeric runs."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _build_tokenizer():
    """Return a callable(text) -> list[str].

    Prefer ragdoc's AdvancedTokenizer; fall back to a simple regex
    tokenizer if it cannot be constructed (e.g. missing nltk data).
    """
    try:
        from ..bm25_tokenizers.advanced_tokenizer import AdvancedTokenizer

        tokenizer = AdvancedTokenizer()
        return tokenizer.tokenize
    except Exception as exc:
        logger.warning(
            "AdvancedTokenizer unavailable, using simple tokenizer: %s", exc
        )
        return _simple_tokenize


def _paper_text(paper: Paper) -> str:
    """Concatenate title, abstract and keywords into one searchable string."""
    title = paper.title or ""
    abstract = paper.abstract or ""
    keywords = " ".join(paper.keywords or [])
    return " ".join([title, abstract, keywords])


def rerank(
    query: str,
    papers: list[Paper],
    limit: Optional[int] = None,
) -> list[Paper]:
    """Rerank papers by BM25 relevance to the query.

    Args:
        query: The search query.
        papers: Papers to rerank.
        limit: If set, return at most this many papers after sorting.

    Returns:
        Papers sorted by descending BM25 score (stable on ties). If
        reranking is not possible (no rank_bm25, empty corpus, all-zero
        scores), papers are returned in their original order, optionally
        truncated to `limit`. Never raises.
    """
    if not papers:
        return papers

    # If BM25 is unavailable, return unchanged (optionally truncated).
    if not _BM25_AVAILABLE:
        return papers[:limit] if limit is not None else papers

    try:
        tokenize = _build_tokenizer()

        corpus_tokens = [tokenize(_paper_text(p)) for p in papers]

        # If every document tokenizes to empty, BM25 has nothing to work on.
        if not any(corpus_tokens):
            return papers[:limit] if limit is not None else papers

        bm25 = BM25Okapi(corpus_tokens)
        query_tokens = tokenize(query or "")
        if not query_tokens:
            return papers[:limit] if limit is not None else papers

        scores = bm25.get_scores(query_tokens)

        # All-zero scores: nothing discriminates, keep input order.
        if not any(score > 0 for score in scores):
            return papers[:limit] if limit is not None else papers

        # Stable sort by descending score (preserve input order on ties).
        order = sorted(
            range(len(papers)),
            key=lambda i: -scores[i],
        )
        ranked = [papers[i] for i in order]
    except Exception as exc:  # pragma: no cover - safety net
        logger.warning("BM25 rerank failed, returning original order: %s", exc)
        return papers[:limit] if limit is not None else papers

    return ranked[:limit] if limit is not None else ranked
