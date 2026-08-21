# -*- coding: utf-8 -*-
"""tests/unit/test_insights_deep.py — AI 洞察管线与客制化模块深度测试。

覆盖：
- save_ai_custom / load_ai_custom 往返与非法输入规范化
- list_provider_presets 内置预设 + 自定义合并
- build_ai_prompt 聚合统计 / 隐私过滤 / 语言指令
- ai_insights 未开启 / 缓存命中 / 刷新成功写缓存 / 坏 JSON 与 HTTP 错误降级
- ollama_models 解析与连接失败
- time_saved_insights 因子估算
- conversation_quality_insights 空数据

全部离线：网络调用一律通过 monkeypatch 替换 urllib.request.urlopen，绝不真实联网。
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest  # noqa: E402

import insights  # noqa: E402


# ---------------------------------------------------------------------------
# 测试辅助（聚合形状与 test_insights_extra.py 保持一致）
# ---------------------------------------------------------------------------
def _base_agg(**overrides) -> dict:
    """构造一份与 report.aggregate() 输出同形的聚合字典。"""
    base = {
        "date": "2026-08-08",
        "total_active_ms": 4 * 3600000,
        "session_count": 2,
        "by_category": {"办公学习": 3600000, "游戏": 1800000, "AI编程": 600000, "社交聊天": 300000},
        "by_browser": {"学习": 1800000},
        "by_ai": {"opencode": 600000},
        "by_contact": {"微信": {"张三": 300000}},
        "hourly_ms": [0] * 24,
        "sessions": [
            {"duration_ms": 30 * 60000, "app": "VS Code"},
            {"duration_ms": 120 * 60000, "app": "Chrome"},
        ],
        "by_app": {"VS Code": 2000000},
    }
    base.update(overrides)
    return base


class _FakeResp:
    """模拟 urlopen 返回的上下文管理器响应对象。"""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _ai_cfg(**overrides) -> dict:
    """构造一份显式配置的 insights.ai 段（provider=custom，不依赖本机自动发现）。"""
    ai = {
        "enabled": True,
        "provider": "custom",
        "base_url": "http://mock.local/v1",
        "api_key": "sk-test",
        "model": "test-model",
        "timeout_s": 5,
    }
    ai.update(overrides)
    return {"insights": {"enabled": True, "ai": ai}}


def _fake_aggregate(day: str, root: str) -> dict:
    """替换 report.aggregate，避免读真实数据目录。"""
    return {
        "date": day,
        "total_active_ms": 3600000,
        "session_count": 3,
        "sessions": [
            {"duration_ms": 1200000, "app": "VS Code", "title": "内部标题", "url": "https://internal.example"}
        ],
        "by_category": {"AI编程": 3600000},
        "by_app": {"VS Code": 3600000},
        "by_ai": {"opencode": 3600000},
        "by_browser": {},
        "by_contact": {},
        "hourly_ms": [0] * 24,
    }


# ---------------------------------------------------------------------------
# 1. save_ai_custom / load_ai_custom 往返与非法输入
# ---------------------------------------------------------------------------
def test_save_load_roundtrip(tmp_path):
    """保存自定义 providers/prompt 后重载应完全一致。"""
    custom = {
        "providers": [
            {"id": "my_ai", "name": "我的中转", "base_url": "https://relay.example.com/v1", "model": "glm-5"},
            # 空 name 应回退为 id
            {"id": "second", "name": "", "base_url": "https://b.example.com/v1", "model": "m2"},
        ],
        "prompt": {
            "sections": {"categories": False, "apps": False},
            "min_insights": 2,
            "max_insights": 5,
            "instruction": "只关注编程效率",
        },
    }
    saved = insights.save_ai_custom(str(tmp_path), custom)
    loaded = insights.load_ai_custom(str(tmp_path))
    assert loaded == saved
    assert [p["id"] for p in loaded["providers"]] == ["my_ai", "second"]
    assert loaded["providers"][1]["name"] == "second"
    assert loaded["prompt"]["sections"]["categories"] is False
    # 未指定的段回退默认开启
    assert loaded["prompt"]["sections"]["schedule"] is True
    assert loaded["prompt"]["min_insights"] == 2
    assert loaded["prompt"]["max_insights"] == 5
    assert loaded["prompt"]["instruction"] == "只关注编程效率"
    # 文件确实原子写入 data_root 下
    assert os.path.exists(os.path.join(str(tmp_path), "ai_custom.json"))
    print("  [PASS] save_load_roundtrip")


def test_save_invalid_inputs_normalized(tmp_path):
    """非法 provider id / 缺字段 / 重复 id / 越界数值 / 超长指令按源码规范化。"""
    custom = {
        "providers": [
            {"id": "Bad ID!", "name": "x", "base_url": "https://a", "model": "m"},  # id 不合法
            {"id": "ok_id", "name": "y", "base_url": "", "model": "m"},             # 缺 base_url
            {"id": "ok_id", "name": "y", "base_url": "https://a", "model": ""},     # 缺 model
            {"id": "dup", "name": "a", "base_url": "https://a", "model": "m"},
            {"id": "dup", "name": "b", "base_url": "https://b", "model": "m"},      # 重复 id 只保留首个
            "not-a-dict",                                                           # 非 dict 条目跳过
        ],
        "prompt": {"min_insights": 99, "max_insights": 99, "instruction": "好" * 600},
    }
    out = insights.save_ai_custom(str(tmp_path), custom)
    assert [p["id"] for p in out["providers"]] == ["dup"]
    # 数值夹取到 [1, 10]
    assert out["prompt"]["min_insights"] == 10
    assert out["prompt"]["max_insights"] == 10
    # 指令截断到 500 字符
    assert len(out["prompt"]["instruction"]) == 500
    # 重载与保存结果一致
    assert insights.load_ai_custom(str(tmp_path)) == out
    print("  [PASS] save_invalid_inputs_normalized")


def test_load_defaults_and_corrupt_file(tmp_path):
    """文件缺失 / 损坏时返回完整默认结构；非 dict 输入同样回退默认。"""
    default = insights.load_ai_custom(str(tmp_path))
    assert default["providers"] == []
    assert default["prompt"]["min_insights"] == 3
    assert default["prompt"]["max_insights"] == 6
    assert default["prompt"]["instruction"] == ""
    assert all(v is True for v in default["prompt"]["sections"].values())
    # 写入损坏 JSON 后仍回退默认（不抛异常）
    (tmp_path / "ai_custom.json").write_text("{broken json", encoding="utf-8")
    assert insights.load_ai_custom(str(tmp_path)) == default
    # save 收到非 dict 输入时规范化为默认结构
    assert insights.save_ai_custom(str(tmp_path), ["oops"]) == default
    print("  [PASS] load_defaults_and_corrupt_file")


# ---------------------------------------------------------------------------
# 2. list_provider_presets
# ---------------------------------------------------------------------------
def test_list_presets_builtin():
    """内置预设存在且不含任何密钥字段。"""
    presets = insights.list_provider_presets()
    by_id = {p["id"]: p for p in presets}
    for pid in ("opencodego", "openai", "deepseek", "moonshot", "openrouter", "zhipu", "qwen", "ollama", "custom"):
        assert pid in by_id
    assert by_id["openai"]["base_url"] == "https://api.openai.com/v1"
    assert by_id["deepseek"]["model"] == "deepseek-chat"
    # 预设列表不携带密钥
    assert all("api_key" not in p and "apiKey" not in p for p in presets)
    print("  [PASS] list_presets_builtin")


def test_list_presets_merge_custom():
    """自定义 providers 追加进列表；与内置 id 冲突时自定义优先；非法条目跳过。"""
    merged = insights.list_provider_presets([
        {"id": "myprov", "name": "自建", "base_url": "http://a.example", "model": "m1"},
        {"id": "openai", "name": "覆盖版", "base_url": "http://override.example", "model": "mm"},
        "not-a-dict",
        {"id": "", "name": "空 id 应被忽略"},
        42,
    ])
    by_id = {p["id"]: p for p in merged}
    assert by_id["myprov"] == {"id": "myprov", "name": "自建", "base_url": "http://a.example", "model": "m1"}
    # 自定义覆盖内置同名 id
    assert by_id["openai"]["name"] == "覆盖版"
    assert by_id["openai"]["base_url"] == "http://override.example"
    # 内置其余条目不受影响
    assert by_id["ollama"]["base_url"] == "http://127.0.0.1:11434/v1"
    print("  [PASS] list_presets_merge_custom")


# ---------------------------------------------------------------------------
# 3. build_ai_prompt
# ---------------------------------------------------------------------------
def test_build_prompt_zh_aggregates():
    """中文提示词包含聚合统计关键数字（总时长 / 会话数 / 类别 Top）。"""
    cfg = {"insights": {"enabled": True}}
    prompt = insights.build_ai_prompt(_base_agg(), cfg, None)
    assert "总活跃时长：240.0 分钟" in prompt          # 4 小时
    assert "会话数：2" in prompt
    assert "最长连续会话：120.0 分钟" in prompt
    assert "办公学习 60.0 分钟" in prompt              # 类别 Top 列表
    assert "请只返回一个 JSON 数组" in prompt           # 默认 3-6 条
    # weekly 段开启时附加近 7 天对比
    prompt_week = insights.build_ai_prompt(
        _base_agg(), cfg, {"total_active_ms": 180 * 60000, "session_count": 5},
        week={"total_ms": 7 * 3600000, "sessions": 21},
    )
    assert "昨日活跃时长：180.0 分钟" in prompt_week
    assert "近 7 天：日均活跃 60 分钟" in prompt_week
    print("  [PASS] build_prompt_zh_aggregates")


def test_build_prompt_language_en():
    """语言指令随 insights.ai.language 配置切换（zh/en）。"""
    zh = insights.build_ai_prompt(_base_agg(), {"insights": {"ai": {"language": "zh"}}}, None)
    en = insights.build_ai_prompt(_base_agg(), {"insights": {"ai": {"language": "en"}}}, None)
    assert "总活跃时长" in zh and "Total active time" not in zh
    assert "Total active time: 240.0 min" in en and "总活跃时长" not in en
    print("  [PASS] build_prompt_language_en")


def test_build_prompt_privacy_filter():
    """include_raw=False 时窗口标题/URL 原文不得出现；True 时可出现；联系人名始终不上送。"""
    cfg = {"insights": {"enabled": True}}
    agg = _base_agg(sessions=[
        {
            "duration_ms": 120 * 60000, "app": "Chrome",
            "title": "机密窗口标题绝密XYZ", "url": "https://secret.example.com/private",
        },
    ])
    safe = insights.build_ai_prompt(agg, cfg, None, include_raw=False)
    assert "机密窗口标题绝密XYZ" not in safe
    assert "secret.example.com" not in safe
    raw = insights.build_ai_prompt(agg, cfg, None, include_raw=True)
    assert "原始样本" in raw
    assert "机密窗口标题绝密XYZ" in raw
    assert "https://secret.example.com/private" in raw
    # 即使 include_raw=True，联系人名也永远不出现在提示词里
    assert "张三" not in raw
    print("  [PASS] build_prompt_privacy_filter")


def test_build_prompt_custom_sections_and_instruction():
    """客制化模块控制统计段开关、洞察数量范围与自定义指令。"""
    cfg = {"insights": {"enabled": True}}
    custom = {
        "prompt": {
            "sections": {"categories": False},
            "min_insights": 2,
            "max_insights": 4,
            "instruction": "关注深度工作占比",
        },
    }
    prompt = insights.build_ai_prompt(_base_agg(), cfg, None, custom=custom)
    assert "按类别时长" not in prompt          # 关闭的段不出现
    assert "按应用时长" in prompt              # 其余段保持默认开启
    assert "用户自定义关注点：关注深度工作占比" in prompt
    assert "包含 2-4 条洞察" in prompt         # 数量范围写入输出格式要求
    print("  [PASS] build_prompt_custom_sections_and_instruction")


# ---------------------------------------------------------------------------
# 4. ai_insights 管线（全程 mock urlopen，绝不真实联网）
# ---------------------------------------------------------------------------
def test_ai_disabled_no_network(monkeypatch, tmp_path):
    """AI 未开启时返回含 error 的 dict 且不发任何网络请求。"""
    calls: list[str] = []

    def forbidden(req, timeout=None):
        calls.append(getattr(req, "full_url", str(req)))
        raise AssertionError("AI 未开启时不允许发起网络请求")

    monkeypatch.setattr(insights.urllib.request, "urlopen", forbidden)
    cfg = {"insights": {"enabled": True, "ai": {"enabled": False}}}
    out = insights.ai_insights("2026-08-08", str(tmp_path), cfg)
    assert out["insights"] is None
    assert "未开启" in (out["error"] or "")
    assert calls == []
    print("  [PASS] ai_disabled_no_network")


def test_ai_cache_hit_no_network(monkeypatch, tmp_path):
    """已有缓存（<root>/<date>/insights.json）时直接返回缓存内容且不发网络请求。"""
    calls: list[str] = []

    def forbidden(req, timeout=None):
        calls.append(getattr(req, "full_url", str(req)))
        raise AssertionError("缓存命中时不允许发起网络请求")

    monkeypatch.setattr(insights.urllib.request, "urlopen", forbidden)
    # 按源码缓存格式预先写入 <data_root>/YYYY-MM-DD/insights.json
    day_dir = tmp_path / "2026-08-08"
    day_dir.mkdir()
    payload = {
        "generated_at": "2026-08-08T20:00:00",
        "model": "cached-model",
        "insights": [{"type": "study", "severity": "info", "title": "缓存洞察", "detail": "来自缓存"}],
        "error": None,
    }
    (day_dir / "insights.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    out = insights.ai_insights("2026-08-08", str(tmp_path), _ai_cfg(), refresh=False)
    assert out["error"] is None
    assert out["model"] == "cached-model"
    assert out["generated_at"] == "2026-08-08T20:00:00"
    assert out["insights"][0]["title"] == "缓存洞察"
    assert calls == []
    print("  [PASS] ai_cache_hit_no_network")


def test_ai_refresh_success_writes_cache(monkeypatch, tmp_path):
    """refresh=True + mock 返回 OpenAI chat completions 格式 → 解析出洞察列表并写缓存。"""
    content = json.dumps([
        {"type": "efficiency", "severity": "info", "title": "效率不错", "detail": "继续保持"},
        {"type": "game", "severity": "warn", "title": "游戏偏多", "detail": "控制时间"},
    ], ensure_ascii=False)
    body = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": content}}]}, ensure_ascii=False,
    )
    calls: list[dict] = []

    def fake_urlopen(req, timeout=None):
        calls.append({
            "url": req.full_url,
            "auth": req.get_header("Authorization"),
            "req_body": json.loads(req.data.decode("utf-8")),
        })
        return _FakeResp(body.encode("utf-8"))

    monkeypatch.setattr(insights.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(insights.report, "aggregate", _fake_aggregate)
    out = insights.ai_insights("2026-08-08", str(tmp_path), _ai_cfg(), refresh=True)
    assert out["error"] is None
    assert isinstance(out["insights"], list) and len(out["insights"]) == 2
    assert out["insights"][0]["type"] == "efficiency"
    assert out["insights"][1]["severity"] == "warn"
    assert out["model"] == "test-model"
    assert out["generated_at"]
    # 请求为 OpenAI 兼容格式：URL、鉴权头、模型名、提示词内容均正确
    assert len(calls) == 1
    assert calls[0]["url"].endswith("/chat/completions")
    assert calls[0]["auth"] == "Bearer sk-test"
    assert calls[0]["req_body"]["model"] == "test-model"
    assert "总活跃时长：60.0 分钟" in calls[0]["req_body"]["messages"][0]["content"]
    # 默认 send_raw_titles=False：窗口标题原文不得进入提示词
    assert "内部标题" not in calls[0]["req_body"]["messages"][0]["content"]
    # 成功后缓存写入 <root>/<date>/insights.json
    cache_file = tmp_path / "2026-08-08" / "insights.json"
    assert cache_file.exists()
    cached = json.loads(cache_file.read_text(encoding="utf-8"))
    assert isinstance(cached["insights"], list) and cached["error"] is None
    print("  [PASS] ai_refresh_success_writes_cache")


def test_ai_bad_json_error(monkeypatch, tmp_path):
    """HTTP 层返回坏 JSON → 降级为 error 非空、insights 为 None、不写缓存。"""

    def fake_urlopen(req, timeout=None):
        return _FakeResp(b"this is not json {{{")

    monkeypatch.setattr(insights.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(insights.report, "aggregate", _fake_aggregate)
    out = insights.ai_insights("2026-08-08", str(tmp_path), _ai_cfg(), refresh=True)
    assert out["insights"] is None
    assert "不是有效 JSON" in (out["error"] or "")
    assert not (tmp_path / "2026-08-08" / "insights.json").exists()
    print("  [PASS] ai_bad_json_error")


def test_ai_http_error(monkeypatch, tmp_path):
    """HTTP 500 错误 → 降级为 error 含状态码，不抛异常、不写缓存。"""

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", None, io.BytesIO(b"boom"))

    monkeypatch.setattr(insights.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(insights.report, "aggregate", _fake_aggregate)
    out = insights.ai_insights("2026-08-08", str(tmp_path), _ai_cfg(), refresh=True)
    assert out["insights"] is None
    assert "HTTP 500" in (out["error"] or "")
    assert not (tmp_path / "2026-08-08" / "insights.json").exists()
    print("  [PASS] ai_http_error")


# ---------------------------------------------------------------------------
# 5. ollama_models
# ---------------------------------------------------------------------------
def test_ollama_models_parse(monkeypatch):
    """GET <base>/api/tags 返回 models 列表 → 解析出名称列表（/v1 后缀被剥掉）。"""
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        payload = {"models": [{"name": "qwen3"}, {"name": "llama3"}, {"no_name": 1}]}
        return _FakeResp(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(insights.urllib.request, "urlopen", fake_urlopen)
    names = insights.ollama_models("http://127.0.0.1:11434/v1")
    assert names == ["qwen3", "llama3"]
    assert len(calls) == 1
    assert calls[0] == "http://127.0.0.1:11434/api/tags"
    print("  [PASS] ollama_models_parse")


def test_ollama_models_connection_error(monkeypatch):
    """连接失败（URLError）→ 抛出中文 InsightsError，不静默吞掉。"""

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(insights.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(insights.InsightsError, match="无法连接 Ollama"):
        insights.ollama_models()
    print("  [PASS] ollama_models_connection_error")


# ---------------------------------------------------------------------------
# 6. time_saved_insights
# ---------------------------------------------------------------------------
def test_time_saved_zero():
    """当日无 AI 编程时长 → saved_ms 为 0 且给出明确标签。"""
    cfg = {"insights": {"enabled": True}}
    out = insights.time_saved_insights(_base_agg(by_category={}, by_ai={}), cfg)
    assert out["enabled"] is True
    assert out["ai_ms"] == 0
    assert out["saved_ms"] == 0
    assert out["label"] == "当日无 AI 编程"
    print("  [PASS] time_saved_zero")


def test_time_saved_positive_factor():
    """有 AI 时长 → saved_ms > 0，且等于 AI 时长 × factor − AI 时长（源码公式）。"""
    cfg = {"insights": {"enabled": True, "time_saved": {"factor": 2.0}}}
    ai_ms = 90 * 60000  # 90 分钟，高于 min_ai_min=10
    out = insights.time_saved_insights(_base_agg(by_category={"AI编程": ai_ms}), cfg)
    assert out["enabled"] is True
    assert out["ai_ms"] == ai_ms
    assert out["factor"] == 2.0
    # 源码：est_manual_ms = ai_ms × factor；saved_ms = est_manual_ms − ai_ms
    assert out["est_manual_ms"] == int(ai_ms * 2.0)
    assert out["saved_ms"] == int(ai_ms * 2.0) - ai_ms
    assert out["saved_ms"] > 0
    assert out["saved_ratio"] == 0.5
    print("  [PASS] time_saved_positive_factor")


# ---------------------------------------------------------------------------
# 7. conversation_quality_insights
# ---------------------------------------------------------------------------
def test_conversation_quality_empty():
    """空会话 / 无数据 / 无已评会话 → 一律返回空列表。"""
    assert insights.conversation_quality_insights(None) == []
    assert insights.conversation_quality_insights({}) == []
    assert insights.conversation_quality_insights({"found": False}) == []
    assert insights.conversation_quality_insights({"found": True, "total": {}}) == []
    assert insights.conversation_quality_insights(
        {"found": True, "total": {"quality_summary": {"sessions_scored": 0}}}
    ) == []
    print("  [PASS] conversation_quality_empty")
