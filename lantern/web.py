"""Project Lantern — web frontend (stdlib only, zero dependencies).

A small Google-esque search UI on top of the Builder-2 search engine,
served with :mod:`http.server`. The engine is an *optional* dependency:
every access to ``lantern.search`` goes through a guarded lookup, so
``/`` and the help output keep working even when the engine is not built
yet.

Public surface:
    run(port=8000)          — start the development server
    make_app() -> LanternApp — framework-free request handler logic
    LanternHandler          — :class:`http.server` adapter for LanternApp
    log_query(query, hits)  — append one row to ``query_log``
"""

from __future__ import annotations

import html
import os
import re
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

__all__ = [
    "LanternApp",
    "LanternHandler",
    "make_app",
    "run",
    "log_query",
    "db_path",
    "search_available",
]

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(PACKAGE_DIR)


def db_path() -> str:
    """Location of the index database.

    ``LANTERN_DB`` env var wins (tests use this); otherwise
    ``lantern.db`` at the repo root.
    """
    return os.environ.get("LANTERN_DB") or os.path.join(REPO_DIR, "lantern.db")


def _engine():
    """Return the ``lantern.search`` module if importable, else None.

    Looked up lazily (via :data:`sys.modules` first) so the web tier keeps
    working — and so tests can inject a stub — without the real engine.
    """
    mod = sys.modules.get("lantern.search")
    if mod is not None:
        return mod
    try:
        import importlib

        mod = importlib.import_module("lantern.search")
    except ImportError:
        return None
    return mod


def search_available() -> bool:
    return _engine() is not None


def _dym_fn():
    """Locate ``did_you_mean``.

    The contract places it in ``lantern.search``; the engine as built
    exposes it at the package top level (``lantern.query``). Prefer the
    former, fall back to the latter.
    """
    mod = _engine()
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


def do_search(query: str, n: int = 10):
    """Run the engine search; returns (results, suggestion) or (None, None)."""
    mod = _engine()
    if mod is None:
        return None, None
    try:
        results = mod.search(query, n=n)
    except Exception:
        results = []
    suggestion = None
    dym = _dym_fn()
    if dym is not None:
        try:
            suggestion = dym(query)
        except Exception:
            suggestion = None
    return results, suggestion


def log_query(query: str, hits: int, path: str | None = None) -> None:
    """Log EVERY search query to ``query_log`` (creates the table if needed)."""
    path = path or db_path()
    try:
        conn = sqlite3.connect(path)
    except sqlite3.Error:
        return
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS query_log (ts TEXT, query TEXT, hits INTEGER)"
        )
        conn.execute(
            "INSERT INTO query_log VALUES (datetime('now'), ?, ?)", (query, hits)
        )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

TOKEN_RE = re.compile(r"[a-z0-9]+")


def _render_snippet(raw: str) -> str:
    """Escape a snippet but keep engine-provided <mark> highlights intact."""
    esc = html.escape(raw or "", quote=False)
    esc = esc.replace("&lt;mark&gt;", "<mark>").replace("&lt;/mark&gt;", "</mark>")
    return esc


