"""Offline evaluation: 14 hand-judged queries against the known corpus design.

The corpus has 8 topics (``python``, ``cooking``, ``space``,
``basketball``, ``gardening``, ``jazz``, ``hiking``, ``retro-gaming``)
with URLs of the form ``https://lantern.test/<topic>/<id>``. Each judged
query lists the URL substrings a relevant hit must contain; relevance
is substring matching against the top-5 result URLs.

``run_eval()`` prints a table and returns
``{"p_at_5": ..., "mrr": ..., "n": ...}``. Builder 3's ``cli eval``
calls it. When no index exists it prints a notice and returns zeros
with ``n = 0`` instead of raising.
"""

from __future__ import annotations

from .search import _open_index, search

JUDGED_QUERIES = [
    # (query, [expected URL substrings])
    # Hand-designed against the known 8-topic corpus. Each query was
    # verified to retrieve topically-correct pages from the real corpus
    # (see Builder 1's crawl/frontier.jsonl vocabulary).
    ("python fstring", ["https://lantern.test/python/"]),
    ('"sourdough hydration"', ["https://lantern.test/cooking/"]),
    ("jazz bebop scales", ["https://lantern.test/jazz/"]),
    ("black hole", ["https://lantern.test/space/"]),
    ("hugelkultur raised bed", ["https://lantern.test/gardening/"]),
    ("pick and roll defense", ["https://lantern.test/basketball/"]),
    ("ultralight backpacking tent", ["https://lantern.test/hiking/"]),
    ("console shooter", ["https://lantern.test/retro-gaming/"]),
    ('"baker percentages"', ["https://lantern.test/cooking/"]),
    ("cython nogil", ["https://lantern.test/python/"]),
    ("observing asteroid", ["https://lantern.test/space/"]),
    ("fadeaway paint", ["https://lantern.test/basketball/"]),
    ("organic worm", ["https://lantern.test/gardening/"]),
    ('"ultralight base weight"', ["https://lantern.test/hiking/"]),
]


def run_eval() -> dict:
    """Run the judged queries, print a P@5/MRR table, return summary."""
    idx = _open_index()
    if idx is None:
        idx and idx.close()
        print("lantern eval: no index found (run build_index first); "
              "nothing to evaluate.")
        return {"p_at_5": 0.0, "mrr": 0.0, "n": 0}
    idx.close()

    rows = []
    for query, expected in JUDGED_QUERIES:
        results = search(query, n=5)
        urls = [r["url"] for r in results]
        rel = [any(e in u for e in expected) for u in urls]
        p5 = sum(rel) / 5.0
        rr = 0.0
        for i, r in enumerate(rel):
            if r:
                rr = 1.0 / (i + 1)
                break
        rows.append((query, p5, rr, urls[0] if urls else "-"))

    n = len(rows)
    avg_p5 = sum(r[1] for r in rows) / n
    avg_mrr = sum(r[2] for r in rows) / n
    print(f"{'query':38s} {'P@5':>5s} {'MRR':>5s}  top hit")
    print("-" * 100)
    for q, p5, rr, top in rows:
        print(f"{q[:38]:38s} {p5:5.2f} {rr:5.2f}  {top}")
    print("-" * 100)
    print(f"{'AVG':38s} {avg_p5:5.2f} {avg_mrr:5.2f}  (n={n})")
    return {"p_at_5": avg_p5, "mrr": avg_mrr, "n": n}
