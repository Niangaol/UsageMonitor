# -*- coding: utf-8 -*-
"""tests/unit/test_timeline.py — Vibe 时间轴回放（v2.5）单元测试。

覆盖：配置兜底、时间戳归一化（含跨时区）、AI 会话段合并、会话深度叠加、
事件化排序、project 过滤、汇总、三源合并全链路（monkeypatch 三源）、降级。
零依赖、确定性、不触发真实数据扫描（AI 会话/git 源一律 monkeypatch）。
"""

from __future__ import annotations

import calendar
import datetime
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import timeline  # noqa: E402


# ---------------------------------------------------------------------------
# 构造助手
# ---------------------------------------------------------------------------
def _s(start, end, tool=None, dur=60000, cat="AI编程", title="demo"):
    """一条 usage.jsonl 会话记录（AI 相关段：category==AI编程 或 ai_tool 非空）。"""
    return {"start": start, "end": end, "duration_ms": dur, "app": "code.exe",
            "exe": "code.exe", "title": title, "category": cat, "ai_tool": tool}


def _conv(first, tool="opencode", model="deepseek-v4-pro", project="VibeTrace",
          tok=1000, cost=0.01, lines=50, last=None):
    """一条 ai_sessions conversation 摘要（_conversation_summary 的输出形态）。"""
    return {"id": "c1", "tool": tool, "model": model, "project": project,
            "turns": 4, "rounds": 2, "tokens_total": tok, "cost_total": cost,
            "generated_lines": lines, "first": first, "last": last or first}


def _commit(date, h="ab12cdef", project="VibeTrace", added=10, deleted=5, author="me"):
    return {"date": date, "hash": h, "project": project,
            "added": added, "deleted": deleted, "author": author}


FAKE_SESSIONS = [
    _s("2026-08-20T09:00:00", "2026-08-20T09:10:00", "opencode", 600000, "AI编程", "VibeTrace/a.py"),
    _s("2026-08-20T10:00:00", "2026-08-20T10:20:00", "opencode", 1200000, "开发工具", "VibeTrace/b.py"),
    _s("2026-08-20T11:00:00", "2026-08-20T11:05:00", "chatgpt", 300000, "AI编程", "SideProj/c.py"),
]
FAKE_CONVS = [
    _conv("2026-08-20T09:03:00", tok=12000, cost=0.31, lines=180,
          last="2026-08-20T09:08:00"),
    _conv("2026-08-20T12:30:00", tool="chatgpt", model="gpt-4o", project="SideProj",
          tok=3000, cost=0.02, lines=40, last="2026-08-20T12:33:00"),
]
FAKE_COMMITS = [
    # 单元测试用 naive 时间戳做事件排序/定位（时区转换另有 test_norm_dt_timezone_equivalence 覆盖），
    # 避免带 +0800 的断言在 UTC CI runner 上因本地时区不同而错位。
    _commit("2026-08-20 09:48:30", added=150, deleted=20),
    _commit("2026-08-20 11:30:00", h="ff00aa", added=10, deleted=5),
]


def _patch_sources(monkeypatch, sessions=FAKE_SESSIONS, convs=FAKE_CONVS, commits=FAKE_COMMITS):
    """把三源收集函数替换为构造数据（避免触发真实数据扫描）。"""
    monkeypatch.setattr(timeline, "_collect_ai_sessions", lambda *a, **k: list(sessions))
    monkeypatch.setattr(timeline, "_collect_conversations", lambda *a, **k: list(convs))
    monkeypatch.setattr(timeline, "_collect_git_commits", lambda *a, **k: list(commits))


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def test_timeline_config_defaults():
    """配置段兜底：缺失/坏值/下限裁剪。"""
    assert timeline.timeline_config({}) == {"enabled": True, "merge_gap_s": 120}
    assert timeline.timeline_config({"vibe_timeline": {"enabled": False}})["enabled"] is False
    assert timeline.timeline_config({"vibe_timeline": {"merge_gap_s": 30}})["merge_gap_s"] == 30
    assert timeline.timeline_config({"vibe_timeline": {"merge_gap_s": "abc"}})["merge_gap_s"] == 120
    assert timeline.timeline_config({"vibe_timeline": {"merge_gap_s": 0}})["merge_gap_s"] == 1
    assert timeline.timeline_config({"vibe_timeline": "junk"})["enabled"] is True
    print("  [PASS] timeline_config_defaults")


