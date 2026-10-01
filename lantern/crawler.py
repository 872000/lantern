#!/usr/bin/env python3
"""Breadth-first crawler for the Lantern synthetic corpus (Builder 1).

Reads ``<corpus_dir>/manifest.json`` + ``<corpus_dir>/pages/*.html`` and
writes one JSON object per fetched page to ``crawl/frontier.jsonl``::

    {"id": ..., "url": ..., "title": ..., "text": ..., "outlinks": [...]}

Robots-style rules (defined in code, see ``DEFAULT_ROBOTS``)
-----------------------------------------------------------
Any URL whose path starts with a prefix in ``disallow_prefixes`` is never
fetched. Disallowed targets are *also* dropped from the recorded ``outlinks``
of fetched pages, so ``frontier.jsonl`` is a closed graph: every outlink id
in it resolves to another fetched line. The generator creates ``/admin/``
pages (and some content pages link to them) precisely so this filtering is
exercised.

Politeness
----------
Each BFS round fetches at most ``max_per_topic_per_round`` pages per topic;
pages over the budget are deferred to the next round. The politeness delay is
simulated as a counter (``politeness_ticks`` in the returned stats); pass
``politeness_delay > 0`` for a real (tiny) sleep per fetch.
"""

import html as htmlmod
import json
import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

BASE_URL = "https://lantern.test"

# Robots-style disallow list. Paths are matched as prefixes against the URL
# path (e.g. "/admin/" blocks "https://lantern.test/admin/a0001").
DEFAULT_ROBOTS = {
    "disallow_prefixes": ["/admin/"],
}

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>",
                       re.IGNORECASE | re.DOTALL)
_A_RE = re.compile(r"<a\b[^>]*?\bhref\s*=\s*[\"']([^\"']+)[\"']",
                   re.IGNORECASE)
_ID_RE = re.compile(r"^([a-zA-Z])(\d+)$")
_WS_RE = re.compile(r"\s+")


def is_allowed(url, robots=None):
    """True unless the URL path starts with a disallowed prefix."""
    robots = robots or DEFAULT_ROBOTS
    path = urlsplit(url).path or "/"
    return not any(path.startswith(p)
                   for p in robots.get("disallow_prefixes", []))


def topic_of(url):
    """Topic segment of a lantern.test URL, e.g. 'python'."""
    parts = urlsplit(url).path.strip("/").split("/")
    return parts[0] if parts else ""


def resolve_href(href):
    """Resolve an href to a corpus page id, or None if unresolvable.

    Handles bare ids (``p0007``, ``a0001``) and absolute lantern.test URLs;
    anything else (external links, fragments, '#') is ignored.
    """
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    m = _ID_RE.match(href)
    if m:
        return "%s%04d" % (m.group(1).lower(), int(m.group(2)))
    if href.startswith(BASE_URL):
        tail = urlsplit(href).path.strip("/").split("/")[-1]
        m = _ID_RE.match(tail)
        if m:
            return "%s%04d" % (m.group(1).lower(), int(m.group(2)))
    return None


def extract_title(page_html):
    """Page <title>, tags stripped and entities unescaped."""
    m = _TITLE_RE.search(page_html or "")
    if not m:
        return ""
    return _WS_RE.sub(" ", htmlmod.unescape(_TAG_RE.sub("", m.group(1)))).strip()


def extract_text(page_html):
    """Visible text: scripts/styles/comments/tags removed, whitespace folded."""
    text = _COMMENT_RE.sub(" ", page_html or "")
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = htmlmod.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def extract_outlinks(page_html):
    """Ordered, deduplicated list of corpus page ids linked from the HTML."""
    ids = []
    seen = set()
    for m in _A_RE.finditer(page_html or ""):
        pid = resolve_href(m.group(1))
        if pid and pid not in seen:
            seen.add(pid)
            ids.append(pid)
    return ids


def default_seeds(manifest):
    """One seed per content topic: the first page id of each topic.

    The pseudo-topic ``admin`` is excluded (its URLs are robots-disallowed).
    """
    first = {}
    for entry in manifest:
        topic = topic_of(entry["url"])
        if topic and topic != "admin" and topic not in first:
            first[topic] = entry["id"]
    return [first[t] for t in sorted(first)]


