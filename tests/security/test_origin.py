# -*- coding: utf-8 -*-
"""tests/security/test_origin.py — Origin / Referer 拦截。"""

from __future__ import annotations

import http.client
import os
import sys
import threading

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import dashboard  # noqa: E402


def _req(port, method, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, headers=headers or {})
    r = conn.getresponse()
    body = r.read().decode("utf-8", errors="ignore")
    conn.close()
    return r.status, body, dict(r.getheaders())


def test_origin_evil_blocked(tmp_path):
    tmp_root = str(tmp_path / "sec1")
    os.makedirs(tmp_root, exist_ok=True)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s, _, _ = _req(port, "GET", "/api/dates", {"Origin": "https://evil.example"})
        assert s == 403, f"expected 403 got {s}"
        print("  [PASS] origin_evil_blocked")
    finally:
        server.shutdown()
        server.server_close()


def test_post_without_origin_allowed(tmp_path):
    tmp_root = str(tmp_path / "sec2")
    os.makedirs(tmp_root, exist_ok=True)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s, _, _ = _req(port, "GET", "/api/dates")
        assert s == 200
        print("  [PASS] post_without_origin_allowed")
    finally:
        server.shutdown()
        server.server_close()


def test_path_traversal_blocked(tmp_path):
    tmp_root = str(tmp_path / "sec3")
    os.makedirs(tmp_root, exist_ok=True)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s, _, _ = _req(port, "GET", "/api/day?date=../etc/passwd")
        assert s in (400, 404, 403), f"unexpected {s}"
        print("  [PASS] path_traversal_blocked")
    finally:
        server.shutdown()
        server.server_close()
