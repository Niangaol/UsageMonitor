# -*- coding: utf-8 -*-
"""tests/api/test_dashboard_timeline_api.py — /api/timeline 端点契约（v2.5）。

覆盖：非法日期 400、无数据 200 空态、有数据 200、访问口令复用（401/200）。
config 控制在数据根的 config.json（ai_sessions.paths → 空目录 + git 关闭），
确保测试不触碰真实用户目录、不拉真实 git。
"""

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


def _req(port, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    conn.request("GET", path, headers=headers or {})
    r = conn.getresponse()
    body = r.read()
    conn.close()
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        data = {}
    return r.status, data


def _setup(root: str, day: str, token: str = ""):
    """建数据根：空 AI 目录 + 受控 config + 一条 AI 会话记录。"""
    os.makedirs(root, exist_ok=True)
    empty_ai = os.path.join(root, "empty_ai")
    os.makedirs(empty_ai, exist_ok=True)
    config = {
        "dashboard_token": token,
        "ai_sessions": {"enabled": True, "paths": {"opencode": [empty_ai]}},
        "insights": {"enabled": False, "git": {"enabled": False, "projects": []}},
        "vibe_timeline": {"enabled": True, "merge_gap_s": 120},
    }
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False)
    d = os.path.join(root, day)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "start": f"{day}T09:00:00", "end": f"{day}T09:10:00", "duration_ms": 600000,
            "exe": "code.exe", "app": "VS Code", "title": "VibeTrace/a.py",
            "category": "AI编程", "contact": None, "ai_tool": "opencode", "active": True,
        }, ensure_ascii=False) + "\n")


def _server(root):
    server = dashboard.create_server(root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def test_api_timeline_invalid_date(tmp_path):
    """date 必填且全匹配 YYYY-MM-DD；非法/缺失 → 400。"""
    root = str(tmp_path / "tl1")
    _setup(root, "2099-01-02")
    server, port = _server(root)
    try:
        for bad in ("bad-date", "2026/08/08", "", "2026-08", "2026-08-"):
            s, d = _req(port, "/api/timeline?date=" + bad)
            assert s == 400, f"{bad!r} 应 400，实际 {s}"
            assert "error" in d
        s, _ = _req(port, "/api/timeline")
        assert s == 400, "缺 date 应 400"
        print("  [PASS] api_timeline_invalid_date")
    finally:
        server.shutdown()
        server.server_close()


def test_api_timeline_empty_day(tmp_path):
    """无数据日 → 200 空态（events 空 + summary 归零），不 500。"""
    root = str(tmp_path / "tl2")
    _setup(root, "2099-01-02")
    server, port = _server(root)
    try:
        # 请求一个完全无数据、无目录的日期
        s, d = _req(port, "/api/timeline?date=2099-01-05")
        assert s == 200, f"空数据日应 200 空态，实际 {s} {d}"
        assert d.get("date") == "2099-01-05"
        assert d.get("events") == []
        ssum = d.get("summary") or {}
        assert ssum.get("ai_blocks") == 0 and ssum.get("commit_count") == 0
        assert ssum.get("total_cost") == 0
        print("  [PASS] api_timeline_empty_day")
    finally:
        server.shutdown()
        server.server_close()


def test_api_timeline_with_data_and_project_filter(tmp_path):
    """有 AI 会话数据 → 200 events 含 session 事件；project 过滤生效。"""
    root = str(tmp_path / "tl3")
    day = "2099-01-03"
    _setup(root, day)
    server, port = _server(root)
    try:
        s, d = _req(port, f"/api/timeline?date={day}")
        assert s == 200
        types = [e["type"] for e in d.get("events", [])]
        assert types == ["session"], types
        e = d["events"][0]
        assert e["time"] == "09:00:00" and e["title"] and e["detail"]
        assert e["detail"]["ai_tool"] == "opencode"
        # project 过滤：命中保留
        s, d2 = _req(port, f"/api/timeline?date={day}&project=VibeTrace")
        assert s == 200 and d2["events"]
        # project 过滤：无命中 → 空态（仍 200）
        s, d3 = _req(port, f"/api/timeline?date={day}&project=NoSuchProject")
        assert s == 200 and d3["events"] == []
        assert d3["summary"]["ai_blocks"] == 0
        print("  [PASS] api_timeline_with_data_and_project_filter")
    finally:
        server.shutdown()
        server.server_close()


def test_api_timeline_auth_reuse(tmp_path):
    """开启访问口令后 /api/timeline 复用统一鉴权：无口令 401 / 正确 200。"""
    root = str(tmp_path / "tl4")
    day = "2099-01-04"
    _setup(root, day, token="s3cret")
    server, port = _server(root)
    try:
        s, d = _req(port, f"/api/timeline?date={day}")
        assert s == 401, "无口令应 401"
        s, d = _req(port, f"/api/timeline?date={day}",
                    headers={"X-Dashboard-Token": "wrong"})
        assert s == 401, "错误口令应 401"
        s, d = _req(port, f"/api/timeline?date={day}",
                    headers={"X-Dashboard-Token": "s3cret"})
        assert s == 200, "正确口令应 200"
        assert "events" in d
        print("  [PASS] api_timeline_auth_reuse")
    finally:
        server.shutdown()
        server.server_close()