def crawl(corpus_dir="corpus", out="crawl/frontier.jsonl", seeds=None,
          max_pages=None, max_per_topic_per_round=40, politeness_delay=0.0,
          robots=None):
    """Crawl the corpus with BFS + politeness budget + robots filtering.

    Args:
        corpus_dir: directory holding ``manifest.json`` and ``pages/``.
        out: path of the JSONL frontier file to write.
        seeds: page ids to start from; defaults to one hub per topic.
        max_pages: stop after fetching this many pages (None = no limit).
        max_per_topic_per_round: politeness budget; at most this many pages
            are fetched per topic in each BFS round, the rest deferred.
        politeness_delay: real seconds to sleep per fetch (0 = simulated
            only; the simulated delay is always counted in stats).
        robots: robots-style rules dict (default ``DEFAULT_ROBOTS``).

    Returns:
        dict of crawl stats: fetched, rounds, skipped_robots,
        skipped_missing, politeness_ticks, per_round_max, seeds.
    """
    robots = robots or DEFAULT_ROBOTS
    corpus_dir = Path(corpus_dir)
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    by_id = {e["id"]: e for e in manifest}

    if seeds is None:
        seeds = default_seeds(manifest)

    stats = {"fetched": 0, "rounds": 0, "skipped_robots": 0,
             "skipped_missing": 0, "politeness_ticks": 0,
             "per_round_max": 0, "seeds": list(seeds)}

    frontier = deque()
    seen = set()

    def enqueue(pid):
        if pid in seen or pid not in by_id:
            return
        seen.add(pid)
        frontier.append(pid)

    for s in seeds:
        if s in by_id and not is_allowed(by_id[s]["url"], robots):
            stats["skipped_robots"] += 1
            seen.add(s)
        else:
            enqueue(s)

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def limit_reached():
        return max_pages is not None and stats["fetched"] >= max_pages

    with out_path.open("w", encoding="utf-8") as fh:
        while frontier and not limit_reached():
            stats["rounds"] += 1
            # Drain the current frontier into per-topic buckets.
            buckets = {}
            pending = list(frontier)
            frontier.clear()
            for pid in pending:
                buckets.setdefault(topic_of(by_id[pid]["url"]), []).append(pid)

            deferred, children = [], []
            round_max = 0
            for topic in sorted(buckets):
                ids = buckets[topic]
                take, leave = ids[:max_per_topic_per_round], ids[max_per_topic_per_round:]
                deferred.extend(leave)
                round_max = max(round_max, len(take))
                for pid in take:
                    if limit_reached():
                        break
                    page_file = corpus_dir / "pages" / (pid + ".html")
                    if not page_file.exists():
                        stats["skipped_missing"] += 1
                        continue
                    page_html = page_file.read_text(encoding="utf-8")
                    raw_links = extract_outlinks(page_html)
                    kept = []
                    for tid in raw_links:
                        target = by_id.get(tid)
                        if target is None:
                            continue
                        if not is_allowed(target["url"], robots):
                            stats["skipped_robots"] += 1
                            continue
                        kept.append(tid)
                    record = {
                        "id": pid,
                        "url": by_id[pid]["url"],
                        "title": extract_title(page_html),
                        "text": extract_text(page_html),
                        "outlinks": kept,
                    }
                    fh.write(json.dumps(record, sort_keys=True,
                                        ensure_ascii=True) + "\n")
                    stats["fetched"] += 1
                    stats["politeness_ticks"] += 1
                    if politeness_delay > 0:
                        time.sleep(politeness_delay)
                    for tid in kept:
                        if tid not in seen:
                            seen.add(tid)
                            children.append(tid)
                if limit_reached():
                    break
            stats["per_round_max"] = max(stats["per_round_max"], round_max)
            # Deferred (shallower) pages go before newly discovered children,
            # preserving non-decreasing BFS depth in fetch order.
            frontier = deque(deferred + children)

    stats["out"] = str(out_path)
    return stats


if __name__ == "__main__":
    import sys
    corpus = sys.argv[1] if len(sys.argv) > 1 else "corpus"
    dest = sys.argv[2] if len(sys.argv) > 2 else "crawl/frontier.jsonl"
    stats = crawl(corpus_dir=corpus, out=dest)
    print("fetched=%d rounds=%d skipped_robots=%d ticks=%d -> %s" % (
        stats["fetched"], stats["rounds"], stats["skipped_robots"],
        stats["politeness_ticks"], stats["out"]))
