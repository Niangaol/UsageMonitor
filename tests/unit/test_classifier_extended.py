# -*- coding: utf-8 -*-
"""tests/unit/test_classifier_extended.py — 边界/异常/覆盖."""

from __future__ import annotations

import sys
import os
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import classifier  # noqa: E402


def test_load_config_missing_returns_default(tmp_path):
    missing = str(tmp_path / "no_such.json")
    cfg = classifier.load_config(missing)
    assert isinstance(cfg, dict) and "categories" in cfg
    print("  [PASS] load_config_missing")


def test_load_config_corrupt_falls_back(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{corrupt", encoding="utf-8")
    cfg = classifier.load_config(str(bad))
    assert isinstance(cfg, dict) and "categories" in cfg
    print("  [PASS] load_config_corrupt")


def test_terminal_tool_detection_with_paths():
    cfg = classifier.load_config()
    # 路径标题不应误判
    assert classifier.detect_term_tool(r"D:\git-stuff - pwsh", cfg) is None
    assert classifier.detect_term_tool(r"C:\Python311 - pwsh", cfg) is None
    # 正常标题命中
    assert classifier.detect_term_tool("lazygit - project", cfg) == "lazygit"
    assert classifier.detect_term_tool("git status", cfg) == "git"
    assert classifier.detect_term_tool("", cfg) is None
    print("  [PASS] term_tool_paths")


def test_subcategory_mapping():
    cfg = classifier.load_config()
    assert classifier.classify_subcategory("影音娱乐", "potplayer.exe", "", cfg) == "视频播放"
    assert classifier.classify_subcategory("影音娱乐", "qqmusic.exe", "", cfg) == "音乐"
    assert classifier.classify_subcategory("游戏", "steam.exe", "", cfg) == "游戏平台"
    assert classifier.classify_subcategory("开发工具", "code.exe", "", cfg) == "编辑器"
    assert classifier.classify_subcategory("社交聊天", "wechat.exe", "", cfg) is None
    print("  [PASS] subcategory")


def test_resolve_app_name_priority(tmp_path):
    cfg = classifier.load_config()
    cfg["data_root"] = str(tmp_path)
    # app_groups 覆盖优先
    groups = {"exe_groups": {}, "custom_categories": [], "app_names": {"myapp.exe": "自定义名"}, "group_meta": {}}
    classifier.save_app_groups(groups, str(tmp_path))
    assert classifier.resolve_app_name("myapp.exe", cfg) == "自定义名"
    # 无覆盖走 config.apps
    assert classifier.resolve_app_name("code.exe", cfg) == "VS Code"
    # 都无则标题化 stem
    assert classifier.resolve_app_name("unknownapp.exe", cfg) == "Unknownapp"
    # 清理
    classifier.save_app_groups({"exe_groups": {}, "custom_categories": [], "app_names": {}, "group_meta": {}}, str(tmp_path))
    print("  [PASS] resolve_app_name")


def test_all_categories_includes_custom(tmp_path):
    cfg = classifier.load_config()
    cfg["data_root"] = str(tmp_path)
    groups = {"exe_groups": {}, "custom_categories": ["我的分组"], "app_names": {}, "group_meta": {}}
    classifier.save_app_groups(groups, str(tmp_path))
    cats = classifier.all_categories(cfg)
    assert "我的分组" in cats
    classifier.save_app_groups({"exe_groups": {}, "custom_categories": [], "app_names": {}, "group_meta": {}}, str(tmp_path))
    print("  [PASS] all_categories_custom")


def test_match_ai_keyword_short_word_boundary():
    cfg = classifier.load_config()
    # 短关键词如 "q" 只整词匹配，不应命中 pip/python，需整词才命中
    assert classifier.match_ai_keyword("pip", cfg) is None  # pip 保护
    assert classifier.match_ai_keyword("python", cfg) is None
    # 长关键词应命中
    assert classifier.match_ai_keyword("opencode", cfg) == "opencode"
    assert classifier.match_ai_keyword("opencode.exe", cfg) == "opencode"
    print("  [PASS] match_ai_short")