# ---------------------------------------------------------------------------
# 时间戳归一化
# ---------------------------------------------------------------------------
def test_norm_dt_formats():
    d = datetime.datetime
    assert timeline._norm_dt("2026-08-20T09:12:00") == d(2026, 8, 20, 9, 12, 0)
    assert timeline._norm_dt("2026-08-20 09:12:00") == d(2026, 8, 20, 9, 12, 0)
    # 带时区 -> 本机本地 naive（期望值动态推导，避免硬编码 +0800、跨时区 CI 失败）
    _tz_expect = datetime.datetime.fromtimestamp(calendar.timegm((2026, 8, 20, 1, 12, 0)))
    assert timeline._norm_dt("2026-08-20 09:12:00 +0800") == _tz_expect
    assert timeline._norm_dt("2026-08-20") == d(2026, 8, 20)
    assert timeline._norm_dt(d(2026, 8, 20, 9, 12)) == d(2026, 8, 20, 9, 12)
    ep = d(2026, 8, 20, 9, 0, 0).timestamp()
    assert timeline._norm_dt(ep) == d(2026, 8, 20, 9, 0, 0)
    assert timeline._norm_dt(None) is None
    assert timeline._norm_dt("") is None
    assert timeline._norm_dt("garbage") is None
    assert timeline._norm_dt("2026-13-40T99:00:00") is None
    print("  [PASS] norm_dt_formats")


def test_norm_dt_timezone_equivalence():
    """同一瞬间的不同时区表示 → 同一本地 naive 时间（不依赖机器时区）。"""
    utc = timeline._norm_dt("2026-08-20 01:48:30 +0000")
    cn = timeline._norm_dt("2026-08-20 09:48:30 +0800")
    cn_colon = timeline._norm_dt("2026-08-20 09:48:30 +08:00")
    z = timeline._norm_dt("2026-08-20 01:48:30Z")
    assert utc is not None
    assert utc == cn == cn_colon == z
    # 期望值由本机时区动态推导（避免硬编码 +8）
    expect = datetime.datetime.fromtimestamp(calendar.timegm((2026, 8, 20, 1, 48, 30)))
    assert utc == expect
    print("  [PASS] norm_dt_timezone_equivalence")


# ---------------------------------------------------------------------------
# 会话段合并
# ---------------------------------------------------------------------------
def test_merge_blocks_adjacent_merge():
    """同 tool + 间隔 < gap_s → 合并为 1 块。"""
    blocks = timeline._merge_blocks([
        _s("2026-08-20T09:00:00", "2026-08-20T09:10:00", "opencode"),
        _s("2026-08-20T09:11:00", "2026-08-20T09:20:00", "opencode"),
    ], 120)
    assert len(blocks) == 1
    assert blocks[0]["sessions"] == 2
    assert blocks[0]["duration_ms"] == 120000
    assert blocks[0]["tool"] == "opencode"
    print("  [PASS] merge_blocks_adjacent_merge")


def test_merge_blocks_gap_breaks():
    """间隔 ≥ gap_s → 拆成两块。"""
    blocks = timeline._merge_blocks([
        _s("2026-08-20T09:00:00", "2026-08-20T09:10:00", "opencode"),
        _s("2026-08-20T09:20:00", "2026-08-20T09:30:00", "opencode"),
    ], 120)
    assert len(blocks) == 2
    print("  [PASS] merge_blocks_gap_breaks")


def test_merge_blocks_different_tool():
    """同时间窗但 tool 不同 → 各自成块。"""
    blocks = timeline._merge_blocks([
        _s("2026-08-20T09:00:00", "2026-08-20T09:10:00", "opencode"),
        _s("2026-08-20T09:05:00", "2026-08-20T09:15:00", "chatgpt"),
    ], 120)
    assert len(blocks) == 2
    print("  [PASS] merge_blocks_different_tool")


def test_merge_blocks_unsorted_input():
    """乱序输入 → 输出按 start 升序且正确合并/拆分。"""
    blocks = timeline._merge_blocks([
        _s("2026-08-20T09:20:00", "2026-08-20T09:30:00", "opencode"),
        _s("2026-08-20T09:00:00", "2026-08-20T09:10:00", "opencode"),
        _s("2026-08-20T09:05:00", "2026-08-20T09:08:00", "opencode"),
    ], 120)
    # 09:05 并入 09:00 块；09:20 距 09:10 间隔 600s ≥ 120 → 拆开
    assert len(blocks) == 2
    assert [b["start"] for b in blocks] == sorted(b["start"] for b in blocks)
    assert blocks[0]["start"] == datetime.datetime(2026, 8, 20, 9, 0, 0)
    assert blocks[0]["end"] == datetime.datetime(2026, 8, 20, 9, 10, 0)
    assert blocks[0]["sessions"] == 2
    assert blocks[1]["start"] == datetime.datetime(2026, 8, 20, 9, 20, 0)
    assert blocks[1]["sessions"] == 1
    print("  [PASS] merge_blocks_unsorted_input")


