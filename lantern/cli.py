"""Project Lantern — command-line interface.

Subcommands:
    build                  corpus_gen -> crawler -> index -> pagerank
    search "query" [--n]   run a query against the built index
    serve [--port 8000]    start the web UI
    stats                  print index statistics
    eval                   run the retrieval evaluation

Every pipeline stage is wrapped in a thin guarded helper so the CLI
degrades gracefully if a sibling builder module isn't present yet.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sqlite3
import sys
import time

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(PACKAGE_DIR)


def _import_or_none(module: str):
    try:
        return importlib.import_module(module)
    except ImportError:
        return None


def _stage(module_name: str, fn_name: str, *args, **kwargs):
    """Call ``fn_name`` from ``module_name``; return (ok, detail).

    (False, 'missing module ...') when the sibling builder hasn't landed
    its module yet — the rest of the pipeline keeps going.
    """
    mod = _import_or_none(module_name)
    if mod is None:
        return False, f"module {module_name!r} not present yet — skipping"
    fn = getattr(mod, fn_name, None)
    if not callable(fn):
        return False, f"{module_name}.{fn_name}() not found — skipping"
    fn(*args, **kwargs)
    return True, "ok"


def cmd_build(args) -> int:
    print("Lantern build pipeline")
    print("=" * 60)
    stages = [
        ("lantern.corpus_gen", "generate", (), {"seed": args.seed, "n": args.docs}),
        ("lantern.crawler", "crawl", (), {}),
        ("lantern.index", "build_index", (), {}),
        ("lantern.pagerank", "compute_pagerank", (), {}),
    ]
    all_ok = True
    for module, fn, fargs, fkwargs in stages:
        label = f"{module}.{fn}"
        print(f"[build] {label} ...", flush=True)
        t0 = time.perf_counter()
        try:
            ok, detail = _stage(module, fn, *fargs, **fkwargs)
        except Exception as exc:  # a stage blew up — report, don't crash
            ok, detail = False, f"raised {type(exc).__name__}: {exc}"
        dt = time.perf_counter() - t0
        mark = "OK " if ok else "SKIP"
        print(f"[build]   {mark} ({dt:.1f}s) {detail}")
        all_ok = all_ok and ok
    print("=" * 60)
    if all_ok:
        print("Build complete.")
    else:
        print("Build finished with skipped/failed stages (see above).")
        print("Sibling builders are still landing their modules — re-run to fill gaps.")
    return 0


def _search_engine():
    return _import_or_none("lantern.search")


def _dym_fn():
    """Locate ``did_you_mean``: prefer ``lantern.search``, fall back to the
    package top level (where the engine actually exposes it)."""
    mod = _search_engine()
    if mod is not None:
        fn = getattr(mod, "did_you_mean", None)
        if callable(fn):
            return fn
    try:
        import lantern as pkg
    except ImportError:
        return None
    fn = getattr(pkg, "did_you_mean", None)
    return fn if callable(fn) else None


def cmd_search(args) -> int:
    mod = _search_engine()
    if mod is None:
        print("The search engine isn't built yet in this checkout.")
        print("Run:  python3 -m lantern.cli build")
        return 0
    t0 = time.perf_counter()
    results = mod.search(args.query, n=args.n)
    dt = (time.perf_counter() - t0) * 1000
    print(f"Query: {args.query} — {len(results)} result(s) in {dt:.1f} ms\n")
    for i, r in enumerate(results, 1):
        snippet = str(r.get("snippet", "")).replace("<mark>", "**").replace("</mark>", "**")
        print(f"{i}. {r.get('title', '(no title)')}")
        print(f"   {r.get('url', '')}")
        print(f"   {snippet[:220]}")
        print(f"   score={r.get('score', 0):.4f}\n")
    dym = _dym_fn()
    sug = None
    if dym is not None:
        try:
            sug = dym(args.query)
        except Exception:
            sug = None
    if sug:
        print(f"Did you mean: {sug}?")
    return 0


def cmd_serve(args) -> int:
    from lantern import web

    web.run(port=args.port)
    return 0


def cmd_stats(args) -> int:
    from lantern import web

    path = web.db_path()
    print(f"Index database: {path}")
    if not os.path.exists(path):
        print("No index built yet. Run:  python3 -m lantern.cli build")
        return 0
    pages, vocab, vocab_note, avg_pr, disk, top = web._stats_from_db()
    print(f"Pages indexed : {pages if pages is not None else '—'}")
    print(f"Vocabulary    : {vocab if vocab is not None else '—'} ({vocab_note or 'n/a'})")
    print(f"Avg PageRank  : {avg_pr:.6f}" if avg_pr is not None else "Avg PageRank  : —")
    print(f"Disk size     : {disk / 1024 / 1024:.2f} MB" if disk else "Disk size     : —")
    print("Top queries   :")
    if top:
        for q, c in top:
            print(f"  {c:>6}  {q}")
    else:
        print("  (none logged yet)")
    return 0


def cmd_eval(args) -> int:
    mod = _import_or_none("lantern.eval")
    if mod is None or not callable(getattr(mod, "run_eval", None)):
        print("Evaluation module (lantern.eval.run_eval) isn't present yet.")
        print("Sibling builders are still landing their modules.")
        return 0
    mod.run_eval()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lantern.cli", description="Project Lantern — a from-scratch search engine"
    )
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="build the index end-to-end")
    b.add_argument("--seed", type=int, default=7, help="corpus generator seed")
    b.add_argument("--docs", type=int, default=2500, help="number of synthetic docs")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("search", help="search the built index")
    s.add_argument("query", help="query string (supports \"phrases\", site:, -exclusion)")
    s.add_argument("--n", type=int, default=10, help="max results")
    s.set_defaults(func=cmd_search)

    v = sub.add_parser("serve", help="start the web UI")
    v.add_argument("--port", type=int, default=8000)
    v.set_defaults(func=cmd_serve)

    t = sub.add_parser("stats", help="print index statistics")
    t.set_defaults(func=cmd_stats)

    e = sub.add_parser("eval", help="run the retrieval evaluation")
    e.set_defaults(func=cmd_eval)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
