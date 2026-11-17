"""DATA-01: the React listing page against a stub of the public Go API.

Runs the built React host (`npm run app:build`); no MongoDB or Flask is involved.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlparse
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER_BUILD = ROOT / "frontend" / "dist" / "server" / "render.js"
CLIENT_MANIFEST = ROOT / "frontend" / "dist" / "client" / ".vite" / "manifest.json"

pytestmark = pytest.mark.skipif(
    not SERVER_BUILD.exists() or not CLIENT_MANIFEST.exists() or not shutil.which("node"),
    reason="Run `npm run app:build` with Node available to test the React host.",
)

TOTAL, SIZE = 7, 3
REQUESTED: list[int] = []


def _event(index):
    return {"_id": f"{index:024x}", "title": f"Tapahtuma {index}", "date": "2099-01-01", "start_time": "12:00",
            "city": "Helsinki", "tags": [], "detail_url": f"/demonstration/{index:024x}"}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/demonstrations":
            page = int(parse_qs(url.query)["page"][0])
            REQUESTED.append(page)
            rows = [_event(n) for n in range((page - 1) * SIZE + 1, min(page * SIZE, TOTAL) + 1)]
            pages = -(-TOTAL // SIZE)
            body = {"page": page, "per_page": SIZE, "total": TOTAL, "total_pages": pages, "results": rows,
                    "result_start": (page - 1) * SIZE + 1 if rows else 0,
                    "result_end": (page - 1) * SIZE + len(rows) if rows else 0}
        elif url.path == "/session":
            body = {"authenticated": False, "locale": "fi", "locales": [{"code": "fi", "name": "Suomi"}]}
        elif url.path == "/api/public-status":
            body = {"available": True}
        else:
            body = {}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def listing_host():
    REQUESTED.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api = f"http://127.0.0.1:{server.server_address[1]}"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "APP_PORT": str(port), "PUBLIC_API_BASE": f"{api}/api", "SESSION_URL": f"{api}/session",
           "STATIC_DIR": str(ROOT / "mielenosoitukset_fi" / "static")}
    process = subprocess.Popen(["node", "frontend/server.mjs"], cwd=ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"{base}/", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("React host did not start")
        yield base
    finally:
        process.terminate()
        process.wait(timeout=10)
        server.shutdown()


def _cards(page):
    return page.eval_on_selector_all(".demo-card", "nodes => nodes.length")


def test_load_more_reaches_the_exact_total_and_hides_at_the_end(listing_host, browser_page):
    browser_page.add_init_script("Object.defineProperty(navigator,'connection',{value:{saveData:true}})")
    browser_page.goto(f"{listing_host}/demonstrations?per_page={SIZE}")
    browser_page.wait_for_selector(".demo-card")
    summary = browser_page.locator("#listing-result-summary")
    assert "1–3" in summary.inner_text() and "yhteensä 7" in summary.inner_text()
    browser_page.click("#load-more-btn")
    browser_page.wait_for_function("document.querySelectorAll('.demo-card').length === 6")
    browser_page.click("#load-more-btn")
    browser_page.wait_for_function("document.querySelectorAll('.demo-card').length === 7")
    assert "1–7" in summary.inner_text() and "yhteensä 7" in summary.inner_text()
    assert browser_page.locator("#load-more-btn").is_hidden()
    titles = browser_page.eval_on_selector_all(".demo-card-title", "nodes => nodes.map(n => n.textContent.trim())")
    assert len(titles) == len(set(titles)) == TOTAL


def test_a_page_beyond_the_last_shows_the_last_page_not_an_empty_state(listing_host, browser_page):
    browser_page.add_init_script("Object.defineProperty(navigator,'connection',{value:{saveData:true}})")
    browser_page.goto(f"{listing_host}/demonstrations?per_page={SIZE}&page=9")
    browser_page.wait_for_selector(".demo-card")
    assert _cards(browser_page) == 1
    assert browser_page.locator("#no-results").is_hidden()
    assert "7–7" in browser_page.locator("#listing-result-summary").inner_text()
    assert browser_page.locator("#load-more-btn").is_hidden()
    assert 9 in REQUESTED and REQUESTED[-1] == 3
    assert "page=3" in browser_page.url
