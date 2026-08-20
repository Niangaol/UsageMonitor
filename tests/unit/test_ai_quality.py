# -*- coding: utf-8 -*-
"""tests/unit/test_ai_quality.py — AI 会话质量评分（v2.5 P1）单元测试。

覆盖：分档边界、纯函数权重边界（全 0 / 全 1）、空会话中性、高产出对比、
返工惩罚、除零兜底、_conversation_summary 结构、_quality_summary 聚合。
零依赖、确定性、不依赖 Win32。
"""

from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402


def test_quality_grade_bands():
    """分档边界：≥80 优 / ≥65 良 / ≥45 中 / 其余待优化（阈值归入高档）。"""
    assert ai_sessions.quality_grade(100) == "优"
    assert ai_sessions.quality_grade(80) == "优"
    assert ai_sessions.quality_grade(79) == "良"
    assert ai_sessions.quality_grade(65) == "良"
    assert ai_sessions.quality_grade(64) == "中"
    assert ai_sessions.quality_grade(45) == "中"
    assert ai_sessions.quality_grade(44) == "待优化"
    assert ai_sessions.quality_grade(0) == "待优化"
    assert ai_sessions.quality_grade(-5) == "待优化"  # 异常输入不炸
    print("  [PASS] quality_grade_bands")


def test_quality_all_zero_inputs():
    """全 0 输入（无任何消息）→ 不崩溃、分数在 0-100、因子 0-1。"""
    qf = ai_sessions._conversation_quality(
        user_n=0, assistant_n=0, rounds=0, tokens_in=0, tokens_out=0,
        tokens_total=0, model_count=0, span_s=0.0, user_tokens=[], generated_chars=0,
    )
    assert 0 <= qf["score"] <= 100
    assert 0.0 <= qf["question_value"] <= 1.0
    assert 0.0 <= qf["rework"] <= 1.0
    assert 0.0 <= qf["stability"] <= 1.0
    assert 0.0 <= qf["context_health"] <= 1.0
    assert qf["grade"] in ("优", "良", "中", "待优化")
    print("  [PASS] quality_all_zero_inputs:", qf["score"])


def test_quality_no_user_messages_neutral():
    """无用户消息的会话：提问含金量取中性 0.5（不惩罚纯流式记录）。"""
    qf = ai_sessions._conversation_quality(
        user_n=0, assistant_n=5, rounds=0, tokens_in=0, tokens_out=250,
        tokens_total=250, model_count=1, span_s=1800.0,
        user_tokens=[], generated_chars=1000,
    )
    assert qf["question_value"] == 0.5
    print("  [PASS] quality_no_user_messages_neutral:", qf["question_value"])


def test_quality_high_output_beats_low_output():
    """等价轮次下，高生成量低 token（代码密度高）的会话分数更高。"""
    base = dict(user_n=6, assistant_n=6, rounds=6, tokens_in=120, tokens_total=3000,
                model_count=1, span_s=1800.0, user_tokens=[60] * 6)
    # 高产出：很长但 token 密度高（代码 → 4 字符/token 左右）
    high = ai_sessions._conversation_quality(
        **base, tokens_out=4000, generated_chars=16000)
    # 低产出：同样轮次但输出极短
    low = ai_sessions._conversation_quality(
        **base, tokens_out=40, generated_chars=40)
    assert high["score"] > low["score"], f"高产出应高于低产出: {high['score']} vs {low['score']}"
    assert high["context_health"] > low["context_health"]
    print(f"  [PASS] quality_high_output_beats_low_output: {low['score']} -> {high['score']}")


def test_quality_rework_penalty():
    """返工惩罚：用户侧 token 占绝对多数（反复粘贴）的会话分数显著更低。"""
    def run(user_n, assistant_n, rounds, tokens_in, tokens_out):
        return ai_sessions._conversation_quality(
            user_n=user_n, assistant_n=assistant_n, rounds=rounds,
            tokens_in=tokens_in, tokens_out=tokens_out,
            tokens_total=tokens_in + tokens_out, model_count=1, span_s=3600.0,
            user_tokens=[60] * user_n, generated_chars=tokens_out * 2,
        )
    normal = run(user_n=5, assistant_n=5, rounds=5, tokens_in=300, tokens_out=3000)
    rework = run(user_n=5, assistant_n=0, rounds=0, tokens_in=20000, tokens_out=0)
    assert rework["rework"] > normal["rework"]
    assert rework["score"] < normal["score"], f"返工会话应更低: {rework['score']} vs {normal['score']}"
    print(f"  [PASS] quality_rework_penalty: {normal['score']} vs {rework['score']}")


def test_quality_question_value_length_curve():
    """提问含金量长度曲线：适中(15-200 token)满值，过短/过长降分。"""
    def qv(tokens):
        qf = ai_sessions._conversation_quality(
            user_n=1, assistant_n=1, rounds=1, tokens_in=tokens, tokens_out=100,
            tokens_total=tokens + 100, model_count=1, span_s=60.0,
            user_tokens=[tokens], generated_chars=400,
        )
        return qf["question_value"]
    assert qv(60) == 1.0
    assert qv(15) == 1.0
    assert qv(200) == 1.0
    assert qv(2) < 1.0      # 单字提问降分
    assert qv(1000) < 1.0   # 整段粘贴降分
    # 语义：适中（满分）应高于两端（降分）
    assert qv(60) > qv(2) and qv(60) > qv(1000)
    print("  [PASS] quality_question_value_length_curve")


