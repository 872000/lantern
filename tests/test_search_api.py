"""Tests for the public search() API: result shape, snippets, operators."""

import re

import pytest

from lantern import search


class TestSearchAPI:
    def test_result_shape(self, db_path):
        results = search("python")
        assert results, "expected hits for 'python'"
        for r in results:
            assert set(r.keys()) == {"id", "url", "title", "snippet", "score"}
            assert re.fullmatch(r"p\d{4}", r["id"])
            assert r["url"].startswith("https://lantern.test/")
            assert isinstance(r["score"], float)

    def test_n_is_respected(self, db_path):
        assert len(search("python", n=1)) == 1
        assert len(search("the", n=10)) == 0  # stopword-only -> no clauses

    def test_scores_descend(self, db_path):
        scores = [r["score"] for r in search("python", n=10)]
        assert scores == sorted(scores, reverse=True)

    def test_snippet_highlights_terms(self, db_path):
        r = search("sourdough")[0]
        assert "<mark>" in r["snippet"]
        assert re.search(r"<mark>sourdough</mark>", r["snippet"],
                         re.IGNORECASE)

    def test_snippet_approx_160_chars(self, db_path):
        r = search("sourdough")[0]
        plain = re.sub(r"</?mark>", "", r["snippet"])
        assert len(plain) <= 200

    def test_empty_query_returns_empty(self, db_path):
        assert search("") == []
        assert search("   ") == []

    def test_no_index_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LANTERN_DB", str(tmp_path / "missing.db"))
        assert search("python") == []

    def test_site_filter_end_to_end(self, db_path):
        results = search("sourdough site:cooking")
        assert results
        assert all("/cooking/" in r["url"] for r in results)

    def test_site_filter_excludes_other_topics(self, db_path):
        # 'python' matches python docs; site:cooking must hide them
        assert search("python site:cooking") == []

    def test_exclusion_end_to_end(self, db_path):
        ids = {r["id"] for r in search("python -django")}
        assert "p0002" not in ids
        assert "p0001" in ids

    def test_or_end_to_end(self, db_path):
        topics = {r["url"].split("/")[3] for r in search("sourdough OR bebop")}
        assert topics == {"cooking", "jazz"}

    def test_zero_hits_pairs_with_did_you_mean(self, db_path):
        from lantern import did_you_mean
        assert search("pythn") == []
        assert did_you_mean("pythn") == "python"

    def test_ranking_quality_smoke(self, db_path):
        top = search("python list comprehension")[0]
        assert "/python/" in top["url"]
