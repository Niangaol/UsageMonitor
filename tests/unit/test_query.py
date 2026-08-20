# -*- coding: utf-8 -*-
"""tests/unit/test_query.py — 受限模板查询引擎（query.py）单测（v2.6 · P7）。

覆盖：周期解析（词表/绝对日期/非法/超长）、5 个模板匹配与解析器（全部注入 fake
数据源，零真实文件）、未命中/空问题/注入拒绝、tpl= 显式模式的参数校验（未知模板/
非法日期/倒置区间/超长区间/默认周期）、JSON 可序列化、config 总开关、模板元数据。

铁律：不触碰真实用户数据目录——ai_sessions.collect / report.aggregate /
insights.behavior_insights / git_insights.git_insights 全部 monkeypatch 为 fake；
tool_compare / growth 通过 query._MODS 整体替换为 fake 模块（惰性加载点）。
"""

from __future__ import annotations

import datetime
import json
import sys
import types

_PROJECT_ROOT = __import__("os").path.dirname(
    __import__("os").path.dirname(__import__("os").path.dirname(
        __import__("os").path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest  # noqa: E402

import query  # noqa: E402

TODAY = datetime.date(2099, 1, 5)  # 周一


# ---------------------------------------------------------------------------
# fake 数据源
# ---------------------------------------------------------------------------
def make_collect(date: str, tools: dict | None = None) -> dict:
    """构造 ai_sessions.collect 的 fake 返回。

    tools: {tool_name: {"cost": float, "tokens": int, "rounds": int,
                        "generated_lines": int, "projects": {proj: cost}}}
    """
    tools = tools or {"opencode": {"cost": 1.0, "tokens": 1000, "rounds": 3}}
    tstats: dict = {}
    total = {"files": 0, "turns": 0, "rounds": 0, "user_messages": 0,
             "assistant_messages": 0, "generated_lines": 0, "generated_chars": 0,
             "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
             "cost_in": 0.0, "cost_out": 0.0, "cost_total": 0.0,
             "by_model": {}, "by_project": {}, "conversations": []}
    for name, t in tools.items():
        cost = float(t.get("cost", 0.0))
        tokens = int(t.get("tokens", 0))
        projects = t.get("projects") or {}
        stats = {
            "files": 1, "turns": 10, "rounds": int(t.get("rounds", 3)),
            "user_messages": 5, "assistant_messages": 5,
            "generated_lines": int(t.get("generated_lines", 20)),
            "generated_chars": int(t.get("generated_chars", 200)),
            "tokens_in": int(tokens * 0.4), "tokens_out": int(tokens * 0.6),
            "tokens_total": tokens,
            "cost_in": round(cost * 0.4, 4), "cost_out": round(cost * 0.6, 4),
            "cost_total": round(cost, 4),
            "by_model": {"tm": {"turns": 10, "tokens_in": int(tokens * 0.4),
                                "tokens_out": int(tokens * 0.6),
                                "tokens_total": tokens,
                                "cost_in": round(cost * 0.4, 4),
                                "cost_out": round(cost * 0.6, 4),
                                "cost_total": round(cost, 4)}},
            "by_project": {p: {"turns": 10, "tokens_in": int(tokens * 0.4),
                               "tokens_out": int(tokens * 0.6),
                               "tokens_total": tokens,
                               "cost_in": round(c * 0.4, 4),
                               "cost_out": round(c * 0.6, 4),
                               "cost_total": round(float(c), 4)}
                           for p, c in projects.items()},
            "conversations": [],
        }
        tstats[name] = stats
        for key in ("files", "turns", "rounds", "user_messages", "assistant_messages",
                    "generated_lines", "generated_chars", "tokens_in", "tokens_out",
                    "tokens_total", "cost_in", "cost_out", "cost_total"):
            total[key] += stats[key]
        for p, c in projects.items():
            e = total["by_project"].setdefault(
                p, {"turns": 0, "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
                    "cost_in": 0.0, "cost_out": 0.0, "cost_total": 0.0})
            e["turns"] += 10
            e["tokens_total"] += tokens
            e["cost_total"] += float(c)
        dm = total["by_model"].setdefault(
            "tm", {"turns": 0, "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
                   "cost_in": 0.0, "cost_out": 0.0, "cost_total": 0.0})
        dm["turns"] += 10
        dm["tokens_total"] += tokens
        dm["cost_total"] += round(cost, 4)
    return {"date": date, "enabled": True, "found": True,
            "tools": tstats, "total": total,
            "web_ai": {"found": False, "sessions": []}}


def make_agg(day: str, minutes_ms: int = 600000, n_ai_sessions: int = 1) -> dict:
    """构造 report.aggregate 的 fake 返回（by_ai 分钟 + 会话 + 活跃时长）。"""
    sessions = [{"start": f"{day}T09:00:00", "end": f"{day}T09:10:00",
                 "duration_ms": minutes_ms, "ai_tool": "opencode",
                 "exe": "code.exe", "app": "VS Code", "title": "t.py",
                 "category": "AI编程", "contact": None, "active": True}
                for _ in range(n_ai_sessions)]
    return {"date": day, "total_active_ms": minutes_ms, "session_count": len(sessions),
            "sessions": sessions, "by_ai": {"opencode": minutes_ms},
            "by_category": {"AI编程": minutes_ms}, "by_app": {}, "by_contact": {}}


@pytest.fixture(autouse=True)
def _clean_mods():
    """每个用例前重置惰性模块缓存（防用例间泄漏）。"""
    query._MODS.clear()
    yield
    query._MODS.clear()


# ---------------------------------------------------------------------------
# 周期解析
# ---------------------------------------------------------------------------
def test_parse_period_forms():
    cfg = query.query_config({})
    assert query._resolve_period("今天", TODAY, cfg)[0] == ["2099-01-05"]
    assert query._resolve_period("昨天", TODAY, cfg)[0] == ["2099-01-04"]
    assert query._resolve_period("前天", TODAY, cfg)[0] == ["2099-01-03"]
    # 本周（周一起）
    assert query._resolve_period("本周", TODAY, cfg)[0] == ["2099-01-05"]
    # 上周 = 周一起 7 天
    assert query._resolve_period("上周", TODAY, cfg)[0] == [
        "2098-12-29", "2098-12-30", "2098-12-31", "2099-01-01",
        "2099-01-02", "2099-01-03", "2099-01-04"]
    assert query._resolve_period("本月", TODAY, cfg)[0][0] == "2099-01-01"
    assert query._resolve_period("上月", TODAY, cfg)[0] == [
        d.isoformat() for d in (
            datetime.date(2098, 12, 1) + datetime.timedelta(days=i)
            for i in range(31))]
    # 最近 N 天：截至昨天
    days, label = query._resolve_period("最近 7 天", TODAY, cfg)
    assert days == ["2098-12-29", "2098-12-30", "2098-12-31",
                    "2099-01-01", "2099-01-02", "2099-01-03", "2099-01-04"]
    assert "截至昨天" in label
    # 绝对日期 / 区间
    assert query._resolve_period("2099-01-03", TODAY, cfg)[0] == ["2099-01-03"]
    days2, label2 = query._resolve_period("2099-01-01到2099-01-03", TODAY, cfg)
    assert days2 == ["2099-01-01", "2099-01-02", "2099-01-03"]
    assert label2 == "2099-01-01 至 2099-01-03"
    # N 钳制到 max_days
    days3, _ = query._resolve_period("最近 9999 天", TODAY, cfg)
    assert len(days3) == cfg["max_days"] == 92
    # 非法
    for bad in ("", "上周五日", "昨天到后天", "2099-13-99", "2099-01-05到2099-01-01"):
        with pytest.raises(ValueError):
            query._resolve_period(bad, TODAY, cfg)


def test_parse_date_strict():
    assert query._parse_date("2026-08-20").isoformat() == "2026-08-20"
    for bad in ("2026-8-1", "2026/08/01", "abcd", "2026-02-31", ""):
        with pytest.raises(ValueError):
            query._parse_date(bad)


# ---------------------------------------------------------------------------
# 模板匹配 + 解析器（全 fake）
# ---------------------------------------------------------------------------
def _patch_sources(monkeypatch, agg_days=None, git_result=None, focus=60):
    agg_days = agg_days or {}
    monkeypatch.setattr(query.ai_sessions, "collect",
                        lambda day, config: make_collect(day))
    monkeypatch.setattr(query.report, "aggregate",
                        lambda day, root: agg_days.get(day, make_agg(day)))
    monkeypatch.setattr(query.insights, "behavior_insights",
                        lambda agg, config=None: {"focus_score": focus})
    monkeypatch.setattr(
        query.git_insights, "git_insights", lambda config, day: git_result or {
            "enabled": True, "found": True,
            "total": {"commit_count": 2, "lines_added": 500,
                      "lines_deleted": 100, "churn": 600, "modify_ratio": 0.17}})


def test_q1_cost_with_tool(monkeypatch):
    """昨天 opencode 花了多少钱 → q1，单日工具口径。"""
    _patch_sources(monkeypatch)
    r = query.run_query("昨天 opencode 花了多少钱", "root", {}, today=TODAY)
    assert r["ok"] and r["tpl"] == "q1"
    assert r["start"] == r["end"] == "2099-01-04"
    d = r["data"]
    assert d["tool"] == "opencode"
    assert d["totals"]["cost"] == pytest.approx(1.0)
    assert d["totals"]["tokens"] == 1000
    assert d["totals"]["minutes"] == pytest.approx(10.0)
    ans = r["answer"]
    assert "昨天" in ans and "opencode" in ans and "$1.00" in ans
    json.dumps(r, ensure_ascii=False)


def test_q1_cost_all_tools_ranking(monkeypatch):
    """本周 AI 成本是多少 → q1 全体口径；by_tool 按成本降序。"""
    def collect(day, config):
        return make_collect(day, tools={
            "opencode": {"cost": 1.0, "tokens": 1000},
            "codex": {"cost": 2.0, "tokens": 2000}})
    monkeypatch.setattr(query.ai_sessions, "collect", collect)
    monkeypatch.setattr(query.report, "aggregate", lambda day, root: make_agg(day))
    r = query.run_query("本周 AI 成本是多少", "root", {}, today=TODAY)
    assert r["ok"] and r["tpl"] == "q1"
    assert r["data"]["tool"] is None
    assert r["data"]["totals"]["cost"] == pytest.approx(3.0)
    ranking = r["data"]["by_tool"]
    assert [x["tool"] for x in ranking] == ["codex", "opencode"]
    assert "AI 工具" in r["answer"] and "$3.00" in r["answer"]


def test_q2_top_project(monkeypatch):
    """最近 2 天哪个项目成本最高 → q2 project 口径（跨天合并、按成本降序）。"""
    def collect(day, config):
        if day == "2099-01-04":
            return make_collect(day, tools={
                "opencode": {"cost": 1.0, "tokens": 500,
                             "projects": {"D:/ProjA": 1.0}}})
        return make_collect(day, tools={
            "opencode": {"cost": 2.0, "tokens": 1000,
                         "projects": {"D:/ProjB": 2.0}}})
    monkeypatch.setattr(query.ai_sessions, "collect", collect)
    monkeypatch.setattr(query.report, "aggregate", lambda day, root: make_agg(day))
    r = query.run_query("最近 2 天哪个项目成本最高", "root", {}, today=TODAY)
    assert r["ok"] and r["tpl"] == "q2"
    d = r["data"]
    assert d["scope"] == "项目"
    assert [x["name"] for x in d["ranking"]] == ["D:/ProjB", "D:/ProjA"]
    assert d["top"]["name"] == "D:/ProjB"
    assert "成本最高" in r["answer"] and "D:/ProjB" in r["answer"]
    json.dumps(r, ensure_ascii=False)


def test_q2_top_tool_uses_tool_compare(monkeypatch):
    """上周哪个工具最贵 → q2 tool 口径，路由到 tool_compare（惰性 fake 模块）。"""
    fake_cmp = types.SimpleNamespace(compare_tools=lambda days, root, config: {
        "tools": [{"tool": "codex", "cost_total": 9.0, "tokens_total": 3000,
                   "sessions": 5, "quality_avg": 70, "cost_per_1k_tokens": 3.0,
                   "chars_per_dollar": 1.5}],
        "notice": "fake notice"})
    monkeypatch.setattr(query, "_MODS", {"tool_compare": fake_cmp})
    _patch_sources(monkeypatch)
    r = query.run_query("上周哪个工具最贵", "root", {}, today=TODAY)
    assert r["ok"] and r["tpl"] == "q2"
    d = r["data"]
    assert d["scope"] == "工具" and d["source"] == "tool_compare"
    assert d["top"]["name"] == "codex"
    assert r["answer"].startswith("上周") and "成本最高" in r["answer"]


def test_q2_top_model(monkeypatch):
    """上周成本最高的模型 → q2 model 口径（by_model 合并）。"""
    def collect(day, config):
        return make_collect(day, tools={
            "opencode": {"cost": 0.5, "tokens": 500},
            "codex": {"cost": 3.0, "tokens": 1500}})
    monkeypatch.setattr(query.ai_sessions, "collect", collect)
    monkeypatch.setattr(query.report, "aggregate", lambda day, root: make_agg(day))
    r = query.run_query("上周成本最高的模型", "root", {}, today=TODAY)
    assert r["ok"] and r["tpl"] == "q2"
    assert r["data"]["scope"] == "模型"
    assert r["data"]["top"]["name"] == "tm"  # fake 输出统一模型名 tm
    assert "模型" in r["answer"]


def test_q3_focus_trend(monkeypatch):
    """最近 3 天专注度怎么样 → q3（逐日 focus + growth 周快照 + 趋势判定）。"""
    fake_growth = types.SimpleNamespace(growth_snapshot=lambda root, config: {
        "weeks": [{"week": "2099-W01", "focus_score": 55, "days": 3},
                  {"week": "2098-W52", "focus_score": 50, "days": 3}],
        "trend": [], "updated_at": "x", "source": "fresh"})
    monkeypatch.setattr(query, "_MODS", {"growth": fake_growth})
    _patch_sources(monkeypatch, focus=60)
    r = query.run_query("最近 3 天专注度怎么样", "root", {}, today=TODAY)
    assert r["ok"] and r["tpl"] == "q3"
    d = r["data"]
    assert d["days"] == 3
    assert d["rows"][0] == {"date": "2099-01-02", "focus_score": 60}
    assert d["stats"]["avg"] == 60 and d["stats"]["days_with_data"] == 3
    assert d["stats"]["trend"] == "flat"
    assert isinstance(d["weekly"], list)
    assert "2099-W01" in [w["week"] for w in d["weekly"]]  # 与区间重叠的周
    assert "专注度" in r["answer"] and "60" in r["answer"]
    json.dumps(r, ensure_ascii=False)


def test_q4_output_vs_git(monkeypatch):
    """昨天 AI 写了多少行代码 → q4（collect.generated_lines + git_insights）。"""
    _patch_sources(monkeypatch)
    r = query.run_query("昨天 AI 写了多少行代码", "root", {}, today=TODAY)
    assert r["ok"] and r["tpl"] == "q4"
    t = r["data"]["totals"]
    assert t["ai_lines"] == 20        # fake collect 默认 generated_lines
    assert t["ai_chars"] == 200
    assert t["git_lines"] == 500
    assert t["git_commits"] == 2
    assert r["data"]["git_configured"] is True
    assert "500" in r["answer"] and "Git" in r["answer"]
    assert "20" in r["answer"]
    json.dumps(r, ensure_ascii=False)


def test_q4_vs_without_git(monkeypatch):
    """Git 未配置 → git_configured=False，文案提示仅 AI 侧统计。"""
    _patch_sources(monkeypatch, git_result={
        "enabled": True, "found": False,
        "total": {"commit_count": 0, "lines_added": 0, "churn": 0,
                  "modify_ratio": 0.0}})
    r = query.run_query("本周 AI 产出 vs Git 产出", "root", {}, today=TODAY)
    assert r["ok"] and r["tpl"] == "q4"
    assert r["data"]["git_configured"] is False
    assert "Git 未配置" in r["answer"]


def test_q5_activity(monkeypatch):
    """昨天 AI 用了多久 → q5（by_ai 分钟 + ai_tool 会话）。"""
    _patch_sources(monkeypatch, agg_days={
        "2099-01-04": make_agg("2099-01-04", minutes_ms=600000, n_ai_sessions=2)})
    r = query.run_query("昨天 AI 用了多久", "root", {}, today=TODAY)
    assert r["ok"] and r["tpl"] == "q5"
    t = r["data"]["totals"]
    assert t["minutes"] == pytest.approx(10.0)
    assert t["sessions"] == 2
    assert r["data"]["by_tool"] == [{"tool": "opencode", "minutes": 10.0}]
    assert "10" in r["answer"] and "会话" in r["answer"]
    json.dumps(r, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 拒绝路径：未命中 / 空 / 注入
# ---------------------------------------------------------------------------
def test_unsupported_and_injection_rejected(monkeypatch):
    _patch_sources(monkeypatch)
    for q in ("今天天气怎么样", "帮我写个爬虫", "SELECT * FROM usage",
              "昨天 opencode 花了多少钱 OR 1=1",
              "'; DROP TABLE usage;--", "本周哪个项目成本最高 UNION SELECT 1",
              "上周专注度趋势 NULL"):
        r = query.run_query(q, "root", {}, today=TODAY)
        assert not r["ok"], f"应拒绝：{q!r}"
        assert r.get("error") == "unsupported question", r


def test_empty_question_rejected(monkeypatch):
    _patch_sources(monkeypatch)
    for q in ("", "   ", "？？", "。。。", None, 12345):
        r = query.run_query(q, "root", {}, today=TODAY)
        assert not r["ok"] and r.get("error") == "empty question", repr(q)


def test_config_disabled(monkeypatch):
    _patch_sources(monkeypatch)
    r = query.run_query("昨天 opencode 花了多少钱", "root",
                        {"query": {"enabled": False}}, today=TODAY)
    assert r["ok"] and r["answer"] == "查询功能未启用（config.query.enabled=false）"
    assert r["data"] == {}


# ---------------------------------------------------------------------------
# tpl= 显式模式（指南 §6.2 兼容）
# ---------------------------------------------------------------------------
def test_run_template_ok(monkeypatch):
    _patch_sources(monkeypatch)
    r = query.run_template("q1", {"start": ["2099-01-03"], "end": ["2099-01-05"]},
                           "root", {}, today=TODAY)
    assert r["ok"] and r["tpl"] == "q1"
    assert r["start"] == "2099-01-03" and r["end"] == "2099-01-05"
    assert r["days"] == 3
    assert r["data"]["totals"]["cost"] == pytest.approx(3.0)
    assert "2099-01-03 至 2099-01-05" in r["answer"]


def test_run_template_default_range(monkeypatch):
    _patch_sources(monkeypatch)
    r = query.run_template("q5", {}, "root", {}, today=TODAY)
    assert r["ok"] and r["days"] == 7
    assert r["end"] == "2099-01-04" and r["start"] == "2098-12-29"
    r3 = query.run_template("q3", {}, "root", {}, today=TODAY)
    assert r3["days"] == 14  # focus 默认 14 天


def test_run_template_errors(monkeypatch):
    _patch_sources(monkeypatch)
    bad = query.run_template("q9", {"start": ["2099-01-01"]}, "root", {})
    assert not bad["ok"] and "unknown template" in bad["error"]
    bad = query.run_template("q1", {"start": ["2099-13-99"], "end": ["2099-01-05"]}, "root", {})
    assert not bad["ok"] and bad["error"] == "invalid date"
    bad = query.run_template("q1", {"start": ["2099-01-05"], "end": ["2099-01-01"]}, "root", {})
    assert not bad["ok"] and bad["error"] == "invalid range"
    bad = query.run_template("q1", {"start": ["2000-01-01"], "end": ["2099-01-01"]}, "root", {})
    assert not bad["ok"] and bad["error"] == "range too large"
    bad = query.run_template("q1", {"start": ["2026-08-aa"]}, "root", {})
    assert not bad["ok"] and bad["error"] == "invalid date"


def test_run_template_project_and_scope(monkeypatch):
    """tpl 模式显式 scope/tool 参数传递。"""
    fake_cmp = types.SimpleNamespace(compare_tools=lambda days, root, config: {
        "tools": [{"tool": "opencode", "cost_total": 4.0, "tokens_total": 2000,
                   "sessions": 2, "quality_avg": 65, "cost_per_1k_tokens": 2.0,
                   "chars_per_dollar": 3.0}]})
    monkeypatch.setattr(query, "_MODS", {"tool_compare": fake_cmp})
    _patch_sources(monkeypatch)
    r = query.run_template("q2", {"scope": ["模型"], "start": ["2099-01-04"],
                                  "end": ["2099-01-04"]}, "root", {}, today=TODAY)
    assert r["ok"] and r["data"]["scope"] == "模型"


# ---------------------------------------------------------------------------
# 元数据与配置
# ---------------------------------------------------------------------------
def test_template_list():
    meta = query.template_list()
    assert [m["id"] for m in meta] == ["q1", "q2", "q3", "q4", "q5"]
    for m in meta:
        assert m["title"] and m["notice"] and m["examples"]
    # 模板 ID 唯一、解析器/文案齐全
    ids = [m["id"] for m in meta]
    assert len(ids) == len(set(ids))
    for tpl in query.TEMPLATES:
        assert tpl["scope"] in query._RESOLVERS
        assert tpl["scope"] in query._ANSWERS
        assert tpl["patterns"], tpl


def test_query_config_defaults():
    cfg = query.query_config({})
    assert cfg == {"enabled": True, "max_days": 92, "top": 10,
                   "flat_threshold": 0.03}
    cfg2 = query.query_config({"query": {"enabled": False, "max_days": "x",
                                         "top": 5, "flat_threshold": 0.1}})
    assert cfg2["enabled"] is False and cfg2["max_days"] == 92
    assert cfg2["top"] == 5 and cfg2["flat_threshold"] == pytest.approx(0.1)


def test_all_results_json_serializable(monkeypatch):
    """所有模板结果可直接 json.dumps（无 datetime/非标准类型）。"""
    fake_cmp = types.SimpleNamespace(compare_tools=lambda days, root, config: {
        "tools": [{"tool": "t1", "cost_total": 1.0, "tokens_total": 1,
                   "sessions": 1, "quality_avg": 60, "cost_per_1k_tokens": 1.0,
                   "chars_per_dollar": 1.0}]})
    fake_growth = types.SimpleNamespace(growth_snapshot=lambda root, config: {
        "weeks": [], "trend": []})
    monkeypatch.setattr(query, "_MODS", {"tool_compare": fake_cmp, "growth": fake_growth})
    _patch_sources(monkeypatch)
    for q in ("昨天 opencode 花了多少钱", "本周哪个项目成本最高",
              "最近 3 天专注度趋势", "上周 AI vs Git", "本周 AI 会话情况"):
        r = query.run_query(q, "root", {}, today=TODAY)
        assert r["ok"], q
        json.dumps(r, ensure_ascii=False)  # 不抛 TypeError 即通过