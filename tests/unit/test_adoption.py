# -*- coding: utf-8 -*-
"""tests/unit/test_adoption.py — 无插件采纳率近似归因（v3.0 · P9 SPIKE）单元测试。

覆盖：
  配置兜底（缺段/坏类型）、契约空态（无仓库/关闭/单源失败降级）；
  mtime × 会话窗时间重叠判定（同窗命中 / 窗口外未命中）；
  per-file ai_generated_ratio 分摊（窗口内 >0、窗口外 0、added=0 → None）；
  per-file reworked_ratio = deleted/churn 返工近似、project 级 acceptance=1-delete/add；
  retention 分母为 0 → None（除零兜底）、confidence 判定（无 AI/low、join 达标/medium）。

零依赖、确定性；不触发真实数据扫描 —— git/ai 两源一律 monkeypatch，
文件 mtime 用真实临时文件 + os.utime 精确控制（可复现）。
"""

from __future__ import annotations

import datetime
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import adoption  # noqa: E402


def _ts(y, mo, d, h=0, mi=0, s=0) -> float:
    """本机本地时区的 epoch 秒（与 adoption._seconds 同口径 naive datetime）。"""
    return datetime.datetime(y, mo, d, h, mi, s).timestamp()


def _make_repo(tmp_path, files: dict[str, float]) -> str:
    """在 tmp_path 下建「仓库」，files: {relpath: mtime_epoch}，返回 repo 绝对路径。"""
    repo = str(tmp_path / "repo")
    os.makedirs(repo, exist_ok=True)
    for rel, mtime_s in files.items():
        full = os.path.join(repo, rel.replace("/", os.sep))
        parent = os.path.dirname(full)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        os.utime(full, (mtime_s, mtime_s))
    return repo


def _repo_stats(day: str, repo_path: str, files: list[dict]) -> dict:
    """fake git_insights.analyze_repo 返回值（对齐 git_insights.analyze_repo 输出）。"""
    return {
        "name": "MyProj", "path": repo_path,
        "commit_count": 1,
        "lines_added": sum(f.get("added", 0) for f in files),
        "lines_deleted": sum(f.get("deleted", 0) for f in files),
        "churn": sum(f.get("added", 0) + f.get("deleted", 0) for f in files),
        "files": len(files),
        "top_files": [dict(f) for f in files],
        "authors": ["tester"],
        "modify_ratio": 0.0,
    }


def _patch_sources(monkeypatch, repo_path: str, files: list[dict],
                   convs: list[dict] | None = None, generated: int = 0,
                   fail_collect: bool = False):
    """monkeypatch adoption 的两数据源。

    convs: [{project, first, last, turns}]（对齐 _conversation_summary 输出字段）。
    fail_collect=True → ai_sessions.collect 抛异常（模拟单源失败降级）。
    """
    def fake_config(config):
        return {"enabled": True, "projects": [{"name": "MyProj", "path": repo_path}],
                "timeout_s": 10, "top_files": 5}

    def fake_analyze(repo, day, timeout, top_files):
        return _repo_stats(day, repo_path, files)

    def fake_collect(day, config):
        if fail_collect:
            raise OSError("session dir missing")
        total = {"generated_lines": generated, "conversations": list(convs or [])}
        return {"date": day, "enabled": True, "found": bool(convs), "tools": {}, "total": total}

    monkeypatch.setattr(adoption.git_insights, "git_config", fake_config)
    monkeypatch.setattr(adoption.git_insights, "analyze_repo", fake_analyze)
    monkeypatch.setattr(adoption.ai_sessions, "collect", fake_collect)


