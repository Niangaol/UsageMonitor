# -*- coding: utf-8 -*-
"""tests/security/test_hardening.py — 安全加固回归。

覆盖：
1. 备份恢复永不覆写 update.api_base（更新供应链信任链）
2. GET /api/insights/ai 只读缓存，绝不触发付费重生成（成本型 CSRF 收敛）
3. 页面 CSP 使用 per-request nonce，script-src 不再依赖 unsafe-inline
4. 前端 markdown 链接 scheme 白名单（safeHref）存在于模板
"""

from __future__ import annotations

import http.client
import io
import json
import os
import re
import shutil
import sys
import threading
import zipfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import dashboard  # noqa: E402
import insights  # noqa: E402


def _req(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    raw = r.read()
    hdr = dict(r.getheaders())
    conn.close()
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        data = {"_raw": raw.decode("utf-8", errors="ignore")}
    return r.status, data, hdr


def _start_server(tmp_path, name):
    root = str(tmp_path / name)
    os.makedirs(root, exist_ok=True)
    server = dashboard.create_server(root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, root, port


# ---------------------------------------------------------------------------
# 1) 恢复净化：update.api_base 以本机为准
# ---------------------------------------------------------------------------
def _make_backup(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_restore_never_overrides_update_api_base(tmp_path):
    """恶意备份把 api_base 改指攻击者域名时，恢复后仍保留本机现值。"""
    server, root, _ = _start_server(tmp_path, "restore_evil")
    try:
        local = {"update": {"api_base": "https://official.example"}}
        with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(local, fh)
        evil = {"update": {"api_base": "https://evil.example"}, "dashboard_token": "pwn"}
        data = _make_backup({
            "config.json": json.dumps(evil),
            "2099-03-04/usage.jsonl": "{}\n",
        })
        tmp_dir = dashboard._safe_extract_zip(root, data)
        try:
            result = dashboard._merge_restore(root, tmp_dir)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        assert "2099-03-04" in result["days"]
        assert "config.json" in result["files"]
        with open(os.path.join(root, "config.json"), "r", encoding="utf-8") as fh:
            restored = json.load(fh)
        assert restored["update"]["api_base"] == "https://official.example"
        print("  [PASS] restore_never_overrides_update_api_base")
    finally:
        server.shutdown()
        server.server_close()


def test_restore_drops_api_base_when_local_absent(tmp_path):
    """本机 config 无 api_base 时，恢复后也不得引入该键。"""
    server, root, _ = _start_server(tmp_path, "restore_clean")
    try:
        with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"dashboard_token": ""}, fh)
        evil = {"update": {"api_base": "https://evil.example"}}
        data = _make_backup({"config.json": json.dumps(evil)})
        tmp_dir = dashboard._safe_extract_zip(root, data)
        try:
            dashboard._merge_restore(root, tmp_dir)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        with open(os.path.join(root, "config.json"), "r", encoding="utf-8") as fh:
            restored = json.load(fh)
        assert "api_base" not in restored.get("update", {})
        print("  [PASS] restore_drops_api_base_when_local_absent")
    finally:
        server.shutdown()
        server.server_close()


def test_restore_skips_corrupt_config_but_keeps_data(tmp_path):
    """备份内 config.json 损坏时跳过该文件，其余数据照常恢复。"""
    server, root, _ = _start_server(tmp_path, "restore_bad")
    try:
        data = _make_backup({
            "config.json": "{not valid json",
            "2099-03-05/usage.jsonl": "{}\n",
        })
        tmp_dir = dashboard._safe_extract_zip(root, data)
        try:
            result = dashboard._merge_restore(root, tmp_dir)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        assert "config.json" not in result["files"]
        assert "2099-03-05" in result["days"]
        assert not os.path.exists(os.path.join(root, "config.json"))
        print("  [PASS] restore_skips_corrupt_config_but_keeps_data")
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# 2) AI 洞察：GET 只读缓存，POST 才允许 refresh
# ---------------------------------------------------------------------------
def test_get_insights_ai_never_refreshes(tmp_path, monkeypatch):
    """GET /api/insights/ai?refresh=1 不得触发重生成（refresh 恒为 False）。"""
    server, root, port = _start_server(tmp_path, "ai_get")
    try:
        with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"insights": {"enabled": True, "ai": {"enabled": True}}}, fh)
        calls: list[dict] = []

        def fake_ai_insights(date, root_, config, refresh=False):
            calls.append({"date": date, "refresh": refresh})
            return {"insights": [], "generated_at": None, "model": None, "error": None}

        monkeypatch.setattr(insights, "ai_insights", fake_ai_insights)
        s, d, _ = _req(port, "GET", "/api/insights/ai?date=2099-01-01&refresh=1")
        assert s == 200 and d.get("ai_enabled") is True
        assert len(calls) == 1 and calls[0]["refresh"] is False
        print("  [PASS] get_insights_ai_never_refreshes")
    finally:
        server.shutdown()
        server.server_close()


