# -*- coding: utf-8 -*-
"""tests/unit/test_classifier.py — 分类规则、AI 误伤防护、联系人别名。"""

from __future__ import annotations

import json

import classifier


# ---------------------------------------------------------------------------
# 1. 分类规则
# ---------------------------------------------------------------------------
def test_classify_category_rules():
    """classify_category 按 exe 关键词匹配正确类别（title 仅对含 title 规则的类别生效）。"""
    cfg = classifier.load_config()
    # 按 exe 匹配
    assert classifier.classify_category("wechat.exe", "", cfg) == "社交聊天"
    assert classifier.classify_category("chrome.exe", "", cfg) == "浏览器"
    assert classifier.classify_category("code.exe", "", cfg) == "开发工具"
    # title 含 bilibili 不会让 unknown.exe 变浏览器（浏览器类别 title 为空）
    assert classifier.classify_category("unknown.exe", "bilibili - 视频", cfg) == "其他"
    assert classifier.classify_category("unknown.exe", "中国大学MOOC - 课程", cfg) == "其他"
    # 无匹配回退 "其他"
    assert classifier.classify_category("unknown.exe", "无特征标题", cfg) == "其他"
    # AI 编程标题匹配
    assert classifier.classify_category("unknown.exe", "opencode - session", cfg) == "AI编程"
    print("  [PASS] classify_category_rules")


def test_classify_browser_priority():
    """classify_browser 按优先级（学习 > 代码 > 视频）匹配。"""
    cfg = classifier.load_config()
    # 同时含 "github"(代码) 与 "视频" 时，按配置优先级取第一个
    # 默认优先级：学习 > 代码 > 视频
    assert classifier.classify_browser("GitHub - 主页", cfg) == "代码"
    assert classifier.classify_browser("bilibili - 视频", cfg) == "视频"
    assert classifier.classify_browser("中国大学MOOC - 课程", cfg) == "学习"
    assert classifier.classify_browser("普通新闻页面", cfg) == "其他"
    print("  [PASS] classify_browser_priority")


def test_social_main_title_vs_contact():
    """is_social_main_title 与 extract_contact 正确区分主界面/聊天窗口。"""
    cfg = classifier.load_config()
    # 主界面
    assert classifier.is_social_main_title("wechat.exe", "微信", cfg) is True
    assert classifier.is_social_main_title("wechat.exe", "QQ", cfg) is True
    # 聊天窗口
    assert classifier.is_social_main_title("wechat.exe", "张三", cfg) is False
    assert classifier.extract_contact("wechat.exe", "张三", cfg) == "张三"
    # 钉钉式标题
    assert classifier.extract_contact("dingtalk.exe", "与 李四 的会话", cfg) == "李四"
    assert classifier.extract_contact("dingtalk.exe", "和 王五 的聊天", cfg) == "王五"
    print("  [PASS] social_main_title_vs_contact")


# ---------------------------------------------------------------------------
# 2. AI 误伤防护
# ---------------------------------------------------------------------------
def test_ai_false_positive_python_pip():
    """python / pip / pypi 不误判为 pi agent。"""
    cfg = classifier.load_config()
    assert classifier.match_ai_keyword("python", cfg) is None
    assert classifier.match_ai_keyword("pip", cfg) is None
    assert classifier.match_ai_keyword("pypi", cfg) is None
    assert classifier.match_ai_keyword("python -m pip install", cfg) is None
    print("  [PASS] ai_false_positive_python_pip")


def test_ai_tool_detection_process_tree():
    """detect_ai_tool 通过进程树识别深层 AI 工具。"""
    cfg = classifier.load_config()
    # 终端里跑 opencode
    tree = {
        100: type("P", (), {"pid": 100, "ppid": 0, "exe": "wt.exe"}),
        200: type("P", (), {"pid": 200, "ppid": 100, "exe": "opencode.exe"}),
    }
    assert classifier.detect_ai_tool(100, tree, "", cfg) == "opencode"
    # 编辑器集成终端
    tree2 = {
        100: type("P", (), {"pid": 100, "ppid": 0, "exe": "code.exe"}),
        200: type("P", (), {"pid": 200, "ppid": 100, "exe": "opencode.exe"}),
    }
    assert classifier.detect_ai_tool(100, tree2, "", cfg) == "opencode"
    print("  [PASS] ai_tool_detection_process_tree")


def test_ai_own_window_chatgpt():
    """chatgpt.exe 前台窗口直接识别为 chatgpt。"""
    cfg = classifier.load_config()
    assert classifier.match_ai_keyword("chatgpt", cfg) == "chatgpt"
    assert classifier.match_ai_keyword("chatgpt.exe", cfg) == "chatgpt"
    print("  [PASS] ai_own_window_chatgpt")


# ---------------------------------------------------------------------------
# 3. 联系人别名
# ---------------------------------------------------------------------------
def test_resolve_alias():
    """resolve_alias 正确映射别名。"""
    aliases = {"aaa123": "张三", "bbb456": "李四"}
    assert classifier.resolve_alias("aaa123", aliases) == "张三"
    assert classifier.resolve_alias("bbb456", aliases) == "李四"
    assert classifier.resolve_alias("未知", aliases) == "未知"
    assert classifier.resolve_alias("", aliases) == ""
    print("  [PASS] resolve_alias")


def test_load_aliases_from_file(tmp_path) -> None:
    """load_aliases 从 JSON 文件读取别名表。"""
    aliases_path = tmp_path / "aliases.json"
    aliases_path.write_text(json.dumps({"wxid_abc": "王五"}, ensure_ascii=False), encoding="utf-8")
    result = classifier.load_aliases(str(aliases_path))
    assert result == {"wxid_abc": "王五"}
    print("  [PASS] load_aliases_from_file")


def test_app_groups_sanitize():
    """sanitize_groups 剔除指向未知类别的孤儿分组。"""
    cfg = classifier.load_config()
    groups = {
        "exe_groups": {"unknown.exe": "不存在的类别"},
        "custom_categories": [],
        "app_names": {},
        "group_meta": {},
    }
    clean = classifier.sanitize_groups(cfg, groups)
    assert "unknown.exe" not in clean.get("exe_groups", {})
    print("  [PASS] app_groups_sanitize")
