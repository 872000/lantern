"""Tests for lantern.crawler — BFS crawl with politeness + robots filtering."""
import hashlib
import inspect
import json
from collections import deque
from pathlib import Path

import pytest

from lantern import corpus_gen, crawler


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("crawl")
    cdir = d / "corpus"
    corpus_gen.generate(seed=7, n=400, out_dir=str(cdir))
    out = d / "crawl" / "frontier.jsonl"
    stats = crawler.crawl(corpus_dir=str(cdir), out=str(out))
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    manifest = json.loads((cdir / "manifest.json").read_text(encoding="utf-8"))
    return {"dir": d, "cdir": cdir, "out": out, "stats": stats,
            "lines": lines, "manifest": manifest}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _bfs_depths(manifest, seeds):
    by_id = {e["id"]: e for e in manifest}
    adj = {}
    for e in manifest:
        kept = [o for o in e["outlinks"]
                if o in by_id and crawler.is_allowed(by_id[o]["url"])]
        adj[e["id"]] = kept
    depth, queue = {}, deque()
    for s in seeds:
        if s in by_id:
            depth[s] = 0
            queue.append(s)
    while queue:
        cur = queue.popleft()
        for nxt in adj[cur]:
            if nxt not in depth:
                depth[nxt] = depth[cur] + 1
                queue.append(nxt)
    return depth


# --- interface ----------------------------------------------------------------

def test_crawl_is_importable_with_documented_defaults():
    sig = inspect.signature(crawler.crawl)
    params = sig.parameters
    assert params["corpus_dir"].default == "corpus"
    assert params["out"].default == "crawl/frontier.jsonl"
    assert params["seeds"].default is None
    assert params["max_pages"].default is None
    assert "max_per_topic_per_round" in params
    assert "politeness_delay" in params


def test_default_seeds_are_content_hubs(built):
    seeds = crawler.default_seeds(built["manifest"])
    assert len(seeds) == 10  # one per content topic
    by_id = {e["id"]: e for e in built["manifest"]}
    assert all(crawler.is_allowed(by_id[s]["url"]) for s in seeds)
    assert all(len(by_id[s]["outlinks"]) >= 30 for s in seeds)


# --- frontier.jsonl schema -----------------------------------------------------

def test_frontier_schema(built):
    assert built["lines"], "frontier.jsonl is empty"
    for rec in built["lines"]:
        assert set(rec.keys()) == {"id", "url", "title", "text", "outlinks"}
        assert isinstance(rec["id"], str)
        assert rec["url"].startswith("https://lantern.test/")
        assert isinstance(rec["title"], str) and rec["title"].strip()
        assert isinstance(rec["text"], str) and len(rec["text"]) > 50
        assert isinstance(rec["outlinks"], list)
        assert all(isinstance(o, str) for o in rec["outlinks"])


def test_titles_match_manifest(built):
    by_id = {e["id"]: e for e in built["manifest"]}
    for rec in built["lines"][::25]:
        assert rec["title"] == by_id[rec["id"]]["title"]


# --- BFS ordering ----------------------------------------------------------------

def test_bfs_fetch_order(built):
    depths = _bfs_depths(built["manifest"], built["stats"]["seeds"])
    order = [depths[rec["id"]] for rec in built["lines"]]
    assert all(b >= a for a, b in zip(order, order[1:])), \
        "fetch order must have non-decreasing BFS depth"


# --- robots / disallow ------------------------------------------------------------

def test_robots_disallow_honored(built):
    assert built["stats"]["skipped_robots"] > 0
    for rec in built["lines"]:
        assert "/admin/" not in rec["url"], rec["url"]
    fetched_ids = {rec["id"] for rec in built["lines"]}
    assert not any(i.startswith("a") for i in fetched_ids)


