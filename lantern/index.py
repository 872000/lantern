"""Positional inverted index for Project Lantern.

Index-format choice: the whole index lives inside the SQLite database
(``lantern.db``) alongside the document/link tables, instead of a
separate ``index/`` directory of custom files. Rationale:

* one artifact to copy and inspect (``sqlite3 lantern.db "SELECT ..."``),
* crash-safe writes via SQLite transactions,
* no custom binary-format parser to maintain or debug.

Schema (all created fresh by :func:`build_index`)::

    docs(id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT,
         text TEXT, length INTEGER, pagerank REAL DEFAULT 0)
    links(src INTEGER, dst INTEGER)
    query_log(ts TEXT, query TEXT, hits INTEGER)
    terms(term TEXT PRIMARY KEY, df INTEGER, surface TEXT)
    postings(term TEXT NOT NULL, doc_id INTEGER NOT NULL,
             field TEXT NOT NULL, tf INTEGER NOT NULL,
             positions TEXT NOT NULL,
             PRIMARY KEY(term, doc_id, field))

Notes on the format:

* ``terms.df`` counts documents containing the term in *either* field.
* ``terms.surface`` is the most frequent raw (unstemmed) token that
  produced the stem; spell correction suggests surfaces, not stems.
* ``postings.field`` is ``'title'`` or ``'body'`` (two postings rows
  per term/doc at most). ``positions`` is a comma-joined list of
  0-based token positions measured on the *stemmed* token stream
  (after lowercasing, stopword removal and stemming), so phrase
  queries work directly on the stored positions.
* ``docs.length`` is the number of stemmed body tokens (BM25 ``dl``).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve(path):
    """Absolute paths pass through; relative ones resolve to the repo root.

    This keeps the documented defaults (``"lantern.db"``,
    ``"crawl/frontier.jsonl"``) working no matter which directory the
    caller runs from.
    """
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


SCHEMA = """
DROP TABLE IF EXISTS postings;
DROP TABLE IF EXISTS terms;
DROP TABLE IF EXISTS links;
DROP TABLE IF EXISTS docs;
DROP TABLE IF EXISTS query_log;
CREATE TABLE docs(
    id INTEGER PRIMARY KEY,
    url TEXT UNIQUE,
    title TEXT,
    text TEXT,
    length INTEGER,
    pagerank REAL DEFAULT 0
);
CREATE TABLE links(src INTEGER, dst INTEGER);
CREATE TABLE query_log(ts TEXT, query TEXT, hits INTEGER);
CREATE TABLE terms(term TEXT PRIMARY KEY, df INTEGER, surface TEXT);
CREATE TABLE postings(
    term TEXT NOT NULL,
    doc_id INTEGER NOT NULL,
    field TEXT NOT NULL,
    tf INTEGER NOT NULL,
    positions TEXT NOT NULL,
    PRIMARY KEY(term, doc_id, field)
);
CREATE INDEX idx_postings_term ON postings(term, field);
"""

# My own stopword list: common English function words that carry no
# topical signal. Single letters and digits-only tokens are handled by
# the length filter in tokenize().
STOPWORDS = frozenset(
    """a an the and or but if then else when at by for with about into
    through during before after above below to from up down in out on off
    over under again further once here there all any both each few more
    most other some such no nor not only own same so than too very can
    will just should now is are was were be been being have has had having
    do does did doing would could ought i me my myself we our ours ourselves
    you your yours yourself yourselves he him his himself she her hers
    herself it its itself they them their theirs themselves what which who
    whom this that these those am as of s t""".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def stem(word):
    """My own rule-based stemmer (Porter-lite).

    Rules, applied in order:

    1. ``sses`` -> ``ss``            (classes -> class)
    2. ``ies``  -> ``i``             (stories -> stori)
    3. trailing ``s`` stripped when len > 3 and the word does not end
       in ``ss`` or ``us``           (runs -> run; jazz, bus kept)
    4. ``ing`` stripped when len > 5; a doubled consonant left behind
       is reduced                     (running -> run, cooking -> cook)
    5. ``ed`` stripped when len > 4; doubled consonant reduced
       (stopped -> stop, played -> play)
    6. trailing ``e`` stripped when len > 3
       (bake -> bak, matching baked -> bak)
    7. ``ly`` stripped when len > 5  (quickly -> quick)

    Only one of rules 4/5 applies (``elif``). The stemmer favours
    *consistency* (bake/baked -> bak, recipe/recipes -> recip) over
    linguistic correctness -- what matters is that related surface
    forms collapse to the same stem.
    """
    w = word
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("ies"):
        w = w[:-3] + "i"
    elif w.endswith("s") and len(w) > 3 and not w.endswith(("ss", "us")):
        w = w[:-1]
    if w.endswith("ing") and len(w) > 5:
        w = w[:-3]
        if len(w) >= 3 and w[-1] == w[-2] and w[-1] not in "aeiou":
            w = w[:-1]
    elif w.endswith("ed") and len(w) > 4:
        w = w[:-2]
        if len(w) >= 3 and w[-1] == w[-2] and w[-1] not in "aeiou":
            w = w[:-1]
    if w.endswith("e") and len(w) > 3:
        w = w[:-1]
    if w.endswith("ly") and len(w) > 5:
        w = w[:-2]
    return w


def tokenize(text):
    """Lowercase -> ``[a-z0-9]+`` tokens -> drop stopwords/short tokens
    -> stem. Returns the list of stemmed tokens (positions preserved
    for the caller to enumerate)."""
    out = []
    for tok in _TOKEN_RE.findall((text or "").lower()):
        if len(tok) < 2 or tok in STOPWORDS:
            continue
        out.append(stem(tok))
    return out


def tokenize_with_surface(text):
    """Like :func:`tokenize` but yields ``(stem, raw_token)`` pairs."""
    out = []
    for tok in _TOKEN_RE.findall((text or "").lower()):
        if len(tok) < 2 or tok in STOPWORDS:
            continue
        out.append((stem(tok), tok))
    return out


def _page_int(pid):
    """``"p0001"`` -> ``1``."""
    if isinstance(pid, str) and pid.startswith("p") and pid[1:].isdigit():
        return int(pid[1:])
    raise ValueError(f"bad page id: {pid!r}")


def build_index(frontier="crawl/frontier.jsonl", db="lantern.db"):
    """Build the positional index from a frontier file.

    ``frontier``: JSON-lines, one object per line::

        {"id": "p0001", "url": "...", "title": "...",
         "text": "...", "outlinks": ["p0002", ...]}

    Populates ``docs`` (id ``p0001`` -> integer ``1``), ``links``
    (outlinks pointing at unknown ids are skipped), ``terms``,
    ``postings`` (title + body fields) and creates the empty
    ``query_log`` table for Builder 3. Rebuilding is idempotent: all
    tables are dropped and recreated.

    Returns ``{"docs": n, "links": n, "terms": n}``.
    """
    frontier = _resolve(frontier)
    db = _resolve(db)
    pages = []
    with open(frontier, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                pages.append(json.loads(line))

    con = sqlite3.connect(str(db))
    try:
        con.executescript(SCHEMA)
        known = {p["id"] for p in pages}
        doc_rows, link_rows = [], []
        post = defaultdict(list)          # (term, doc_id, field) -> [positions]
        df_docs = defaultdict(set)       # term -> {doc_id}
        surfaces = defaultdict(Counter)  # term -> Counter(raw token)
        for p in pages:
            did = _page_int(p["id"])
            title = p.get("title") or ""
            text = p.get("text") or ""
            t_pairs = tokenize_with_surface(title)
            b_pairs = tokenize_with_surface(text)
            doc_rows.append((did, p.get("url"), title, text, len(b_pairs), 0.0))
            for out in p.get("outlinks") or []:
                if out in known:
                    link_rows.append((did, _page_int(out)))
            for pos, (st, raw) in enumerate(t_pairs):
                post[(st, did, "title")].append(pos)
                df_docs[st].add(did)
                surfaces[st][raw] += 1
            for pos, (st, raw) in enumerate(b_pairs):
                post[(st, did, "body")].append(pos)
                df_docs[st].add(did)
                surfaces[st][raw] += 1
        con.executemany(
            "INSERT INTO docs(id, url, title, text, length, pagerank)"
            " VALUES (?,?,?,?,?,?)",
            doc_rows,
        )
        con.executemany("INSERT INTO links(src, dst) VALUES (?,?)", link_rows)
        con.executemany(
            "INSERT INTO terms(term, df, surface) VALUES (?,?,?)",
            [(t, len(ds), surfaces[t].most_common(1)[0][0])
             for t, ds in df_docs.items()],
        )
        con.executemany(
            "INSERT INTO postings(term, doc_id, field, tf, positions)"
            " VALUES (?,?,?,?,?)",
            [(t, d, f, len(pl), ",".join(map(str, pl)))
             for (t, d, f), pl in post.items()],
        )
        con.commit()
    finally:
        con.close()
    return {"docs": len(doc_rows), "links": len(link_rows),
            "terms": len(df_docs)}


class Index:
    """Read-only view over a built ``lantern.db``."""

    def __init__(self, db="lantern.db"):
        path = _resolve(db)
        if not os.path.exists(path):
            raise FileNotFoundError(f"no index db at {path}")
        self.path = str(path)
        self._con = sqlite3.connect(self.path)
        self._con.row_factory = sqlite3.Row
        self._doc_count = self._con.execute(
            "SELECT COUNT(*) AS c FROM docs").fetchone()["c"]
        self._avgdl = self._con.execute(
            "SELECT AVG(length) AS a FROM docs").fetchone()["a"] or 0.0
        self._title_lens = {}
        total = 0
        rows = self._con.execute("SELECT id, title FROM docs").fetchall()
        for r in rows:
            tl = len(tokenize(r["title"] or ""))
            self._title_lens[r["id"]] = tl
            total += tl
        self._avg_title = (total / len(rows)) if rows else 0.0
        self._vocab = None
        self._surfaces = None

    def close(self):
        self._con.close()

    def doc_count(self):
        return self._doc_count

    def avgdl(self):
        """Mean stemmed body length (BM25 ``avgdl``)."""
        return self._avgdl

    def avg_title_len(self):
        return self._avg_title

    def title_len(self, doc_id):
        return self._title_lens.get(doc_id, 0)

    def df(self, term):
        row = self._con.execute(
            "SELECT df FROM terms WHERE term = ?", (term,)).fetchone()
        return row["df"] if row else 0

    def postings(self, term, field="body"):
        """``{doc_id: (tf, [positions])}`` for one term/field."""
        out = {}
        for r in self._con.execute(
                "SELECT doc_id, tf, positions FROM postings"
                " WHERE term = ? AND field = ?", (term, field)):
            out[r["doc_id"]] = (
                r["tf"],
                [int(x) for x in r["positions"].split(",") if x],
            )
        return out

    def doc_positions(self, term, field, doc_id):
        """Position list for one (term, field, doc), or None."""
        row = self._con.execute(
            "SELECT positions FROM postings"
            " WHERE term = ? AND field = ? AND doc_id = ?",
            (term, field, doc_id)).fetchone()
        if not row:
            return None
        return [int(x) for x in row["positions"].split(",") if x]

    def get_doc(self, doc_id):
        row = self._con.execute(
            "SELECT id, url, title, text, length, pagerank FROM docs"
            " WHERE id = ?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def pagerank(self, doc_id):
        row = self._con.execute(
            "SELECT pagerank FROM docs WHERE id = ?", (doc_id,)).fetchone()
        return row["pagerank"] if row else 0.0

    def links(self):
        return [(r["src"], r["dst"])
                for r in self._con.execute("SELECT src, dst FROM links")]

    def doc_ids(self):
        return [r["id"]
                for r in self._con.execute("SELECT id FROM docs ORDER BY id")]

    def vocab(self):
        if self._vocab is None:
            self._vocab = {r["term"]
                           for r in self._con.execute("SELECT term FROM terms")}
        return self._vocab

    def surfaces(self):
        """``{stem: most-frequent raw token}`` (for spell correction)."""
        if self._surfaces is None:
            self._surfaces = {
                r["term"]: r["surface"]
                for r in self._con.execute("SELECT term, surface FROM terms")
            }
        return self._surfaces

    def popular_query_terms(self, limit=200):
        """Most frequent stemmed terms from ``query_log`` (may be empty)."""
        try:
            rows = self._con.execute(
                "SELECT query FROM query_log ORDER BY ts DESC LIMIT ?",
                (limit,)).fetchall()
        except sqlite3.OperationalError:
            return []
        counts = Counter()
        for r in rows:
            for t in tokenize(r["query"] or ""):
                counts[t] += 1
        return [t for t, _ in counts.most_common(limit)]
