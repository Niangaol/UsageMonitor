# -*- coding: utf-8 -*-
"""tests/unit/test_updater_extended.py — updater 网络解析与更新执行路径深度测试。

覆盖 latest_release（mock urlopen，绝不真实联网）、check_for_update 判定逻辑、
download 成功落盘与校验失败清理、apply_update dry_run 脚本生成与开发模式拒绝。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import updater  # noqa: E402


class _FakeResponse:
    """模拟 urlopen 返回的响应对象：支持上下文管理器 / headers.get / read(chunk)。"""

    def __init__(self, data: bytes):
        self._data = data
        self.headers = {"Content-Length": str(len(data))}

    def read(self, size: int = -1) -> bytes:
        # 按请求大小切片并前移游标，模拟流式读取
        if size is None or size < 0:
            size = len(self._data)
        chunk = self._data[:size]
        self._data = self._data[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _release_payload(tag="v1.8.0", asset_name="VibeTrace.exe",
                     asset_url="https://github.com/Niangaol/VibeTrace/releases/download/v1.8.0/VibeTrace.exe"):
    """构造一份合法的 GitHub Releases JSON payload。"""
    return {
        "tag_name": tag,
        "name": f"Release {tag}",
        "html_url": f"https://github.com/Niangaol/VibeTrace/releases/tag/{tag}",
        "published_at": "2026-08-01T12:00:00Z",
        "body": "修复若干问题",
        "assets": [{
            "name": asset_name,
            "size": 2048,
            "digest": "sha256:" + "ab" * 32,
            "browser_download_url": asset_url,
        }],
    }


def _patch_urlopen(monkeypatch, responder):
    """把 fake 响应/异常挂到 updater.urllib.request.urlopen 上，返回调用记录列表。"""
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append({"url": getattr(request, "full_url", str(request)), "timeout": timeout})
        result = responder()
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen)
    return calls


# ---------------------------------------------------------------------------
# latest_release：网络解析路径
# ---------------------------------------------------------------------------
def test_latest_release_parses_payload(monkeypatch):
    payload = _release_payload()
    calls = _patch_urlopen(monkeypatch, lambda: _FakeResponse(json.dumps(payload).encode("utf-8")))
    info = updater.latest_release(api_base="https://example.com/api/")
    # 请求地址应为 api_base 去掉尾部斜杠
    assert calls[0]["url"] == "https://example.com/api"
    # 元数据字段正确解析
    assert info["tag"] == "v1.8.0"
    assert info["name"] == "Release v1.8.0"
    assert info["notes"] == "修复若干问题"
    assert info["published_at"] == "2026-08-01T12:00:00Z"
    assert info["url"] == payload["html_url"]
    # 资产字段正确解析且名称归一化为 ASSET_NAME
    asset = info["asset"]
    assert asset is not None
    assert asset["name"] == updater.ASSET_NAME
    assert asset["size"] == 2048
    assert asset["digest"] == "sha256:" + "ab" * 32
    assert asset["url"] == payload["assets"][0]["browser_download_url"]
    print("  [PASS] latest_release_parses_payload")


def test_latest_release_disallowed_asset_url(monkeypatch):
    # 资产下载地址不在白名单内 → asset 为 None（调用方视为无可用更新）
    payload = _release_payload(asset_url="https://evil.com/VibeTrace.exe")
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(json.dumps(payload).encode("utf-8")))
    info = updater.latest_release()
    assert info["tag"] == "v1.8.0"
    assert info["asset"] is None
    print("  [PASS] latest_release_disallowed_asset_url")


def test_latest_release_no_matching_asset(monkeypatch):
    # assets 中没有 VibeTrace.exe / UsageMonitor.exe 名 → asset 为 None
    payload = _release_payload()
    payload["assets"] = [{"name": "setup.msi", "size": 1,
                          "browser_download_url": "https://github.com/Niangaol/VibeTrace/setup.msi"}]
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(json.dumps(payload).encode("utf-8")))
    info = updater.latest_release()
    assert info["asset"] is None
    # 完全没有 assets 字段同样视为无资产
    bare = {"tag_name": "v9.9.9"}
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(json.dumps(bare).encode("utf-8")))
    info2 = updater.latest_release()
    assert info2["asset"] is None
    print("  [PASS] latest_release_no_matching_asset")


def test_latest_release_legacy_asset_name_normalized(monkeypatch):
    # 旧名 UsageMonitor.exe 也能识别，且归一化输出为 ASSET_NAME
    legacy_url = "https://github.com/Niangaol/VibeTrace/releases/download/v1.8.0/UsageMonitor.exe"
    payload = _release_payload(asset_name="UsageMonitor.exe", asset_url=legacy_url)
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(json.dumps(payload).encode("utf-8")))
    info = updater.latest_release()
    assert info["asset"] is not None
    assert info["asset"]["name"] == updater.ASSET_NAME
    assert info["asset"]["url"] == legacy_url
    print("  [PASS] latest_release_legacy_asset_name_normalized")


def test_latest_release_http_error(monkeypatch):
    # HTTPError → UpdateError 且信息含 "HTTP"
    def responder():
        return urllib.error.HTTPError("https://api.github.com/x", 403, "Forbidden", None, None)

    _patch_urlopen(monkeypatch, responder)
    with pytest.raises(updater.UpdateError) as excinfo:
        updater.latest_release()
    assert "HTTP" in str(excinfo.value)
    assert "403" in str(excinfo.value)
    print("  [PASS] latest_release_http_error")


def test_latest_release_url_error(monkeypatch):
    # URLError（连不上）→ UpdateError
    def responder():
        return urllib.error.URLError("connection refused")

    _patch_urlopen(monkeypatch, responder)
    with pytest.raises(updater.UpdateError):
        updater.latest_release()
    print("  [PASS] latest_release_url_error")


def test_latest_release_invalid_json(monkeypatch):
    # 返回非 JSON → UpdateError
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(b"<html>not json</html>"))
    with pytest.raises(updater.UpdateError) as excinfo:
        updater.latest_release()
    assert "JSON" in str(excinfo.value)
    print("  [PASS] latest_release_invalid_json")


def test_latest_release_non_dict_json(monkeypatch):
    # JSON 合法但不是 dict（如数组）→ UpdateError
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(b"[1, 2, 3]"))
    with pytest.raises(updater.UpdateError) as excinfo:
        updater.latest_release()
    assert "格式" in str(excinfo.value)
    print("  [PASS] latest_release_non_dict_json")


# ---------------------------------------------------------------------------
# check_for_update：判定逻辑
# ---------------------------------------------------------------------------
def test_check_for_update_has_update(monkeypatch):
    # 远端 tag 更新且有可用资产 → has_update True
    payload = _release_payload(tag="v1.8.0")
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(json.dumps(payload).encode("utf-8")))
    result = updater.check_for_update(current="1.7.0")
    assert result["error"] is None
    assert result["has_update"] is True
    assert result["latest"] == "1.8.0"
    assert result["asset"]["url"].endswith("VibeTrace.exe")
    print("  [PASS] check_for_update_has_update")


def test_check_for_update_same_older_or_no_asset(monkeypatch):
    # tag 相同 → False
    payload = _release_payload(tag="v1.8.0")
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(json.dumps(payload).encode("utf-8")))
    assert updater.check_for_update(current="1.8.0")["has_update"] is False
    # tag 更旧 → False
    older = _release_payload(tag="v1.7.0")
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(json.dumps(older).encode("utf-8")))
    assert updater.check_for_update(current="1.8.0")["has_update"] is False
    # tag 更新但资产不可用（白名单外）→ False
    no_asset = _release_payload(tag="v9.0.0", asset_url="https://evil.com/VibeTrace.exe")
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(json.dumps(no_asset).encode("utf-8")))
    result = updater.check_for_update(current="1.0.0")
    assert result["asset"] is None
    assert result["has_update"] is False
    print("  [PASS] check_for_update_same_older_or_no_asset")


def test_check_for_update_strips_v_prefix(monkeypatch):
    # latest 字段经 lstrip 后不带 "v" 前缀（避免展示层拼出 "vv1.x"）
    payload = _release_payload(tag="2.0.0")  # 远端 tag 本身就没有 v 前缀
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(json.dumps(payload).encode("utf-8")))
    result = updater.check_for_update(current="1.0.0")
    assert result["latest"] == "2.0.0"
    assert not result["latest"].startswith("v")
    assert result["has_update"] is True
    print("  [PASS] check_for_update_strips_v_prefix")


# ---------------------------------------------------------------------------
# download：成功落盘与校验失败
# ---------------------------------------------------------------------------
def test_download_success(tmp_path, monkeypatch):
    data = bytes(range(256)) * 64  # 16384 字节，配合小 chunk 触发多轮读取/回调
    digest = hashlib.sha256(data).hexdigest()
    dest = tmp_path / "nested" / "VibeTrace.exe"  # 目录不存在，验证自动创建
    progress_calls = []
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(data))
    result = updater.download(
        "https://example.com/VibeTrace.exe", str(dest),
        expected_size=len(data),
        expected_digest="sha256:" + digest.upper(),  # 大写摘要也应通过（比较不区分大小写）
        progress=lambda got, total: progress_calls.append((got, total)),
        chunk=1000,
        api_base="https://example.com",
    )
    assert result == str(dest)
    # 内容完整落盘，sha256 校验通过（未抛异常即通过）
    assert dest.read_bytes() == data
    # 无 .part 残留（原子替换成功）
    assert not os.path.exists(str(dest) + ".part")
    # progress 回调被多次调用，终点字节数与 total 正确
    assert len(progress_calls) >= 2
    assert progress_calls[-1] == (len(data), len(data))
    print("  [PASS] download_success")


def test_download_digest_mismatch_cleans_part(tmp_path, monkeypatch):
    data = b"A" * 4096
    dest = tmp_path / "out.exe"
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(data))
    bad_digest = "sha256:" + "00" * 32
    with pytest.raises(updater.UpdateError) as excinfo:
        updater.download("https://example.com/out.exe", str(dest),
                         expected_digest=bad_digest, api_base="https://example.com")
    assert "SHA256" in str(excinfo.value)
    # 失败后 .part 已清理，目标文件不存在
    assert not os.path.exists(str(dest) + ".part")
    assert not dest.exists()
    print("  [PASS] download_digest_mismatch_cleans_part")


def test_download_size_mismatch_cleans_part(tmp_path, monkeypatch):
    data = b"B" * 2048
    dest = tmp_path / "out.exe"
    _patch_urlopen(monkeypatch, lambda: _FakeResponse(data))
    with pytest.raises(updater.UpdateError) as excinfo:
        updater.download("https://example.com/out.exe", str(dest),
                         expected_size=len(data) + 1, api_base="https://example.com")
    assert "大小" in str(excinfo.value)
    assert not os.path.exists(str(dest) + ".part")
    assert not dest.exists()
    print("  [PASS] download_size_mismatch_cleans_part")


# ---------------------------------------------------------------------------
# apply_update：dry_run 与开发模式拒绝
# ---------------------------------------------------------------------------
def test_apply_update_dry_run_generates_script(tmp_path, monkeypatch):
    src_file = tmp_path / "download" / "VibeTrace.exe"
    dst_file = tmp_path / "install" / "VibeTrace.exe"
    src_file.parent.mkdir(parents=True)
    dst_file.parent.mkdir(parents=True)
    src_file.write_bytes(b"new-binary")
    dst_file.write_bytes(b"old-binary")
    # 把临时目录指到 tmp_path，便于检查生成的脚本文件
    fake_tmp = tmp_path / "tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(fake_tmp))
    info = updater.apply_update(str(src_file), str(dst_file), dry_run=True)
    assert info["dry_run"] is True
    script_path = info["script"]
    assert os.path.isfile(script_path)
    # 脚本以 UTF-8 BOM 写出，内容含 Copy-Item 与原始路径
    content = open(script_path, encoding="utf-8-sig").read()
    assert "Copy-Item" in content
    assert str(src_file) in content
    assert str(dst_file) in content
    print("  [PASS] apply_update_dry_run_generates_script")


def test_apply_update_dry_run_escapes_single_quotes(tmp_path, monkeypatch):
    # src/dst 路径含单引号时，脚本中应转义为双单引号（PowerShell 单引号字符串规则）
    src_file = tmp_path / "down'load dir" / "it's.exe"
    src_file.parent.mkdir(parents=True)
    src_file.write_bytes(b"x")
    dst_file = tmp_path / "app" / "VibeTrace.exe"
    dst_file.parent.mkdir(parents=True)
    dst_file.write_bytes(b"y")
    fake_tmp = tmp_path / "tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(fake_tmp))
    info = updater.apply_update(str(src_file), str(dst_file), dry_run=True)
    content = open(info["script"], encoding="utf-8-sig").read()
    # 单引号全部翻倍
    assert "down''load dir" in content
    assert "it''s.exe" in content
    # 不存在未转义的单引号路径片段
    assert "down'load" not in content
    assert "it's.exe" not in content
    print("  [PASS] apply_update_dry_run_escapes_single_quotes")


def test_apply_update_rejected_when_not_frozen(monkeypatch, tmp_path):
    # 非打包环境（is_frozen False）不带 dry_run → UpdateError 提示开发模式
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    with pytest.raises(updater.UpdateError) as excinfo:
        updater.apply_update(str(tmp_path / "a.exe"))
    assert "开发模式" in str(excinfo.value)
    print("  [PASS] apply_update_rejected_when_not_frozen")