def test_merge_blocks_missing_ts():
    """无 start 的会话不崩，单独成块。"""
    bad = {"start": None, "end": None, "duration_ms": 1000, "ai_tool": "opencode"}
    blocks = timeline._merge_blocks([bad, _s("2026-08-20T09:00:00", "2026-08-20T09:10:00", "opencode")], 120)
    assert len(blocks) == 2
    print("  [PASS] merge_blocks_missing_ts")


# ---------------------------------------------------------------------------
# 会话深度叠加
# ---------------------------------------------------------------------------
def test_attach_conversations_hit_and_miss():
    """first 落入块窗口内 → 叠加；远处会话 → 不归属。"""
    blocks = timeline._merge_blocks([
        _s("2026-08-20T09:00:00", "2026-08-20T09:10:00", "opencode"),
    ], 120)
    out = timeline._attach_conversations(blocks, [
        _conv("2026-08-20T09:05:00"),
        _conv("2026-08-20T12:00:00"),
    ], 120)
    assert out[0]["conversations"] == 1
    assert out[0]["tokens_total"] == 1000
    assert out[0]["cost_total"] == 0.01
    assert out[0]["generated_lines"] == 50
    print("  [PASS] attach_conversations_hit_and_miss")


def test_attach_conversations_gap_tolerance():
    """first 稍出块窗外但 ±gap_s 内容忍归属（对齐误差容错）。"""
    base = [
        _s("2026-08-20T09:00:00", "2026-08-20T09:10:00", "opencode"),
    ]
    inside = timeline._attach_conversations(
        timeline._merge_blocks(list(base), 120), [_conv("2026-08-20T09:11:30")], 120)
    assert inside[0]["conversations"] == 1  # 9:12:00 窗沿内
    outside = timeline._attach_conversations(
        timeline._merge_blocks(list(base), 120), [_conv("2026-08-20T09:13:00")], 120)
    assert outside[0]["conversations"] == 0  # 超窗沿
    print("  [PASS] attach_conversations_gap_tolerance")


# ---------------------------------------------------------------------------
# 三源合并全链路（monkeypatch）
# ---------------------------------------------------------------------------
def test_timeline_events_three_sources(monkeypatch, tmp_path):
    """三源 → session/ai_session/git_commit 事件流，按时间升序。"""
    _patch_sources(monkeypatch)
    events = timeline.timeline_events("2026-08-20", str(tmp_path), {})
    types = [e["type"] for e in events]
    assert types.count("session") == 3
    assert types.count("ai_session") == 2
    assert types.count("git_commit") == 2
    for e in events:
        assert {"time", "type", "title", "detail"} <= set(e.keys())
    times = [e["time"] for e in events]
    assert times == sorted(times), f"未按时间升序: {times}"
    # 乱序 git 输入也能落在正确位置（09:48:30 在 09:00~10:00 会话之间）
    idx = {e["time"]: e["type"] for e in events}
    assert idx["09:48:30"] == "git_commit"
    # 首事件为最早会话，detail 保留起始时间
    first = events[0]
    assert first["time"] == "09:00:00" and first["type"] == "session"
    assert first["detail"]["start"] == "2026-08-20T09:00:00"
    assert first["detail"]["category"] == "AI编程"
    print("  [PASS] timeline_events_three_sources")


def test_timeline_events_bad_ts_dropped(monkeypatch, tmp_path):
    """时间戳解析失败的事件被丢弃（不崩、不占位）。"""
    _patch_sources(monkeypatch,
                   convs=[_conv("not-a-time"), _conv("2026-08-20T09:03:00")],
                   commits=[_commit("garbage"), _commit("2026-08-20 09:48:30 +0800")])
    events = timeline.timeline_events("2026-08-20", str(tmp_path), {})
    types = [e["type"] for e in events]
    assert types.count("ai_session") == 1
    assert types.count("git_commit") == 1
    print("  [PASS] timeline_events_bad_ts_dropped")