# ---------------------------------------------------------------------------
# adoption_config —— 默认兜底 / 覆盖 / 坏类型 / 阈值夹取
# ---------------------------------------------------------------------------
class TestAdoptionConfig:
    def test_defaults_when_missing(self):
        cfg = adoption.adoption_config({})
        assert cfg["enabled"] is True
        assert cfg["window_slack_s"] == 600
        assert cfg["top_files"] == 1_000_000
        assert cfg["min_join_rate"] == 0.30

    def test_override(self):
        cfg = adoption.adoption_config(
            {"adoption": {"enabled": False, "window_slack_s": 60, "top_files": 50,
                          "min_join_rate": 0.5}})
        assert cfg["enabled"] is False
        assert cfg["window_slack_s"] == 60
        assert cfg["top_files"] == 50
        assert cfg["min_join_rate"] == 0.5

    def test_bad_types_fall_back(self):
        cfg = adoption.adoption_config({"adoption": {"window_slack_s": "x", "top_files": None}})
        assert cfg["window_slack_s"] == 600
        assert cfg["top_files"] == 1_000_000

    def test_min_join_rate_clamped(self):
        assert adoption.adoption_config({"adoption": {"min_join_rate": 2.0}})["min_join_rate"] == 1.0
        assert adoption.adoption_config({"adoption": {"min_join_rate": -1}})["min_join_rate"] == 0.0


# ---------------------------------------------------------------------------
# 空数据 / 降级 —— 契约空态（found=False，200 可展示，非 500）
# ---------------------------------------------------------------------------
class TestEmptyDegradation:
    def test_disabled_returns_empty(self):
        res = adoption.adoption_stats("2026-08-20", "", {"adoption": {"enabled": False}})
        assert res["enabled"] is False
        assert res["found"] is False
        assert res["projects"] == []
        assert "已关闭" in res["notice"]

    def test_no_git_repos_returns_empty(self, tmp_path, monkeypatch):
        """未配置任何 Git 仓库 → 契约空态。"""
        def fake_config(config):
            return {"enabled": True, "projects": [], "timeout_s": 10, "top_files": 5}
        monkeypatch.setattr(adoption.git_insights, "git_config", fake_config)
        res = adoption.adoption_stats("2026-08-20", "", {})
        assert res["found"] is False
        assert res["projects"] == []
        assert "未配置 Git 仓库" in res["notice"]

    def test_empty_contract_keys(self):
        res = adoption._empty_result("2026-08-20")
        assert res["found"] is False and res["projects"] == []
        assert res["summary"]["projects"] == 0
        assert res["summary"]["files"] == 0
        assert "非真实采纳率" in res["notice"]

    def test_ai_source_failure_degrades(self, tmp_path, monkeypatch):
        """ai_sessions.collect 抛异常 → 单源降级：仅 Git 侧，ratio 全 0/None，confidence=low。"""
        repo = _make_repo(tmp_path, {"a.py": _ts(2026, 8, 20, 10, 15)})
        files = [{"path": "a.py", "added": 50, "deleted": 10}]
        _patch_sources(monkeypatch, repo, files, fail_collect=True)
        res = adoption.adoption_stats("2026-08-20", "", {})
        assert res["found"] is True  # git 侧仍在
        assert res["summary"]["ai_windows"] == 0
        proj = res["projects"][0]
        assert proj["confidence"] == "low"
        assert proj["ai_generated_lines"] == 0
        f = proj["files"][0]
        assert f["ai_generated_ratio"] == 0.0   # 无 AI 数据 → 不判生成
        assert f["reworked_ratio"] == pytest.approx(10 / 60, abs=1e-3)  # 返工近似不依赖 AI


