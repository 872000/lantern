"""Integration tests for Project Lantern's experience layer (web + CLI).

The real search engine is NOT required: ``lantern.search`` is stubbed
behind the public contract (search / did_you_mean) via sys.modules.
"""

import html
import io
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lantern import cli, web  # noqa: E402

FIXTURE_RESULTS = [
    {
        "id": 1,
        "url": "https://example.com/neural-nets",
        "title": "Neural Networks 101",
        "snippet": "A gentle intro to <mark>neural</mark> <mark>networks</mark> and deep learning.",
        "score": 12.5,
    },
    {
        "id": 2,
        "url": "https://example.com/backprop",
        "title": "Backpropagation Explained",
        "snippet": "How <mark>neural</mark> nets learn via backprop.",
        "score": 9.75,
    },
]


def make_stub(dym_for=None, results=None):
    stub = types.ModuleType("lantern.search")

    def search(q, n=10):
        return list(results if results is not None else FIXTURE_RESULTS)[:n]

    def did_you_mean(q):
        if dym_for and dym_for in q:
            return "neural networks"
        return None

    stub.search = search
    stub.did_you_mean = did_you_mean
    return stub


@contextmanager
def stubbed_search(**kwargs):
    stub = make_stub(**kwargs)
    prev = sys.modules.get("lantern.search")
    sys.modules["lantern.search"] = stub
    try:
        yield stub
    finally:
        if prev is None:
            sys.modules.pop("lantern.search", None)
        else:
            sys.modules["lantern.search"] = prev


@contextmanager
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    prev = os.environ.get("LANTERN_DB")
    os.environ["LANTERN_DB"] = path
    try:
        yield path
    finally:
        if prev is None:
            os.environ.pop("LANTERN_DB", None)
        else:
            os.environ["LANTERN_DB"] = prev
        os.unlink(path)


def seed_docs(path, n=5):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE docs (id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, "
        "text TEXT, length INTEGER, pagerank REAL DEFAULT 0)"
    )
    for i in range(n):
        conn.execute(
            "INSERT INTO docs (url, title, text, length, pagerank) VALUES (?,?,?,?,?)",
            (
                f"https://example.com/page{i}",
                f"Page {i} about neural networks",
                "neural networks deep learning page content " * 5,
                100,
                0.2,
            ),
        )
    conn.commit()
    conn.close()