def test_timeline_events_project_filter(monkeypatch, tmp_path):
    """project 模糊过滤（substring，大小写不敏感）；不匹配的事件被剔除。"""
    _patch_sources(monkeypatch,
                   convs=[_conv("2026-08-20T09:03:00", project="SideProj"),
                          _conv("2026-08-20T09:05:00", project="VibeTrace")],
                   commits=[_commit("2026-08-20 09:48:30 +0800", project="OtherRepo"),
                            _commit("2026-08-20 10:30:00 +0800", project="VibeTrace")])
    events = timeline.timeline_events("2026-08-20", str(tmp_path), {}, project="vibetrace")
    assert events, "应至少保留匹配事件"
    assert all(e["type"] != "git_commit" for e in events if "OtherRepo" in e["detail"].get("project", ""))
    projs = {e["type"] for e in events}
    assert "git_commit" in projs  # VibeTrace 的 commit 保留
    assert "ai_session" in projs  # VibeTrace 的会话保留
    # 全部事件的 detail 中都应找到 VibeTrace 痕迹（会话按 title/project、提交按 project）
    for e in events:
        blob = (e["title"] + " " + " ".join(str(v) for v in e["detail"].values())).lower()
        assert "vibetrace" in blob, f"过滤后仍混入不匹配事件: {e}"
    print("  [PASS] timeline_events_project_filter")


def test_build_timeline_summary(monkeypatch, tmp_path):
    """summary：ai_minutes/commit_count/churn/total_cost/ai_blocks/conversations。"""
    _patch_sources(monkeypatch)
    out = timeline.build_timeline("2026-08-20", str(tmp_path), {})
    assert out["date"] == "2026-08-20"
    s = out["summary"]
    assert s["commit_count"] == 2
    assert s["churn"] == (150 + 20) + (10 + 5)
    assert s["ai_blocks"] == 3
    assert s["conversations"] == 2
    assert abs(s["total_cost"] - 0.33) < 1e-6
    assert s["ai_minutes"] == 35.0  # (600000+1200000+300000) ms = 35 分钟
    print("  [PASS] build_timeline_summary")


def test_build_timeline_disabled(monkeypatch, tmp_path):
    """vibe_timeline.enabled=false → 事件空 + summary 归零（空态）。"""
    _patch_sources(monkeypatch)
    cfg = {"vibe_timeline": {"enabled": False}}
    assert timeline.timeline_events("2026-08-20", str(tmp_path), cfg) == []
    out = timeline.build_timeline("2026-08-20", str(tmp_path), cfg)
    assert out["events"] == []
    assert out["summary"]["ai_blocks"] == 0 and out["summary"]["commit_count"] == 0
    print("  [PASS] build_timeline_disabled")


def test_build_timeline_empty_data(tmp_path, monkeypatch):
    """无任何数据（真实 report 聚合空目录）→ 200 空态，summary 全零。"""
    # 只 patch 会话深度/git 源（避免触发真实用户目录扫描）；usage 源走真实聚合（空目录）
    monkeypatch.setattr(timeline, "_collect_conversations", lambda *a, **k: [])
    monkeypatch.setattr(timeline, "_collect_git_commits", lambda *a, **k: [])
    out = timeline.build_timeline("2099-01-01", str(tmp_path),
                                  {"vibe_timeline": {"merge_gap_s": 120}})
    assert out["date"] == "2099-01-01"
    assert out["events"] == []
    assert out["summary"] == {"ai_minutes": 0, "commit_count": 0, "churn": 0,
                              "total_cost": 0.0, "ai_blocks": 0, "conversations": 0}
    print("  [PASS] build_timeline_empty_data")


def test_timeline_events_source_failure_degrades(monkeypatch, tmp_path):
    """任一源抛异常 → 时间轴整体降级为空列表（best-effort，不 500）。"""

    def boom(*a, **k):
        raise RuntimeError("source down")

    monkeypatch.setattr(timeline, "_collect_ai_sessions", boom)
    monkeypatch.setattr(timeline, "_collect_conversations", lambda *a, **k: [])
    monkeypatch.setattr(timeline, "_collect_git_commits", lambda *a, **k: [])
    assert timeline.timeline_events("2026-08-20", str(tmp_path), {}) == []
    out = timeline.build_timeline("2026-08-20", str(tmp_path), {})
    assert out["events"] == [] and out["summary"]["ai_blocks"] == 0
    print("  [PASS] timeline_events_source_failure_degrades")


def test_timeline_events_missing_sources(monkeypatch, tmp_path):
    """某源正常为空（无会话/无提交的日子）→ 不影响其它源事件。"""
    monkeypatch.setattr(timeline, "_collect_ai_sessions", lambda *a, **k: list(FAKE_SESSIONS))
    monkeypatch.setattr(timeline, "_collect_conversations", lambda *a, **k: [])
    monkeypatch.setattr(timeline, "_collect_git_commits", lambda *a, **k: [])
    events = timeline.timeline_events("2026-08-20", str(tmp_path), {})
    assert [e["type"] for e in events] == ["session"] * 3
    print("  [PASS] timeline_events_missing_sources")