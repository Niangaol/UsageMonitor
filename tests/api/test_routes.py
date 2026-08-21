# -*- coding: utf-8 -*-
"""tests/api/test_routes.py — 路由表完整性与全端点冒烟。

配合 dashboard.py 的路由表重构（do_GET/do_POST 只做分发）：
1. 全部 GET 路由带合法参数请求一遍，断言不出现 500（端点级降级约定）；
2. 全部 POST 路由带最小请求体请求一遍，断言不出现 5xx；
3. Handler 上每个 _api_* 方法都必须登记进路由表（无孤儿处理器）；
4. 未知路径 404 / 405、页面与 favicon 正常。
"""

from __future__ import annotations

import inspect
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import dashboard  # noqa: E402

from tests.conftest import make_record, seed_day  # noqa: E402

_DAY = "2099-01-10"
_MONTH = "2099-01"

# GET 冒烟参数：默认无参，需要日期/月份/查询词的端点在此补充
_GET_PARAMS = {
    "/api/day": f"?date={_DAY}",
    "/api/hourly": f"?date={_DAY}",
    "/api/report": f"?date={_DAY}",
    "/api/ai-sessions": f"?date={_DAY}",
    "/api/timeline": f"?date={_DAY}",
    "/api/insights": f"?date={_DAY}",
    "/api/insights/ai": f"?date={_DAY}",
    "/api/urls": f"?date={_DAY}",
    "/api/month": f"?month={_MONTH}",
    "/api/export": f"?type=json&scope=day&date={_DAY}",
    "/api/ai-compare": f"?start={_DAY}&end={_DAY}",
    "/api/tool-compare": f"?start={_DAY}&end={_DAY}",
    "/api/budget": f"?date={_DAY}",
    "/api/days": "?n=7",
    "/api/heatmap": "?days=14",
    "/api/log": "?n=50",
    "/api/query": "",  # 缺 q/tpl → 400（文档化行为）
}

# POST 冒烟最小请求体；断言状态码 < 500（400 属合法拒绝）
_POST_BODIES = {
    "/api/insights/settings": {"enabled": False},
    "/api/pricing": {"pricing": {"test-model": [1, 2]}},
    "/api/ai/module": {"prompt": {"instruction": "x"}},
    "/api/ai/module/import": {"custom": {"providers": []}},
    "/api/update/download": {},
    "/api/update/apply": {},
    "/api/groups/set": {},              # 缺 exe → 400
    "/api/groups/rename": {},           # 缺 exe → 400
    "/api/groups/import": {"groups": {}},
    "/api/groups/add": {},              # 缺 name → 400
    "/api/groups/delete": {},           # 缺 name → 400
}


def test_get_routes_smoke_no_500(api_server):
    """全部 GET 路由带合法参数冒烟：不允许 500（best-effort 降级约定）。"""
    client, root = api_server
    seed_day(root, _DAY, [make_record(_DAY, 10, 30), make_record(_DAY, 11, 20, ai_tool="opencode")])
    for path in dashboard._GET_ROUTES:
        if path == "/favicon.ico":
            continue
        s, d, _ = client.get(path + _GET_PARAMS.get(path, ""))
        assert s < 500, f"GET {path} 返回 {s}: {d}"
        if path == "/api/query":
            assert s == 400, "缺参 query 应 400"
        else:
            assert s in (200, 204), f"GET {path} 返回 {s}: {d}"
    print("  [PASS] get_routes_smoke_no_500")


def test_post_routes_smoke_no_5xx(api_server):
    """全部 POST 路由带最小请求体冒烟：不允许 5xx。"""
    client, root = api_server
    seed_day(root, _DAY, [make_record(_DAY, 10, 30)])
    for path, body in _POST_BODIES.items():
        assert path in dashboard._POST_ROUTES, f"{path} 未在 _POST_ROUTES 登记"
        s, d, _ = client.post(path, body)
        assert s < 500, f"POST {path} 返回 {s}: {d}"
    print("  [PASS] post_routes_smoke_no_5xx")


def test_all_api_handlers_registered():
    """Handler 上每个 _api_* 方法都必须被路由表引用（防孤儿处理器）。"""
    handler_funcs = {
        v for k, v in vars(dashboard.Handler).items()
        if k.startswith("_api_") and inspect.isfunction(v)
    }
    registered = set(dashboard._GET_ROUTES.values()) | set(dashboard._POST_ROUTES.values())
    # _api_backup_restore 由 do_POST 内联派发（二进制体先于 JSON 解析），白名单豁免
    inline_ok = dashboard.Handler.__dict__["_api_backup_restore"]
    orphans = handler_funcs - registered - {inline_ok}
    assert not orphans, f"未注册的处理器: {sorted(f.__name__ for f in orphans)}"
    # 路由表里的值也必须都是 Handler 的方法（防手滑挂错函数）
    assert registered <= handler_funcs | {inline_ok}
    print("  [PASS] all_api_handlers_registered")


def test_unknown_routes(api_server):
    """未知 GET → 404；未知 POST → 405；favicon → 204。"""
    client, _ = api_server
    s, _, _ = client.get("/api/not_exist_zzz")
    assert s == 404
    s, _, _ = client.post("/api/not_exist_zzz", {})
    assert s == 405
    s, _, _ = client.get("/favicon.ico")
    assert s == 204
    print("  [PASS] unknown_routes")


def test_page_served_with_nonce_script(api_server):
    """/ 返回 HTML，含版本号与 nonce 化脚本标签。"""
    client, root = api_server
    seed_day(root, _DAY, [make_record(_DAY, 10, 30)])
    s, d, hdr = client.get("/")
    assert s == 200
    html = d.get("_raw", "")
    assert "VibeTrace" in html and "<script nonce=" in html
    assert hdr.get("Content-Type", "").startswith("text/html")
    print("  [PASS] page_served_with_nonce_script")


def test_seeded_data_visible_via_api(api_server):
    """种子数据经 /api/dates 与 /api/day 可读（conftest 设施自检）。"""
    client, root = api_server
    seed_day(root, _DAY, [make_record(_DAY, 10, 30)])
    s, d, _ = client.get("/api/dates")
    assert s == 200 and _DAY in d["dates"]
    s, d, _ = client.get(f"/api/day?date={_DAY}")
    assert s == 200
    agg = d["aggregate"]
    assert agg["total_active_ms"] >= 30 * 60000
    print("  [PASS] seeded_data_visible_via_api")
