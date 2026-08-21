# -*- coding: utf-8 -*-
"""tests/unit/test_learn.py — 在线统计基线（Welford 样本环 / z-score / 自愈 / 幂等）。"""

from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import learn  # noqa: E402


def _metrics(active=60.0, coding=30.0, sessions=10.0):
    return {"active_min": active, "coding_min": coding, "sessions": sessions}


def test_config_defaults_and_clamps():
    cfg = learn.baseline_config({})
    assert cfg["enabled"] is True and cfg["min_days"] == 7 and cfg["z_warn"] == 2.0
    cfg2 = learn.baseline_config({"insights": {"baseline": {
        "enabled": False, "min_days": 1, "z_warn": 99, "z_alert": "bad"}}})
    assert cfg2["enabled"] is False
    assert cfg2["min_days"] == 3          # 下限夹取
    assert cfg2["z_warn"] == 5.0          # 上限夹取
    assert cfg2["z_alert"] == cfg2["z_warn"]  # alert ≥ warn 不变式
    print("  [PASS] config_defaults_and_clamps")


def test_extract_metrics_from_agg():
    agg = {"total_active_ms": 120 * 60000, "session_count": 7,
           "by_category": {"开发工具": 30 * 60000, "AI编程": 10 * 60000, "游戏": 999}}
    m = learn.extract_metrics(agg)
    assert abs(m["active_min"] - 120) < 1e-6
    assert abs(m["coding_min"] - 40) < 1e-6   # 开发工具+AI编程，不含游戏
    assert m["sessions"] == 7
    print("  [PASS] extract_metrics_from_agg")


def test_score_warming_then_levels(tmp_path):
    root = str(tmp_path)
    # 样本不足 → warming
    learn.record(root, "2099-01-01", _metrics())
    r = learn.score(root, "2099-01-02", _metrics(600.0))
    assert r["n"] < 2 and all(s["level"] == "warming" for s in r["scores"].values())
    # 铺 10 天完全相同的常态（active=60），当日 200 分钟 → 全同值历史后突变，
    # std→0 但语义上就是异常（z 有显示上限，不会爆炸）
    for i in range(10, 20):
        learn.record(root, f"2099-01-{i:02d}", _metrics())
    r2 = learn.score(root, "2099-01-20", _metrics(active=200.0))
    sc = r2["scores"]["active_min"]
    assert sc["level"] == "anomaly" and abs(sc["z"]) <= 99
    # 当日与常态一致 → normal
    r3 = learn.score(root, "2099-01-20", _metrics(active=60.0))
    assert r3["scores"]["active_min"]["level"] == "normal"
    print("  [PASS] score_warming_then_levels")


def test_zscore_detects_deviation(tmp_path):
    root = str(tmp_path)
    # 交替样本制造非零方差：偶数日 30 分钟，奇数日 90 分钟
    for i in range(4, 14):
        v = 30.0 if i % 2 == 0 else 90.0
        learn.record(root, f"2099-02-{i:02d}", _metrics(active=v))
    # 当日 300 分钟 → 显著偏高
    r = learn.score(root, "2099-02-14", _metrics(active=300.0))
    sc = r["scores"]["active_min"]
    assert sc["z"] > 2.0 and sc["level"] in ("unusual", "anomaly")
    # 当日 45 分钟 → 轻微偏低
    r2 = learn.score(root, "2099-02-14", _metrics(active=45.0))
    assert r2["scores"]["active_min"]["z"] < 0
    print("  [PASS] zscore_detects_deviation")


def test_today_excluded_from_own_baseline(tmp_path):
    root = str(tmp_path)
    for i in range(4, 14):
        learn.record(root, f"2099-03-{i:02d}", _metrics(active=60.0))
    # 打分时传入极端当日值：不得影响自身分布（n 保持 10）
    r = learn.record_and_score_agg(root, "2099-03-14",
                                   {"total_active_ms": 900 * 60000, "session_count": 50,
                                    "by_category": {}})
    assert r["n"] == 10
    assert r["recorded"] is True
    # 再看基线文件：当日样本已记录，但打分用的是排除当日的旧分布
    state = json.load(open(os.path.join(root, "baselines.json"), encoding="utf-8"))
    assert state["days"]["2099-03-14"]["active_min"] == 900.0
    print("  [PASS] today_excluded_from_own_baseline")


def test_same_day_overwrite_not_duplicate(tmp_path):
    root = str(tmp_path)
    learn.record(root, "2099-04-01", _metrics(active=60.0))
    learn.record(root, "2099-04-01", _metrics(active=480.0))  # 同日重写=覆盖
    state = json.load(open(os.path.join(root, "baselines.json"), encoding="utf-8"))
    days = [d for d in state["days"] if d == "2099-04-01"]
    assert len(days) == 1
    assert state["days"]["2099-04-01"]["active_min"] == 480.0
    print("  [PASS] same_day_overwrite_not_duplicate")


def test_corrupt_state_self_heals(tmp_path):
    root = str(tmp_path)
    with open(os.path.join(root, "baselines.json"), "w", encoding="utf-8") as fh:
        fh.write("{broken json")
    r = learn.record_and_score_agg(root, "2099-05-01", {"total_active_ms": 60000,
                                                        "session_count": 1, "by_category": {}})
    assert r["n"] == 0 and r["recorded"] is True  # 自愈为空状态后正常写入
    print("  [PASS] corrupt_state_self_heals")


def test_window_pruned_to_max_days(tmp_path):
    root = str(tmp_path)
    for i in range(1, learn._MAX_DAYS + 10):
        d = f"2099-06-{i:02d}" if i <= 28 else (f"2099-07-{i - 28:02d}")
        learn.record(root, d, _metrics(active=float(i)))
    state = json.load(open(os.path.join(root, "baselines.json"), encoding="utf-8"))
    assert len(state["days"]) <= learn._MAX_DAYS
    print("  [PASS] window_pruned_to_max_days")
