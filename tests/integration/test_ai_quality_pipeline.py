# -*- coding: utf-8 -*-
"""tests/integration/test_ai_quality_pipeline.py — AI 会话质量（v2.5 P1）集成测试。

覆盖：collect → quality_summary 全链路、insights 卡片、report 日报章节、
dashboard /api/ai-sessions 与 /api/insights 透出。全部在临时目录内隔离。
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import threading

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402


def _write_fixture(root: str, day: str) -> str:
    """构造隔离的 data_root：config.json（paths 指向 tmp 内目录）+ 两个会话文件。

    返回会话目录路径。会话1：高质量多轮；会话2：单条未完成提问（质量应更低）。
    """
    sess_dir = os.path.join(root, "ai_sessions_data")
    os.makedirs(sess_dir, exist_ok=True)
    cfg = {
        "data_root": root,
        "ai_sessions": {"enabled": True, "paths": {"opencode": [sess_dir]}},
    }
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)

    # 会话 1：高质量（4 消息、2 轮、有实质输出）
    with open(os.path.join(sess_dir, "good.jsonl"), "w", encoding="utf-8") as fh:
        for i, (role, content) in enumerate([
            ("user", "请帮我设计一个缓存模块"),
            ("assistant", "```python\nclass Cache:\n    def get(self, k): ...\n```"),
            ("user", "请补充并发安全"),
            ("assistant", "```python\nimport threading\nclass Cache:\n    _lock = threading.Lock()\n```"),
        ]):
            fh.write(json.dumps({
                "timestamp": f"{day}T09:0{i}:00", "role": role, "content": content,
                "model": "deepseek-chat", "cwd": "/repo/demo",
            }, ensure_ascii=False) + "\n")
    # 会话 2：单条未完成提问 + 粘贴大段日志（返工信号）
    with open(os.path.join(sess_dir, "bad.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "timestamp": f"{day}T10:00:00", "role": "user",
            "content": "ERROR" * 400,  # 大量粘贴日志
            "model": "deepseek-chat", "cwd": "/repo/demo",
        }, ensure_ascii=False) + "\n")
    return sess_dir


def _req(port, method, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, headers=headers or {})
    r = conn.getresponse()
    body = r.read()
    conn.close()
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        data = {}
    return r.status, data


def test_collect_quality_pipeline(tmp_path):
    """collect 全链路：两会话质量分合理排序 + 汇总字段齐全。"""
    day = "2099-03-10"
    root = str(tmp_path / "qpipe")
    os.makedirs(root, exist_ok=True)
    sess_dir = _write_fixture(root, day)
    cfg = {"ai_sessions": {"enabled": True, "paths": {"opencode": [sess_dir]}}}
    r = ai_sessions.collect(day, cfg)
    assert r["found"] is True
    convs = r["total"]["conversations"]
    assert len(convs) == 2
    by_id = {c["id"]: c for c in convs}
    good_id = next(k for k in by_id if "good" in k)
    bad_id = next(k for k in by_id if "bad" in k)
    good, bad = by_id[good_id], by_id[bad_id]
    assert good["quality_score"] > bad["quality_score"], (
        f"高质量会话应高于粘贴未完成会话: {good['quality_score']} vs {bad['quality_score']}")
    assert all(0 <= c["quality_score"] <= 100 for c in convs)
    qs = r["total"]["quality_summary"]
    assert qs["sessions_scored"] == 2
    assert 0 <= qs["avg"] <= 100
    assert sum(qs["grade_dist"].values()) == 2
    assert qs["best"] == good_id and qs["best_score"] == good["quality_score"]
    # 会话级透明声明
    assert "非真实采纳率" in good["quality_notice"]
    print("  [PASS] collect_quality_pipeline")


def test_quality_insights_cards(tmp_path):
    """insights.conversation_quality_insights：有数据产卡 / 空数据空列表。"""
    import insights  # noqa: PLC0415
    day = "2099-03-11"
    root = str(tmp_path / "qcards")
    os.makedirs(root, exist_ok=True)
    sess_dir = _write_fixture(root, day)
    data = ai_sessions.collect(day, {"ai_sessions": {"enabled": True, "paths": {"opencode": [sess_dir]}}})
    cards = insights.conversation_quality_insights(data)
    assert len(cards) >= 1
    card = cards[0]
    assert card["type"] == "ai_quality"
    assert card["severity"] in ("info", "warn", "alert")
    assert "AI 会话质量" in card["title"]
    assert "启发式估算" in card["detail"]
    # 空/无效输入 → 空列表（不炸）
    assert insights.conversation_quality_insights(None) == []
    assert insights.conversation_quality_insights({}) == []
    assert insights.conversation_quality_insights({"found": False}) == []
    empty = ai_sessions.collect("2099-03-12", {"ai_sessions": {"enabled": True, "paths": {"opencode": [sess_dir]}}})
    assert insights.conversation_quality_insights(empty) == []
    print("  [PASS] quality_insights_cards")


def test_quality_report_section(tmp_path):
    """日报 AI 会话深度章节：质量摘要行 + 表格质量列。"""
    import report  # noqa: PLC0415
    day = "2099-03-13"
    root = str(tmp_path / "qreport")
    os.makedirs(root, exist_ok=True)
    _write_fixture(root, day)
    md = report._ai_sessions_daily(day, root)
    assert md is not None
    assert "会话质量" in md
    assert "均分" in md
    assert "| 质量" in md  # 表格列头
    assert "非采纳率" in md
    # 无数据日期 → None（不影响日报主体）
    assert report._ai_sessions_daily("2099-03-14", root) is None
    print("  [PASS] quality_report_section")


def test_quality_dashboard_endpoint(tmp_path):
    """dashboard：/api/ai-sessions 透出 quality；/api/insights 含 ai_quality。"""
    import dashboard  # noqa: PLC0415
    day = "2099-03-15"
    root = str(tmp_path / "qdash")
    os.makedirs(root, exist_ok=True)
    _write_fixture(root, day)
    server = dashboard.create_server(root, port=0, config_path=os.path.join(root, "config.json"))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        st, d = _req(port, "GET", f"/api/ai-sessions?date={day}")
        assert st == 200, f"/api/ai-sessions status {st} {d}"
        convs = (d.get("ai_sessions") or {}).get("total", {}).get("conversations", [])
        assert len(convs) >= 1
        c = convs[0]
        assert isinstance(c.get("quality_score"), int)
        assert set((c.get("quality_factors") or {}).keys()) == {
            "question_value", "rework", "stability", "context_health"}
        qs = d["ai_sessions"]["total"]["quality_summary"]
        assert qs["sessions_scored"] == 2 and 0 <= qs["avg"] <= 100
        # /api/insights 契约追加 ai_quality（失败也应为可序列化空数组，非 500）
        st2, d2 = _req(port, "GET", f"/api/insights?date={day}")
        assert st2 == 200, f"/api/insights status {st2} {d2}"
        assert "ai_quality" in d2
        assert isinstance(d2["ai_quality"], list)
        assert d2["ai_quality"][0]["type"] == "ai_quality"  # 本 fixture 有数据
        print("  [PASS] quality_dashboard_endpoint")
    finally:
        server.shutdown()
        server.server_close()