CSS = """\
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Arial,Helvetica,sans-serif;color:#202124;background:#fff;min-height:100vh;display:flex;flex-direction:column}
a{color:#1a0dab;text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:700px;margin:0 auto;width:100%;padding:0 20px}
header.top{display:flex;align-items:center;gap:18px;padding:14px 28px;border-bottom:1px solid #e8eaed}
.brand{font-size:22px;font-weight:700;letter-spacing:-.5px;color:#1a73e8}
.brand span{color:#ea4335}
.brand em{font-style:normal;color:#fbbc05}
.top form{display:flex;flex:1;max-width:560px}
.top input[type=text]{flex:1;height:40px;border:1px solid #dfe1e5;border-radius:24px 0 0 24px;padding:0 18px;font-size:15px;outline:none}
.top input[type=text]:focus{box-shadow:0 1px 6px rgba(32,33,36,.18)}
.top button{height:40px;border:1px solid #1a73e8;background:#1a73e8;color:#fff;border-radius:0 24px 24px 0;padding:0 22px;font-size:15px;cursor:pointer}
.hero{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:60px 20px 40px}
.wordmark{font-size:88px;font-weight:700;letter-spacing:-4px;margin-bottom:34px;user-select:none}
.wordmark .b{color:#1a73e8}.wordmark .r{color:#ea4335}.wordmark .y{color:#fbbc05}.wordmark .g{color:#34a853}
.searchbox{display:flex;width:min(584px,92vw);height:52px;border:1px solid #dfe1e5;border-radius:28px;overflow:hidden;box-shadow:0 1px 6px rgba(32,33,36,.10)}
.searchbox:focus-within{box-shadow:0 1px 8px rgba(32,33,36,.22)}
.searchbox input{flex:1;border:0;outline:none;font-size:17px;padding:0 22px}
.searchbox button{border:0;background:#f8f9fa;padding:0 30px;font-size:16px;color:#3c4043;cursor:pointer;border-left:1px solid #eef0f2}
.searchbox button:hover{background:#e8f0fe;color:#1a73e8}
.hint{margin-top:22px;color:#5f6368;font-size:14px}
.stats-line{margin-top:14px;color:#9aa0a6;font-size:13px}
main.results{flex:1;padding:18px 28px;max-width:760px}
.meta{color:#70757a;font-size:13px;margin:6px 0 22px}
.dym{margin:4px 0 20px;font-size:16px;color:#202124}
.dym a{font-style:italic;font-weight:700}
.result{margin-bottom:30px;max-width:640px}
.result .url{color:#006621;font-size:14px;margin-bottom:2px;word-break:break-all}
.result h3{font-size:20px;font-weight:400;margin:2px 0 4px;line-height:1.35}
.result p{color:#4d5156;font-size:14px;line-height:1.6}
.result mark{background:#fff3b0;color:inherit;padding:0 1px;border-radius:2px}
.empty{padding:40px 0;color:#5f6368;font-size:16px;line-height:1.7}
.empty b{color:#202124}
table.stats{border-collapse:collapse;margin:24px 0;width:100%;max-width:640px;font-size:15px}
table.stats td,table.stats th{border:1px solid #dfe1e5;padding:10px 14px;text-align:left}
table.stats th{background:#f8f9fa;width:240px;font-weight:600}
table.stats td.num{font-variant-numeric:tabular-nums}
.note{color:#9aa0a6;font-size:12.5px;margin-top:8px;line-height:1.6}
footer{border-top:1px solid #e8eaed;background:#f2f2f2;color:#70757a;font-size:13px;text-align:center;padding:14px}
footer .tag{font-style:italic}
.missing{background:#fef7e0;border:1px solid #fde293;border-radius:8px;padding:26px;max-width:640px;margin:30px 0;color:#5f6368;line-height:1.7}
.missing code{background:#fff;border:1px solid #e8eaed;border-radius:4px;padding:1px 6px;font-size:13px}
"""

WORDMARK = (
    '<div class="wordmark" aria-label="Lantern">'
    '<span class="b">L</span><span class="r">a</span><span class="y">n</span>'
    '<span class="b">t</span><span class="g">e</span><span class="r">r</span><span class="y">n</span>'
    "</div>"
)

HEADER = """\
<header class="top">
  <a class="brand" href="/">Lan<span>ter</span><em>n</em></a>
  <form action="/search" method="get" role="search">
    <input type="text" name="q" value="{q}" aria-label="Search" autocomplete="off"/>
    <button type="submit">Search</button>
  </form>
</header>
"""

FOOTER = """\
<footer>
  <div class="tag">Project Lantern — lighting up the web, one crawl at a time.</div>
  <div style="margin-top:6px">Built from scratch: crawler · inverted index · BM25 · PageRank</div>
</footer>
"""


def _page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'/>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'/>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
        f"<body>{body}{FOOTER}</body></html>"
    )


def homepage(indexed: int | None = None) -> str:
    stats_line = ""
    if indexed is not None:
        stats_line = (
            f'<div class="stats-line">Indexing {indexed:,} pages · '
            "BM25 ranking · PageRank · spellcheck</div>"
        )
    body = (
        f'<div class="hero">{WORDMARK}'
        '<form class="searchbox" action="/search" method="get" role="search">'
        '<input type="text" name="q" aria-label="Search Lantern" autocomplete="off" '
        'placeholder="Search the Lantern index…"/>'
        '<button type="submit">Lantern Search</button>'
        "</form>"
        '<div class="hint">Try: <b>black hole</b> · <b>"sourdough hydration"</b> · '
        "<b>site:cooking</b> · <b>python -django</b></div>"
        f"{stats_line}</div>"
    )
    return _page("Lantern", body)


