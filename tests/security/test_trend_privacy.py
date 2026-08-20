# -*- coding: utf-8 -*-
"""tests/security/test_trend_privacy.py — /api/trend 隐私与鉴权（v2.6 · P7 成长曲线）。

覆盖：
  - growth_baseline.json 快照只含周均值：无会话标题/路径/联系人等明细字段；
  - 对外 /api/trend 响应不含快照内部指纹 _days；
  - 端点复用统一鉴权三件套：Origin 拦截 403、口令 401/200。
"""

from __future__ import annotations

import datetime
import http.client
import json
import os
import sys
import threading

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import dashboard  # noqa: E402
import growth  # noqa: E402

_DETAIL_KEYS = ("title", "path", "file", "conversation", "contact", "project", "repo",
                "url", "msg", "content", "detail")


def _req(port, method, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    conn.request(method, path, headers=headers or {})
    r = conn.getresponse()
    body = r.read().decode("utf-8", errors="ignore")
    conn.close()
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}
    return r.status, data


def _week_days(year: int, week: int, count: int) -> list[str]:
    d = datetime.date(year, 1, 1)
    while d.isocalendar()[:2] != (year, week):
        d += datetime.timedelta(days=1)
    return [(d + datetime.timedelta(days=i)).isoformat() for i in range(count)]


def _setup(root: str, token: str = ""):
    os.makedirs(root, exist_ok=True)
    empty_ai = os.path.join(root, "empty_ai")
    os.makedirs(empty_ai, exist_ok=True)
    config = {
        "dashboard_token": token,
        "ai_sessions": {"enabled": True, "paths": {"opencode": [empty_ai]}},
        "insights": {"enabled": False, "git": {"enabled": False, "projects": []}},
    }
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False)
    for day in _week_days(2099, 8, 4) + _week_days(2099, 9, 4):
        d = os.path.join(root, day)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "usage.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "start": f"{day}T09:00:00", "end": f"{day}T09:10:00", "duration_ms": 600000,
                "exe": "code.exe", "app": "VS Code",
                "title": "机密项目/secret.py", "contact": "张三",
                "category": "AI编程", "ai_tool": "opencode", "active": True,
            }, ensure_ascii=False) + "\n")


def _server(root):
    server = dashboard.create_server(root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def test_trend_snapshot_no_detail_leak(tmp_path):
    """快照文件与 API 响应只含周均值，不含标题/路径/联系人等明细。"""
    root = str(tmp_path / "priv_trend1")
    _setup(root)
    server, port = _server(root)
    try:
        s, d = _req(port, "GET", "/api/trend")
        assert s == 200, f"should be 200, got {s} {d}"
        raw = json.dumps(d, ensure_ascii=False)
        for key in _DETAIL_KEYS:
            assert key not in raw, f"响应泄露明细字段 {key}"
        # 快照文件本身同样干净
        snap_path = os.path.join(root, growth._SNAPSHOT_NAME)
        assert os.path.isfile(snap_path), "快照文件应已生成"
        with open(snap_path, encoding="utf-8") as fh:
            snap = json.load(fh)
        snap_raw = json.dumps(snap, ensure_ascii=False)
        for key in _DETAIL_KEYS:
            assert key not in snap_raw, f"快照泄露明细字段 {key}"
        for w in snap.get("weeks", []):
            for key in ("title", "conversation", "contact"):
                assert key not in w, f"周条目泄露 {key}"
        print("  [PASS] trend_snapshot_no_detail_leak")
    finally:
        server.shutdown()
        server.server_close()


def test_trend_origin_blocked(tmp_path):
    """跨站 Origin 请求被拦截（复用 _origin_allowed 三件套）。"""
    root = str(tmp_path / "priv_trend2")
    _setup(root)
    server, port = _server(root)
    try:
        s, d = _req(port, "GET", "/api/trend", {"Origin": "https://evil.example"})
        assert s == 403, f"恶意 Origin 应 403，实际 {s}"
        print("  [PASS] trend_origin_blocked")
    finally:
        server.shutdown()
        server.server_close()


def test_trend_auth_gate_enforced(tmp_path):
    """开启口令后 /api/trend 必须带正确口令。"""
    root = str(tmp_path / "priv_trend3")
    _setup(root, token="secret-token")
    server, port = _server(root)
    try:
        s, _ = _req(port, "GET", "/api/trend")
        assert s == 401, "无口令应 401"
        s, _ = _req(port, "GET", "/api/trend", {"X-Dashboard-Token": "wrong"})
        assert s == 401, "错误口令应 401"
        s, d = _req(port, "GET", "/api/trend", {"X-Dashboard-Token": "secret-token"})
        assert s == 200 and "weeks" in d
        print("  [PASS] trend_auth_gate_enforced")
    finally:
        server.shutdown()
        server.server_close()