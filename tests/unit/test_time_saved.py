# -*- coding: utf-8 -*-
"""tests/unit/test_time_saved.py — Phase3 时间节省估算。"""

from __future__ import annotations

import insights


def _agg(ai_ms: int, total_ms: int = 3600000) -> dict:
    return {
        "total_active_ms": total_ms,
        "by_category": {"AI编程": ai_ms, "开发工具": total_ms - ai_ms},
        "by_ai": {"opencode": ai_ms} if ai_ms else {},
        "sessions": [{"duration_ms": total_ms}],
    }


def test_time_saved_basic():
    cfg = {"insights": {"enabled": True, "time_saved": {"enabled": True, "factor": 2.0, "min_ai_min": 10}}}
    agg = _agg(60 * 60000)  # 60 min AI
    r = insights.time_saved_insights(agg, cfg)
    assert r["enabled"] is True
    assert r["ai_ms"] == 3600000
    assert r["factor"] == 2.0
    assert r["saved_ms"] == 3600000  # 60 min saved
    assert r["est_manual_ms"] == 7200000
    print("  [PASS] time_saved_basic")


def test_time_saved_disabled():
    cfg = {"insights": {"enabled": True, "time_saved": {"enabled": False}}}
    agg = _agg(60 * 60000)
    r = insights.time_saved_insights(agg, cfg)
    assert r["enabled"] is False
    print("  [PASS] time_saved_disabled")


def test_time_saved_low_ai():
    cfg = {"insights": {"enabled": True, "time_saved": {"enabled": True, "factor": 2.0, "min_ai_min": 30}}}
    agg = _agg(5 * 60000)  # 5 min < 30
    r = insights.time_saved_insights(agg, cfg)
    assert r["enabled"] is True
    assert "较少" in r["label"] or "仅作参考" in r["label"]
    print("  [PASS] time_saved_low_ai")


def test_time_saved_factor_clamped():
    cfg = {"insights": {"enabled": True, "time_saved": {"enabled": True, "factor": 10.0}}}
    agg = _agg(30 * 60000)
    r = insights.time_saved_insights(agg, cfg)
    assert r["factor"] == 5.0  # clamped to 5.0
    print("  [PASS] time_saved_factor_clamped")


def test_time_saved_no_ai():
    cfg = {"insights": {"enabled": True, "time_saved": {"enabled": True}}}
    agg = _agg(0)
    r = insights.time_saved_insights(agg, cfg)
    assert r["enabled"] is True
    assert "无 AI" in r["label"]
    print("  [PASS] time_saved_no_ai")
