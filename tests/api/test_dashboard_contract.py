# -*- coding: utf-8 -*-
"""tests/api/test_dashboard_contract.py — Dashboard /api/* 契约。"""

from __future__ import annotations

import http.client
import json
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
    body = r.read()
    hdr = dict(r.getheaders())
    conn.close()
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        data = {"_raw": body.decode("utf-8", errors="ignore")}
    return r.status, data, hdr


def test_api_dates_and_day(tmp_path):
    tmp_root = str(tmp_path / "api1")
    os.makedirs(tmp_root, exist_ok=True)
    day = "2099-01-02"
    os.makedirs(os.path.join(tmp_root, day), exist_ok=True)
    with open(os.path.join(tmp_root, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"start": f"{day}T10:00:00", "end": f"{day}T10:01:00", "duration_ms": 60000, "exe": "code.exe", "app": "VS Code", "title": "a.py", "category": "开发工具", "contact": None, "ai_tool": None, "active": True}, ensure_ascii=False) + "\n")
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s, d, _ = _req(port, "GET", "/api/dates")
        assert s == 200, f"/api/dates status {s} {d}"
        assert isinstance(d.get("dates"), list)

        s2, d2, _ = _req(port, "GET", f"/api/day?date={day}")
        assert s2 == 200, f"/api/day status {s2} {d2}"
        assert "aggregate" in d2 or "sessions" in d2 or "total" in d2
        print("  [PASS] api_dates_and_day")
    finally:
        server.shutdown()
        server.server_close()


def test_api_report_and_heatmap(tmp_path):
    tmp_root = str(tmp_path / "api2")
    os.makedirs(tmp_root, exist_ok=True)
    day = "2099-01-03"
    os.makedirs(os.path.join(tmp_root, day), exist_ok=True)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s, _, _ = _req(port, "GET", f"/api/report?date={day}")
        assert s in (200, 404), f"/api/report status {s}"
        s2, d2, _ = _req(port, "GET", "/api/heatmap?days=7")
        assert s2 == 200
        assert "days" in d2
        print("  [PASS] api_report_and_heatmap")
    finally:
        server.shutdown()
        server.server_close()


def test_api_unknown_returns_404(tmp_path):
    tmp_root = str(tmp_path / "api3")
    os.makedirs(tmp_root, exist_ok=True)
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s, _, _ = _req(port, "GET", "/api/not_exist_zzz")
        assert s == 404
        print("  [PASS] api_unknown_returns_404")
    finally:
        server.shutdown()
        server.server_close()


def test_api_insights_includes_time_saved(tmp_path):
    tmp_root = str(tmp_path / "api4")
    os.makedirs(tmp_root, exist_ok=True)
    day = "2099-01-06"
    os.makedirs(os.path.join(tmp_root, day), exist_ok=True)
    with open(os.path.join(tmp_root, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"start": f"{day}T10:00:00", "end": f"{day}T11:00:00", "duration_ms": 3600000, "exe": "code.exe", "app": "VS Code", "title": "a.py", "category": "AI编程", "contact": None, "ai_tool": "opencode", "active": True}, ensure_ascii=False) + "\n")
    server = dashboard.create_server(tmp_root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        s, d, _ = _req(port, "GET", f"/api/insights?date={day}")
        assert s == 200, f"/api/insights status {s} {d}"
        assert "time_saved" in d, f"missing time_saved {d.keys()}"
        assert "saved_ms" in d["time_saved"]
        print("  [PASS] api_insights_includes_time_saved")
    finally:
        server.shutdown()
        server.server_close()
