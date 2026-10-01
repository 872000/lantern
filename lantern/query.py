"""Query parsing, spell correction and prefix suggestions.

Supported query syntax (see :func:`parse_query`)::

    "exact phrase"      words must appear consecutively (positional index)
    -excluded           drop docs containing the term (or -"a phrase")
    OR                  uppercase only; splits the query into OR-groups
                        (default connective inside a group is AND)
    site:topic          keep only docs whose URL contains /<topic>/
                        (matches the <topic> path segment of
                        https://lantern.test/<topic>/<id>)

Examples:
    ``python OR "list comprehension" -django site:python``
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .index import REPO_ROOT, STOPWORDS, Index, stem, tokenize

_QUERY_TOKEN = re.compile(r'"([^"]*)"|(-"[^"]*")|(\S+)')


@dataclass
class TermClause:
    tokens: list  # stemmed tokens (ANDed together)
    raw: str      # surface form, for snippets/highlighting


@dataclass
class PhraseClause:
    tokens: list  # stemmed tokens, must be consecutive
    raw: str


@dataclass
class ParsedQuery:
    groups: list = field(default_factory=list)   # OR of AND-groups
    exclude: list = field(default_factory=list)  # clauses to subtract
    site: str = None

    def clauses(self):
        """All positive clauses across groups."""
        return [c for g in self.groups for c in g]

    def terms(self):
        """All stemmed query terms, including phrase words."""
        out = []
        for c in self.clauses():
            out.extend(c.tokens)
        return out

    def surface_terms(self):
        """Raw alphanumeric words (for snippet extraction/highlight)."""
        out = []
        for c in self.clauses():
            out.extend(re.findall(r"[a-z0-9]+", c.raw.lower()))
        return out


def parse_query(query):
    """Parse a raw query string into a :class:`ParsedQuery`.

    * ``"..."`` spans become phrase clauses (quote-aware: an ``OR``
      inside quotes is literal).
    * Bare uppercase ``OR`` starts a new OR-group.
    * ``site:topic`` sets the topic filter (lowercased).
    * ``-term`` / ``-"a phrase"`` become exclusions.
    * Everything else is tokenized (lowercased, stopwords removed,
      stemmed); a token that tokenizes to nothing (e.g. ``"the"``)
      contributes no clause.
    * A lowercase ``or`` is just a stopword and disappears, so
      ``cats or dogs`` behaves as an AND query.
    """
    pq = ParsedQuery()
    groups = [[]]
    for m in _QUERY_TOKEN.finditer(query or ""):
        quoted, negquoted, word = m.group(1), m.group(2), m.group(3)
        if quoted is not None:
            toks = tokenize(quoted)
            if toks:
                groups[-1].append(PhraseClause(toks, quoted))
            continue
        if negquoted is not None:
            inner = negquoted[2:-1]
            toks = tokenize(inner)
            if toks:
                pq.exclude.append(PhraseClause(toks, inner))
            continue
        tok = word
        if tok == "OR":
            groups.append([])
            continue
        low = tok.lower()
        if low.startswith("site:"):
            pq.site = low[5:].strip().strip("/")
            continue
        if tok.startswith("-") and len(tok) > 1:
            inner = tok[1:]
            if len(inner) >= 2 and inner.startswith('"') and inner.endswith('"'):
                toks = tokenize(inner[1:-1])
                if toks:
                    pq.exclude.append(PhraseClause(toks, inner[1:-1]))
            else:
                toks = tokenize(inner)
                if toks:
                    pq.exclude.append(TermClause(toks, inner))
            continue
        toks = tokenize(tok)
        if toks:
            groups[-1].append(TermClause(toks, tok))
    pq.groups = [g for g in groups if g]
    return pq


def levenshtein(a, b):
    """Edit distance between two strings (plain dynamic programming)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1,        # deletion
                           cur[j - 1] + 1,    # insertion
                           prev[j - 1] + (ca != cb)))  # substitution
        prev = cur
    return prev[-1]


