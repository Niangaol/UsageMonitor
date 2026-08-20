# -*- coding: utf-8 -*-
"""tests/security/test_privacy.py — 隐私黑名单 / URL 掩蔽 / 更新白名单."""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import classifier  # noqa: E402
import updater  # noqa: E402
import monitor  # noqa: E402


def test_title_blacklist_hides_contact_and_browser():
    cfg = classifier.load_config()
    cfg["title_blacklist"] = [".*密码.*"]
    # 黑名单命中
    assert classifier.is_blacklisted_title("我的密码是abc", cfg) is True
    assert classifier.is_blacklisted_title("password=123", cfg) is False  # 大小写敏感按正则
    cfg["title_blacklist"] = [".*password.*"]
    assert classifier.is_blacklisted_title("password=123", cfg) is True
    # 非法正则不抛异常
    cfg["title_blacklist"] = ["[invalid"]
    assert classifier.is_blacklisted_title("anything", cfg) is False
    print("  [PASS] title_blacklist")


def test_blacklisted_session_no_contact(tmp_path):
    """黑名单会话在 monitor._open_session 中标题被替换且不解析联系人."""
    import datetime as dt
    from tests.conftest import FG

    root = str(tmp_path / "priv1")
    os.makedirs(root, exist_ok=True)
    cfg = classifier.load_config()
    cfg["data_root"] = root
    cfg["title_blacklist"] = [".*密码.*"]
    fg = FG("wechat.exe", "我的密码是abc", pid=999)
    sess = monitor._open_session(fg, cfg, {}, dt.datetime.now())
    assert sess["title"] == "[已隐藏]"
    assert sess["contact"] is None
    assert sess.get("browser_category") is None
    print("  [PASS] blacklisted_no_contact")


def test_browser_url_masking_via_history(tmp_path):
    """browser_history 对命中黑名单的 URL 掩蔽为 [已隐藏]."""
    import sqlite3
    import datetime
    import time
    import browser_history

    tmp = str(tmp_path / "priv2")
    os.makedirs(tmp, exist_ok=True)
    db = os.path.join(tmp, "History")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL, visit_time INTEGER NOT NULL, visit_duration INTEGER DEFAULT 0);
    """)
    ft = int((time.time() + 11644473600) * 1e6)
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://example.com/page?password=secret", "page"))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (1, ?, 10000000)", (ft,))
    conn.commit()
    conn.close()
    cfg = classifier.load_config()
    cfg["title_blacklist"] = [".*password.*"]
    today = datetime.date.today().isoformat()
    data = browser_history.collect(today, tmp, cfg, db_paths=[db])
    assert any(v["url"] == "[已隐藏]" for v in data["visits"])
    print("  [PASS] browser_url_masking")


def test_update_whitelist_rejects_evil():
    assert updater._is_allowed_asset_url("https://evil.com/UsageMonitor.exe") is False
    assert updater._is_allowed_asset_url("https://github.com/org/repo/releases/download/v1/UsageMonitor.exe") is True
    # 带自定义 api_base 的放行
    assert updater._is_allowed_asset_url("https://my-mirror.com/file.exe", api_base="https://my-mirror.com") is True
    assert updater._is_allowed_asset_url("https://evil.com/file.exe", api_base="https://my-mirror.com") is False
    print("  [PASS] update_whitelist")


def test_classifier_sanitize_removes_orphan():
    cfg = classifier.load_config()
    groups = {"exe_groups": {"steam.exe": "不存在的组"}, "custom_categories": [], "app_names": {}, "group_meta": {}}
    clean = classifier.sanitize_groups(cfg, groups)
    assert "steam.exe" not in clean["exe_groups"]
    # 合法组保留
    cfg_categories = classifier.all_categories(cfg)
    if cfg_categories:
        groups2 = {"exe_groups": {"steam.exe": cfg_categories[0]}, "custom_categories": [], "app_names": {}, "group_meta": {}}
        clean2 = classifier.sanitize_groups(cfg, groups2)
        assert clean2["exe_groups"].get("steam.exe") == cfg_categories[0]
    print("  [PASS] sanitize_orphan")
