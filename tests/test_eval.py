"""Tests for eval.run_eval()."""

from lantern import JUDGED_QUERIES, run_eval

TOPICS = {"python", "cooking", "space", "basketball",
          "gardening", "jazz", "hiking", "retro-gaming"}


def test_judged_query_count():
    assert len(JUDGED_QUERIES) >= 12


def test_judged_queries_cover_all_topics():
    covered = set()
    for _, expected in JUDGED_QUERIES:
        for topic in TOPICS:
            if any(f"/{topic}/" in e for e in expected):
                covered.add(topic)
    assert covered == TOPICS


def test_run_eval_returns_summary(db_path, capsys):
    summary = run_eval()
    assert set(summary.keys()) == {"p_at_5", "mrr", "n"}
    assert summary["n"] == len(JUDGED_QUERIES)
    assert 0.0 <= summary["p_at_5"] <= 1.0
    assert 0.0 <= summary["mrr"] <= 1.0
    out = capsys.readouterr().out
    assert "P@5" in out and "AVG" in out


def test_run_eval_without_index(tmp_path, monkeypatch):
    monkeypatch.setenv("LANTERN_DB", str(tmp_path / "missing.db"))
    assert run_eval() == {"p_at_5": 0.0, "mrr": 0.0, "n": 0}
