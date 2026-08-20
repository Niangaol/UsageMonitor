# -*- coding: utf-8 -*-
"""tests/api/test_dashboard_query_api.py — /api/query 端点契约（v2.6 · P7 受限模板查询）。

覆盖：缺 q/tpl 400、未命中模板 400、注入文本 400、自然语言模板 200（真实数据走通
q1 成本统计）、tpl= 显式模板模式 200/非法参数 400、访问口令复用（401/200）、
空数据 200 空态（不 500）。

config 控制在数据根的 config.json（AI 会话目录指向受控空/样例目录 + 固定模型定价），
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

DAY_RE = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}$")


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


def _write_usage(root: str, day: str):
    """写一天 usage.jsonl（10 分钟 AI 编程会话，ai_tool=opencode）。"""
    d = os.path.join(root, day)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "start": f"{day}T09:00:00", "end": f"{day}T09:10:00", "duration_ms": 600000,
            "exe": "code.exe", "app": "VS Code", "title": "VibeTrace/a.py",
            "category": "AI编程", "contact": None, "ai_tool": "opencode", "active": True,
        }, ensure_ascii=False) + "\n")


def _write_ai_conv(ai_dir: str, day: str):
    """写一天 AI 会话 jsonl（模型 tm 定价 1/5 美元每百万 token，产出可预期成本）。"""
    os.makedirs(ai_dir, exist_ok=True)
    with open(os.path.join(ai_dir, "conv.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "timestamp": f"{day}T10:00:00", "role": "user",
            "content": "请帮我写一个排序算法", "model": "tm", "project": "D:/DemoProj",
        }, ensure_ascii=False) + "\n")
        fh.write(json.dumps({
            "timestamp": f"{day}T10:01:00", "role": "assistant",
            "content": "def sort(x):\n    return sorted(x)\n # padding " * 200,
            "model": "tm", "project": "D:/DemoProj",
        }, ensure_ascii=False) + "\n")


def _setup(root: str, days: list[str], token: str = "", with_ai: bool = True):
    """建数据根：config.json + usage.jsonl +（可选）AI 会话样例。"""
    os.makedirs(root, exist_ok=True)
    ai_dir = os.path.join(root, "ai_demo")
    config = {
        "dashboard_token": token,
        "ai_sessions": {
            "enabled": True,
            "paths": {"opencode": [ai_dir]},
            "costs": {"enabled": True, "model_pricing": {"tm": [1.0, 5.0]}},
        },
        "insights": {"enabled": True, "git": {"enabled": False, "projects": []}},
    }
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False)
    for day in days:
        _write_usage(root, day)
        if with_ai:
            _write_ai_conv(ai_dir, day)


def _server(root):
    server = dashboard.create_server(root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


_YESTERDAY = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


def test_api_query_validation(tmp_path):
    """缺 q/tpl、未命中模板、注入文本 → 400，不 500。"""
    root = str(tmp_path / "qy1")
    _setup(root, [])
    server, port = _server(root)
    try:
        # 缺 q 且缺 tpl
        s, d = _req(port, "/api/query")
        assert s == 400 and d.get("error") == "missing q or tpl", d
        # 空 q
        s, d = _req(port, "/api/query?q=")
        assert s == 400 and d.get("error") == "missing q or tpl", d
        # 未命中模板（自由文本不受支持）
        s, d = _req(port, "/api/query?q=" + __import__("urllib.parse").parse.quote("今天天气怎么样"))
        assert s == 400 and d.get("error") == "unsupported question", d
        # 注入尝试
        for evil in ("SELECT * FROM usage", "昨天 opencode 花了多少钱 OR 1=1",
                     "'; DROP TABLE usage;--"):
            s, d = _req(port, "/api/query?q=" + __import__("urllib.parse").parse.quote(evil))
            assert s == 400 and d.get("error") == "unsupported question", (evil, d)
        print("  [PASS] api_query_validation")
    finally:
        server.shutdown()
        server.server_close()


def test_api_query_natural_language_with_data(tmp_path):
    """真实数据：自然语言模板命中 q1，answer + data 契约齐、成本>0。"""
    root = str(tmp_path / "qy2")
    _setup(root, [_YESTERDAY])
    server, port = _server(root)
    try:
        from urllib.parse import quote
        s, d = _req(port, "/api/query?q=" + quote("昨天 opencode 花了多少钱"))
        assert s == 200, f"应 200，实际 {s} {d}"
        assert d.get("ok") is True
        assert d["tpl"] == "q1"
        assert d["start"] == d["end"] == _YESTERDAY
        assert isinstance(d["answer"], str) and "昨天" in d["answer"]
        assert "opencode" in d["answer"]
        data = d["data"]
        assert data["tool"] == "opencode" and data["days"] == 1
        assert float(data["totals"]["cost"]) > 0, "样例会话应产生可估算成本"
        assert float(data["totals"]["minutes"]) == 10.0
        assert data["notice"]
        print("  [PASS] api_query_natural_language_with_data")
    finally:
        server.shutdown()
        server.server_close()


def test_api_query_tpl_mode_and_errors(tmp_path):
    """tpl= 显式模板模式：合法 200；未知 tpl/非法日期/倒置/超长 400。"""
    root = str(tmp_path / "qy3")
    _setup(root, [_YESTERDAY])
    server, port = _server(root)
    try:
        # 合法：显式日期
        s, d = _req(port, f"/api/query?tpl=q1&start={_YESTERDAY}&end={_YESTERDAY}")
        assert s == 200, f"应 200，实际 {s} {d}"
        assert d["tpl"] == "q1" and d["answer"] and "data" in d
        assert d["start"] == d["end"] == _YESTERDAY
        # 合法：省略日期 → 默认最近 7 天（截至昨天）
        s, d = _req(port, "/api/query?tpl=q3")
        assert s == 200 and d["days"] == 14 and d["data"]["rows"]
        # 未知模板
        s, d = _req(port, "/api/query?tpl=zzz")
        assert s == 400 and "unknown template" in d.get("error", ""), d
        # 非法日期
        for bad in ("2026-13-99", "abc", "2026/08/01"):
            s, d = _req(port, f"/api/query?tpl=q1&start={bad}&end=2026-08-20")
            assert s == 400 and d.get("error") == "invalid date", (bad, d)
        # 倒置区间
        s, d = _req(port, "/api/query?tpl=q1&start=2026-08-20&end=2026-08-01")
        assert s == 400 and d.get("error") == "invalid range", d
        # 超长区间（>92 天）
        s, d = _req(port, "/api/query?tpl=q1&start=2000-01-01&end=2099-01-01")
        assert s == 400 and d.get("error") == "range too large", d
        print("  [PASS] api_query_tpl_mode_and_errors")
    finally:
        server.shutdown()
        server.server_close()


def test_api_query_empty_state(tmp_path):
    """无数据根 → 200 空态（answer 提示未找到、data 空），不 500。"""
    root = str(tmp_path / "qy4")
    _setup(root, [], with_ai=False)
    server, port = _server(root)
    try:
        from urllib.parse import quote
        s, d = _req(port, "/api/query?q=" + quote("昨天 opencode 花了多少钱"))
        assert s == 200, f"空数据应 200 空态，实际 {s} {d}"
        assert d["tpl"] == "q1"
        assert "未找到" in d["answer"]
        assert float(d["data"]["totals"]["cost"]) == 0.0
        assert d["data"]["rows"] == []
        print("  [PASS] api_query_empty_state")
    finally:
        server.shutdown()
        server.server_close()


def test_api_query_auth_reuse(tmp_path):
    """开启访问口令后 /api/query 复用统一鉴权：无口令 401 / 正确 200。"""
    root = str(tmp_path / "qy5")
    _setup(root, [], token="s3cret")
    server, port = _server(root)
    try:
        s, d = _req(port, "/api/query?tpl=q3")
        assert s == 401, "无口令应 401"
        s, d = _req(port, "/api/query?tpl=q3", headers={"X-Dashboard-Token": "wrong"})
        assert s == 401, "错误口令应 401"
        s, d = _req(port, "/api/query?tpl=q3", headers={"X-Dashboard-Token": "s3cret"})
        assert s == 200, "正确口令应 200"
        assert "answer" in d and "data" in d
        print("  [PASS] api_query_auth_reuse")
    finally:
        server.shutdown()
        server.server_close()