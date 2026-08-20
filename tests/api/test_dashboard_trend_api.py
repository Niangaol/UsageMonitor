# -*- coding: utf-8 -*-
"""tests/api/test_dashboard_trend_api.py — /api/trend 端点契约（v2.6 · P7 能力成长曲线）。

覆盖：weeks 缺失默认 8 / 越界 400 / 非法 400、无数据 200 空态、有数据 200 契约字段齐、
快照文件生成且第二次请求 source=snapshot、访问口令复用（401/200）、周数截断。
config 控制在数据根的 config.json（insights 关闭 + AI 目录为空），
确保测试不触碰真实用户目录、不拉真实 git。
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


def _req(port, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request("GET", path, headers=headers or {})
    r = conn.getresponse()
    body = r.read()
    conn.close()
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        data = {}
    return r.status, data


def _iso_week_days(year: int, week: int, count: int) -> list[str]:
    """某 ISO 周开头的 count 个 YYYY-MM-DD 文本（2099 年，必定早于「今天」）。"""
    d = datetime.date(year, 1, 1)
    while d.isocalendar()[:2] != (year, week):
        d += datetime.timedelta(days=1)
    return [(d + datetime.timedelta(days=i)).isoformat() for i in range(count)]


def _setup(root: str, days: list[str], token: str = ""):
    """建数据根：空 AI 目录 + 受控 config + 每日期 usage.jsonl（10 分钟 AI 编程会话）。"""
    os.makedirs(root, exist_ok=True)
    empty_ai = os.path.join(root, "empty_ai")
    os.makedirs(empty_ai, exist_ok=True)
    config = {
        "dashboard_token": token,
        "ai_sessions": {"enabled": True, "paths": {"opencode": [empty_ai]}},
        "insights": {"enabled": False, "git": {"enabled": False, "projects": []}},
        "growth": {"enabled": True, "weeks": 8, "min_days_per_week": 3, "flat_threshold": 0.03},
    }
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False)
    for day in days:
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


def test_api_trend_invalid_weeks(tmp_path):
    """weeks 非 1..52 整数 → 400 invalid weeks；缺失默认 8。"""
    root = str(tmp_path / "tr1")
    _setup(root, _iso_week_days(2099, 1, 4))
    server, port = _server(root)
    try:
        for bad in ("0", "53", "-1", "abc", "8.5"):
            s, d = _req(port, "/api/trend?weeks=" + bad)
            assert s == 400, f"weeks={bad!r} 应 400，实际 {s} {d}"
            assert d.get("error") == "invalid weeks", d
        # 缺失 → 默认 8
        s, d = _req(port, "/api/trend")
        assert s == 200, f"缺失 weeks 应 200，实际 {s} {d}"
        print("  [PASS] api_trend_invalid_weeks")
    finally:
        server.shutdown()
        server.server_close()


def test_api_trend_empty_state(tmp_path):
    """无数据目录 → 200 空态 weeks=[] trend=[] source=fresh，不 500。"""
    root = str(tmp_path / "tr2")
    _setup(root, [])
    server, port = _server(root)
    try:
        s, d = _req(port, "/api/trend?weeks=8")
        assert s == 200, f"空数据应 200 空态，实际 {s} {d}"
        assert d.get("weeks") == [] and d.get("trend") == []
        assert d.get("source") == "fresh"
        assert d.get("notice")
        print("  [PASS] api_trend_empty_state")
    finally:
        server.shutdown()
        server.server_close()


def test_api_trend_with_data_and_snapshot_hit(tmp_path):
    """有 2 周×4 天数据 → 200 契约字段齐；快照文件生成；第二次请求 source=snapshot。"""
    root = str(tmp_path / "tr3")
    w1 = _iso_week_days(2099, 10, 4)
    w2 = _iso_week_days(2099, 11, 4)
    _setup(root, w1 + w2)
    server, port = _server(root)
    try:
        s, d = _req(port, "/api/trend?weeks=8")
        assert s == 200, f"有数据应 200，实际 {s} {d}"
        assert d.get("source") == "fresh"
        weeks = d.get("weeks") or []
        assert len(weeks) == 2
        assert weeks[0]["week"] < weeks[1]["week"]  # 升序
        for w in weeks:
            for key in ("week", "days", "scored_days", "focus_score", "quality_avg",
                        "generated_lines", "lines_added", "modify_ratio",
                        "ai_minutes", "saved_minutes"):
                assert key in w, f"缺少字段 {key}: {w}"
            assert "_days" not in w  # 内部指纹不外泄
        assert len(d.get("trend") or []) == len(growth._METRICS)  # 7 个指标
        assert os.path.exists(os.path.join(root, growth._SNAPSHOT_NAME))
        # 第二次请求 → 命中快照
        s2, d2 = _req(port, "/api/trend?weeks=8")
        assert s2 == 200 and d2.get("source") == "snapshot"
        assert d2.get("weeks") == weeks
        print("  [PASS] api_trend_with_data_and_snapshot_hit")
    finally:
        server.shutdown()
        server.server_close()


def test_api_trend_weeks_truncation(tmp_path):
    """weeks 参数截断最近 N 周（4 周数据请求 weeks=2 → 返回最近 2 周）。"""
    root = str(tmp_path / "tr4")
    w_list = [_iso_week_days(2099, w, 4) for w in (20, 21, 22, 23)]
    days = [d for w in w_list for d in w]
    _setup(root, days)
    server, port = _server(root)
    try:
        s, d = _req(port, "/api/trend?weeks=2")
        assert s == 200
        weeks = d.get("weeks") or []
        assert len(weeks) == 2
        assert weeks[0]["week"] == "2099-W22" and weeks[1]["week"] == "2099-W23"  # 最近两周
        assert len(d.get("trend") or []) == len(growth._METRICS)
        print("  [PASS] api_trend_weeks_truncation")
    finally:
        server.shutdown()
        server.server_close()


def test_api_trend_auth_reuse(tmp_path):
    """开启访问口令后 /api/trend 复用统一鉴权：无口令 401 / 正确 200。"""
    root = str(tmp_path / "tr5")
    _setup(root, _iso_week_days(2099, 30, 4), token="s3cret")
    server, port = _server(root)
    try:
        s, d = _req(port, "/api/trend")
        assert s == 401, "无口令应 401"
        s, d = _req(port, "/api/trend", headers={"X-Dashboard-Token": "wrong"})
        assert s == 401, "错误口令应 401"
        s, d = _req(port, "/api/trend", headers={"X-Dashboard-Token": "s3cret"})
        assert s == 200, "正确口令应 200"
        assert "weeks" in d and "trend" in d
        print("  [PASS] api_trend_auth_reuse")
    finally:
        server.shutdown()
        server.server_close()