def test_post_insights_ai_can_refresh(tmp_path, monkeypatch):
    """POST /api/insights/ai + body {"refresh": true} 才触发重生成。"""
    server, root, port = _start_server(tmp_path, "ai_post")
    try:
        with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"insights": {"enabled": True, "ai": {"enabled": True}}}, fh)
        calls: list[dict] = []

        def fake_ai_insights(date, root_, config, refresh=False):
            calls.append({"date": date, "refresh": refresh})
            return {"insights": [], "generated_at": None, "model": None, "error": None}

        monkeypatch.setattr(insights, "ai_insights", fake_ai_insights)
        payload = json.dumps({"refresh": True}).encode("utf-8")
        s, d, _ = _req(port, "POST", "/api/insights/ai?date=2099-01-01",
                       headers={"Content-Type": "application/json",
                                "Content-Length": str(len(payload))},
                       body=payload)
        assert s == 200 and d.get("ai_enabled") is True
        assert len(calls) == 1 and calls[0]["refresh"] is True
        print("  [PASS] post_insights_ai_can_refresh")
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# 3) 页面 CSP nonce
# ---------------------------------------------------------------------------
def test_page_csp_uses_per_request_nonce(tmp_path):
    """页面 CSP 带 per-request nonce；script-src 无 unsafe-inline；nonce 与脚本标签一致。"""
    server, _, port = _start_server(tmp_path, "csp_nonce")
    try:
        s1, b1, h1 = _req(port, "GET", "/")
        s2, _, h2 = _req(port, "GET", "/")
        assert s1 == 200 and s2 == 200
        csp1 = h1.get("Content-Security-Policy", "")
        csp2 = h2.get("Content-Security-Policy", "")
        m1 = re.search(r"script-src[^;]*'nonce-([A-Za-z0-9_-]+)'", csp1)
        assert m1, f"CSP 缺少 script nonce: {csp1}"
        script_directive = re.search(r"script-src[^;]*", csp1).group(0)
        assert "unsafe-inline" not in script_directive
        # per-request：两次请求 nonce 不同
        assert m1.group(1) not in csp2
        # 页面内联脚本带同一 nonce
        page_html = b1.get("_raw", "") if isinstance(b1, dict) else ""
        assert f'<script nonce="{m1.group(1)}">' in page_html
        print("  [PASS] page_csp_uses_per_request_nonce")
    finally:
        server.shutdown()
        server.server_close()


def test_template_has_link_scheme_whitelist():
    """前端 markdown 链接渲染必须经 safeHref 白名单（防 javascript: 存储型 XSS）。"""
    html = dashboard.load_page_template()
    assert "function safeHref" in html, "模板缺少 safeHref 白名单函数"
    assert "safeHref(b)" in html, "md2html 链接未经过 safeHref 校验"
    print("  [PASS] template_has_link_scheme_whitelist")
