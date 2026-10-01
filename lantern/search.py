"""Public search API -- the ONLY interface Builder 3's web UI and CLI use.

``search(query, n=10)`` handles phrase/exclusion/OR/site: syntax via
:mod:`lantern.query` and ranking via :mod:`lantern.rank`.

The index database is ``lantern.db`` at the repo root by default. For
tests, set the ``LANTERN_DB`` environment variable to point at a
fixture database. When no usable index exists, ``search`` returns ``[]``
instead of raising.
"""

from __future__ import annotations

import os
import re

from .index import REPO_ROOT, Index
from .query import did_you_mean, parse_query
from .rank import rank

__all__ = ["search", "did_you_mean"]

SNIPPET_LEN = 160


def _db_path():
    return os.environ.get("LANTERN_DB") or str(REPO_ROOT / "lantern.db")


def _open_index():
    path = _db_path()
    if not os.path.exists(path):
        return None
    try:
        idx = Index(path)
    except Exception:
        return None
    if idx.doc_count() == 0:
        idx.close()
        return None
    return idx


def _make_snippet(text, parsed):
    """~160-char window around the first query-term hit, with <mark>
    highlighting of matched terms (case-insensitive)."""
    terms = sorted(set(parsed.surface_terms()), key=len, reverse=True)
    text = re.sub(r"\s+", " ", text or "").strip()
    start = 0
    if terms and text:
        best = None
        for t in terms:
            m = re.search(re.escape(t), text, re.IGNORECASE)
            if m and (best is None or m.start() < best):
                best = m.start()
        if best is not None:
            start = max(0, best - 60)
    snip = text[start:start + SNIPPET_LEN]
    if start > 0:
        snip = "\u2026" + snip
    if start + SNIPPET_LEN < len(text):
        snip = snip + "\u2026"
    if terms:
        pat = re.compile("|".join(re.escape(t) for t in terms),
                         re.IGNORECASE)
        snip = pat.sub(r"<mark>\g<0></mark>", snip)
    return snip


def search(query: str, n: int = 10) -> list[dict]:
    """Search the index.

    Args:
        query: supports ``"exact phrases"``, ``-excluded`` terms,
            ``OR`` (uppercase), and ``site:topic`` filters.
        n: maximum number of results.

    Returns:
        A list of dicts, each ``{"id", "url", "title", "snippet",
        "score"}`` with ``id`` like ``"p0001"`` and the snippet's
        matched terms wrapped in ``<mark>`` tags. Empty list when the
        query is blank, matches nothing, or no index exists.
    """
    idx = _open_index()
    if idx is None or not (query or "").strip():
        return []
    try:
        parsed = parse_query(query)
        ranked = rank(parsed, idx, n=n)
        out = []
        for doc_id, score in ranked:
            doc = idx.get_doc(doc_id)
            if doc is None:
                continue
            out.append({
                "id": f"p{doc_id:04d}",
                "url": doc["url"],
                "title": doc["title"],
                "snippet": _make_snippet(doc["text"], parsed),
                "score": round(float(score), 4),
            })
        return out
    finally:
        idx.close()