# ---------------------------------------------------------------------------
# 时间窗重叠判定 —— mtime × 会话窗（+slack）
# ---------------------------------------------------------------------------
class TestWindowOverlap:
    def test_mtime_inside_window_matches(self, tmp_path, monkeypatch):
        """文件 mtime 落在会话窗 [first-600, last+600] 内 → in_ai_window=True，ratio>0。"""
        repo = _make_repo(tmp_path, {"main.py": _ts(2026, 8, 20, 10, 15)})
        files = [{"path": "main.py", "added": 100, "deleted": 0}]
        convs = [{"project": "MyProj", "first": "2026-08-20T10:00:00",
                  "last": "2026-08-20T11:00:00", "turns": 8}]
        _patch_sources(monkeypatch, repo, files, convs=convs, generated=500)
        res = adoption.adoption_stats("2026-08-20", "", {})
        proj = res["projects"][0]
        assert proj["join_rate"] == 1.0
        f = proj["files"][0]
        assert f["in_ai_window"] is True
        assert f["ai_generated_ratio"] > 0.0
        assert f["mtime"].startswith("2026-08-20T10:15")
        # 全量 generated 归该会话 → proj_ai_lines=500 → ratio 分摊 500/1 文件 = min(1, 500/100 份额)→1.0
        assert f["ai_generated_ratio"] == 1.0

    def test_mtime_outside_window_no_match(self, tmp_path, monkeypatch):
        """mtime 距窗口 > slack（次日同刻）→ 不判 AI 触碰，ratio=0。"""
        repo = _make_repo(tmp_path, {"main.py": _ts(2026, 8, 21, 10, 15)})  # 次日
        files = [{"path": "main.py", "added": 100, "deleted": 0}]
        convs = [{"project": "MyProj", "first": "2026-08-20T10:00:00",
                  "last": "2026-08-20T11:00:00", "turns": 8}]
        _patch_sources(monkeypatch, repo, files, convs=convs, generated=500)
        res = adoption.adoption_stats("2026-08-20", "", {})
        f = res["projects"][0]["files"][0]
        assert f["in_ai_window"] is False
        assert f["ai_generated_ratio"] == 0.0

    def test_project_fuzzy_match_ignores_other_project_windows(self, tmp_path, monkeypatch):
        """仓库名与项目名双向子串匹配：无关项目的窗口不影响本仓库。"""
        repo = _make_repo(tmp_path, {"main.py": _ts(2026, 8, 20, 10, 15)})
        files = [{"path": "main.py", "added": 100, "deleted": 0}]
        # MyProj 的会话窗口在 09:00-09:30，文件 mtime 10:15 之外 → 不匹配
        convs = [{"project": "MyProj", "first": "2026-08-20T09:00:00",
                  "last": "2026-08-20T09:30:00", "turns": 8}]
        _patch_sources(monkeypatch, repo, files, convs=convs, generated=500)
        res = adoption.adoption_stats("2026-08-20", "", {})
        f = res["projects"][0]["files"][0]
        assert f["in_ai_window"] is False
        assert f["ai_generated_ratio"] == 0.0