class TestHomepage(unittest.TestCase):
    def test_homepage_200_and_search_form(self):
        app = web.make_app()
        status, headers, body = app.handle("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("Lantern", body)
        self.assertIn('<form', body)
        self.assertIn('name="q"', body)
        self.assertIn('action="/search"', body)

    def test_homepage_shows_indexed_count(self):
        with temp_db() as path:
            seed_docs(path, n=7)
            app = web.make_app()
            status, _, body = app.handle("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn("7", body)


class TestSearchPage(unittest.TestCase):
    def test_results_contain_fixture_title_and_url(self):
        with stubbed_search():
            app = web.make_app()
            status, _, body = app.handle("GET", "/search?q=neural+networks")
            self.assertEqual(status, 200)
            self.assertIn("Neural Networks 101", body)
            self.assertIn("https://example.com/neural-nets", body)
            self.assertIn("Backpropagation Explained", body)

    def test_snippet_mark_highlights_preserved(self):
        with stubbed_search():
            app = web.make_app()
            _, _, body = app.handle("GET", "/search?q=neural")
            self.assertIn("<mark>neural</mark>", body)
            self.assertNotIn("&lt;mark&gt;", body)

    def test_result_count_and_timing_shown(self):
        with stubbed_search():
            app = web.make_app()
            _, _, body = app.handle("GET", "/search?q=neural")
            self.assertIn("2 results", body)
            self.assertIn("ms", body)

    def test_did_you_mean_shown_for_misspelling(self):
        with stubbed_search(dym_for="neurl"):
            app = web.make_app()
            _, _, body = app.handle("GET", "/search?q=neurl+netwroks")
            self.assertIn("Did you mean", body)
            self.assertIn("neural networks", body)

    def test_no_results_state(self):
        with stubbed_search(results=[]):
            app = web.make_app()
            status, _, body = app.handle("GET", "/search?q=zzzzqqqq")
            self.assertEqual(status, 200)
            self.assertIn("did not match", body)

    def test_missing_engine_degrades_gracefully(self):
        # Simulate "engine not built yet" deterministically, regardless of
        # which sibling modules exist in this checkout.
        from unittest import mock

        with mock.patch.object(web, "_engine", return_value=None):
            with temp_db():
                app = web.make_app()
                status, _, body = app.handle("GET", "/search?q=neural")
            self.assertEqual(status, 200)
            self.assertIn("isn't built yet", body)
            self.assertIn("lantern.cli build", body)

    def test_query_is_html_escaped(self):
        with stubbed_search():
            app = web.make_app()
            _, _, body = app.handle("GET", "/search?q=%3Cscript%3Ealert(1)%3C%2Fscript%3E")
            self.assertNotIn("<script>alert(1)</script>", body)
            self.assertIn(html.escape("<script>alert(1)</script>"), body)

    def test_empty_query_prompts_user(self):
        with stubbed_search():
            app = web.make_app()
            status, _, body = app.handle("GET", "/search?q=")
            self.assertEqual(status, 200)
            self.assertIn("Type a query", body)

    def test_404_for_unknown_route(self):
        app = web.make_app()
        status, _, _ = app.handle("GET", "/definitely-not-here")
        self.assertEqual(status, 404)


class TestQueryLogging(unittest.TestCase):
    def test_search_writes_to_query_log(self):
        with temp_db() as path:
            with stubbed_search():
                app = web.make_app()
                app.handle("GET", "/search?q=neural+networks")
                app.handle("GET", "/search?q=backprop")
            conn = sqlite3.connect(path)
            rows = conn.execute("SELECT query, hits FROM query_log ORDER BY query").fetchall()
            conn.close()
            self.assertEqual(rows, [("backprop", 2), ("neural networks", 2)])

    def test_log_query_creates_table_if_missing(self):
        with temp_db() as path:
            web.log_query("hello", 3, path=path)
            conn = sqlite3.connect(path)
            rows = conn.execute("SELECT query, hits FROM query_log").fetchall()
            conn.close()
            self.assertEqual(rows, [("hello", 3)])


class TestStatsPage(unittest.TestCase):
    def test_stats_shows_real_db_numbers(self):
        with temp_db() as path:
            seed_docs(path, n=12)
            web.log_query("neural networks", 2, path=path)
            web.log_query("neural networks", 2, path=path)
            web.log_query("backprop", 1, path=path)
            app = web.make_app()
            status, _, body = app.handle("GET", "/stats")
            self.assertEqual(status, 200)
            self.assertIn("12", body)  # pages indexed
            self.assertIn("0.200000", body)  # avg pagerank
            self.assertIn("neural networks", body)  # top query
            self.assertIn("Index statistics", body)


class TestCLIParsing(unittest.TestCase):
    def test_all_five_subcommands_parse(self):
        cases = [
            (["build"], "build"),
            (["build", "--seed", "3", "--docs", "100"], "build"),
            (["search", "neural networks"], "search"),
            (["search", "x", "--n", "5"], "search"),
            (["serve"], "serve"),
            (["serve", "--port", "9000"], "serve"),
            (["stats"], "stats"),
            (["eval"], "eval"),
        ]
        for argv, cmd in cases:
            with self.subTest(argv=argv):
                args = cli.build_parser().parse_args(argv)
                self.assertEqual(args.command, cmd)
                self.assertTrue(callable(args.func))

    def test_serve_port_default_and_override(self):
        self.assertEqual(cli.build_parser().parse_args(["serve"]).port, 8000)
        self.assertEqual(cli.build_parser().parse_args(["serve", "--port", "9000"]).port, 9000)

    def test_search_n_default(self):
        self.assertEqual(cli.build_parser().parse_args(["search", "q"]).n, 10)


class TestCLISearch(unittest.TestCase):
    def test_search_prints_fixture_results(self):
        with stubbed_search():
            args = cli.build_parser().parse_args(["search", "neural networks"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_search(args)
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("Neural Networks 101", out)
            self.assertIn("https://example.com/neural-nets", out)
            self.assertIn("2 result(s)", out)

    def test_search_without_engine_exits_zero_with_message(self):
        from unittest import mock

        with mock.patch.object(cli, "_search_engine", return_value=None):
            args = cli.build_parser().parse_args(["search", "neural"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_search(args)
            self.assertEqual(rc, 0)
            self.assertIn("isn't built yet", buf.getvalue())

    def test_build_degrades_without_sibling_modules(self):
        from unittest import mock

        with mock.patch.object(cli, "_import_or_none", return_value=None):
            args = cli.build_parser().parse_args(["build"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_build(args)
            self.assertEqual(rc, 0)
            self.assertIn("SKIP", buf.getvalue())

    def test_eval_degrades_without_module(self):
        from unittest import mock

        with mock.patch.object(cli, "_import_or_none", return_value=None):
            args = cli.build_parser().parse_args(["eval"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_eval(args)
            self.assertEqual(rc, 0)
            self.assertIn("isn't available", buf.getvalue())


class TestAppTestability(unittest.TestCase):
    def test_make_app_needs_no_port(self):
        app = web.make_app()
        self.assertIsInstance(app, web.LanternApp)
        # handle() is pure: no socket, no binding
        status, _, body = app.handle("GET", "/")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith("<!DOCTYPE html>"))


if __name__ == "__main__":
    unittest.main()
