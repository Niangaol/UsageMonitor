# -*- coding: utf-8 -*-
"""tests/unit/test_adoption.py — 采纳率「Git 侧」代理指标（v3.0 · P9 SPIKE §5.3）单元测试。

覆盖：
  配置兜底（缺段/坏类型）、契约空态（关闭/无仓库/git_config 失败/当日无提交）；
  单仓库失败仅跳过该仓库（其余照常）；
  指标口径：retention = 新增/(新增+删除)、reworked_ratio = 删除/(新增+删除)（互补）；
  除零兜底（churn=0 → None + low）、confidence 规则（有数据 medium，绝不 high）；
  summary 聚合、per-file top_files 返工近似、强制免责声明；
  收敛断言：不再依赖 ai_sessions / 时间窗归因（spike 判砍的 AI 侧已移除）。

零依赖、确定性；不触发真实 git —— git_insights.git_config / analyze_repo 一律
monkeypatch（模拟单仓库失败/无提交等分支）。
"""

from __future__ import annotations

import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import adoption  # noqa: E402


def _repo_stats(repo_path: str, files: list[dict], name: str | None = None,
                commit_count: int = 1) -> dict:
    """fake git_insights.analyze_repo 返回值（对齐 git_insights.analyze_repo 输出）。"""
    added = sum(f.get("added", 0) for f in files)
    deleted = sum(f.get("deleted", 0) for f in files)
    return {
        "name": name or os.path.basename(repo_path.rstrip("\\/")), "path": repo_path,
        "commit_count": commit_count,
        "lines_added": added,
        "lines_deleted": deleted,
        "churn": added + deleted,
        "files": len(files),
        "top_files": [dict(f) for f in files],
        "authors": ["tester"],
        "modify_ratio": 0.0,
    }


def _patch_git(monkeypatch, stats_by_path: dict, fail_config: bool = False) -> None:
    """monkeypatch adoption.git_insights 两处。

    stats_by_path: {repo_path: stats_dict | None}；None 表示该仓库读取失败（仅跳过）。
    fail_config: True → git_config 抛异常（整源失败 → 契约空态）。
    """
    def fake_config(config):
        if fail_config:
            raise OSError("config broken")
        projects = [{"name": os.path.basename(p.rstrip("\\/")), "path": p}
                    for p in stats_by_path]
        return {"enabled": True, "projects": projects, "timeout_s": 10, "top_files": 5}

    def fake_analyze(repo, day, timeout, top_files):
        return stats_by_path.get(repo["path"])

    monkeypatch.setattr(adoption.git_insights, "git_config", fake_config)
    monkeypatch.setattr(adoption.git_insights, "analyze_repo", fake_analyze)


# ---------------------------------------------------------------------------
# adoption_config —— 默认兜底 / 覆盖 / 坏类型 / 下限
# ---------------------------------------------------------------------------
class TestAdoptionConfig:
    def test_defaults_when_missing(self):
        cfg = adoption.adoption_config({})
        assert cfg["enabled"] is True
        assert cfg["top_files"] == 100

    def test_override(self):
        cfg = adoption.adoption_config({"adoption": {"enabled": False, "top_files": 50}})
        assert cfg["enabled"] is False
        assert cfg["top_files"] == 50

    def test_bad_types_fall_back(self):
        cfg = adoption.adoption_config({"adoption": {"enabled": "x", "top_files": None}})
        assert cfg["enabled"] is True
        assert cfg["top_files"] == 100

    def test_top_files_min_1(self):
        assert adoption.adoption_config({"adoption": {"top_files": 0}})["top_files"] == 1


