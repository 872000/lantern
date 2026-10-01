"""Engine tests: tokenizer/stemmer, index build, phrase search, PageRank,
BM25 ranking, query parser, spell correction, suggestions."""

import sqlite3

import pytest

from lantern import (
    Index,
    build_index,
    compute_pagerank,
    did_you_mean,
    parse_query,
    search,
    stem,
    suggest,
    tokenize,
)
from lantern.query import PhraseClause, TermClause
from lantern.rank import _bm25_scores
from tests.conftest import PAGES, build_db, write_frontier


# ---------------------------------------------------------------- tokenizer

class TestTokenizer:
    def test_lowercases_and_strips_punctuation(self):
        assert tokenize("Hello, WORLD!") == ["hello", "world"]

    def test_keeps_numbers(self):
        assert tokenize("h264 codec") == ["h264", "codec"]

    def test_stopwords_removed(self):
        assert tokenize("the cat and the dog") == ["cat", "dog"]

    def test_short_tokens_dropped(self):
        assert tokenize("a I x9 ok") == ["x9", "ok"]

    def test_empty_string(self):
        assert tokenize("") == []

    def test_stemming_rules(self):
        assert stem("running") == "run"
        assert stem("classes") == "class"
        assert stem("stories") == "stori"
        assert stem("played") == "play"
        assert stem("stopped") == "stop"
        assert stem("quickly") == "quick"

    def test_stemmer_is_consistent(self):
        # bake/baked and recipe/recipes must collapse together
        assert stem("baked") == stem("bake") == "bak"
        assert stem("recipes") == stem("recipe") == "recip"
        assert stem("cooking") == stem("cook") == "cook"

    def test_stemmer_keeps_double_s(self):
        assert stem("jazz") == "jazz"
        assert stem("class") == "class"


# ---------------------------------------------------------------- index build

