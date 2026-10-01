"""Shared fixtures: a hand-built 8-doc corpus used by all engine tests.

The corpus covers six topics with known text so phrase/boolean/site:
behavior can be asserted exactly. Nothing here touches the real
corpus (Builder 1); every test database lives under pytest's tmp_path.
"""

import json

import pytest

from lantern import build_index, compute_pagerank

PAGES = [
    {"id": "p0001",
     "url": "https://lantern.test/python/p0001",
     "title": "Python list comprehensions explained",
     "text": ("List comprehensions provide a concise way to create lists "
              "in Python. This Python tutorial covers list comprehension "
              "syntax with examples."),
     "outlinks": ["p0002", "p0003"]},
    {"id": "p0002",
     "url": "https://lantern.test/python/p0002",
     "title": "Django web framework guide",
     "text": ("Django is a high-level Python web framework. Learn Django "
              "models, views, and the Django ORM in this guide."),
     "outlinks": ["p0001"]},
    {"id": "p0003",
     "url": "https://lantern.test/cooking/p0003",
     "title": "Sourdough hydration levels",
     "text": ("Sourdough hydration determines crumb structure. A 75 percent "
              "hydration sourdough loaf gives an open crumb."),
     "outlinks": ["p0004"]},
    {"id": "p0004",
     "url": "https://lantern.test/cooking/p0004",
     "title": "Mise en place basics",
     "text": "Mise en place means having every ingredient prepped before cooking begins.",
     "outlinks": []},
    {"id": "p0005",
     "url": "https://lantern.test/jazz/p0005",
     "title": "Bebop scales for improvisers",
     "text": ("Bebop scales add chromatic passing tones to jazz "
              "improvisation. Practice bebop scales over ii-V-I progressions."),
     "outlinks": ["p0006"]},
    {"id": "p0006",
     "url": "https://lantern.test/space/p0006",
     "title": "Black holes and event horizons",
     "text": ("The event horizon of a black hole marks the point of no "
              "return. Astronomers image black hole shadows."),
     "outlinks": ["p0005"]},
    {"id": "p0007",
     "url": "https://lantern.test/basketball/p0007",
     "title": "The cat sat on the mat",
     "text": "The cat sat on the mat while the dog watched.",
     "outlinks": []},
    {"id": "p0008",
     "url": "https://lantern.test/basketball/p0008",
     "title": "The mat sat near the lazy cat",
     "text": "In this odd tale the mat sat quietly on the cat.",
     "outlinks": []},
]


def write_frontier(path, pages):
    with open(path, "w", encoding="utf-8") as fh:
        for p in pages:
            fh.write(json.dumps(p) + "\n")
    return str(path)


def build_db(tmp_path, pages, run_pr=True):
    """Write a frontier file, build the index, optionally run PageRank."""
    fp = write_frontier(str(tmp_path / "frontier.jsonl"), pages)
    db = str(tmp_path / "lantern.db")
    build_index(frontier=fp, db=db)
    if run_pr:
        compute_pagerank(db=db)
    return db


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Full 8-doc corpus with PageRank; LANTERN_DB pointed at it."""
    db = build_db(tmp_path, PAGES)
    monkeypatch.setenv("LANTERN_DB", db)
    return db


@pytest.fixture
def pagerank_pages():
    return [
        {"id": "p0001", "url": "https://lantern.test/t/p0001",
         "title": "a", "text": "alpha", "outlinks": ["p0002", "p0003"]},
        {"id": "p0002", "url": "https://lantern.test/t/p0002",
         "title": "b", "text": "beta", "outlinks": ["p0003"]},
        {"id": "p0003", "url": "https://lantern.test/t/p0003",
         "title": "c", "text": "gamma", "outlinks": []},
    ]
