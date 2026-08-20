# -*- coding: utf-8 -*-
"""tests/api/test_dashboard_compare_api.py — /api/ai-compare 端点契约（v2.6 · P6 多工具对比）。

覆盖：start/end 缺失或非法 400 invalid date、end<start 400 invalid range、
范围 >90 天 400 invalid range、无数据 200 空态（契约字段齐）、
有数据 200（monkeypatch tool_compare.compare_tools 返回契约数据，验证路由+参数透传）、
访问口令复用（401/200）、tool-compare 别名同 handler。
config 控制在数据根的 config.json（AI 目录为空），确保测试不触碰真实用户目录。
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
import tool_compare  # noqa: E402


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


def _setup(root: str, days: list[str], token: str = ""):
    """建数据根：空 AI 目录 + 受控 config + 每日期一条 AI 会话（by_ai 有 opencode）。"""
    os.makedirs(root, exist_ok=True)
    empty_ai = os.path.join(root, "empty_ai")
    os.makedirs(empty_ai, exist_ok=True)
    config = {
        "dashboard_token": token,
        "ai_sessions": {"enabled": True, "paths": {"opencode": [empty_ai]}},
        "insights": {"enabled": False, "git": {"enabled": False, "projects": []}},
        "tool_compare": {"enabled": True, "sort_by": "chars_per_dollar",
                         "top": 10, "min_sessions": 1, "max_days": 90},
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


def test_api_compare_invalid_dates(tmp_path):
    """start/end 必填且全匹配 YYYY-MM-DD；缺失/非法 → 400 invalid date。"""
    root = str(tmp_path / "cmp1")
    _setup(root, ["2099-01-02"])
    server, port = _server(root)
    try:
        for bad in ("bad-date", "2099/01/02", "", "2099-01", "20990102"):
            s, d = _req(port, f"/api/ai-compare?start={bad}&end=2099-01-02")
            assert s == 400, f"start={bad!r} 应 400，实际 {s}"
            assert d.get("error") == "invalid date", d
            s, d = _req(port, f"/api/ai-compare?start=2099-01-02&end={bad}")
            assert s == 400, f"end={bad!r} 应 400，实际 {s}"
        for path in ("/api/ai-compare", "/api/ai-compare?start=2099-01-02",
                     "/api/ai-compare?end=2099-01-02"):
            s, d = _req(port, path)
            assert s == 400 and d.get("error") == "invalid date", f"{path} 应 400 invalid date"
        print("  [PASS] api_compare_invalid_dates")
    finally:
        server.shutdown()
        server.server_close()


def test_api_compare_invalid_range(tmp_path):
    """end<start 或范围 >90 天 → 400 invalid range；90 天整合法。"""
    root = str(tmp_path / "cmp2")
    _setup(root, ["2099-01-01"])
    server, port = _server(root)
    try:
        s, d = _req(port, "/api/ai-compare?start=2099-02-01&end=2099-01-01")
        assert s == 400 and d.get("error") == "invalid range", d
        # 92 天 > 90 上限
        s, d = _req(port, "/api/ai-compare?start=2099-01-01&end=2099-04-02")
        assert s == 400 and d.get("error") == "invalid range", d
        # 恰好 90 天合法（无数据 → 200 空态）
        s, d = _req(port, "/api/ai-compare?start=2099-01-01&end=2099-03-31")
        assert s == 200, f"90 天应 200，实际 {s} {d}"
        print("  [PASS] api_compare_invalid_range")
    finally:
        server.shutdown()
        server.server_close()


def test_api_compare_empty_state(tmp_path):
    """无会话数据目录 → 200 空态（tools 空 + summary 归零 + notice），不 500。"""
    root = str(tmp_path / "cmp3")
    _setup(root, ["2099-01-02"])
    server, port = _server(root)
    try:
        s, d = _req(port, "/api/ai-compare?start=2099-01-02&end=2099-01-02")
        assert s == 200, f"应 200 空态，实际 {s} {d}"
        assert d.get("tools") == []
        assert d.get("summary") == {"tools": 0, "total_sessions": 0,
                                    "total_cost": 0.0, "total_minutes": 0.0}
        assert d.get("start") == "2099-01-02" and d.get("end") == "2099-01-02"
        assert d.get("days") == 1
        assert "仅参考" in (d.get("notice") or "")
        print("  [PASS] api_compare_empty_state")
    finally:
        server.shutdown()
        server.server_close()


def test_api_compare_with_data(monkeypatch, tmp_path):
    """有数据 → 200 契约字段齐；project 参数透传；别名 /api/tool-compare 同 handler。"""
    root = str(tmp_path / "cmp4")
    _setup(root, ["2099-01-02", "2099-01-03"])

    def fake_compare(days, data_root, config, project=None):
        assert days == ["2099-01-02", "2099-01-03"]
        assert project == "VibeTrace"
        return {
            "start": days[0], "end": days[-1], "days": len(days),
            "notice": "仅参考", "tools": [
                {"tool": "opencode", "sessions": 7, "minutes": 15.0, "tokens_total": 20000,
                 "cost_total": 0.5, "generated_chars": 100000, "generated_lines": 1400,
                 "rounds": 14, "quality_avg": 76,
                 "grade_dist": {"优": 3, "良": 4, "中": 0, "待优化": 0},
                 "cost_per_1k_tokens": 0.025, "chars_per_dollar": 200000,
                 "chars_per_session": 14285.71, "tokens_per_session": 2857.14,
                 "share_pct": {"cost": 1.0, "sessions": 1.0, "tokens": 1.0}},
            ],
            "summary": {"tools": 1, "total_sessions": 7, "total_cost": 0.5, "total_minutes": 15.0},
        }

    monkeypatch.setattr(tool_compare, "compare_tools", fake_compare)
    server, port = _server(root)
    try:
        path = "/api/ai-compare?start=2099-01-02&end=2099-01-03&project=VibeTrace"
        for url in (path, path.replace("/api/ai-compare?", "/api/tool-compare?")):
            s, d = _req(port, url)
            assert s == 200, f"{url} 应 200，实际 {s} {d}"
            assert d["days"] == 2 and d["tools"][0]["tool"] == "opencode"
            assert d["tools"][0]["chars_per_dollar"] == 200000
            assert d["summary"]["total_sessions"] == 7
        print("  [PASS] api_compare_with_data")
    finally:
        server.shutdown()
        server.server_close()


def test_api_compare_token_auth(tmp_path):
    """token 开启时：无头 401、带头 200。"""
    root = str(tmp_path / "cmp5")
    _setup(root, ["2099-01-02"], token="s3cret")
    server, port = _server(root)
    try:
        s, _ = _req(port, "/api/ai-compare?start=2099-01-02&end=2099-01-02")
        assert s == 401, f"无 token 应 401，实际 {s}"
        s, _ = _req(port, "/api/ai-compare?start=2099-01-02&end=2099-01-02",
                    headers={"X-Dashboard-Token": "s3cret"})
        assert s == 200, f"带头应 200，实际 {s}"
        print("  [PASS] api_compare_token_auth")
    finally:
        server.shutdown()
        server.server_close()