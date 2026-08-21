# -*- coding: utf-8 -*-
"""tests/unit/test_alerts.py — 告警调度纯函数（配置归一化/工作累计/告警判定）。"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import alerts  # noqa: E402


def test_config_defaults_and_clamps():
    # 缺省段 → 全默认
    cfg = alerts.alerts_config({})
    assert cfg["enabled"] is True
    assert cfg["check_interval_s"] == 60
    assert cfg["rest_after_min"] == 120
    # 非法值回退默认、数值夹取
    cfg2 = alerts.alerts_config({"alerts": {
        "check_interval_s": 1,          # 低于下限 10 → 10
        "budget_check_min": "abc",      # 非法 → 默认 15
        "rest_after_min": 99999,        # 超上限 → 1440
        "enabled": "yes",               # 真值 → True
    }})
    assert cfg2["check_interval_s"] == 10
    assert cfg2["budget_check_min"] == 15
    assert cfg2["rest_after_min"] == 1440
    assert cfg2["enabled"] is True
    print("  [PASS] config_defaults_and_clamps")


def test_update_work_accumulate_and_reset():
    state = alerts.AlertState()
    cfg = alerts.alerts_config({})
    # 活跃：累加
    alerts.update_work(state, idle_seconds=0, dt_seconds=60, cfg=cfg)
    assert state.work_seconds == 60
    alerts.update_work(state, idle_seconds=10, dt_seconds=60, cfg=cfg)
    assert state.work_seconds == 120
    # 空闲达阈值：清零
    alerts.update_work(state, idle_seconds=cfg["idle_reset_s"], dt_seconds=60, cfg=cfg)
    assert state.work_seconds == 0.0
    # 负 dt 不减
    alerts.update_work(state, idle_seconds=0, dt_seconds=-5, cfg=cfg)
    assert state.work_seconds == 0.0
    print("  [PASS] update_work_accumulate_and_reset")


def test_evaluate_disabled_or_paused():
    state = alerts.AlertState()
    state.work_seconds = 999999
    cfg = alerts.alerts_config({"alerts": {"enabled": False}})
    assert alerts.evaluate_alerts(cfg, state, now=100.0) == []
    cfg_on = alerts.alerts_config({})
    assert alerts.evaluate_alerts(cfg_on, state, now=100.0, paused=True) == []
    print("  [PASS] evaluate_disabled_or_paused")


def test_rest_reminder_threshold_and_cooldown():
    state = alerts.AlertState()
    cfg = alerts.alerts_config({"alerts": {"rest_after_min": 30, "cooldown_min": 60}})
    # 未达阈值不触发
    state.work_seconds = 29 * 60
    assert alerts.evaluate_alerts(cfg, state, now=100.0) == []
    # 达阈值触发一次
    state.work_seconds = 31 * 60
    fired = alerts.evaluate_alerts(cfg, state, now=100.0)
    assert len(fired) == 1 and fired[0]["key"] == "rest"
    # 冷却期内不重复
    assert alerts.evaluate_alerts(cfg, state, now=100.0 + 59 * 60) == []
    # 冷却期过后再次触发
    fired2 = alerts.evaluate_alerts(cfg, state, now=100.0 + 61 * 60)
    assert len(fired2) == 1 and fired2[0]["key"] == "rest"
    print("  [PASS] rest_reminder_threshold_and_cooldown")


def test_budget_alert_transitions_and_daily_dedup():
    state = alerts.AlertState()
    cfg = alerts.alerts_config({})
    warn_st = {"enabled": True, "status": "warn", "period": "daily",
               "start": "2099-01-01", "spent": 8.5, "budget": 10.0, "ratio": 0.85}
    exceed_st = dict(warn_st, status="exceed", ratio=1.2)
    # ok 不触发
    assert alerts.evaluate_alerts(cfg, state, now=1.0,
                                  budget_st=dict(warn_st, status="ok")) == []
    # warn 触发一次，当日去重
    first = alerts.evaluate_alerts(cfg, state, now=1.0, budget_st=warn_st)
    assert len(first) == 1 and first[0]["key"] == "budget_warn"
    assert alerts.evaluate_alerts(cfg, state, now=3600.0, budget_st=warn_st) == []
    # 升级到 exceed 是独立键，可再触发
    second = alerts.evaluate_alerts(cfg, state, now=3700.0, budget_st=exceed_st)
    assert len(second) == 1 and second[0]["key"] == "budget_exceed"
    # 关闭对应开关则不触发
    cfg_off = alerts.alerts_config({"alerts": {"budget_warn": False}})
    fresh = alerts.AlertState()
    assert alerts.evaluate_alerts(cfg_off, fresh, now=1.0, budget_st=warn_st) == []
    # 跨天重新武装（start 变化）
    next_day = dict(warn_st, start="2099-01-02")
    again = alerts.evaluate_alerts(cfg, state, now=90000.0, budget_st=next_day)
    assert len(again) == 1 and again[0]["key"] == "budget_warn"
    print("  [PASS] budget_alert_transitions_and_daily_dedup")
