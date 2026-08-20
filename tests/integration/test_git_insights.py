# -*- coding: utf-8 -*-
"""tests/integration/test_git_insights.py — 本地 git 提交只读分析."""

from __future__ import annotations

import os
import subprocess
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import git_insights  # noqa: E402


def _init_repo(path: str, day: str = "2026-08-08"):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    # 提交 1
    with open(os.path.join(path, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("hello\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, stdout=subprocess.DEVNULL)
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = f"{day}T10:00:00"
    env["GIT_COMMITTER_DATE"] = f"{day}T10:00:00"
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, env=env, stdout=subprocess.DEVNULL)
    # 提交 2 — 修改 + 新增
    with open(os.path.join(path, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("hello world\nnew line\n")
    with open(os.path.join(path, "b.txt"), "w", encoding="utf-8") as fh:
        fh.write("another file\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, stdout=subprocess.DEVNULL)
    env["GIT_AUTHOR_DATE"] = f"{day}T15:00:00"
    env["GIT_COMMITTER_DATE"] = f"{day}T15:00:00"
    subprocess.run(["git", "commit", "-m", "update"], cwd=path, check=True, env=env, stdout=subprocess.DEVNULL)


def test_git_insights_basic(tmp_path):
    repo = str(tmp_path / "repo1")
    day = "2026-08-08"
    _init_repo(repo, day)
    cfg = {"insights": {"enabled": True, "git": {"enabled": True, "projects": [repo], "top_files": 5, "timeout_s": 5}}}
    result = git_insights.git_insights(cfg, day)
    assert result.get("found") is True
    total = result.get("total", {})
    assert total.get("commit_count") == 2
    assert total.get("lines_added", 0) > 0
    assert total.get("churn", 0) > 0
    # repos 明细
    assert len(result.get("repos", [])) == 1
    print("  [PASS] git_insights_basic")


def test_git_insights_empty_day(tmp_path):
    repo = str(tmp_path / "repo2")
    _init_repo(repo, "2026-08-08")
    cfg = {"insights": {"enabled": True, "git": {"enabled": True, "projects": [repo], "timeout_s": 5}}}
    result = git_insights.git_insights(cfg, "2099-01-01")
    # 当天无提交 -> commit_count 0 但不报错
    total = result.get("total", {})
    assert total.get("commit_count", 0) == 0
    print("  [PASS] git_insights_empty_day")


def test_git_insights_disabled():
    cfg = {"insights": {"enabled": False}}
    result = git_insights.git_insights(cfg, "2026-08-08")
    assert result.get("found") is False or result.get("total", {}).get("commit_count", 0) == 0
    print("  [PASS] git_insights_disabled")


def test_git_insights_no_repo(tmp_path):
    empty = str(tmp_path / "empty")
    os.makedirs(empty, exist_ok=True)
    cfg = {"insights": {"enabled": True, "git": {"enabled": True, "projects": [empty], "timeout_s": 2}}}
    result = git_insights.git_insights(cfg, "2026-08-08")
    # 非仓库路径 -> 优雅降级
    assert isinstance(result, dict)
    print("  [PASS] git_insights_no_repo")


def test_git_parse_numstat():
    raw = "\x1eabc123\x1f2026-08-08 10:00:00 +0800\x1fTest\n2\t1\ta.txt\n5\t0\tb.txt\n-\t-\tbinary.png\n"
    parsed = git_insights._parse_numstat(raw)
    assert len(parsed) == 1
    assert parsed[0]["hash"] == "abc123"
    assert len(parsed[0]["files"]) == 2
    assert parsed[0]["files"][0]["added"] == 2
    print("  [PASS] parse_numstat")