def missing_engine_page(query: str) -> str:
    q = html.escape(query)
    body = (
        HEADER.format(q=q)
        + '<main class="results"><div class="missing">'
        "<h2 style='color:#202124;margin-bottom:10px'>The index isn't built yet</h2>"
        f"<p>No results for <b>{q}</b> — the search engine hasn't been built "
        "in this checkout.</p>"
        "<p style='margin-top:10px'>Build it with:</p>"
        "<p style='margin-top:8px'><code>python3 -m lantern.cli build</code></p>"
        "</div></main>"
    )
    return _page(f"{query} - Lantern Search", body)


def results_page(query: str, results: list, suggestion: str | None, elapsed_ms: float) -> str:
    q = html.escape(query)
    count = len(results)
    meta = (
        f"About {count} result{'s' if count != 1 else ''} "
        f"({elapsed_ms:.1f} ms)"
    )
    if suggestion:
        sug = html.escape(suggestion)
        dym = (
            f'<div class="dym">Did you mean: <a href="/search?q={html.escape(suggestion, quote=True)}">'
            f"{sug}</a>?</div>"
        )
    else:
        dym = ""
    if results:
        items = []
        for r in results:
            url = html.escape(str(r.get("url", "#")), quote=True)
            title = html.escape(str(r.get("title", "(no title)")))
            disp_url = html.escape(str(r.get("url", "")))
            snippet = _render_snippet(str(r.get("snippet", "")))
            items.append(
                f'<div class="result"><div class="url">{disp_url}</div>'
                f'<h3><a href="{url}">{title}</a></h3><p>{snippet}</p></div>'
            )
        body_results = "\n".join(items)
    else:
        body_results = (
            '<div class="empty">'
            f"<p>Your search — <b>{q}</b> — did not match any documents.</p>"
            "<p style='margin-top:10px'>Suggestions:</p>"
            "<ul style='margin:8px 0 0 20px;line-height:1.9'>"
            "<li>Check your spelling</li>"
            "<li>Try fewer or different keywords</li>"
            "<li>Remove quotes or exclusions like <b>-term</b></li>"
            "</ul></div>"
        )
    body = (
        HEADER.format(q=q)
        + f'<main class="results"><div class="meta">{meta}</div>{dym}{body_results}</main>'
    )
    return _page(f"{query} - Lantern Search", body)


def _stats_from_db():
    """Return (pages, vocab, vocab_note, avg_pr, disk_bytes, top_queries)."""
    path = db_path()
    pages = vocab = avg_pr = disk = None
    top = []
    vocab_note = ""
    if os.path.exists(path):
        try:
            disk = os.path.getsize(path)
        except OSError:
            pass
        try:
            conn = sqlite3.connect(path)
            try:
                pages = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
            except sqlite3.Error:
                pass
            try:
                row = conn.execute("SELECT AVG(pagerank) FROM docs").fetchone()
                avg_pr = row[0] if row else None
            except sqlite3.Error:
                pass
            try:
                rows = conn.execute(
                    "SELECT query, COUNT(*) AS c FROM query_log "
                    "GROUP BY query ORDER BY c DESC LIMIT 10"
                ).fetchall()
                top = [(r[0], r[1]) for r in rows]
            except sqlite3.Error:
                pass
            # Vocabulary: prefer an engine-exposed count, else approximate
            # from distinct tokens in docs.text.
            vocab, vocab_note = _vocab_size(conn)
            conn.close()
        except sqlite3.Error:
            pass
    return pages, vocab, vocab_note, avg_pr, disk, top


def _vocab_size(conn):
    """(size, note). Real engine count when exposed, else a documented estimate."""
    mod = _engine()
    for attr in ("vocab_size", "vocabulary_size", "num_terms"):
        fn = getattr(mod, attr, None) if mod else None
        if callable(fn):
            try:
                return int(fn()), f"reported by lantern.search.{attr}()"
            except Exception:
                pass
    # The engine stores its real vocabulary in the `terms` table.
    try:
        n = conn.execute("SELECT COUNT(*) FROM terms").fetchone()[0]
        if n:
            return int(n), "exact: COUNT(*) FROM terms (engine's vocabulary table)"
    except sqlite3.Error:
        pass
    try:
        cur = conn.execute("SELECT text FROM docs")
        terms = set()
        for (text,) in cur:
            if text:
                terms.update(TOKEN_RE.findall(text.lower()))
        return len(terms), "approximate: distinct [a-z0-9]+ tokens across docs.text"
    except sqlite3.Error:
        return None, ""


