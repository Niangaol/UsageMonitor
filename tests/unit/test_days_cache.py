# -*- coding: utf-8 -*-
"""tests/unit/test_days_cache.py — _available_days 的 mtime/TTL 缓存（ROADMAP §9.2 #2）。"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import dashboard  # noqa: E402


def _mk_days(root: str, days: list[str]) -> None:
    for d in days:
        os.makedirs(os.path.join(root, d), exist_ok=True)


def test_available_days_sorted_and_filtered(tmp_path):
    """只认 YYYY-MM-DD 目录，且返回升序。"""
    root = str(tmp_path / "d1")
    os.makedirs(root, exist_ok=True)
    _mk_days(root, ["2026-08-12", "2026-08-10", "2026-08-11"])
    os.makedirs(os.path.join(root, "logs"), exist_ok=True)
    os.makedirs(os.path.join(root, "2026-08"), exist_ok=True)  # 月目录不算
    with open(os.path.join(root, "2026-08-09"), "w", encoding="utf-8") as fh:
        fh.write("file not dir")  # 同名文件也会被 fullmatch 命中，但仍非目录

    dashboard.invalidate_days_cache()
    days = dashboard._available_days(root)
    assert days == sorted(days)
    assert "2026-08-10" in days and "2026-08-12" in days
    assert "logs" not in days and "2026-08" not in days


def test_available_days_uses_cache(tmp_path, monkeypatch):
    """TTL 内 mtime 未变时命中缓存，不再 os.listdir。"""
    root = str(tmp_path / "d2")
    os.makedirs(root, exist_ok=True)
    _mk_days(root, ["2026-08-01", "2026-08-02"])

    dashboard.invalidate_days_cache()
    first = dashboard._available_days(root)
    assert len(first) == 2

    calls = {"n": 0}
    real_listdir = os.listdir

    def counting_listdir(path):
        calls["n"] += 1
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", counting_listdir)
    second = dashboard._available_days(root)
    assert second == first
    assert calls["n"] == 0, "TTL 内应命中缓存，不应再扫目录"


def test_available_days_returns_copy(tmp_path):
    """返回浅拷贝，调用方修改不污染缓存。"""
    root = str(tmp_path / "d3")
    os.makedirs(root, exist_ok=True)
    _mk_days(root, ["2026-08-05"])

    dashboard.invalidate_days_cache()
    a = dashboard._available_days(root)
    a.append("HACKED")
    b = dashboard._available_days(root)
    assert "HACKED" not in b


def test_available_days_detects_new_day_after_invalidate(tmp_path):
    """新增日期目录后（显式失效缓存）能感知。"""
    root = str(tmp_path / "d4")
    os.makedirs(root, exist_ok=True)
    _mk_days(root, ["2026-08-01"])

    dashboard.invalidate_days_cache()
    assert dashboard._available_days(root) == ["2026-08-01"]

    _mk_days(root, ["2026-08-02"])
    dashboard.invalidate_days_cache(root)
    assert dashboard._available_days(root) == ["2026-08-01", "2026-08-02"]


def test_available_days_mtime_change_busts_cache(tmp_path):
    """目录 mtime 变化时缓存失效（不依赖 TTL 到期）。"""
    root = str(tmp_path / "d5")
    os.makedirs(root, exist_ok=True)
    _mk_days(root, ["2026-08-01"])

    dashboard.invalidate_days_cache()
    dashboard._available_days(root)

    key = dashboard._days_cache_key(root)
    dashboard._days_cache[key]["mtime"] = -1.0  # 伪造旧 mtime
    _mk_days(root, ["2026-08-03"])
    days = dashboard._available_days(root)
    assert "2026-08-03" in days


def test_available_days_missing_root(tmp_path):
    """目录不存在时返回空列表且不抛异常。"""
    dashboard.invalidate_days_cache()
    assert dashboard._available_days(str(tmp_path / "nope")) == []


def test_days_cache_key_normalizes(tmp_path):
    """缓存键归一化：相对/绝对、大小写（Windows）同桶。"""
    root = str(tmp_path / "d6")
    os.makedirs(root, exist_ok=True)
    k1 = dashboard._days_cache_key(root)
    k2 = dashboard._days_cache_key(root.upper())
    k3 = dashboard._days_cache_key(os.path.join(root, ".", ""))
    assert k1 == k3
    if os.name == "nt":
        assert k1 == k2