# ---------------------------------------------------------------------------
# 空数据 / 降级 —— 契约空态（found=False，200 可展示，非 500）
# ---------------------------------------------------------------------------
class TestEmptyDegradation:
    def test_disabled_returns_empty(self):
        res = adoption.adoption_stats("2026-08-20", "", {"adoption": {"enabled": False}})
        assert res["enabled"] is False
        assert res["found"] is False
        assert res["confidence"] == "low"
        assert res["projects"] == []
        assert "已关闭" in res["notice"]

    def test_no_git_repos_returns_empty(self, monkeypatch):
        def fake_config(config):
            return {"enabled": True, "projects": [], "timeout_s": 10, "top_files": 5}
        monkeypatch.setattr(adoption.git_insights, "git_config", fake_config)
        res = adoption.adoption_stats("2026-08-20", "", {})
        assert res["found"] is False
        assert res["confidence"] == "low"
        assert res["projects"] == []
        assert "未配置 Git 仓库" in res["notice"]

    def test_git_config_failure_returns_empty(self, monkeypatch):
        _patch_git(monkeypatch, {}, fail_config=True)
        res = adoption.adoption_stats("2026-08-20", "", {})
        assert res["found"] is False
        assert res["confidence"] == "low"
        assert res["projects"] == []

    def test_all_repos_fail_returns_empty(self, monkeypatch, tmp_path):
        """所有仓库读取失败 → 空态（不 500、不出现半成品）。"""
        repo = str(tmp_path / "broken")
        _patch_git(monkeypatch, {repo: None})
        res = adoption.adoption_stats("2026-08-20", "", {})
        assert res["found"] is False
        assert res["projects"] == []
        assert res["summary"]["retention"] is None

    def test_no_commits_returns_empty(self, monkeypatch, tmp_path):
        """当天无提交 → 空态。"""
        repo = str(tmp_path / "idle")
        stats = {repo: _repo_stats(repo, [{"path": "a.py", "added": 10, "deleted": 0}],
                                   commit_count=0)}
        _patch_git(monkeypatch, stats)
        res = adoption.adoption_stats("2026-08-20", "", {})
        assert res["found"] is False
        assert res["projects"] == []

    def test_empty_contract_keys(self):
        res = adoption._empty_result("2026-08-20")
        assert res["found"] is False and res["projects"] == []
        assert res["confidence"] == "low"
        assert res["summary"]["projects"] == 0
        assert res["summary"]["retention"] is None
        assert res["summary"]["reworked_ratio"] is None
        assert "非真实采纳率" in res["notice"]


# ---------------------------------------------------------------------------
# 单仓库失败 —— 仅跳过该仓库，其余照常
# ---------------------------------------------------------------------------
class TestSingleRepoFailureSkipped:
    def test_bad_repo_skipped_good_repo_kept(self, monkeypatch, tmp_path):
        good = str(tmp_path / "good")
        bad = str(tmp_path / "bad")
        stats = {
            good: _repo_stats(good, [{"path": "a.py", "added": 100, "deleted": 25}]),
            bad: None,
        }
        _patch_git(monkeypatch, stats)
        res = adoption.adoption_stats("2026-08-20", "", {})
        assert res["found"] is True
        assert len(res["projects"]) == 1
        assert res["projects"][0]["project"] == "good"
        assert res["summary"]["projects"] == 1


# ---------------------------------------------------------------------------
# 指标口径 —— retention / reworked_ratio / confidence
# ---------------------------------------------------------------------------
class TestMetrics:
    def test_retention_and_reworked_are_complements(self, monkeypatch, tmp_path):
        repo = str(tmp_path / "r")
        stats = {repo: _repo_stats(repo, [{"path": "a.py", "added": 300, "deleted": 100}])}
        _patch_git(monkeypatch, stats)
        res = adoption.adoption_stats("2026-08-20", "", {})
        proj = res["projects"][0]
        assert proj["retention"] == pytest.approx(300 / 400, abs=1e-4)     # 0.75
        assert proj["reworked_ratio"] == pytest.approx(100 / 400, abs=1e-4)  # 0.25
        assert proj["retention"] + proj["reworked_ratio"] == pytest.approx(1.0)
        assert res["summary"]["retention"] == pytest.approx(0.75, abs=1e-4)
        assert res["summary"]["reworked_ratio"] == pytest.approx(0.25, abs=1e-4)

    def test_confidence_medium_when_data_backed(self, monkeypatch, tmp_path):
        repo = str(tmp_path / "r")
        stats = {repo: _repo_stats(repo, [{"path": "a.py", "added": 10, "deleted": 0}])}
        _patch_git(monkeypatch, stats)
        res = adoption.adoption_stats("2026-08-20", "", {})
        assert res["confidence"] == "medium"
        assert res["projects"][0]["confidence"] == "medium"

    def test_confidence_never_high(self, monkeypatch, tmp_path):
        repo = str(tmp_path / "r")
        stats = {repo: _repo_stats(repo, [{"path": "a.py", "added": 999, "deleted": 1}])}
        _patch_git(monkeypatch, stats)
        res = adoption.adoption_stats("2026-08-20", "", {})
        assert res["confidence"] in ("low", "medium")
        assert all(p["confidence"] in ("low", "medium") for p in res["projects"])

    def test_zero_churn_gives_none_and_low(self, monkeypatch, tmp_path):
        """纯二进制/重命名（churn=0）→ 两值 None，confidence=low，不除零。"""
        repo = str(tmp_path / "r")
        stats = {repo: _repo_stats(repo, [{"path": "bin.dat", "added": 0, "deleted": 0}])}
        _patch_git(monkeypatch, stats)
        res = adoption.adoption_stats("2026-08-20", "", {})
        proj = res["projects"][0]
        assert proj["retention"] is None
        assert proj["reworked_ratio"] is None
        assert proj["confidence"] == "low"
        assert res["confidence"] == "low"
        assert res["summary"]["retention"] is None
        assert res["summary"]["reworked_ratio"] is None


