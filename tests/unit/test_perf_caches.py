# -*- coding: utf-8 -*-
"""tests/unit/test_perf_caches.py — 性能缓存的行为回归（对象复用 + 指纹失效）。

不测耗时（CI 不稳定），测语义：
- ai_sessions.collect：同指纹 → 返回同一共享对象；文件追加 → 指纹变 → 重算；
- browser_history.collect：同库 → 共享对象；库 mtime 变 → 重算；
- sqlite_store：共享连接幂等、rebuild 前释放句柄、close_connections 可清理。
"""

from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402
import browser_history  # noqa: E402
import sqlite_store  # noqa: E402


def _write_ai_file(sess_dir: str, day: str, rows: list[tuple[str, str]]) -> None:
    os.makedirs(sess_dir, exist_ok=True)
    with open(os.path.join(sess_dir, "s.jsonl"), "w", encoding="utf-8") as fh:
        for i, (role, content) in enumerate(rows):
            fh.write(json.dumps({"timestamp": f"{day}T09:{i:02d}:00", "role": role,
                                 "content": content, "model": "m1",
                                 "cwd": "/repo/demo"}, ensure_ascii=False) + "\n")


def test_ai_collect_cache_identity_and_invalidation(tmp_path):
    root = str(tmp_path)
    sess = os.path.join(root, "sess")
    day = "2099-11-01"
    _write_ai_file(sess, day, [("user", "hi"), ("assistant", "```\nx\n```")])
    cfg = {"ai_sessions": {"enabled": True, "paths": {"t": [sess]}}}
    a = ai_sessions.collect(day, cfg)
    b = ai_sessions.collect(day, cfg)
    assert a["total"] is b["total"], "同指纹应返回共享 total 对象"
    # 追加一条消息 → mtime 变 → 重算且 turns+1
    with open(os.path.join(sess, "s.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": f"{day}T10:00:00", "role": "user",
                             "content": "再来", "model": "m1"}, ensure_ascii=False) + "\n")
    c = ai_sessions.collect(day, cfg)
    assert c["total"]["turns"] == a["total"]["turns"] + 1, "追加后应自动失效重算"
    ai_sessions.invalidate_collect_cache()
    print("  [PASS] ai_collect_cache_identity_and_invalidation")


def test_browser_collect_cache_identity_and_invalidation(tmp_path):
    import make_demo_data  # noqa: E402 —— 复用演示 History 构造器
    db = os.path.join(str(tmp_path), "History")
    make_demo_data.make_history(db)
    day = "2026-08-12"
    cfg = {"browser_history_enabled": True}
    browser_history.invalidate_visits_cache()
    a = browser_history.collect(day, str(tmp_path), cfg, db_paths=[db])
    b = browser_history.collect(day, str(tmp_path), cfg, db_paths=[db])
    assert a is b, "同库指纹应返回共享对象"
    # touch 库 → mtime 变 → 重算（新对象）
    os.utime(db, None)
    c = browser_history.collect(day, str(tmp_path), cfg, db_paths=[db])
    assert c is not a, "库变化后应重算"
    browser_history.invalidate_visits_cache()
    print("  [PASS] browser_collect_cache_identity_and_invalidation")


def test_sqlite_shared_conn_and_rebuild_release(tmp_path):
    root = str(tmp_path)
    day = "2099-11-02"
    rec = {"start": f"{day}T10:00:00", "end": f"{day}T10:01:00", "duration_ms": 60000,
           "exe": "a.exe", "app": "A", "title": "t", "category": "其他", "active": True}
    # JSONL 是事实源：rebuild 从 JSONL 回填，需先落 JSONL
    day_dir = os.path.join(root, day)
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    assert sqlite_store.append_record(root, day, rec) is True
    # rebuild 前必须释放共享句柄：重建后能全量回填
    r = sqlite_store.rebuild(root)
    assert r["inserted"] == 1 and r["skipped"] == 0, r
    # 清理后数据仍在（JSONL 事实源回填成功）
    rows = sqlite_store.read_day(root, day)
    assert len(rows) == 1
    sqlite_store.close_connections()
    print("  [PASS] sqlite_shared_conn_and_rebuild_release")
