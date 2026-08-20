# -*- coding: utf-8 -*-
"""tests/unit/test_dashboard_template.py — 页面模板外置加载（ROADMAP §9.2 #1）。"""

from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import dashboard  # noqa: E402


def test_template_file_exists_in_assets():
    """assets/dashboard.html 必须存在（打包 datas 依赖它）。"""
    path = os.path.join(_PROJECT_ROOT, "assets", "dashboard.html")
    assert os.path.isfile(path), "assets/dashboard.html 缺失"
    assert os.path.getsize(path) > 10000, "模板过小，可能被截断"


def test_template_paths_priority_includes_meipass(monkeypatch):
    """打包态优先使用 sys._MEIPASS/assets，其次程序目录。"""
    monkeypatch.setattr(sys, "_MEIPASS", r"C:\fake_meipass", raising=False)
    paths_list = dashboard.template_paths()
    assert paths_list[0] == os.path.join(r"C:\fake_meipass", "assets", "dashboard.html")
    assert len(paths_list) >= 2
    # 去重：不应有重复候选
    assert len(paths_list) == len(set(paths_list))


def test_load_page_template_returns_real_template():
    """真实模板含关键占位符与骨架标记。"""
    html = dashboard.load_page_template()
    assert html.startswith("<!DOCTYPE html>")
    assert "DATA_ROOT" in html
    assert "AUTH_FLAG" in html
    assert "VibeTrace" in html
    assert html.rstrip().endswith("</html>")


def test_load_page_template_cached(monkeypatch):
    """mtime/size 未变时走缓存，不重复读盘。"""
    dashboard._template_cache.update(path=None, mtime=None, size=None, data=None)
    first = dashboard.load_page_template()
    calls = {"n": 0}
    real_open = open

    def counting_open(*args, **kwargs):
        calls["n"] += 1
        return real_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", counting_open)
    second = dashboard.load_page_template()
    assert second == first
    assert calls["n"] == 0, "命中缓存不应再读盘"


def test_fallback_when_all_paths_missing(monkeypatch):
    """所有候选路径不可用时回退内联兜底页（不白屏、不抛异常）。"""
    dashboard._template_cache.update(path=None, mtime=None, size=None, data=None)
    monkeypatch.setattr(dashboard, "template_paths", lambda: [r"C:\no_such_dir\dashboard.html"])
    html = dashboard.load_page_template()
    assert html == dashboard._FALLBACK_TEMPLATE
    assert "VibeTrace" in html
    assert "/api/dates" in html


def test_page_html_injects_root_and_auth():
    """_page_html 注入 data_root 与鉴权标记，占位符不残留。"""
    html = dashboard._page_html(r"D:\VibeTrace", True)
    assert "DATA_ROOT" not in html
    assert "AUTH_FLAG" not in html
    assert json.dumps(r"D:\VibeTrace") in html
    assert "const AUTH_REQUIRED = true;" in html

    html_off = dashboard._page_html(r"D:\VibeTrace", False)
    assert "const AUTH_REQUIRED = false;" in html_off


def test_page_html_escapes_dollar_in_root():
    """data_root 含 $ 时转义，避免破坏前端模板字符串。"""
    html = dashboard._page_html(r"D:\$pecial", True)
    assert "\\$pecial" in html
