# -*- coding: utf-8 -*-
"""tests/unit/test_updater.py — 版本比较/白名单/脚本生成."""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import updater  # noqa: E402


def test_parse_version_variants():
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("1.10.2-beta") == (1, 10, 2)
    assert updater.parse_version("v2.3.0") == (2, 3, 0)
    assert updater.parse_version("not-a-version") is None
    assert updater.parse_version("") is None
    print("  [PASS] parse_version")


def test_version_gt_numeric():
    assert updater.version_gt("1.10.0", "1.9.9") is True
    assert updater.version_gt("2.0.0", "1.99.99") is True
    assert updater.version_gt("1.2.3", "1.2.3") is False
    assert updater.version_gt("1.2.3", "1.2.4") is False
    # 长度不同补 0：1.2 == 1.2.0
    assert updater.version_gt("1.2.0", "1.2") is False
    assert updater.version_gt("1.2.1", "1.2") is True
    # 非法返回 False
    assert updater.version_gt("bad", "1.0.0") is False
    print("  [PASS] version_gt")


def test_allowed_asset_url():
    # 官方域名
    assert updater._is_allowed_asset_url("https://github.com/Niangaol/UsageMonitor/releases/download/v1.0/UsageMonitor.exe") is True
    assert updater._is_allowed_asset_url("https://objects.githubusercontent.com/xxx/UsageMonitor.exe") is True
    # 非 http/https 拒绝
    assert updater._is_allowed_asset_url("ftp://github.com/file.exe") is False
    # 空拒绝
    assert updater._is_allowed_asset_url("") is False
    # 自定义 api_base 域名放行
    assert updater._is_allowed_asset_url("https://example.com/file.exe", api_base="https://example.com/api") is True
    assert updater._is_allowed_asset_url("https://evil.com/file.exe", api_base="https://example.com/api") is False
    # 非法 url
    assert updater._is_allowed_asset_url("not a url") is False
    print("  [PASS] allowed_asset_url")


def test_build_update_script_contains_paths():
    src = r"C:\Temp\UsageMonitor.exe"
    dst = r"D:\App\UsageMonitor.exe"
    script = updater.build_update_script(src, dst)
    assert src.replace("'", "''") in script or src in script
    assert dst.replace("'", "''") in script or dst in script
    assert "Get-Process" in script
    assert "Copy-Item" in script
    print("  [PASS] build_update_script")


def test_update_request_signal(tmp_path):
    root = str(tmp_path / "upd_signal")
    os.makedirs(root, exist_ok=True)
    path = updater.update_request_path(root)
    assert path.endswith(".update-requested")
    updater.request_update(root)
    assert os.path.isfile(path)
    updater.clear_update_request(root)
    assert not os.path.isfile(path)
    # 重复清除不抛异常
    updater.clear_update_request(root)
    print("  [PASS] update_request_signal")


def test_check_for_update_offline_returns_error():
    # 用不可能连通的 api_base，验证返回 error 而非抛异常
    result = updater.check_for_update(current="1.0.0", api_base="http://127.0.0.1:1", timeout=1.0)
    assert result["current"] == "1.0.0"
    assert result["has_update"] is False
    assert result["error"] is not None
    print("  [PASS] check_for_update_offline")


def test_download_rejects_empty_url(tmp_path):
    try:
        updater.download("", str(tmp_path / "out.exe"))
        assert False, "should raise"
    except updater.UpdateError as exc:
        assert "为空" in str(exc)
    print("  [PASS] download_rejects_empty")


def test_download_rejects_disallowed_url(tmp_path):
    """download 本体复核白名单（纵深防御，不依赖调用方先经 latest_release 过滤）。"""
    # 白名单外域名直接拒绝（校验发生在任何网络请求之前）
    try:
        updater.download("https://evil.com/file.exe", str(tmp_path / "out.exe"))
        assert False, "should raise"
    except updater.UpdateError as exc:
        assert "白名单" in str(exc)
    assert not (tmp_path / "out.exe").exists()
    assert not (tmp_path / "out.exe.part").exists()
    # api_base 指定域名放行、其余仍拒绝
    try:
        updater.download("https://other.com/file.exe", str(tmp_path / "out2.exe"),
                         api_base="https://mirror.example")
        assert False, "should raise"
    except updater.UpdateError:
        pass
    print("  [PASS] download_rejects_disallowed")
