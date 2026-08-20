# -*- coding: utf-8 -*-
"""tests/integration/test_timeline_pipeline.py — Vibe 时间轴回放端到端（v2.5）。

真实 git 仓库（2 commits）+ usage.jsonl（AI 相关段）+ 注入 ai_sessions.conversations，
验证 build_timeline 三源合并/排序/summary，以及 /api/timeline HTTP 契约。
config 控制在数据根目录：AI 深度 paths 指向空目录，git 指向测试仓库。
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402
import dashboard  # noqa: E402
import timeline  # noqa: E402


def _init_repo(path: str, day: str) -> None:
    """初始化测试仓库并造 2 个当日提交（10:05 / 11:45）。"""
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "t@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=path, check=True)
    with open(os.path.join(path, "a.py"), "w", encoding="utf-8") as fh:
        fh.write("print(1)\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, stdout=subprocess.DEVNULL)
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = f"{day}T10:05:00"
    env["GIT_COMMITTER_DATE"] = f"{day}T10:05:00"
    subprocess.run(["git", "commit", "-m", "feat: init"], cwd=path, check=True,
                   env=env, stdout=subprocess.DEVNULL)
    with open(os.path.join(path, "a.py"), "w", encoding="utf-8") as fh:
        fh.write("print(1)\nprint(2)\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, stdout=subprocess.DEVNULL)
    env["GIT_AUTHOR_DATE"] = f"{day}T11:45:00"
    env["GIT_COMMITTER_DATE"] = f"{day}T11:45:00"
    subprocess.run(["git", "commit", "-m", "feat: more"], cwd=path, check=True,
                   env=env, stdout=subprocess.DEVNULL)


def _write_usage(root: str, day: str) -> None:
    """4 条记录：3 条 AI 相关（应进时间轴）+ 1 条社交（应被过滤）。"""
    d = os.path.join(root, day)
    os.makedirs(d, exist_ok=True)
    rows = [
        {"start": f"{day}T09:00:00", "end": f"{day}T09:10:00", "duration_ms": 600000,
         "exe": "code.exe", "app": "VS Code", "title": "VibeTrace/dashboard.py",
         "category": "AI编程", "contact": None, "ai_tool": "opencode", "active": True},
        {"start": f"{day}T09:30:00", "end": f"{day}T09:35:00", "duration_ms": 300000,
         "exe": "code.exe", "app": "VS Code", "title": "VibeTrace/timeline.py",
         "category": "开发工具", "contact": None, "ai_tool": "opencode", "active": True},
        {"start": f"{day}T14:00:00", "end": f"{day}T14:40:00", "duration_ms": 2400000,
         "exe": "wechat.exe", "app": "微信", "title": "同学群", "category": "社交聊天",
         "contact": None, "ai_tool": None, "active": True},
        {"start": f"{day}T15:00:00", "end": f"{day}T15:07:00", "duration_ms": 420000,
         "exe": "browser.exe", "app": "Chrome", "title": "AI 文档", "category": "浏览器",
         "contact": None, "ai_tool": "chatgpt", "active": True},
    ]
    with open(os.path.join(d, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _req(port: int, path: str, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    conn.request("GET", path, headers=headers or {})
    r = conn.getresponse()
    body = r.read()
    conn.close()
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        data = {}
    return r.status, data


def _server(root):
    server = dashboard.create_server(root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def test_timeline_pipeline_full(tmp_path, monkeypatch):
    day = "2026-08-09"
    root = str(tmp_path / "tl_root")
    repo = str(tmp_path / "tl_repo")
    empty_ai = str(tmp_path / "empty_ai")
    os.makedirs(root, exist_ok=True)
    os.makedirs(empty_ai, exist_ok=True)
    _init_repo(repo, day)
    _write_usage(root, day)
    config = {
        "ai_sessions": {"enabled": True, "paths": {"opencode": [empty_ai]}},
        "insights": {"enabled": True, "git": {
            "enabled": True, "projects": [repo], "timeout_s": 5, "top_files": 5}},
        "vibe_timeline": {"enabled": True, "merge_gap_s": 120},
    }
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False)

    # 注入 ai_sessions.collect：模拟一条当日会话深度（命中 09:00 前台段）
    def fake_collect(date_str, cfg, web_visits=None):
        return {"date": date_str, "enabled": True, "found": True, "tools": {}, "total": {
            "conversations": [
                {"id": "c1", "tool": "opencode", "model": "deepseek-v4-pro",
                 "project": "VibeTrace", "turns": 4, "rounds": 2,
                 "tokens_total": 12000, "cost_total": 0.31, "generated_lines": 180,
                 "first": f"{day}T09:03:00", "last": f"{day}T09:08:00"},
            ]}}
    monkeypatch.setattr(ai_sessions, "collect", fake_collect)

    # ---- 函数级：三源合并 ----
    out = timeline.build_timeline(day, root, config)
    types = [e["type"] for e in out["events"]]
    assert types.count("session") == 3, f"应 3 条 AI 前台会话: {types}"
    assert types.count("git_commit") == 2, f"应 2 个提交: {types}"
    assert types.count("ai_session") == 1, f"应 1 个会话深度: {types}"
    times = [e["time"] for e in out["events"]]
    assert times == sorted(times), f"事件未按时间递增: {times}"
    s = out["summary"]
    assert s["commit_count"] == 2 and s["ai_blocks"] == 3 and s["conversations"] == 1
    assert s["ai_minutes"] == 22.0  # (600000+300000+420000) ms = 22 分钟
    assert s["churn"] > 0 and abs(s["total_cost"] - 0.31) < 1e-9
    # git 提交落在正确时间点（时区 +0800 → 本地）
    gc = [e for e in out["events"] if e["type"] == "git_commit"]
    assert gc[0]["time"] == "10:05:00" and gc[1]["time"] == "11:45:00"
    # 前台段 detail 标注命中会话深度数
    sess0 = next(e for e in out["events"] if e["type"] == "session" and e["time"] == "09:00:00")
    assert sess0["detail"]["ai_convs"] == 1
    sess1 = next(e for e in out["events"] if e["type"] == "session" and e["time"] == "15:00:00")
    assert sess1["detail"].get("ai_convs", 0) == 0

    # ---- HTTP 层：/api/timeline 契约 ----
    server, port = _server(root)
    try:
        s, d = _req(port, "/api/timeline?date=bad")
        assert s == 400 and "error" in d
        s, d = _req(port, f"/api/timeline?date={day}")
        assert s == 200
        assert d["date"] == day and len(d["events"]) == 6
        # project 过滤：repo 名不含 VibeTrace → commit 被剔除；15:00 的 Chrome 会话
        # 标题/应用均不含 VibeTrace → 也被剔除；其余 3 条保留（2 session + 1 ai_session）
        s, d = _req(port, f"/api/timeline?date={day}&project=VibeTrace")
        assert s == 200 and d["events"]
        assert all(e["type"] != "git_commit" for e in d["events"])
        assert len(d["events"]) == 3
    finally:
        server.shutdown()
        server.server_close()
    print("  [PASS] timeline_pipeline_full")


def test_timeline_git_disabled_and_no_repos(tmp_path):
    """git 未配置 / 关闭 → commit 源降级为空，不拖垮时间轴。"""
    root = str(tmp_path / "tl5")
    empty_ai = str(tmp_path / "empty_ai5")
    os.makedirs(root, exist_ok=True)
    os.makedirs(empty_ai, exist_ok=True)
    config = {"ai_sessions": {"enabled": True, "paths": {"opencode": [empty_ai]}},
              "insights": {"enabled": True, "git": {"enabled": False, "projects": []}},
              "vibe_timeline": {"enabled": True}}
    assert timeline._collect_git_commits(config, "2026-08-09") == []
    empty = {"insights": {"enabled": True, "git": {"enabled": True, "projects": []}}}
    assert timeline._collect_git_commits(empty, "2026-08-09") == []
    print("  [PASS] timeline_git_disabled_and_no_repos")