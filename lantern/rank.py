"""Ranking: BM25 from scratch + title field boost + PageRank blend.

Design
------
* BM25 (k1=1.5, b=0.75) is computed separately over the ``body`` and
  ``title`` postings; the title component is weighted ``TITLE_WEIGHT``
  (2.0) times the body component. Title tokens live in their own
  postings rows (``field='title'``), so no extra structure is needed.
* Documents matching a ``"phrase"`` clause get their BM25 score
  multiplied by ``PHRASE_BOOST`` (1.5).
* Final score = ``0.7 * bm25_norm + 0.3 * pagerank_norm`` where both
  are per-query min-max normalized over the candidate set (a constant
  candidate set normalizes to 1.0).

Everything is implemented manually -- no sklearn.
"""

from __future__ import annotations

import math

from .query import PhraseClause

K1 = 1.5
B = 0.75
TITLE_WEIGHT = 2.0
PHRASE_BOOST = 1.5
W_BM25 = 0.7
W_PR = 0.3


def _idf(n_docs, df):
    return math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))


def _bm25(tf, doc_len, avg_len, idf):
    if tf <= 0 or avg_len <= 0:
        return 0.0
    return idf * (tf * (K1 + 1.0)) / (tf + K1 * (1.0 - B + B * doc_len / avg_len))


def _phrase_match(tokens, index, doc_id):
    """True if the stemmed tokens appear consecutively in body or title."""
    for fld in ("body", "title"):
        lists = [index.doc_positions(t, fld, doc_id) for t in tokens]
        if any(not l for l in lists):
            continue
        following = [set(l) for l in lists[1:]]
        for p in lists[0]:
            if all((p + i + 1) in s for i, s in enumerate(following)):
                return True
    return False


def _docs_for_clause(clause, index):
    if isinstance(clause, PhraseClause):
        return _docs_for_phrase(clause.tokens, index)
    docs = None
    for t in clause.tokens:
        d = set(index.postings(t, "body")) | set(index.postings(t, "title"))
        docs = d if docs is None else docs & d
        if not docs:
            break
    return docs or set()


def _docs_for_phrase(tokens, index):
    if not tokens:
        return set()
    first = (set(index.postings(tokens[0], "body"))
             | set(index.postings(tokens[0], "title")))
    return {d for d in first if _phrase_match(tokens, index, d)}


def _bm25_scores(parsed, index, doc_ids):
    """Raw BM25 scores (title boost + phrase boost), pre-normalization.

    White-box hook used by tests to verify the phrase multiplier.
    """
    n = index.doc_count()
    avgdl = index.avgdl()
    atitle = index.avg_title_len()
    terms = parsed.terms()
    uniq = set(terms)
    idfs = {t: _idf(n, index.df(t)) for t in uniq if index.df(t) > 0}
    body_p = {t: index.postings(t, "body") for t in uniq}
    title_p = {t: index.postings(t, "title") for t in uniq}
    scores = {}
    for d in doc_ids:
        doc = index.get_doc(d)
        if doc is None:
            continue
        s = 0.0
        for t in terms:
            idf = idfs.get(t)
            if idf is None:
                continue
            bp = body_p[t].get(d)
            if bp:
                s += _bm25(bp[0], doc["length"], avgdl, idf)
            tp = title_p[t].get(d)
            if tp:
                s += TITLE_WEIGHT * _bm25(tp[0], index.title_len(d),
                                          atitle, idf)
        scores[d] = s
    boosted = set()
    for c in parsed.clauses():
        if isinstance(c, PhraseClause):
            for d in doc_ids:
                if d not in boosted and _phrase_match(c.tokens, index, d):
                    scores[d] = scores.get(d, 0.0) * PHRASE_BOOST
                    boosted.add(d)
    return scores


def _minmax_norm(scores):
    if not scores:
        return {}
    lo = min(scores.values())
    hi = max(scores.values())
    if hi == lo:
        return {d: 1.0 for d in scores}
    return {d: (v - lo) / (hi - lo) for d, v in scores.items()}


def rank(parsed, index, n=10):
    """Rank documents for a parsed query.

    1. Candidates: AND within each OR-group, union across groups.
    2. ``site:`` filter, then exclusions.
    3. BM25 (+title boost, +phrase boost), per-query min-max norm.
    4. Blend with min-max normalized PageRank:
       ``0.7 * bm25_norm + 0.3 * pagerank_norm``.

    Returns ``[(doc_id, score)]`` sorted descending, top ``n``.
    """
    cand = set()
    for group in parsed.groups:
        g = None
        for clause in group:
            docs = _docs_for_clause(clause, index)
            g = docs if g is None else (g & docs)
            if not g:
                break
        cand |= g or set()
    if not cand:
        return []
    if parsed.site:
        seg = "/" + parsed.site.strip("/").lower() + "/"
        keep = set()
        for d in cand:
            doc = index.get_doc(d)
            if doc and doc.get("url") and seg in doc["url"].lower():
                keep.add(d)
        cand = keep
    for clause in parsed.exclude:
        cand -= _docs_for_clause(clause, index)
    if not cand:
        return []

    bm25 = _bm25_scores(parsed, index, cand)
    bm25n = _minmax_norm(bm25)
    prn = _minmax_norm({d: index.pagerank(d) for d in cand})
    final = {d: W_BM25 * bm25n.get(d, 0.0) + W_PR * prn.get(d, 0.0)
             for d in cand}
    return sorted(final.items(), key=lambda kv: kv[1], reverse=True)[:n]
