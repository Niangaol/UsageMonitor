# -*- coding: utf-8 -*-
"""tests/api/test_baseline_api.py — 「简单学习」基线洞察经 /api/insights 透出。

场景：预写 baselines.json 制造 10 天常态，再种一个异常日 →
/api/insights 的 rules 里出现 type=trend 的基线异常卡片。
"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import learn  # noqa: E402

from tests.conftest import make_record, seed_day  # noqa: E402

_DAY = "2099-09-20"


def test_baseline_anomaly_surfaces_in_insights(api_server):
    client, root = api_server
    # 10 天常态：每天 60 分钟活跃（写入 learn 样本环）
    for i in range(10, 20):
        learn.record(root, f"2099-09-{i:02d}", {"active_min": 60.0,
                                                "coding_min": 30.0, "sessions": 5.0})
    # 当日异常：600 分钟活跃 + 60 会话
    seed_day(root, _DAY, [
        make_record(_DAY, h, 60) for h in range(8, 18)
    ])
    s, d, _ = client.get(f"/api/insights?date={_DAY}")
    assert s == 200 and "rules" in d
    trend_rules = [r for r in d["rules"] if r.get("type") == "trend"]
    assert trend_rules, f"应出现基线异常卡片: {d['rules']}"
    active_rule = next((r for r in trend_rules if "总活跃" in r.get("title", "")), None)
    assert active_rule is not None
    assert active_rule["severity"] in ("warn", "alert")
    print("  [PASS] baseline_anomaly_surfaces_in_insights")


def test_baseline_warming_period_quiet(api_server):
    """样本不足 min_days 时预热期不打扰：rules 无 trend 卡片。"""
    client, root = api_server
    learn.record(root, "2099-09-25", {"active_min": 60.0, "coding_min": 30.0,
                                      "sessions": 5.0})
    seed_day(root, _DAY, [make_record(_DAY, 9, 300)])
    s, d, _ = client.get("/api/insights?date=2099-09-26")
    assert s == 200
    assert not [r for r in d["rules"] if r.get("type") == "trend"]
    print("  [PASS] baseline_warming_period_quiet")
