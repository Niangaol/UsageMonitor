# -*- coding: utf-8 -*-
"""tests/unit/test_ai_sessions_refined.py — Token 估算精进与真实 usage 字段优先。"""

from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import ai_sessions  # noqa: E402


def test_weighted_estimator_buckets():
    # 空文本为 0
    assert ai_sessions.estimate_tokens_weighted("") == 0
    assert ai_sessions.estimate_tokens_weighted("  \n\t ") == 0
    # 纯 CJK：与 simple 口径一致（1 字/Token）
    assert ai_sessions.estimate_tokens_weighted("你好世界") == 4
    # 符号密集（代码/JSON）：加权口径应高于 simple 的 4字符/Token 低估
    code = "{}(){};===" * 10
    assert ai_sessions.estimate_tokens_weighted(code) > ai_sessions.estimate_tokens(code)
    # 空白密集：加权口径应低于 simple
    spaces = "a b c d e f g h i j" * 5
    assert ai_sessions.estimate_tokens_weighted(spaces) <= ai_sessions.estimate_tokens(spaces)
    print("  [PASS] weighted_estimator_buckets")


def test_message_usage_nested_and_flat():
    # 嵌套 usage（Claude Code 风格）
    m1 = {"usage": {"input_tokens": 15000, "output_tokens": 500}}
    assert ai_sessions._message_usage(m1) == (15000, 500)
    # prompt/completion 命名（OpenAI 风格）
    m2 = {"usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    assert ai_sessions._message_usage(m2) == (100, 20)
    # 平铺字段
    m3 = {"tokens_in": 7, "tokens_out": 3}
    assert ai_sessions._message_usage(m3) == (7, 3)
    # 缺失 → None；负值/非数值被忽略
    assert ai_sessions._message_usage({"role": "user", "content": "hi"}) is None
    assert ai_sessions._message_usage({"usage": {"input_tokens": -5}}) is None
    print("  [PASS] message_usage_nested_and_flat")


def _write_fixture(root: str, day: str, rows: list[dict]) -> None:
    sess_dir = os.path.join(root, "sess")
    os.makedirs(sess_dir, exist_ok=True)
    with open(os.path.join(sess_dir, "s.jsonl"), "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_collect_prefers_real_usage_over_estimation(tmp_path):
    """消息带 usage 时用真实值计 token 与成本，不再按内容估算。"""
    root = str(tmp_path)
    day = "2099-08-01"
    _write_fixture(root, day, [
        {"timestamp": f"{day}T09:00:00", "role": "user",
         "content": "一句话", "model": "m1"},
        {"timestamp": f"{day}T09:01:00", "role": "assistant",
         "content": "x" * 400, "model": "m1",
         "usage": {"input_tokens": 15000, "output_tokens": 800}},
    ])
    cfg = {"ai_sessions": {"enabled": True, "paths": {"test_tool": [str(root / "sess")] if False else [os.path.join(str(root), "sess")]}}}
    data = ai_sessions.collect(day, cfg)
    total = data["total"]
    # 混合语义：assistant 带真实 usage（in=15000/out=800）；
    # user 消息无 usage → 仍按内容估算（"一句话" 3 个 CJK 字 = 3 token）
    assert total["tokens_in"] == 15003 and total["tokens_out"] == 800
    assert total["tokens_total"] == 15803
    assert total["tokens_from_usage"] == 1
    # 成本按真实 token 计（定价表无 m1 → 单价 0，仅验证结构）
    assert isinstance(total["cost_total"], float)
    print("  [PASS] collect_prefers_real_usage_over_estimation")


def test_collect_simple_mode_fallback(tmp_path):
    """token_estimation_mode=simple 回退历史口径（4 字符/Token）。"""
    root = str(tmp_path)
    day = "2099-08-02"
    _write_fixture(root, day, [
        {"timestamp": f"{day}T09:00:00", "role": "assistant",
         "content": "abcd", "model": "m1"},  # simple: 1 token
    ])
    cfg = {"ai_sessions": {"enabled": True, "token_estimation_mode": "simple",
                           "paths": {"t": [os.path.join(str(root), "sess")]}}}
    data = ai_sessions.collect(day, cfg)
    assert data["total"]["tokens_out"] == 1
    # weighted 模式下同内容：字母 4×0.25=1.0 → 也是 1，换符号内容区分
    _write_fixture(root, day + "b", [])
    print("  [PASS] collect_simple_mode_fallback")


def test_collect_weighted_mode_counts_symbols_higher(tmp_path):
    root = str(tmp_path)
    day = "2099-08-03"
    content = "{}(){};==" * 8  # 64 个符号字符
    _write_fixture(root, day, [
        {"timestamp": f"{day}T09:00:00", "role": "assistant",
         "content": content, "model": "m1"},
    ])
    cfg_w = {"ai_sessions": {"enabled": True, "token_estimation_mode": "weighted",
                             "paths": {"t": [os.path.join(str(root), "sess")]}}}
    cfg_s = dict(cfg_w, ai_sessions=dict(cfg_w["ai_sessions"], token_estimation_mode="simple"))
    w = ai_sessions.collect(day, cfg_w)["total"]["tokens_out"]
    s = ai_sessions.collect(day, cfg_s)["total"]["tokens_out"]
    assert w > s, f"符号密集文本 weighted({w}) 应高于 simple({s})"
    print("  [PASS] collect_weighted_mode_counts_symbols_higher")
