# -*- coding: utf-8 -*-
"""tests/frontend/test_frontend_smoke.py — 前端静态接线冒烟测试。

对 assets/dashboard.html + dashboard.py 做确定性静态校验（纯标准库 re/读文件，
不起浏览器）：nav 项 / section id / loader / TITLES 一一对应，前端调用的每个
/api/* 路径在后端有路由，模板无致命结构缺口。
"""

from __future__ import annotations

import os
import re

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TEMPLATE = os.path.join(_PROJECT_ROOT, "assets", "dashboard.html")
_DASHBOARD = os.path.join(_PROJECT_ROOT, "dashboard.py")


def _template() -> str:
    with open(_TEMPLATE, "r", encoding="utf-8") as fh:
        return fh.read()


def _dashboard_src() -> str:
    with open(_DASHBOARD, "r", encoding="utf-8") as fh:
        return fh.read()


def _route_table_keys(src: str, table: str) -> set[str]:
    """解析 dashboard.py 中 _GET_ROUTES / _POST_ROUTES 表的全部 /api/* 键。

    用花括号配平定位字典块，避免类型标注（dict[str, Callable]）干扰正则。
    """
    start = src.index(table + ":")
    assign = src.index("=", start)
    brace = src.index("{", assign)
    depth = 0
    end = brace
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    block = src[brace : end + 1]
    return set(re.findall(r'"(/api/[^"]+)"', block))


def test_nav_section_loader_titles_one_to_one():
    """nav 项 ↔ section id ↔ loader ↔ TITLES 四张表必须一一对应。"""
    html = _template()
    views = set(re.findall(r'data-view="([a-z-]+)"', html))
    sections = set(re.findall(r'id="view-([a-z-]+)"', html))
    loaders_block = re.search(r"const loaders = \{(.*?)\};", html, re.S)
    titles_block = re.search(r"const TITLES = \{(.*?)\};", html, re.S)
    assert views, "未解析到 nav 项"
    assert loaders_block, "未找到 loaders 注册表"
    assert titles_block, "未找到 TITLES 注册表"
    loaders = set(re.findall(r"(\w+)\s*:", loaders_block.group(1)))
    titles = set(re.findall(r"(\w+)\s*:", titles_block.group(1)))
    assert sections == views, f"section 与 nav 不一致：缺 {sorted(views - sections)} / 多 {sorted(sections - views)}"
    assert loaders == views, f"loaders 与 nav 不一致：缺 {sorted(views - loaders)} / 多 {sorted(loaders - views)}"
    assert titles == views, f"TITLES 与 nav 不一致：缺 {sorted(views - titles)} / 多 {sorted(titles - views)}"


def test_each_loader_function_is_defined():
    """loaders 注册的每个加载器都要有对应函数定义，避免点击空白页。"""
    html = _template()
    loaders_block = re.search(r"const loaders = \{(.*?)\};", html, re.S)
    assert loaders_block, "未找到 loaders 注册表"
    loaders = re.findall(r"(\w+)\s*:", loaders_block.group(1))
    assert loaders, "loaders 注册表为空"
    for key in loaders:
        fn = "load" + key[0].upper() + key[1:]
        assert re.search(rf"function\s+{fn}\s*\(", html), f"loader {key} 缺少函数定义 {fn}()"


def test_frontend_api_calls_exist_in_backend():
    """前端调用的每个 /api/* 路径都必须在后端有路由，避免 404。"""
    html = _template()
    src = _dashboard_src()
    get_routes = _route_table_keys(src, "_GET_ROUTES")
    post_routes = _route_table_keys(src, "_POST_ROUTES")
    assert get_routes, "未解析到 _GET_ROUTES"
    assert post_routes, "未解析到 _POST_ROUTES"

    api_calls = set(re.findall(r'api\("(/api/[a-z0-9/_-]+)', html))
    postjson_calls = set(re.findall(r'postJson\("(/api/[a-z0-9/_-]+)', html))
    fetch_calls = set(re.findall(r'fetch\("(/api/[a-z0-9/_-]+)', html))
    assert api_calls, "未解析到前端 api() 调用"

    registered = get_routes | post_routes
    missing_api = api_calls - registered
    assert not missing_api, f"api() 调用但后端无路由：{sorted(missing_api)}"

    missing_post = postjson_calls - post_routes
    assert not missing_post, f"postJson() 调用但 POST 路由表无：{sorted(missing_post)}"

    # 原始 fetch 端点可能走 do_GET/do_POST 内联分支（如 /api/backup/restore），
    # 此时路径必须以字符串字面量登记在后端源码中。
    for ep in sorted(fetch_calls - registered):
        assert f'"{ep}"' in src, f"fetch() 调用 {ep} 既不在路由表也未在后端源码登记"


def test_template_has_no_fatal_structural_gaps():
    """模板无致命结构缺口：文档骨架、关键 section 与交互容器都在。"""
    html = _template()
    assert html.startswith("<!DOCTYPE html>"), "模板缺少 DOCTYPE"
    assert html.rstrip().endswith("</html>"), "模板未闭合 </html>"
    assert "<nav" in html, "缺少导航栏"
    assert "switchView" in html, "缺少视图切换函数 switchView"
    for view in ("overview", "trends", "report", "week", "month", "sessions",
                 "timeline", "growth", "compare", "log", "groups", "insights", "settings"):
        assert f'id="view-{view}"' in html, f"关键 section view-{view} 缺失"
    for cid in ("cmpBody", "cmpMeta", "qInput", "qGo", "qAnswer"):
        assert f'id="{cid}"' in html, f"关键容器 #{cid} 缺失"
    assert re.search(r"const loaders = \{", html), "缺少 loaders 注册表"
    assert re.search(r"const TITLES = \{", html), "缺少 TITLES 注册表"
