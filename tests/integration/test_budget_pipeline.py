# -*- coding: utf-8 -*-
"""tests/integration/test_budget_pipeline.py — 成本预算告警端到端（v2.6 · P3）。

真实 data_root + config.json + 注入 budget._collect_day（避免扫描真实 AI 会话），
验证：budget_status 三态/月度聚合、report 月报/周报预算章节、
/api/budget HTTP 契约（400 非法参数 / 200 各状态 / 默认关闭空态）。
browser_history_enabled=False 让 web 收集降级为空 visits，不碰真实浏览器数据。
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

import budget  # noqa: E402
import dashboard  # noqa: E402
import report  # noqa: E402

DAY = "2026-08-20"
MONTH = "2026-08"
COSTS = {
    "2026-08-18": 3.0,     # ok
    "2026-08-19": 3.0,     # ok
    "2026-08-20": 12.34,   # 超支
    "2026-08-21": 8.0,     # 接近（80% 边界）
}


def _setup(root: str, budget_cfg: dict | None) -> None:
    """构造测试数据根：config.json + 空 AI 路径 + 关浏览器探测。"""
    empty_ai = os.path.join(root, "empty_ai")
    os.makedirs(empty_ai, exist_ok=True)
    config = {
        "data_root": root,
        "browser_history_enabled": False,
        "ai_sessions": {"enabled": True, "paths": {"opencode": [empty_ai]},
                        "costs": {"enabled": True}},
        "insights": {"enabled": True},
    }
    if budget_cfg is not None:
        config["insights"]["budget"] = budget_cfg
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False)


def _fake_collect(costs: dict):
    def _inner(date_str, _root, _config):
        if date_str not in costs:
            return {"found": False, "cost": 0.0, "by_tool": {}, "by_project": {}}
        c = float(costs[date_str])
        return {"found": True, "cost": c,
                "by_tool": {"opencode": c}, "by_project": {"Demo": c}}
    return _inner


def _req(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    conn.request("GET", path)
    r = conn.getresponse()
    body = r.read()
    conn.close()
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        data = {}
    return r.status, data


def _server(root):
    server = dashboard.create_server(root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def test_budget_pipeline_full(tmp_path, monkeypatch):
    root = str(tmp_path / "bd_root")
    os.makedirs(root, exist_ok=True)
    _setup(root, {"enabled": True, "daily": 10.0, "monthly": 200.0})
    monkeypatch.setattr(budget, "_collect_day", _fake_collect(COSTS))

    # ---- 函数级：三态与月度聚合 ----
    st = budget.budget_status(DAY, root, {"insights": {"budget": {"enabled": True, "daily": 10.0, "monthly": 200.0}}})
    assert st["status"] == "exceed" and st["period"] == "daily"
    assert st["spent"] == 12.34 and st["by_tool"] == {"opencode": 12.34}
    st = budget.budget_status(MONTH, root, {"insights": {"budget": {"enabled": True, "daily": 10.0, "monthly": 200.0}}})
    assert st["status"] == "ok" and st["spent"] == 26.34  # 3+3+12.34+8
    assert [d["date"] for d in st["days"]] == ["2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"]

    # ---- 月报集成：预算章节出现/默认关闭不出现 ----
    month_md = report.generate_month_report_md(MONTH, root)
    assert "AI 成本预算（月度）" in month_md, "月报应含预算小结章节"
    assert "超支" in month_md or "正常" in month_md or "接近预算" in month_md
    # 默认关闭：budget 段缺失 → 月报不含预算章节
    root_off = str(tmp_path / "bd_off")
    os.makedirs(root_off, exist_ok=True)
    _setup(root_off, None)
    off_md = report.generate_month_report_md(MONTH, root_off)
    assert "AI 成本预算" not in off_md, "预算默认关闭时月报不应显示预算章节"

    # ---- 周报小结 ----
    week = budget.budget_week_summary([f"2026-08-{d:02d}" for d in range(16, 23)], root,
                                      {"insights": {"budget": {"enabled": True, "daily": 10.0, "monthly": 200.0}}})
    assert week is not None and "超支 1 天" in week and "接近 1 天" in week

    # ---- HTTP 层：/api/budget 契约 ----
    server, port = _server(root)
    try:
        s, d = _req(port, "/api/budget?date=bad")
        assert s == 400 and "error" in d
        s, d = _req(port, "/api/budget?date=2026-08-20&period=weekly")
        assert s == 400 and d["error"] == "invalid period"
        # 默认开启：daily 超支
        s, d = _req(port, "/api/budget?date=2026-08-20")
        assert s == 200 and d["status"] == "exceed" and d["period"] == "daily"
        assert d["days"] and d["by_tool"]["opencode"] == 12.34
        # 月度聚合
        s, d = _req(port, "/api/budget?date=2026-08")
        assert s == 200 and d["period"] == "monthly" and d["status"] == "ok"
        assert abs(d["spent"] - 26.34) < 1e-9
        # 显式 period 与粒度不匹配 → 400（API 层严格校验）
        s, d = _req(port, "/api/budget?date=2026-08-20&period=monthly")
        assert s == 400
        s, d = _req(port, "/api/budget?date=2026-08&period=daily")
        assert s == 400
    finally:
        server.shutdown()
        server.server_close()

    # ---- HTTP 层：默认关闭 → 200 disabled 空态 ----
    server2, port2 = _server(root_off)
    try:
        s, d = _req(port2, "/api/budget?date=2026-08-20")
        assert s == 200 and d["status"] == "disabled" and d["spent"] == 0.0
    finally:
        server2.shutdown()
        server2.server_close()
    print("  [PASS] budget_pipeline_full")


def test_budget_degradation_no_crash(tmp_path, monkeypatch):
    """成本聚合抛异常/无数据 → 预算端点 200 空态（best-effort 不 500 拖垮概览）。"""
    root = str(tmp_path / "bd_degrade")
    os.makedirs(root, exist_ok=True)
    _setup(root, {"enabled": True, "daily": 10.0, "monthly": 200.0})

    def boom(_d, _r, _c):
        raise RuntimeError("collect exploded")
    monkeypatch.setattr(budget, "_collect_day", boom)
    st = budget.budget_status(DAY, root, {"insights": {"budget": {"enabled": True, "daily": 10.0, "monthly": 200.0}}})
    assert st["status"] == "ok" and st["spent"] == 0.0  # 异常日被吞掉，不传播

    server, port = _server(root)
    try:
        s, d = _req(port, "/api/budget?date=2026-08-20")
        assert s == 200 and d["status"] == "ok" and d["spent"] == 0.0
    finally:
        server.shutdown()
        server.server_close()
    print("  [PASS] budget_degradation_no_crash")