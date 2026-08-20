# -*- coding: utf-8 -*-
"""tests/unit/test_sqlite_extra.py — sqlite_store 分支覆盖。"""

from __future__ import annotations

import json
import os

import sqlite_store


def _rec(day, start="10:00:00", dur=60000, exe="code.exe", app="VS Code"):
    return {
        "start": f"{day}T{start}",
        "end": f"{day}T10:01:00",
        "duration_ms": dur,
        "exe": exe,
        "app": app,
        "title": "a.py",
        "category": "开发工具",
        "contact": None,
        "ai_tool": None,
        "active": True,
    }


def test_sqlite_append_and_read(tmp_path):
    root = str(tmp_path / "sq1")
    os.makedirs(root, exist_ok=True)
    day = "2099-03-01"
    rec = _rec(day)
    ok = sqlite_store.append_record(root, day, rec)
    assert ok is True
    # 幂等
    ok2 = sqlite_store.append_record(root, day, rec)
    assert ok2 is False
    rows = sqlite_store.read_day(root, day)
    assert len(rows) == 1
    assert rows[0]["exe"] == "code.exe"
    print("  [PASS] sqlite_append_and_read")


def test_sqlite_backfill_and_verify(tmp_path):
    root = str(tmp_path / "sq2")
    day = "2099-03-02"
    os.makedirs(os.path.join(root, day), exist_ok=True)
    jl = os.path.join(root, day, "usage.jsonl")
    recs = [_rec(day, dur=1000 * (i + 1)) for i in range(3)]
    with open(jl, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    res = sqlite_store.backfill(root, days=[day])
    assert res["inserted"] == 3
    res2 = sqlite_store.backfill(root, days=[day])
    assert res2["inserted"] == 0  # 幂等跳过
    v = sqlite_store.verify(root)
    assert v["mismatches"] == []
    st = sqlite_store.status(root)
    assert st["exists"] is True and st["rows"] == 3
    print("  [PASS] sqlite_backfill_and_verify")


def test_sqlite_query_range_and_rebuild(tmp_path):
    root = str(tmp_path / "sq3")
    for d in ["2099-03-10", "2099-03-11"]:
        os.makedirs(os.path.join(root, d), exist_ok=True)
        jl = os.path.join(root, d, "usage.jsonl")
        with open(jl, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(_rec(d), ensure_ascii=False) + "\n")
    sqlite_store.backfill(root)
    rows = sqlite_store.query_range(root, "2099-03-10", "2099-03-11")
    assert len(rows) == 2
    # rebuild
    res = sqlite_store.rebuild(root)
    assert res["inserted"] == 2
    print("  [PASS] sqlite_query_range_and_rebuild")


def test_sqlite_bad_json_skipped(tmp_path):
    root = str(tmp_path / "sq4")
    day = "2099-03-15"
    os.makedirs(os.path.join(root, day), exist_ok=True)
    jl = os.path.join(root, day, "usage.jsonl")
    with open(jl, "w", encoding="utf-8") as fh:
        fh.write('{"bad":\n')  # 坏行
        fh.write(json.dumps(_rec(day), ensure_ascii=False) + "\n")
        fh.write("\n")  # 空行
    res = sqlite_store.backfill(root, days=[day])
    assert res["inserted"] == 1
    print("  [PASS] sqlite_bad_json_skipped")