def test_outlinks_drop_disallowed_targets(built):
    by_id = {e["id"]: e for e in built["manifest"]}
    leaked_src = next(
        e for e in built["manifest"]
        if not e["id"].startswith("a")
        and any(by_id[o]["id"].startswith("a") for o in e["outlinks"]))
    rec = next(r for r in built["lines"] if r["id"] == leaked_src["id"])
    assert not any(o.startswith("a") for o in rec["outlinks"]), \
        "disallowed targets must be filtered from recorded outlinks"


def test_is_allowed_unit():
    assert crawler.is_allowed("https://lantern.test/python/p0001")
    assert not crawler.is_allowed("https://lantern.test/admin/a0001")
    assert not crawler.is_allowed("https://lantern.test/admin/sub/a0002")


# --- closed graph -------------------------------------------------------------------

def test_frontier_outlinks_all_resolve(built):
    fetched_ids = {rec["id"] for rec in built["lines"]}
    for rec in built["lines"]:
        for o in rec["outlinks"]:
            assert o in fetched_ids, f"{rec['id']} -> unfetched {o}"


def test_full_corpus_reachable(built):
    content_ids = {e["id"] for e in built["manifest"]
                   if not e["id"].startswith("a")}
    fetched_ids = {rec["id"] for rec in built["lines"]}
    assert content_ids == fetched_ids


# --- text extraction ------------------------------------------------------------------

def test_text_strips_markup_scripts_styles(built):
    for rec in built["lines"]:
        assert "<" not in rec["text"], rec["id"]
        assert ">" not in rec["text"], rec["id"]
    blob = " ".join(rec["text"] for rec in built["lines"])
    assert "__trk_" not in blob  # injected <script> analytics token
    assert "lantern analytics beacon" not in blob


def test_extract_text_unit():
    page_html = """<html><head><title>T</title><style>.x{color:red}</style>
    <script>var __trk_1 = 1;</script></head>
    <body><!-- comment --><h1>Hello</h1><p>World &amp; friends</p></body></html>"""
    text = crawler.extract_text(page_html)
    assert "__trk_" not in text
    assert "color:red" not in text
    assert "comment" not in text
    assert "Hello" in text and "World & friends" in text
    assert "<" not in text


def test_extract_outlinks_unit():
    page_html = ('<a href="p0007">seven</a> '
                 "<a HREF='a0001'>admin</a> "
                 '<a href="https://lantern.test/jazz/p0042">jazz</a> '
                 '<a href="https://example.com/x">ext</a> '
                 '<a href="#frag">frag</a> '
                 '<a href="p0007">dup</a>')
    assert crawler.extract_outlinks(page_html) == ["p0007", "a0001", "p0042"]


def test_extract_title_unit():
    assert crawler.extract_title("<html><head><title>Hi &amp; Bye</title></head></html>") == "Hi & Bye"
    assert crawler.extract_title("<html></html>") == ""


# --- politeness / limits / determinism ---------------------------------------------------

def test_politeness_budget_respected(built):
    out = built["dir"] / "polite.jsonl"
    stats = crawler.crawl(corpus_dir=str(built["cdir"]), out=str(out),
                          max_per_topic_per_round=2)
    assert stats["per_round_max"] <= 2
    assert stats["rounds"] > 1
    assert stats["fetched"] == built["stats"]["fetched"]  # same coverage
    assert stats["politeness_ticks"] == stats["fetched"]


def test_max_pages_limit(built):
    out = built["dir"] / "limited.jsonl"
    stats = crawler.crawl(corpus_dir=str(built["cdir"]), out=str(out), max_pages=50)
    assert stats["fetched"] == 50
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 50


def test_crawl_deterministic(built):
    out = built["dir"] / "again.jsonl"
    crawler.crawl(corpus_dir=str(built["cdir"]), out=str(out))
    assert _sha(out) == _sha(built["out"])


def test_custom_seeds(built):
    out = built["dir"] / "seeded.jsonl"
    stats = crawler.crawl(corpus_dir=str(built["cdir"]), out=str(out),
                          seeds=["p0001"])
    assert stats["seeds"] == ["p0001"]
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["id"] == "p0001"
    assert stats["fetched"] == len(lines) > 0
