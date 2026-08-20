# -*- coding: utf-8 -*-
"""tests/conftest.py — pytest 全局 fixtures。

复用 test_all.py 的 FG / P / FakeClock 思路，改用 pytest monkeypatch/fixtures 风格。
零第三方依赖（仅 pytest）。
"""

from __future__ import annotations

import json
import os
import shutil
import sys

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest  # noqa: E402

import classifier  # noqa: E402
import monitor  # noqa: E402
import win32core  # noqa: E402

TMP_ROOT = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "usage_monitor_pytest")


# ---------------------------------------------------------------------------
# 数据模型（与 test_all.py 的 FG / P 等价）
# ---------------------------------------------------------------------------
class FG:
    def __init__(self, exe: str, title: str, pid: int = 999):
        self.exe = exe
        self.title = title
        self.pid = pid
        self.hwnd = 1


class P:
    def __init__(self, exe: str, ppid: int, pid: int):
        self.exe = exe
        self.ppid = ppid
        self.pid = pid


class FakeClock:
    """按轮询序号输出前台窗口与空闲秒数（最后一个元素重复）。

    每个轮询中 monitor 先调 idle_seconds() 再调 get_foreground_info()，
    因此 idle_now 不推进索引，fg_now 推进（两者对齐同一轮）。
    """

    def __init__(self, fg_list: list, idle_list: list | None = None):
        self.fg = fg_list
        self.idle = idle_list or [1.0]
        self.i = 0

    def fg_now(self):
        i = min(self.i, len(self.fg) - 1)
        self.i += 1
        return self.fg[i]

    def idle_now(self):
        i = min(self.i, len(self.idle) - 1)
        return self.idle[i]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_root() -> str:
    """提供隔离的临时数据根目录；测试结束后自动清理。"""
    path = os.path.join(TMP_ROOT, "pytest_tmp")
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def mock_config(monkeypatch) -> dict:
    """返回一份可安全修改的配置副本，data_root 指向临时目录。"""
    cfg = classifier.load_config()
    # 深拷贝避免污染全局缓存
    cfg = json.loads(json.dumps(cfg, ensure_ascii=False))
    cfg.setdefault("data_root", os.path.join(TMP_ROOT, "pytest_tmp"))
    cfg.setdefault("poll_interval_s", 1)
    cfg.setdefault("idle_threshold_s", 180)
    return cfg


@pytest.fixture
def fake_clock():
    """返回 FakeClock 工厂函数。"""
    def _make(fg_list, idle_list=None):
        return FakeClock(fg_list, idle_list)
    return _make


@pytest.fixture
def mock_win32(monkeypatch):
    """猴子补丁 win32core 的 get_foreground_info / idle_seconds / enum_processes。

    用法：
        clock = FakeClock([FG("a.exe", "t")] * 3)
        mock_win32(clock)
        # ... 调用 monitor.run_daemon ...
    """
    def _apply(clock: FakeClock | None = None, process_tree: dict | None = None):
        if clock is not None:
            monkeypatch.setattr(win32core, "get_foreground_info", clock.fg_now)
            monkeypatch.setattr(win32core, "idle_seconds", clock.idle_now)
        if process_tree is not None:
            monkeypatch.setattr(win32core, "enum_processes", lambda: dict(process_tree))
    return _apply


@pytest.fixture(autouse=True)
def _reset_monitor_state(monkeypatch):
    """每个测试前重置 monitor 的停止事件与暂停状态，防止测试间泄漏。"""
    monitor.stop_event.clear()
    monitor.set_paused(False)
    yield
    monitor.stop_event.clear()
    monitor.set_paused(False)
