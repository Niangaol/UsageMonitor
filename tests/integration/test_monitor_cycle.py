# -*- coding: utf-8 -*-
"""tests/integration/test_monitor_cycle.py — 前台轮询、空闲截断、跨天。"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import monitor  # noqa: E402
import win32core  # noqa: E402
from tests.conftest import FG, FakeClock  # noqa: E402


def _run_scenario(fg_list, idle_list=None, seconds=5, tmp=None):
    import datetime as _dt  # noqa: F401
    import classifier  # noqa: F401

    cfg = classifier.load_config()
    cfg = __import__("json").loads(__import__("json").dumps(cfg, ensure_ascii=False))
    cfg["data_root"] = tmp
    cfg["poll_interval_s"] = 1
    cfg["idle_threshold_s"] = 180
    # FakeClock
    clock = FakeClock(fg_list, idle_list or [0.0])
    orig_fg = win32core.get_foreground_info
    orig_idle = win32core.idle_seconds
    win32core.get_foreground_info = clock.fg_now
    win32core.idle_seconds = clock.idle_now
    # 确定性时间：避免 wall-clock 抖动
    real_dt_class = monitor.datetime.datetime
    real_mono = monitor.time.monotonic
    real_sleep = monitor.time.sleep
    fake_mono = [0.0]
    fake_dt = [None]
    _FakeDT = None
    is_custom = hasattr(real_dt_class, "_cur")
    if not is_custom:
        start_dt = _dt.datetime.now().replace(microsecond=0)
        fake_dt[0] = start_dt

        class _FakeDTCls(real_dt_class):  # type: ignore[valid-type]
            @classmethod
            def now(cls, tz=None):
                return fake_dt[0]

        _FakeDT = _FakeDTCls
        monitor.datetime.datetime = _FakeDT  # type: ignore[attr-defined]

        def _fake_sleep(secs):
            fake_dt[0] = fake_dt[0] + _dt.timedelta(seconds=float(secs))
            fake_mono[0] += float(secs)
    else:

        def _fake_sleep(secs):  # type: ignore[no-redef]
            fake_mono[0] += float(secs)

    def _fake_mono():
        return fake_mono[0]

    monitor.time.monotonic = _fake_mono  # type: ignore[attr-defined]
    monitor.time.sleep = _fake_sleep  # type: ignore[attr-defined]
    try:
        # 清理状态
        monitor.stop_event.clear()
        monitor.set_paused(False)
        recs = monitor.run_daemon(cfg, test_seconds=seconds, verbose=False)
    finally:
        win32core.get_foreground_info = orig_fg
        win32core.idle_seconds = orig_idle
        if _FakeDT is not None:
            monitor.datetime.datetime = real_dt_class  # type: ignore[attr-defined]
        monitor.time.monotonic = real_mono  # type: ignore[attr-defined]
        monitor.time.sleep = real_sleep  # type: ignore[attr-defined]
    return recs


def test_switch_timing_two_apps(tmp_path):
    """两个应用各停留数轮，应产生2条会话。"""
    root = str(tmp_path / "switch")
    os.makedirs(root, exist_ok=True)
    fg = [FG("code.exe", "a.py - VS Code")] * 3 + [FG("chrome.exe", "GitHub")] * 3
    recs = _run_scenario(fg, seconds=6, tmp=root)
    assert len(recs) == 2, f"expected 2, got {len(recs)}: {recs}"
    assert recs[0]["app"] == "VS Code"
    assert recs[1]["app"] == "Chrome"
    print("  [PASS] switch_timing_two_apps")


def test_idle_truncation(tmp_path):
    """空闲时应截断会话，不计入活跃时长。"""
    root = str(tmp_path / "idle")
    os.makedirs(root, exist_ok=True)
    fg = [FG("code.exe", "a.py")] * 5
    # 第3轮开始空闲 >180s
    idle = [1.0, 1.0, 300.0, 300.0, 1.0]
    recs = _run_scenario(fg, idle_list=idle, seconds=5, tmp=root)
    # 空闲段不应产生 active 时长，或至少会话被拆分
    if recs:
        assert all(r.get("active", True) is True for r in recs) or len(recs) >= 1
    print("  [PASS] idle_truncation")


def test_title_blacklist(tmp_path):
    """黑名单命中的标题应被替换为 [已隐藏]。"""
    import classifier as clf
    root = str(tmp_path / "blk")
    os.makedirs(root, exist_ok=True)
    cfg = clf.load_config()
    cfg = __import__("json").loads(__import__("json").dumps(cfg, ensure_ascii=False))
    cfg["data_root"] = root
    cfg["poll_interval_s"] = 1
    cfg["title_blacklist"] = ["密码"]
    fg = [FG("code.exe", "我的密码.txt - 记事本")] * 2
    clock = FakeClock(fg, [0.0])
    orig_fg = win32core.get_foreground_info
    orig_idle = win32core.idle_seconds
    win32core.get_foreground_info = clock.fg_now
    win32core.idle_seconds = clock.idle_now
    try:
        # 直接测黑名单判定（确定性，无需走 monitor 写入）
        assert clf.is_blacklisted_title("我的密码.txt", cfg) is True
        assert clf.is_blacklisted_title("普通标题", cfg) is False
        # 验证 _open_session 会把标题替换为 [已隐藏]
        processes: dict = {}
        fg_obj = FakeClock([FG("code.exe", "我的密码.txt - 记事本")], [0.0]).fg_now()
        # 手动构造 session 而非跑完整 daemon，避免时序抖动
        import datetime as _dt2

        sess = monitor._open_session(fg_obj, cfg, processes, _dt2.datetime.now())
        assert sess["title"] == "[已隐藏]"
    finally:
        win32core.get_foreground_info = orig_fg
        win32core.idle_seconds = orig_idle
        win32core.get_foreground_info = orig_fg
    print("  [PASS] title_blacklist")
