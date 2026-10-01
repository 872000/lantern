"""Tests for lantern.corpus_gen — the synthetic web corpus generator."""
import hashlib
import json
import re
from pathlib import Path

import pytest

from lantern import corpus_gen

TOPICS = ["python", "cooking", "space", "basketball", "gardening", "jazz",
          "hiking", "retro-gaming", "coffee", "cycling"]


@pytest.fixture(scope="module")
def small_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("corpus_small")
    corpus_gen.generate(seed=7, n=400, out_dir=str(d))
    return d


@pytest.fixture(scope="module")
def small_manifest(small_dir):
    return json.loads((small_dir / "manifest.json").read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- scale / determinism ----------------------------------------------------

def test_default_page_count(tmp_path):
    res = corpus_gen.generate(seed=7, n=2500, out_dir=str(tmp_path / "corpus"))
    pages = list((tmp_path / "corpus" / "pages").glob("p*.html"))
    admin_pages = list((tmp_path / "corpus" / "pages").glob("a*.html"))
    assert len(pages) == 2500
    assert len(admin_pages) == 25
    manifest = json.loads((tmp_path / "corpus" / "manifest.json").read_text())
    assert len(manifest) == 2500 + 25  # 25 admin pages (2500 // 100)
    assert res["pages"] == 2500 and res["admin"] == 25


def test_same_seed_byte_identical_manifest(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    corpus_gen.generate(seed=7, n=400, out_dir=str(a))
    corpus_gen.generate(seed=7, n=400, out_dir=str(b))
    assert _sha(a / "manifest.json") == _sha(b / "manifest.json")


def test_same_seed_byte_identical_pages(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    corpus_gen.generate(seed=7, n=400, out_dir=str(a))
    corpus_gen.generate(seed=7, n=400, out_dir=str(b))
    for name in ["p0001.html", "p0133.html", "p0400.html"]:
        assert _sha(a / "pages" / name) == _sha(b / "pages" / name)


def test_different_seed_differs(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    corpus_gen.generate(seed=7, n=400, out_dir=str(a))
    corpus_gen.generate(seed=8, n=400, out_dir=str(b))
    assert _sha(a / "manifest.json") != _sha(b / "manifest.json")


def test_generate_returns_summary(tmp_path):
    res = corpus_gen.generate(seed=7, n=120, out_dir=str(tmp_path / "c"))
    assert res["pages"] == 120
    assert res["admin"] == 1
    assert Path(res["manifest"]).exists()


# --- manifest schema / consistency ------------------------------------------

def test_manifest_schema(small_manifest):
    url_re = re.compile(r"^https://lantern\.test/[a-z-]+/[a-z]\d+$")
    for entry in small_manifest:
        assert set(entry.keys()) == {"id", "url", "title", "outlinks"}
        assert url_re.match(entry["url"]), entry["url"]
        assert isinstance(entry["title"], str) and entry["title"].strip()
        assert isinstance(entry["outlinks"], list)
        assert all(isinstance(o, str) for o in entry["outlinks"])
        # url path mirrors the id
        assert entry["url"].endswith("/" + entry["id"])


def test_manifest_outlinks_resolve(small_dir, small_manifest):
    ids = {e["id"] for e in small_manifest}
    for entry in small_manifest:
        for o in entry["outlinks"]:
            assert o in ids, f"{entry['id']} -> unknown {o}"
    for pid in ids:
        assert (small_dir / "pages" / (pid + ".html")).exists()


def test_admin_pages_generated(small_manifest):
    admin = [e for e in small_manifest if "/admin/" in e["url"]]
    assert len(admin) == 4  # 400 // 100
    assert all(e["id"].startswith("a") for e in admin)


# --- html structure / text quality -------------------------------------------

def test_html_structure(small_dir, small_manifest):
    sample = small_manifest[::17]  # deterministic sample across the corpus
    assert len(sample) >= 20
    for entry in sample:
        page_html = (small_dir / "pages" / (entry["id"] + ".html")).read_text()
        assert re.search(r"<title>.*?</title>", page_html, re.DOTALL)
        assert "<h1>" in page_html
        assert "<p>" in page_html
        assert re.search(r'<a\s+href="[pa]\d+"', page_html), entry["id"]


def test_no_lorem_ipsum(small_dir, small_manifest):
    for entry in small_manifest[::9]:
        page_html = (small_dir / "pages" / (entry["id"] + ".html")).read_text()
        assert "lorem" not in page_html.lower()


def test_varied_page_lengths(small_dir, small_manifest):
    sizes = [(small_dir / "pages" / (e["id"] + ".html")).stat().st_size
             for e in small_manifest if not e["id"].startswith("a")]
    assert max(sizes) > 3 * min(sizes)


def test_rare_terms_have_clear_winners(small_dir):
    pages_dir = small_dir / "pages"
    for term in ["sourdough hydration", "bebop scales"]:
        hits = [p for p in pages_dir.glob("*.html")
                if term in p.read_text(encoding="utf-8").lower()]
        assert 1 <= len(hits) <= 10, (term, len(hits))


# --- link structure ----------------------------------------------------------

def _topic_of(entry):
    return entry["url"].split("/")[3]


def test_hub_pages_exist(small_manifest):
    by_topic = {}
    for e in small_manifest:
        by_topic.setdefault(_topic_of(e), []).append(e)
    for topic in TOPICS:
        hubs = [e for e in by_topic[topic] if len(e["outlinks"]) >= 30]
        assert hubs, f"no hub page in topic {topic}"


def test_intra_topic_density(small_manifest):
    fracs = []
    for e in small_manifest[:200]:
        if e["id"].startswith("a"):
            continue
        t = _topic_of(e)
        by_id = {x["id"]: x for x in small_manifest}
        intra = sum(1 for o in e["outlinks"] if _topic_of(by_id[o]) == t)
        if e["outlinks"]:
            fracs.append(intra / len(e["outlinks"]))
    assert sum(fracs) / len(fracs) > 0.7


def test_cross_topic_links_exist(small_manifest):
    by_id = {x["id"]: x for x in small_manifest}
    cross = sum(
        1 for e in small_manifest for o in e["outlinks"]
        if _topic_of(by_id[o]) != _topic_of(e)
        and not by_id[o]["id"].startswith("a"))
    assert cross > 20


def test_content_pages_link_to_admin(small_manifest):
    by_id = {x["id"]: x for x in small_manifest}
    leaked = [e for e in small_manifest
              if not e["id"].startswith("a")
              and any(by_id[o]["id"].startswith("a") for o in e["outlinks"])]
    assert leaked, "expected some content pages to link into /admin/"