def test_quality_conversation_summary_structure(tmp_path):
    """_conversation_summary 合成桶 → 质量字段齐全（4 因子 + 分 + 档 + 声明）。"""
    day = "2099-03-01"
    msgs = [
        {"role": "user", "content": "帮我重构这个函数", "timestamp": f"{day}T09:00:00", "model": "m1", "cwd": "/repo/x"},
        {"role": "assistant", "content": "已经重构完成，测试通过。", "timestamp": f"{day}T09:01:00", "model": "m1", "cwd": "/repo/x"},
        {"role": "user", "content": "再优化一下性能", "timestamp": f"{day}T09:02:00", "model": "m1", "cwd": "/repo/x"},
        {"role": "assistant", "content": "性能提升 30%，见 diff。", "timestamp": f"{day}T09:03:00", "model": "m1", "cwd": "/repo/x"},
    ]
    items = [{"msg": m, "role": m["role"], "tokens": ai_sessions.estimate_tokens(ai_sessions._message_content(m)),
              "model": m["model"], "project": "x", "cost_in": 0.0, "cost_out": 0.0} for m in msgs]
    s = ai_sessions._conversation_summary("conv-1", "opencode", items, token_est=True)
    assert s is not None
    assert isinstance(s["quality_score"], int) and 0 <= s["quality_score"] <= 100
    assert s["quality_grade"] in ("优", "良", "中", "待优化")
    for key in ("question_value", "rework", "stability", "context_health"):
        assert key in s["quality_factors"]
        assert 0.0 <= s["quality_factors"][key] <= 1.0
    assert "非真实采纳率" in s["quality_notice"]
    # 空桶返回 None（与旧行为一致）
    assert ai_sessions._conversation_summary("conv-empty", "opencode", [], token_est=True) is None
    print("  [PASS] quality_conversation_summary_structure")


def test_quality_summary_aggregation():
    """_quality_summary：空列表空态；有分数 → avg/best/worst/分布正确。"""
    empty = ai_sessions._quality_summary([])
    assert empty["sessions_scored"] == 0 and empty["avg"] == 0
    assert empty["best"] is None and empty["worst"] is None
    assert sum(empty["grade_dist"].values()) == 0
    convs = [
        {"id": "a", "quality_score": 90, "quality_grade": "优"},
        {"id": "b", "quality_score": 70, "quality_grade": "良"},
        {"id": "c", "quality_score": 50, "quality_grade": "中"},
    ]
    agg = ai_sessions._quality_summary(convs)
    assert agg["sessions_scored"] == 3
    assert agg["avg"] == 70  # (90+70+50)/3 = 70
    assert agg["best"] == "a" and agg["best_score"] == 90
    assert agg["worst"] == "c" and agg["worst_score"] == 50
    assert agg["grade_dist"] == {"优": 1, "良": 1, "中": 1, "待优化": 0}
    # 无 quality_score 的旧会话被跳过
    mixed = ai_sessions._quality_summary([{"id": "old"}, {"id": "n", "quality_score": 60, "quality_grade": "良"}])
    assert mixed["sessions_scored"] == 1 and mixed["avg"] == 60
    print("  [PASS] quality_summary_aggregation")


def test_quality_collect_end_to_end(tmp_path):
    """collect 端到端：合成会话文件 → 会话带质量字段 + total.quality_summary。"""
    day = "2099-03-02"
    tool_dir = os.path.join(str(tmp_path), "opencode")
    os.makedirs(tool_dir, exist_ok=True)
    path = os.path.join(tool_dir, "sessions.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for i, (role, content) in enumerate([
            ("user", "帮我写一个函数"),
            ("assistant", "```python\ndef f(): return 42\n```"),
            ("user", "再加个测试"),
            ("assistant", "```python\ndef test_f(): assert f() == 42\n```"),
        ]):
            fh.write(json.dumps({
                "timestamp": f"{day}T09:0{i}:00", "role": role, "content": content,
                "model": "deepseek-chat", "cwd": "/repo/demo",
            }, ensure_ascii=False) + "\n")
    cfg = {"ai_sessions": {"enabled": True, "paths": {"opencode": [tool_dir]}}}
    r = ai_sessions.collect(day, cfg)
    assert r["found"] is True
    total = r["total"]
    assert total["quality_summary"]["sessions_scored"] == 1
    assert 0 <= total["quality_summary"]["avg"] <= 100
    conv = total["conversations"][0]
    assert isinstance(conv["quality_score"], int)
    assert set(conv["quality_factors"].keys()) == {
        "question_value", "rework", "stability", "context_health"}
    # 空数据日：quality_summary 空态而非报错
    r2 = ai_sessions.collect("2099-03-03", cfg)
    assert r2["found"] is False
    assert r2["total"]["quality_summary"]["sessions_scored"] == 0
    assert r2["total"]["quality_summary"]["avg"] == 0
    print("  [PASS] quality_collect_end_to_end")