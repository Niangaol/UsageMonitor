# -*- coding: utf-8 -*-
"""tests/unit/test_frontend_wiring.py — 前端与后端端点接线一致性（防功能做完没入口）。"""

from __future__ import annotations

import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_TEMPLATE = os.path.join(_PROJECT_ROOT, "assets", "dashboard.html")
_DASHBOARD = os.path.join(_PROJECT_ROOT, "dashboard.py")


def _template() -> str:
    with open(_TEMPLATE, "r", encoding="utf-8") as fh:
        return fh.read()


def _dashboard_src() -> str:
    with open(_DASHBOARD, "r", encoding="utf-8") as fh:
        return fh.read()


def test_all_nav_views_have_section_and_loader():
    """每个 nav 项都要有对应 section 与 loader，避免点击空白页。"""
    html = _template()
    views = set(re.findall(r'data-view="([a-z-]+)"', html))
    assert views, "未解析到 nav 项"
    loaders_block = re.search(r"const loaders = \{(.*?)\};", html, re.S)
    assert loaders_block, "未找到 loaders 注册表"
    loaders = set(re.findall(r"(\w+)\s*:", loaders_block.group(1)))
    for v in sorted(views):
        assert f'id="view-{v}"' in html, f"nav 项 {v} 缺少 <section id=view-{v}>"
        assert v in loaders, f"nav 项 {v} 未在 loaders 注册"


def test_titles_cover_all_views():
    """TITLES 必须覆盖所有 nav 项（否则页面标题为 undefined）。"""
    html = _template()
    views = set(re.findall(r'data-view="([a-z-]+)"', html))
    titles_block = re.search(r"const TITLES = \{(.*?)\};", html, re.S)
    assert titles_block
    titles = set(re.findall(r"(\w+)\s*:", titles_block.group(1)))
    missing = views - titles
    assert not missing, f"TITLES 缺少：{missing}"


def test_key_endpoints_are_called_by_frontend():
    """核心分析端点必须被前端调用，防止后端做完前端没接。"""
    html = _template()
    required = [
        "/api/timeline",   # P2
        "/api/budget",     # P3
        "/api/ai-compare", # P4
        "/api/trend",      # P5
        "/api/query",      # P7
        "/api/insights",
        "/api/ai-sessions",
    ]
    for ep in required:
        assert ep in html, f"前端未调用端点 {ep}"


def test_frontend_endpoints_exist_in_backend():
    """前端调用的 /api/* 都必须在 dashboard.py 有路由，避免 404。"""
    html = _template()
    src = _dashboard_src()
    called = set(re.findall(r'api\("(/api/[a-z0-9/_-]+)', html))
    assert called, "未解析到前端 api() 调用"
    for ep in sorted(called):
        assert f'"{ep}"' in src, f"前端调用了 {ep}，但后端无该路由"


def test_compare_view_has_table_columns():
    """对比视图必须有表体容器与关键列，保证渲染目标存在。"""
    html = _template()
    assert 'id="cmpBody"' in html
    assert 'id="cmpMeta"' in html
    for col in ["工具", "会话", "成本", "质量均分"]:
        assert col in html, f"对比表缺少列 {col}"


def test_query_panel_present():
    """快速提问面板输入框与按钮存在。"""
    html = _template()
    assert 'id="qInput"' in html
    assert 'id="qGo"' in html
    assert 'id="qAnswer"' in html