def stats_page() -> str:
    pages, vocab, vocab_note, avg_pr, disk, top = _stats_from_db()

    def fmt(v, suffix=""):
        return f"{v:,}{suffix}" if isinstance(v, int) else ("—" if v is None else f"{v}{suffix}")

    rows = [
        ("Pages indexed", fmt(pages)),
        ("Vocabulary size", fmt(vocab) if vocab is None else f"{vocab:,}"),
        ("Average PageRank", "—" if avg_pr is None else f"{avg_pr:.6f}"),
        ("Index size on disk", "—" if disk is None else f"{disk / 1024 / 1024:.2f} MB"),
    ]
    tr = "".join(
        f"<tr><th>{html.escape(k)}</th><td class='num'>{html.escape(v)}</td></tr>"
        for k, v in rows
    )
    if top:
        qrows = "".join(
            f"<tr><td>{html.escape(q)}</td><td class='num'>{c:,}</td></tr>" for q, c in top
        )
        top_html = (
            "<h2 style='margin:28px 0 6px;font-size:18px'>Top queries</h2>"
            f"<table class='stats'>{qrows}</table>"
        )
    else:
        top_html = (
            "<h2 style='margin:28px 0 6px;font-size:18px'>Top queries</h2>"
            "<p class='note'>No queries logged yet — run a search first.</p>"
        )
    note = (
        f"<p class='note'>Vocabulary size: {html.escape(vocab_note or 'unavailable')}.</p>"
        if vocab_note or vocab is None
        else ""
    )
    body = (
        HEADER.format(q="")
        + "<main class='results'><h1 style='font-size:24px;margin:10px 0 4px'>Index statistics</h1>"
        f"<table class='stats'>{tr}</table>{note}{top_html}</main>"
    )
    return _page("Index statistics - Lantern", body)


# --------------------------------------------------------------------------
# App: pure request handling (no sockets — trivially testable)
# --------------------------------------------------------------------------

class LanternApp:
    """Framework-free request logic.

    ``handle(method, path)`` returns ``(status, headers, body)`` where body
    is a ``str`` of HTML. Used directly by tests and by LanternHandler.
    """

    def handle(self, method: str, path: str):
        parsed = urlparse(path)
        route = parsed.path.rstrip("/") or "/"
        if route == "/":
            return 200, {"Content-Type": "text/html; charset=utf-8"}, homepage(
                self._indexed_count()
            )
        if route == "/search":
            qs = parse_qs(parsed.query)
            q = (qs.get("q") or [""])[0].strip()
            n = 10
            try:
                n = max(1, min(50, int((qs.get("n") or ["10"])[0])))
            except ValueError:
                pass
            return self._handle_search(q, n)
        if route == "/stats":
            return 200, {"Content-Type": "text/html; charset=utf-8"}, stats_page()
        body = _page("Not found - Lantern", HEADER.format(q="") +
                     "<main class='results'><div class='empty'><b>404</b> — nothing here.</div></main>")
        return 404, {"Content-Type": "text/html; charset=utf-8"}, body

    def _indexed_count(self):
        pages, *_ = _stats_from_db()
        return pages

    def _handle_search(self, query: str, n: int):
        headers = {"Content-Type": "text/html; charset=utf-8"}
        if not query:
            return (
                200,
                headers,
                _page("Lantern Search", HEADER.format(q="") +
                      "<main class='results'><div class='empty'>Type a query above to search the index.</div></main>"),
            )
        t0 = time.perf_counter()
        results, suggestion = do_search(query, n)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if results is None:  # engine missing
            log_query(query, 0)
            return 200, headers, missing_engine_page(query)
        log_query(query, len(results))
        return 200, headers, results_page(query, results, suggestion, elapsed_ms)


def make_app() -> LanternApp:
    return LanternApp()


# --------------------------------------------------------------------------
# http.server adapter
# --------------------------------------------------------------------------

class LanternHandler(BaseHTTPRequestHandler):
    app = None  # set by run(); tests can inject

    def _get_app(self):
        return self.app or make_app()

    def do_GET(self):
        app = self._get_app()
        status, headers, body = app.handle("GET", self.path)
        data = body.encode("utf-8")
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # quieter dev logs
        sys.stderr.write("lantern: " + fmt % args + "\n")


def run(port: int = 8000) -> None:
    """Start the Lantern web server (blocking)."""
    LanternHandler.app = make_app()
    server = ThreadingHTTPServer(("127.0.0.1", port), LanternHandler)
    print(f"Lantern search UI at http://127.0.0.1:{port}/  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Lantern web UI")
    p.add_argument("--port", type=int, default=8000)
    run(p.parse_args().port)
