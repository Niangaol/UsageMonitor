# -*- coding: utf-8 -*-
"""tests/e2e/test_smoke.py — 仪表盘端到端冒烟测试。

用 seed_day 在临时数据根造一天 usage.jsonl，起 dashboard.create_server(root, port=0)
的真实 HTTP 服务，走完整链路请求页面与数据端点，断言 200 + 关键字段。
纯标准库 + pytest，确定性，Windows CI 可跑。

说明：任务里提到的 /api/overview、/api/trends 是本仓库前端视图名（nav "overview"
/ "trends"），后端并无这两个路由（请求会 404）。对应实际数据端点为 /api/day
（单日概览聚合）与 /api/trend（趋势曲线），本文件按真实端点做整链路冒烟。
"""

from __future__ import annotations

import json
import os
import sys
import threading

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest  # noqa: E402

import dashboard  # noqa: E402
from tests.conftest import ApiClient, make_record, seed_day  # noqa: E402

_DAY = "2099-06-15"


@pytest.fixture
def e2e_server(tmp_path):
    """起本地仪表盘服务器（随机端口）；yield (ApiClient, data_root)；结束自动 shutdown。

    预写 config.json：update.api_base 指向不可达地址（/api/update/check 快速失败不触网）、
    ai_sessions.enabled=false（隔离开发机真实 AI 会话目录），保证确定性。
    """
    root = str(tmp_path / "e2e_root")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"update": {"api_base": "http://127.0.0.1:1"},
                   "ai_sessions": {"enabled": False}}, fh)
    dashboard.invalidate_days_cache()
    server = dashboard.create_server(root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = ApiClient(port)
    try:
        yield client, root
    finally:
        server.shutdown()
        server.server_close()
        dashboard.invalidate_days_cache()


def _seed_smoke_day(root: str) -> None:
    """造一天 3 条会话：开发 / AI 编程 / 游戏。"""
    seed_day(root, _DAY, [
        make_record(_DAY, 9, 300),
        make_record(_DAY, 14, 45, category="AI编程", ai_tool="opencode"),
        make_record(_DAY, 18, 15, exe="steam.exe", app="Steam", category="游戏"),
    ])


def test_root_serves_dashboard_page(e2e_server):
    """GET / 返回仪表盘页面：200 + HTML 骨架标记。"""
    client, _ = e2e_server
    status, payload, _ = client.get("/")
    assert status == 200, f"/ status {status}"
    assert isinstance(payload, dict) and "_raw" in payload, "页面响应未被 _raw 解析"
    html = payload["_raw"]
    assert "<!DOCTYPE html>" in html
    assert "VibeTrace" in html
    assert "const AUTH_REQUIRED = false;" in html, "鉴权标记未注入（默认关）"


def test_api_chain_overview_and_trends(e2e_server):
    """seed_day 造一天 → /api/dates + /api/day（概览）+ /api/trend（趋势）整链路 200。"""
    client, root = e2e_server
    _seed_smoke_day(root)

    status, dates, _ = client.get("/api/dates")
    assert status == 200, f"/api/dates status {status}"
    assert isinstance(dates.get("dates"), list)
    assert _DAY in dates["dates"], f"种子日期未出现在 /api/dates: {dates.get('dates')}"

    status, day_data, _ = client.get(f"/api/day?date={_DAY}")
    assert status == 200, f"/api/day status {status}"
    agg = day_data.get("aggregate")
    assert isinstance(agg, dict), "缺 aggregate"
    assert agg.get("session_count") == 3
    assert agg.get("total_active_ms", 0) > 0
    assert agg.get("by_app") and agg.get("by_category"), "概览聚合缺分类统计"

    status, trend, _ = client.get("/api/trend?weeks=8")
    assert status == 200, f"/api/trend status {status}"
    assert "weeks" in trend, "趋势响应缺 weeks"
    assert "trend" in trend, "趋势响应缺 trend"
