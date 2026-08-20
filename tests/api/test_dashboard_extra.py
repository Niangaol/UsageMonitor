# -*- coding: utf-8 -*-
"""tests/api/test_dashboard_extra.py — 覆盖 dashboard 更多分支以提升覆盖率。"""

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


def _req(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = headers or {}
    if body is not None:
        h["Content-Type"] = "application/json"
        conn.request(method, path, body=json.dumps(body), headers=h)
    else:
        conn.request(method, path, headers=h)
    r = conn.getresponse()
    data = r.read()
    hdr = dict(r.getheaders())
    conn.close()
    try:
        j = json.loads(data.decode("utf-8")) if data else {}
    except Exception:
        j = {"_raw": data.decode("utf-8", errors="ignore")}
    return r.status, j, hdr


def _server(tmp):
    s = dashboard.create_server(tmp, port=0)
    p = s.server_address[1]
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, p


def test_dashboard_root_and_security_headers(tmp_path):
    tmp = str(tmp_path / "extra1")
    os.makedirs(tmp, exist_ok=True)
    s, p = _server(tmp)
    try:
        status, _, hdr = _req(p, "GET", "/")
        assert status == 200
        assert hdr.get("X-Frame-Options") == "DENY"
        assert "Content-Security-Policy" in hdr
        print("  [PASS] dashboard_root_and_security_headers")
    finally:
        s.shutdown()
        s.server_close()


def test_dashboard_api_days_and_heatmap(tmp_path):
    tmp = str(tmp_path / "extra2")
    os.makedirs(tmp, exist_ok=True)
    day = "2099-02-01"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"start": f"{day}T10:00:00", "end": f"{day}T10:10:00", "duration_ms": 600000, "exe": "code.exe", "app": "VS Code", "title": "a.py", "category": "开发工具", "contact": None, "ai_tool": None, "active": True}, ensure_ascii=False) + "\n")
    s, p = _server(tmp)
    try:
        st, j, _ = _req(p, "GET", "/api/days?n=5")
        assert st == 200 and "days" in j
        st2, j2, _ = _req(p, "GET", "/api/heatmap?days=7")
        assert st2 == 200 and "days" in j2
        st3, j3, _ = _req(p, "GET", "/api/log?n=10")
        assert st3 == 200 and "entries" in j3
        print("  [PASS] dashboard_api_days_and_heatmap")
    finally:
        s.shutdown()
        s.server_close()


def test_dashboard_api_groups_and_report(tmp_path):
    tmp = str(tmp_path / "extra3")
    os.makedirs(tmp, exist_ok=True)
    day = "2099-02-02"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"start": f"{day}T09:00:00", "end": f"{day}T09:05:00", "duration_ms": 300000, "exe": "chrome.exe", "app": "Chrome", "title": "GitHub", "category": "浏览器", "contact": None, "ai_tool": None, "active": True}, ensure_ascii=False) + "\n")
    s, p = _server(tmp)
    try:
        st, j, _ = _req(p, "GET", "/api/groups")
        assert st == 200 and "categories" in j
        st2, _, _ = _req(p, "GET", f"/api/day?date={day}")
        assert st2 == 200
        st3, _, _ = _req(p, "GET", "/api/report?date=2099-01-01")
        assert st3 in (200, 404)
        st4, _, _ = _req(p, "GET", "/api/export?type=json&scope=day&date=2099-02-02")
        assert st4 in (200, 404, 400)
        print("  [PASS] dashboard_api_groups_and_report")
    finally:
        s.shutdown()
        s.server_close()


def test_dashboard_api_insights_settings(tmp_path):
    tmp = str(tmp_path / "extra4")
    os.makedirs(tmp, exist_ok=True)
    s, p = _server(tmp)
    try:
        st, j, _ = _req(p, "GET", "/api/insights/settings")
        assert st == 200 and "ai" in j
        print("  [PASS] dashboard_api_insights_settings")
    finally:
        s.shutdown()
        s.server_close()