def _db_path():
    return os.environ.get("LANTERN_DB") or str(REPO_ROOT / "lantern.db")


def _get_index():
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


def _correct_word(word, vocab_list, vocab_set, surfaces):
    """Return the corrected surface form for one word, or the word."""
    raw = word.lower()
    if len(raw) < 2 or raw in STOPWORDS or raw == "or":
        return word
    s = stem(raw)
    if s in vocab_set:
        return word  # already a known term
    best, bestd = None, None
    for term in vocab_list:
        if abs(len(term) - len(s)) > 2:
            continue
        d = levenshtein(s, term)
        if bestd is None or d < bestd:
            best, bestd = term, d
    thresh = 1 if len(s) <= 4 else 2
    if best is not None and bestd <= thresh:
        return surfaces.get(best, best)
    return word


def did_you_mean(query):
    """Suggest a corrected query string, or None.

    Each unknown (out-of-vocabulary) word is replaced by its closest
    index-vocabulary term by edit distance (<=1 for short words, <=2
    otherwise); the suggestion uses the term's most frequent surface
    form. Operators (``OR``, ``site:``, ``-``) and quoted phrases are
    preserved. Returns None when the query needs no correction, when
    nothing is close enough, or when no index exists.
    """
    idx = _get_index()
    if idx is None or not (query or "").strip():
        return None
    try:
        vocab_set = idx.vocab()
        if not vocab_set:
            return None
        vocab_list = sorted(vocab_set)
        surfaces = idx.surfaces()
        changed = False
        out = []
        for m in _QUERY_TOKEN.finditer(query):
            quoted, negquoted, word = m.group(1), m.group(2), m.group(3)
            if quoted is not None:
                words = quoted.split()
                new_words = [_correct_word(w, vocab_list, vocab_set, surfaces)
                             for w in words]
                if new_words != words:
                    changed = True
                out.append('"' + " ".join(new_words) + '"')
            elif negquoted is not None:
                inner = negquoted[2:-1]
                words = inner.split()
                new_words = [_correct_word(w, vocab_list, vocab_set, surfaces)
                             for w in words]
                if new_words != words:
                    changed = True
                out.append('-"' + " ".join(new_words) + '"')
            else:
                tok = word
                low = tok.lower()
                if (low == "or" or low.startswith("site:")
                        or (tok.startswith("-") and len(tok) > 1)):
                    out.append(tok)
                else:
                    new = _correct_word(tok, vocab_list, vocab_set, surfaces)
                    if new != tok:
                        changed = True
                    out.append(new)
        return " ".join(out) if changed else None
    finally:
        idx.close()


class Trie:
    """Minimal prefix trie over the suggestion vocabulary."""

    def __init__(self):
        self.root = {}

    def insert(self, word):
        node = self.root
        for ch in word:
            node = node.setdefault(ch, {})
        node["$"] = True

    def completions(self, prefix, k=5):
        node = self.root
        for ch in prefix:
            node = node.get(ch)
            if node is None:
                return []
        out = []

        def dfs(nd, cur):
            if len(out) >= k:
                return
            if "$" in nd:
                out.append(cur)
            for ch in sorted(nd):
                if ch == "$":
                    continue
                dfs(nd[ch], cur + ch)
                if len(out) >= k:
                    return

        dfs(node, prefix)
        return out


def suggest(prefix, k=5):
    """Up to ``k`` completions for ``prefix`` from the index vocabulary
    (surface forms) plus popular ``query_log`` terms when the table has
    rows. Returns ``[]`` when no index exists."""
    idx = _get_index()
    if idx is None:
        return []
    try:
        trie = Trie()
        surfaces = idx.surfaces()
        for surf in surfaces.values():
            if surf:
                trie.insert(surf.lower())
        for stemmed in idx.popular_query_terms():
            trie.insert(surfaces.get(stemmed, stemmed))
        return trie.completions((prefix or "").lower(), k)
    finally:
        idx.close()