class TestBuildIndex:
    def test_tables_created(self, db_path):
        con = sqlite3.connect(db_path)
        tables = {r[0] for r in
                  con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert {"docs", "links", "terms", "postings", "query_log"} <= tables

    def test_doc_ids_map_p0001_to_1(self, db_path):
        idx = Index(db_path)
        try:
            assert idx.doc_count() == 8
            doc = idx.get_doc(1)
            assert doc["url"] == "https://lantern.test/python/p0001"
        finally:
            idx.close()

    def test_positions_are_stored(self, db_path):
        idx = Index(db_path)
        try:
            # "The cat sat on the mat while the dog watched."
            #  -> cat@0 sat@1 mat@2 while@3 dog@4 watch@5
            assert idx.postings("cat", "body")[7][1] == [0]
            assert idx.postings("mat", "body")[7][1] == [2]
            assert idx.postings("watch", "body")[7][1] == [5]
            assert idx.get_doc(7)["length"] == 6
        finally:
            idx.close()

    def test_outlinks_to_unknown_ids_are_skipped(self, tmp_path):
        pages = [dict(PAGES[0], outlinks=["p0002", "p9999"]), PAGES[1]]
        db = build_db(tmp_path, pages, run_pr=False)
        con = sqlite3.connect(db)
        dsts = [r[0] for r in con.execute("SELECT dst FROM links")]
        con.close()
        assert 9999 not in dsts
        assert sorted(dsts) == [1, 2]

    def test_rebuild_is_idempotent(self, tmp_path):
        db = build_db(tmp_path, PAGES, run_pr=False)
        fp = write_frontier(str(tmp_path / "frontier.jsonl"), PAGES)
        build_index(frontier=fp, db=db)
        con = sqlite3.connect(db)
        n = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        con.close()
        assert n == 8

    def test_query_log_table_exists(self, db_path):
        con = sqlite3.connect(db_path)
        n = con.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
        con.close()
        assert n == 0


# ---------------------------------------------------------------- phrase search

class TestPhraseSearch:
    def test_phrase_matches_exact_order(self, db_path):
        ids = [r["id"] for r in search('"cat sat"')]
        assert ids == ["p0007"]

    def test_phrase_rejects_reordered_words(self, db_path):
        # doc p0008 has "sat ... cat" but never "sat cat" adjacently
        assert search('"sat cat"') == []

    def test_unquoted_terms_are_plain_and(self, db_path):
        ids = {r["id"] for r in search("cat sat")}
        assert ids == {"p0007", "p0008"}

    def test_phrase_boost_multiplier(self, tmp_path, monkeypatch):
        pages = [{"id": "p0001", "url": "https://lantern.test/t/p0001",
                  "title": "t", "text": "the red fox jumps", "outlinks": []}]
        db = build_db(tmp_path, pages, run_pr=False)
        monkeypatch.setenv("LANTERN_DB", db)
        idx = Index(db)
        try:
            s_phrase = _bm25_scores(parse_query('"red fox"'), idx, {1})
            s_terms = _bm25_scores(parse_query("red fox"), idx, {1})
            assert s_phrase[1] == pytest.approx(s_terms[1] * 1.5)
        finally:
            idx.close()


# ---------------------------------------------------------------- pagerank

def _reference_pagerank(links, ids, damping=0.85, tol=1e-6):
    """Independent pure-python power iteration (no numpy)."""
    n = len(ids)
    pos = {d: i for i, d in enumerate(ids)}
    out = [[] for _ in range(n)]
    for s, d in links:
        if s in pos and d in pos:
            out[pos[s]].append(pos[d])
    r = [1.0 / n] * n
    for _ in range(10000):
        new = [(1 - damping) / n] * n
        for i in range(n):
            if out[i]:
                share = damping * r[i] / len(out[i])
                for j in out[i]:
                    new[j] += share
            else:
                share = damping * r[i] / n
                for j in range(n):
                    new[j] += share
        if sum(abs(a - b) for a, b in zip(new, r)) < tol:
            r = new
            break
        r = new
    total = sum(r)
    return {d: r[pos[d]] / total for d in ids}


class TestPageRank:
    def _cycle_db(self, tmp_path):
        pages = [
            {"id": "p0001", "url": "https://lantern.test/t/p0001",
             "title": "a", "text": "alpha", "outlinks": ["p0002"]},
            {"id": "p0002", "url": "https://lantern.test/t/p0002",
             "title": "b", "text": "beta", "outlinks": ["p0003"]},
            {"id": "p0003", "url": "https://lantern.test/t/p0003",
             "title": "c", "text": "gamma", "outlinks": ["p0001"]},
        ]
        return build_db(tmp_path, pages, run_pr=False)

    def test_symmetric_cycle_is_uniform(self, tmp_path):
        # Hand-computed: a 3-cycle is symmetric, so the stationary
        # distribution must be exactly uniform.
        db = self._cycle_db(tmp_path)
        scores = compute_pagerank(db=db)
        for d in (1, 2, 3):
            assert scores[d] == pytest.approx(1 / 3, abs=1e-6)

    def test_scores_sum_to_one(self, tmp_path):
        db = self._cycle_db(tmp_path)
        scores = compute_pagerank(db=db)
        assert sum(scores.values()) == pytest.approx(1.0)

    def test_dangling_graph_ordering(self, tmp_path, pagerank_pages):
        # 1 -> {2, 3}, 2 -> 3, 3 dangling: 3 should outrank 2 outrank 1.
        db = build_db(tmp_path, pagerank_pages, run_pr=False)
        scores = compute_pagerank(db=db)
        assert scores[3] > scores[2] > scores[1]
        assert sum(scores.values()) == pytest.approx(1.0)

    def test_single_dangling_node_gets_all_mass(self, tmp_path):
        pages = [{"id": "p0001", "url": "https://lantern.test/t/p0001",
                  "title": "solo", "text": "lone page", "outlinks": []}]
        db = build_db(tmp_path, pages, run_pr=False)
        assert compute_pagerank(db=db) == {1: pytest.approx(1.0)}

    def test_matches_independent_reference(self, tmp_path, pagerank_pages):
        db = build_db(tmp_path, pagerank_pages, run_pr=False)
        got = compute_pagerank(db=db)
        idx = Index(db)
        try:
            want = _reference_pagerank(idx.links(), idx.doc_ids())
        finally:
            idx.close()
        for d in want:
            assert got[d] == pytest.approx(want[d], abs=1e-5)

    def test_result_is_a_fixed_point(self, tmp_path, pagerank_pages):
        # One more manual iteration must change the result by < tol.
        import numpy as np
        db = build_db(tmp_path, pagerank_pages, run_pr=False)
        got = compute_pagerank(db=db)
        idx = Index(db)
        try:
            ids = idx.doc_ids()
            links = idx.links()
        finally:
            idx.close()
        n = len(ids)
        pos = {d: i for i, d in enumerate(ids)}
        A = np.zeros((n, n))
        outdeg = np.zeros(n)
        for s, d in links:
            A[pos[d], pos[s]] += 1.0
            outdeg[pos[s]] += 1.0
        for i in range(n):
            A[:, i] = A[:, i] / outdeg[i] if outdeg[i] else 1.0 / n
        r = np.array([got[d] for d in ids])
        r_new = 0.85 * (A @ r) + 0.15 / n
        assert np.abs(r_new - r).sum() < 1e-6

    def test_scores_persisted_to_docs_table(self, tmp_path, pagerank_pages):
        db = build_db(tmp_path, pagerank_pages, run_pr=False)
        compute_pagerank(db=db)
        con = sqlite3.connect(db)
        vals = [r[0] for r in con.execute("SELECT pagerank FROM docs")]
        con.close()
        assert all(v > 0 for v in vals)
        assert sum(vals) == pytest.approx(1.0)


# ---------------------------------------------------------------- BM25 ranking

class TestBM25:
    def test_exact_title_match_ranks_first(self, tmp_path, monkeypatch):
        pages = [
            {"id": "p0001", "url": "https://lantern.test/python/p0001",
             "title": "Python list comprehensions",
             "text": "lorem ipsum dolor sit amet consectetur adipiscing elit",
             "outlinks": []},
            {"id": "p0002", "url": "https://lantern.test/cooking/p0002",
             "title": "Cooking recipes",
             "text": "python python python tutorial for beginners",
             "outlinks": []},
            {"id": "p0003", "url": "https://lantern.test/jazz/p0003",
             "title": "Jazz history",
             "text": "lorem ipsum dolor sit amet",
             "outlinks": []},
        ]
        db = build_db(tmp_path, pages)
        monkeypatch.setenv("LANTERN_DB", db)
        ids = [r["id"] for r in search("python list comprehensions")]
        assert ids[0] == "p0001"

    def test_title_match_beats_body_match(self, tmp_path, monkeypatch):
        # A: term once in title. B: term once in body. Title weight 2x wins.
        pages = [
            {"id": "p0001", "url": "https://lantern.test/t/p0001",
             "title": "python guide",
             "text": "lorem ipsum dolor sit amet consectetur adipiscing elit",
             "outlinks": []},
            {"id": "p0002", "url": "https://lantern.test/t/p0002",
             "title": "random musings",
             "text": "python lorem ipsum dolor sit amet",
             "outlinks": []},
        ]
        db = build_db(tmp_path, pages)
        monkeypatch.setenv("LANTERN_DB", db)
        ids = [r["id"] for r in search("python")]
        assert ids == ["p0001", "p0002"]


# ---------------------------------------------------------------- query parser

class TestQueryParser:
    def test_phrase_clause(self):
        pq = parse_query('"sourdough hydration"')
        assert len(pq.groups) == 1
        assert isinstance(pq.groups[0][0], PhraseClause)

    def test_term_exclusion(self):
        pq = parse_query("python -django")
        assert isinstance(pq.exclude[0], TermClause)
        assert pq.exclude[0].tokens == ["django"]

    def test_phrase_exclusion(self):
        pq = parse_query('-"machine learning"')
        assert isinstance(pq.exclude[0], PhraseClause)

    def test_or_splits_groups(self):
        pq = parse_query("python OR jazz")
        assert len(pq.groups) == 2
        assert pq.groups[0][0].tokens == ["python"]
        assert pq.groups[1][0].tokens == ["jazz"]

    def test_or_inside_quotes_is_literal(self):
        pq = parse_query('"cats OR dogs"')
        assert len(pq.groups) == 1
        assert isinstance(pq.groups[0][0], PhraseClause)

    def test_site_filter(self):
        pq = parse_query("bebop scales site:jazz")
        assert pq.site == "jazz"
        assert pq.terms() == ["bebop", "scal"]

    def test_stopword_only_query_is_empty(self):
        assert parse_query("the").groups == []

    def test_empty_query(self):
        pq = parse_query("")
        assert pq.groups == [] and pq.exclude == [] and pq.site is None


# ---------------------------------------------------------------- spell correction

class TestSpellCorrection:
    def test_corrects_misspelling(self, db_path):
        assert did_you_mean("pythn") == "python"

    def test_corrects_inside_phrase(self, db_path):
        assert did_you_mean('"sourdogh hydration"') == '"sourdough hydration"'

    def test_none_for_good_query(self, db_path):
        assert did_you_mean("python list comprehension") is None

    def test_none_for_garbage(self, db_path):
        assert did_you_mean("zzzqqq") is None

    def test_none_for_empty(self, db_path):
        assert did_you_mean("") is None
        assert did_you_mean("   ") is None

    def test_preserves_operators(self, db_path):
        got = did_you_mean('site:python pythn OR "list comprehesion"')
        assert got is not None
        assert "site:python" in got and " OR " in got
        assert "pythn" not in got

    def test_none_without_index(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LANTERN_DB", str(tmp_path / "missing.db"))
        assert did_you_mean("pythn") is None


# ---------------------------------------------------------------- suggestions

class TestSuggest:
    def test_prefix_completion(self, db_path):
        assert "python" in suggest("pyt")

    def test_respects_k(self, db_path):
        assert len(suggest("p", k=3)) <= 3

    def test_unknown_prefix(self, db_path):
        assert suggest("zzz") == []

    def test_empty_without_index(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LANTERN_DB", str(tmp_path / "missing.db"))
        assert suggest("pyt") == []
