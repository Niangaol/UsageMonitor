# -*- coding: utf-8 -*-
"""tests/unit/test_insights_extra.py — rule/behavior/persona 真分支覆盖。"""

from __future__ import annotations

import insights


def _base_agg(**overrides) -> dict:
    base = {
        "date": "2026-08-08",
        "total_active_ms": 4 * 3600000,
        "by_category": {"办公学习": 3600000, "游戏": 1800000, "AI编程": 600000, "社交聊天": 300000},
        "by_browser": {"学习": 1800000},
        "by_ai": {"opencode": 600000},
        "by_contact": {"微信": {"张三": 300000}},
        "hourly_ms": [0] * 24,
        "sessions": [
            {"duration_ms": 30 * 60000, "app": "VS Code"},
            {"duration_ms": 120 * 60000, "app": "Chrome"},
        ],
        "by_app": {"VS Code": 2000000},
    }
    base.update(overrides)
    return base


def test_rule_study_reached():
    cfg = {"insights": {"enabled": True, "rules": {"study_goal_hours": 1}}}
    agg = _base_agg()
    out = insights.rule_insights(agg, cfg)
    assert any(o["type"] == "study" for o in out)
    print("  [PASS] rule_study_reached")


def test_rule_game_warn():
    cfg = {"insights": {"enabled": True, "rules": {"game_alert_hours": 1, "game_ratio_warn": 0.3}}}
    agg = _base_agg(total_active_ms=2 * 3600000, by_category={"游戏": 5400000})
    out = insights.rule_insights(agg, cfg)
    game = [o for o in out if o["type"] == "game"]
    assert game and game[0]["severity"] in ("warn", "alert")
    print("  [PASS] rule_game_warn")


def test_rule_health_long_session():
    cfg = {"insights": {"enabled": True, "rules": {"long_session_min": 60}}}
    agg = _base_agg(sessions=[{"duration_ms": 200 * 60000}])
    out = insights.rule_insights(agg, cfg)
    assert any(o["type"] == "health" for o in out)
    print("  [PASS] rule_health_long_session")


def test_rule_ai_efficiency():
    cfg = {"insights": {"enabled": True}}
    agg = _base_agg(by_category={"AI编程": 3600000}, by_ai={"opencode": 3600000})
    out = insights.rule_insights(agg, cfg)
    assert any(o["type"] == "efficiency" for o in out)
    print("  [PASS] rule_ai_efficiency")


def test_rule_disabled():
    cfg = {"insights": {"enabled": False}}
    agg = _base_agg()
    out = insights.rule_insights(agg, cfg)
    assert out == []
    print("  [PASS] rule_disabled")


def test_behavior_focus():
    cfg = {"insights": {"enabled": True}}
    agg = {
        "total_active_ms": 2 * 3600000,
        "by_category": {"AI编程": 3600000, "开发工具": 0},
        "sessions": [
            {"duration_ms": 30 * 60000, "app": "VS Code", "start": "2026-08-08T10:00:00"},
            {"duration_ms": 45 * 60000, "app": "VS Code", "start": "2026-08-08T10:30:00"},
            {"duration_ms": 20 * 60000, "app": "Chrome", "start": "2026-08-08T11:30:00"},
        ],
        "hourly_ms": [0] * 24,
    }
    # set some hourly to have data
    agg["hourly_ms"][10] = 3600000
    b = insights.behavior_insights(agg, cfg)
    assert "focus_score" in b and 0 <= b["focus_score"] <= 100
    assert b["grade"] in ("高", "中", "低")
    print("  [PASS] behavior_focus")


def test_persona_label():
    cfg = {"insights": {"enabled": True, "persona": {"enabled": True}}}
    agg = {
        "total_active_ms": 180 * 60000,
        "by_category": {"AI编程": 120 * 60000, "开发工具": 30 * 60000},
        "by_ai": {"opencode": 120 * 60000},
        "sessions": [{"duration_ms": 120 * 60000, "app": "VS Code", "start": "2026-08-08T10:00:00"}],
        "hourly_ms": [0] * 10 + [3600000] + [0] * 13,
    }
    p = insights.persona_insights(agg, cfg)
    assert "label" in p
    print("  [PASS] persona_label")