# ---------------------------------------------------------------------------
# summary 聚合 —— 多仓库口径
# ---------------------------------------------------------------------------
class TestSummaryAggregation:
    def test_summary_totals_and_averages(self, monkeypatch, tmp_path):
        r1, r2 = str(tmp_path / "r1"), str(tmp_path / "r2")
        stats = {
            r1: _repo_stats(r1, [{"path": "a.py", "added": 100, "deleted": 0}], name="r1"),
            r2: _repo_stats(r2, [{"path": "b.py", "added": 50, "deleted": 50}], name="r2"),
        }
        _patch_git(monkeypatch, stats)
        res = adoption.adoption_stats("2026-08-20", "", {})
        s = res["summary"]
        assert s["projects"] == 2
        assert s["files"] == 2
        assert s["commit_count"] == 2
        assert s["lines_added"] == 150
        assert s["lines_deleted"] == 50
        assert s["churn"] == 200
        # retention 均值 = (1.0 + 0.5) / 2 = 0.75；reworked 均值 = (0.0 + 0.5) / 2 = 0.25
        assert s["retention"] == pytest.approx(0.75, abs=1e-4)
        assert s["reworked_ratio"] == pytest.approx(0.25, abs=1e-4)
        assert res["confidence"] == "medium"


# ---------------------------------------------------------------------------
# per-file top_files —— 返工近似 + 免责声明
# ---------------------------------------------------------------------------
class TestPerFileAndNotice:
    def test_top_files_reworked_ratio(self, monkeypatch, tmp_path):
        repo = str(tmp_path / "r")
        stats = {repo: _repo_stats(repo, [
            {"path": "a.py", "added": 100, "deleted": 50},
            {"path": "b.py", "added": 0, "deleted": 30},
        ])}
        _patch_git(monkeypatch, stats)
        res = adoption.adoption_stats("2026-08-20", "", {})
        by_path = {f["path"]: f for f in res["projects"][0]["top_files"]}
        assert by_path["a.py"]["reworked_ratio"] == pytest.approx(50 / 150, abs=1e-4)
        assert by_path["a.py"]["churn"] == 150
        assert by_path["b.py"]["reworked_ratio"] == 1.0
        assert by_path["b.py"]["deleted"] == 30

    def test_disclaimer_present_when_found(self, monkeypatch, tmp_path):
        repo = str(tmp_path / "r")
        stats = {repo: _repo_stats(repo, [{"path": "a.py", "added": 10, "deleted": 0}])}
        _patch_git(monkeypatch, stats)
        res = adoption.adoption_stats("2026-08-20", "", {})
        assert "非真实采纳率" in res["notice"]
        assert "仅供参考" in res["notice"]
        assert res["notice"]  # 非空强制声明

    def test_no_ai_sessions_dependency(self):
        """收敛后不依赖 AI 会话 / 时间窗归因（spike 判砍的 AI 侧已移除）。"""
        assert not hasattr(adoption, "ai_sessions")
        assert not hasattr(adoption, "_collect_ai")
        assert not hasattr(adoption, "_in_window")
        assert not hasattr(adoption, "_fuzzy_match")
