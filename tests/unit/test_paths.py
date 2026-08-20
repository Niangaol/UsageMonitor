# -*- coding: utf-8 -*-
"""tests/unit/test_paths.py — 路径解析."""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import paths  # noqa: E402


def test_script_dir_is_absolute_and_exists():
    d = paths.script_dir()
    assert os.path.isabs(d)
    assert os.path.isdir(d)
    print("  [PASS] script_dir")


def test_default_data_root_equals_script_dir():
    assert paths.default_data_root() == paths.script_dir()
    print("  [PASS] default_data_root")


def test_script_dir_env_override(monkeypatch):
    # frozen 模拟：script_dir 返回 exe 所在目录
    import sys as _sys

    orig_frozen = getattr(_sys, "frozen", None)
    orig_exe = getattr(_sys, "executable", None)
    try:
        _sys.frozen = True  # type: ignore[attr-defined]
        _sys.executable = os.path.join(os.path.dirname(paths.script_dir()), "UsageMonitor.exe")  # type: ignore[attr-defined]
        d = paths.script_dir()
        assert os.path.isabs(d)
    finally:
        if orig_frozen is None:
            try:
                delattr(_sys, "frozen")
            except AttributeError:
                pass
        else:
            _sys.frozen = orig_frozen  # type: ignore[attr-defined]
        if orig_exe is not None:
            _sys.executable = orig_exe  # type: ignore[attr-defined]
    print("  [PASS] script_dir_frozen_branch")