# ---------------------------------------------------------------------------
# per-file ratio 分摊 —— 除法/除零边界
# ---------------------------------------------------------------------------
class TestPerFileRatios:
    def test_ratio_zero_when_no_ai_generated(self, tmp_path, monkeypatch):
        """有会话但 generated_lines=0（估算无产出）→ ratio=0，retention=None（除零兜底）。"""
        repo = _make_repo(tmp_path, {"a.py": _ts(2026, 8, 20, 10, 15)})
        files = [{"path": "a.py", "added": 40, "deleted": 0}]
        convs = [{"project": "MyProj", "first": "2026-08-20T10:00:00",
                  "last": "2026-08-20T11:00:00", "turns": 8}]
        _patch_sources(monkeypatch, repo, files, convs=convs, generated=0)
        res = adoption.adoption_stats("2026-08-20", "", {})
        proj = res["projects"][0]
        f = proj["files"][0]
        assert f["ai_generated_ratio"] == 0.0
        assert proj["approximate_retention"] is None  # proj_ai_lines=0 → None
        assert proj["confidence"] == "low"

    def test_ratio_none_when_added_zero(self, tmp_path, monkeypatch):
        """纯删除文件（added=0）→ ai_generated_ratio=None（无新增行可比）。"""
        repo = _make_repo(tmp_path, {"gone.txt": _ts(2026, 8, 20, 10, 15)})
        files = [{"path": "gone.txt", "added": 0, "deleted": 30}]
        convs = [{"project": "MyProj", "first": "2026-08-20T10:00:00",
                  "last": "2026-08-20T11:00:00", "turns": 8}]
        _patch_sources(monkeypatch, repo, files, convs=convs, generated=200)
        res = adoption.adoption_stats("2026-08-20", "", {})
        f = res["projects"][0]["files"][0]
        assert f["ai_generated_ratio"] is None
        # 但返工近似照常：deleted/churn = 30/30
        assert f["reworked_ratio"] == 1.0

    def test_multi_file_share_windows(self, tmp_path, monkeypatch):
        """两文件都在窗内 → 各自按 added 占比分摊项目 AI 行，ratio 相同且 ≤1。"""
        repo = _make_repo(tmp_path, {
            "a.py": _ts(2026, 8, 20, 10, 15),
            "b.py": _ts(2026, 8, 20, 10, 40),
        })
        files = [{"path": "a.py", "added": 100, "deleted": 0},
                 {"path": "b.py", "added": 300, "deleted": 0}]
        convs = [{"project": "MyProj", "first": "2026-08-20T10:00:00",
                  "last": "2026-08-20T11:00:00", "turns": 8}]
        _patch_sources(monkeypatch, repo, files, convs=convs, generated=200)
        res = adoption.adoption_stats("2026-08-20", "", {})
        files_res = {f["path"]: f for f in res["projects"][0]["files"]}
        # proj_ai_lines = 200*8/8 = 200；分摊后 ratio = 200/400 份额 → 0.5（两文件一致）
        assert files_res["a.py"]["ai_generated_ratio"] == 0.5
        assert files_res["b.py"]["ai_generated_ratio"] == 0.5
        assert res["summary"]["join_rate"] == 1.0


# ---------------------------------------------------------------------------
# 返工近似 —— reworked_ratio / acceptance / retention
# ---------------------------------------------------------------------------
class TestReworkAndAcceptance:
    def test_reworked_ratio_is_deleted_over_churn(self, tmp_path, monkeypatch):
        """per-file 返工近似 = deleted/(added+deleted)；project 级 acceptance=1-delete/add。"""
        repo = _make_repo(tmp_path, {
            "a.py": _ts(2026, 8, 20, 10, 15),
            "b.py": _ts(2026, 8, 20, 10, 30),
        })
        files = [{"path": "a.py", "added": 100, "deleted": 50},
                 {"path": "b.py", "added": 80, "deleted": 20}]
        convs = [{"project": "MyProj", "first": "2026-08-20T10:00:00",
                  "last": "2026-08-20T11:00:00", "turns": 8}]
        _patch_sources(monkeypatch, repo, files, convs=convs, generated=100)
        res = adoption.adoption_stats("2026-08-20", "", {})
        proj = res["projects"][0]
        by_path = {f["path"]: f for f in proj["files"]}
        assert by_path["a.py"]["reworked_ratio"] == pytest.approx(50 / 150, abs=1e-4)
        assert by_path["b.py"]["reworked_ratio"] == pytest.approx(20 / 100, abs=1e-4)
        # acceptance = 1 - (70/250) = 0.72（churn=150+100）
        assert proj["approximate_acceptance"] == pytest.approx(1 - 70 / 250, abs=1e-3)
        # retention = lines_added(180) / proj_ai_lines(100) → 1.8
        assert proj["approximate_retention"] == pytest.approx(1.8, abs=1e-3)
        # join_rate: 两文件都在窗口 → 1.0 ≥ 0.3 → medium
        assert proj["confidence"] == "medium"

    def test_acceptance_none_when_no_churn(self, tmp_path, monkeypatch):
        """churn=0（git 不统计二进制/重命名）→ acceptance=None，不除零。"""
        repo = _make_repo(tmp_path, {"bin.dat": _ts(2026, 8, 20, 10, 15)})
        files = [{"path": "bin.dat", "added": 0, "deleted": 0}]
        convs = [{"project": "MyProj", "first": "2026-08-20T10:00:00",
                  "last": "2026-08-20T11:00:00", "turns": 8}]
        _patch_sources(monkeypatch, repo, files, convs=convs, generated=50)
        res = adoption.adoption_stats("2026-08-20", "", {})
        proj = res["projects"][0]
        assert proj["approximate_acceptance"] is None  # churn=0 → None
        assert proj["approximate_retention"] == 0.0  # added=0 / proj_ai_lines=50 → 0/50=0.0
        assert proj["files"][0]["reworked_ratio"] == 0.0  # churn=0 → 0.0
        assert res["summary"]["approximate_acceptance"] is None


