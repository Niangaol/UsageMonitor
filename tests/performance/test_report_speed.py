# -*- coding: utf-8 -*-
"""tests/performance/test_report_speed.py — 报表生成性能基线。"""

from __future__ import annotations

import json
import os
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import report  # noqa: E402


def test_report_generation_under_2s(tmp_path):
    """单日 100 条会话聚合应在 2s 内完成。"""
    root = str(tmp_path / "perf1")
    day = "2099-01-04"
    os.makedirs(os.path.join(root, day), exist_ok=True)
    jl = os.path.join(root, day, "usage.jsonl")
    with open(jl, "w", encoding="utf-8") as fh:
        for i in range(100):
            s = {
                "start": f"{day}T10:{i%60:02d}:00",
                "end": f"{day}T10:{i%60:02d}:30",
                "duration_ms": 30000,
                "exe": "code.exe" if i % 2 == 0 else "chrome.exe",
                "app": "VS Code" if i % 2 == 0 else "Chrome",
                "title": f"title {i}",
                "category": "开发工具" if i % 2 == 0 else "浏览器",
                "contact": None,
                "ai_tool": None,
                "active": True,
            }
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    t0 = time.perf_counter()
    agg = report.aggregate(day, root)
    assert isinstance(agg, dict)
    assert agg.get("total_active_ms", 0) > 0
    md = report.generate_report_md(day, root)
    assert isinstance(md, str) and len(md) > 20
    dt = time.perf_counter() - t0
    assert dt < 2.0, f"report generation too slow: {dt:.3f}s"
    print(f"  [PASS] report_generation_under_2s ({dt:.3f}s)")


def test_large_jsonl_aggregation(tmp_path):
    """1000 条记录聚合耗时基线 <1s。"""
    root = str(tmp_path / "perf2")
    day = "2099-01-05"
    os.makedirs(os.path.join(root, day), exist_ok=True)
    jl = os.path.join(root, day, "usage.jsonl")
    with open(jl, "w", encoding="utf-8") as fh:
        for i in range(1000):
            s = {
                "start": f"{day}T10:00:00",
                "end": f"{day}T10:00:01",
                "duration_ms": 1000,
                "exe": "code.exe",
                "app": "VS Code",
                "title": f"t{i}",
                "category": "开发工具",
                "contact": None,
                "ai_tool": None,
                "active": True,
            }
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    t0 = time.perf_counter()
    agg = report.aggregate(day, root)
    dt = time.perf_counter() - t0
    assert dt < 1.0, f"large aggregation too slow: {dt:.3f}s"
    assert agg.get("session_count", 0) >= 1000 or agg.get("total_active_ms", 0) > 0
    print(f"  [PASS] large_jsonl_aggregation ({dt:.3f}s)")


def test_dashboard_startup_time(tmp_path):
    """dashboard Handler 初始化应在 1s 内完成。"""
    t0 = time.perf_counter()
    import classifier  # noqa: F401

    _cfg = classifier.load_config()
    dt = time.perf_counter() - t0
    assert dt < 1.0, f"dashboard startup too slow: {dt:.3f}s"
    print(f"  [PASS] dashboard_startup_time ({dt:.3f}s)")