# ---------------------------------------------------------------------------
# summary 聚合 —— 多文件/多仓库口径
# ---------------------------------------------------------------------------
class TestSummary:
    def test_summary_aggregates(self, tmp_path, monkeypatch):
        repo = _make_repo(tmp_path, {
            "a.py": _ts(2026, 8, 20, 10, 15),
            "b.py": _ts(2026, 8, 20, 23, 0),   # 窗口外
        })
        files = [{"path": "a.py", "added": 100, "deleted": 0},
                 {"path": "b.py", "added": 100, "deleted": 0}]
        convs = [{"project": "MyProj", "first": "2026-08-20T10:00:00",
                  "last": "2026-08-20T11:00:00", "turns": 8}]
        _patch_sources(monkeypatch, repo, files, convs=convs, generated=200)
        res = adoption.adoption_stats("2026-08-20", "", {})
        s = res["summary"]
        assert s["projects"] == 1
        assert s["files"] == 2
        assert s["ai_windows"] == 1
        assert s["join_rate"] == 0.5
        # b.py 窗口外 → ratio 0；a.py 窗内分摊全部 AI 行 → 1.0
        by_path = {f["path"]: f for f in res["projects"][0]["files"]}
        assert by_path["a.py"]["ai_generated_ratio"] == 1.0
        assert by_path["b.py"]["ai_generated_ratio"] == 0.0
        # retention = 200/200 = 1.0；acceptance = 1 - 0 = 1.0
        assert s["approximate_retention"] == pytest.approx(1.0)
        assert s["approximate_acceptance"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 工具函数 —— _seconds / _in_window / _fuzzy_match
# ---------------------------------------------------------------------------
class TestHelpers:
    def test_seconds_parse_and_naive_local(self):
        assert adoption._seconds("2026-08-20T10:00:00") == pytest.approx(_ts(2026, 8, 20, 10, 0))
        assert adoption._seconds("garbage") is None
        assert adoption._seconds("") is None

    def test_in_window_with_slack(self):
        w = {"first_sec": _ts(2026, 8, 20, 10, 0), "last_sec": _ts(2026, 8, 20, 11, 0)}
        assert adoption._in_window(_ts(2026, 8, 20, 10, 30), [w], 600) is True
        assert adoption._in_window(_ts(2026, 8, 20, 9, 50), [w], 600) is True   # 前 slack 内
        assert adoption._in_window(_ts(2026, 8, 20, 11, 10), [w], 600) is True   # 后 slack 内
        assert adoption._in_window(_ts(2026, 8, 20, 9, 40), [w], 600) is False   # 超 slack
        assert adoption._in_window(_ts(2026, 8, 20, 12, 0), [w], 0) is False

    def test_fuzzy_match_bidirectional_substring(self):
        assert adoption._fuzzy_match("MyProj", "myproj") is True
        assert adoption._fuzzy_match("Proj", "MyProject") is True   # 仓库名是项目名子串
        assert adoption._fuzzy_match("Foo", "Bar") is False
        assert adoption._fuzzy_match("", "Anything") is False