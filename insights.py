# -*- coding: utf-8 -*-
"""insights.py — 智能洞察模块（规则引擎 + 可选 AI 建议）。

规则引擎：离线、零依赖、确定性，基于 report.aggregate() 的聚合结果生成
学习 / 游戏 / 健康 / 效率 / 平衡 / 趋势 六类结构化建议。

AI 建议：可选、默认关闭。聚合数据（隐私安全，默认不含标题 / URL / 联系人名）
发送到你配置的 OpenAI 兼容 chat/completions 端点，纯标准库 urllib 实现，
成功结果缓存到 <data_root>/YYYY-MM-DD/insights.json，并用模块级锁单飞。

CLI：python insights.py --day 2026-08-10 [--ai] [--json] [--data-root ...]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request

import paths  # noqa: E402
import report  # noqa: E402
import version  # noqa: E402

DEFAULT_DATA_ROOT = paths.default_data_root()

# 规则类型 -> 中文标签（日报「今日建议」段与仪表盘卡片使用）
TYPE_LABELS = {
    "study": "学习",
    "game": "游戏",
    "health": "健康",
    "efficiency": "效率",
    "balance": "平衡",
    "trend": "趋势",
    "ai": "AI",
}

_DEFAULT_RULES = {
    "long_session_min": 90,
    "late_night_hour": 23,
    "game_alert_hours": 2,
    "study_goal_hours": 1,
    "game_ratio_warn": 0.4,
}

_DEFAULT_AI = {
    "enabled": False,
    "provider": "opencodego",
    "base_url": "",
    "api_key": "",
    "model": "deepseek-v4-flash",
    "timeout_s": 60,
    "send_raw_titles": False,
    "language": "zh",
}

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class InsightsError(RuntimeError):
    """AI 调用失败（中文可读信息）。"""


def _merge_dict(base: dict, override: dict | None) -> dict:
    """浅递归合并：override 优先（供 insights 子配置使用）。"""
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def _insights_config(config: dict) -> dict:
    """从完整 config 中提取 insights 段，并补齐规则/AI 默认值。"""
    raw = (config or {}).get("insights")
    ins = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(ins.get("enabled", True)),
        "in_report": bool(ins.get("in_report", True)),
        "rules": _merge_dict(_DEFAULT_RULES, ins.get("rules") if isinstance(ins.get("rules"), dict) else None),
        "ai": _merge_dict(_DEFAULT_AI, ins.get("ai") if isinstance(ins.get("ai"), dict) else None),
    }


def _fmt_hours(ms: int | float) -> str:
    """毫秒 -> 紧凑中文时长（2.5 小时 / 45 分钟）。"""
    ms = max(0, int(ms))
    if ms >= 3600000:
        h = ms / 3600000
        text = f"{h:.1f}".rstrip("0").rstrip(".")
        return f"{text} 小时"
    if ms >= 60000:
        return f"{ms // 60000} 分钟"
    return "不足 1 分钟"


def _fmt_minutes(ms: int | float) -> int:
    """毫秒 -> 整分钟数（向上取整，避免 0 分钟误导）。"""
    return max(0, int(round(max(0, int(ms)) / 60000)))


def _hours(ms: int | float) -> float:
    return max(0, int(ms)) / 3600000


def rule_insights(agg: dict, config: dict, prev_agg: dict | None = None) -> list[dict]:
    """根据聚合结果生成确定性规则洞察。

    返回 [{type, severity: "info"|"warn"|"alert", title, detail}]；
    无数据 / insights.enabled=false 时返回空列表。
    """
    if not isinstance(agg, dict):
        return []
    ins = _insights_config(config or {})
    if not ins["enabled"]:
        return []
    rules = ins["rules"]

    sessions = [s for s in (agg.get("sessions") or []) if isinstance(s, dict)]
    total = int(agg.get("total_active_ms") or 0)
    if not sessions and total <= 0:
        return []

    by_category = agg.get("by_category") if isinstance(agg.get("by_category"), dict) else {}
    by_browser = agg.get("by_browser") if isinstance(agg.get("by_browser"), dict) else {}
    by_ai = agg.get("by_ai") if isinstance(agg.get("by_ai"), dict) else {}
    hourly = agg.get("hourly_ms") if isinstance(agg.get("hourly_ms"), list) else []

    out: list[dict] = []

    # ---- 学习：浏览器「学习」分类 + 「办公学习」类别 ----
    study_ms = int(by_category.get("办公学习", 0) or 0) + int(by_browser.get("学习", 0) or 0)
    if study_ms > 0:
        online_ms = int(by_browser.get("学习", 0) or 0)
        goal_hours = max(0.0, float(rules.get("study_goal_hours", 1) or 0))
        reached = _hours(study_ms) + 1e-9 >= goal_hours
        online_part = f"，其中网课 {_fmt_hours(online_ms)}" if online_ms > 0 else ""
        if reached:
            advice = f"已达到 {goal_hours:g} 小时学习目标，保持节奏，建议搭配笔记 / 练习巩固"
        else:
            advice = f"距离 {goal_hours:g} 小时学习目标还差一点，建议安排固定学习时段持续投入"
        out.append({
            "type": "study",
            "severity": "info",
            "title": TYPE_LABELS["study"],
            "detail": f"今日学习 {_fmt_hours(study_ms)}{online_part}；{advice}",
        })

    # ---- 游戏：时长提醒 + 占比平衡建议 ----
    game_ms = int(by_category.get("游戏", 0) or 0)
    if game_ms > 0:
        alert_hours = max(0.0, float(rules.get("game_alert_hours", 2) or 0))
        ratio_warn = max(0.0, min(1.0, float(rules.get("game_ratio_warn", 0.4) or 0)))
        game_ratio = game_ms / total if total > 0 else 0.0
        parts = [f"游戏时长 {_fmt_hours(game_ms)}"]
        if alert_hours > 0 and _hours(game_ms) >= alert_hours:
            parts.append(f"已达到 {alert_hours:g} 小时提醒线，注意劳逸结合，避免长时间连续游戏")
        if ratio_warn > 0 and game_ratio > ratio_warn:
            parts.append(f"占活跃时长 {game_ratio * 100:.0f}%，建议搭配学习 / 运动平衡节奏")
        warn = _hours(game_ms) >= alert_hours or (ratio_warn > 0 and game_ratio > ratio_warn)
        out.append({
            "type": "game",
            "severity": "warn" if warn else "info",
            "title": TYPE_LABELS["game"],
            "detail": "；".join(parts),
        })

    # ---- 健康：最长连续会话 + 深夜使用 ----
    durations = [int(s.get("duration_ms") or 0) for s in sessions]
    if durations:
        longest_ms = max(durations)
        longest_min = _fmt_minutes(longest_ms)
        long_min = max(1, int(rules.get("long_session_min", 90) or 90))
        if longest_min >= long_min:
            out.append({
                "type": "health",
                "severity": "alert" if longest_min >= long_min * 2 else "warn",
                "title": TYPE_LABELS["health"],
                "detail": f"最长连续使用 {longest_min} 分钟（提醒线 {long_min} 分钟），建议起身休息 5-10 分钟",
            })

    if hourly:
        late_start = int(rules.get("late_night_hour", 23) or 23) % 24
        window = [(late_start + i) % 24 for i in range(7)]  # 23:00 ~ 次日 05:59
        active = [h for h in window if h < len(hourly) and int(hourly[h] or 0) > 0]
        if active:
            latest = active[-1]
            out.append({
                "type": "health",
                "severity": "warn",
                "title": TYPE_LABELS["health"],
                "detail": f"深夜时段（{late_start}:00 后）仍有使用，最晚活跃至 {latest}:59，注意睡眠，尽量规律作息",
            })

    # ---- 效率：AI 编程时长 ----
    ai_ms = int(by_category.get("AI编程", 0) or 0)
    if ai_ms > 0:
        tools = sorted(by_ai.items(), key=lambda kv: -int(kv[1] or 0))[:2]
        tool_part = ""
        if tools:
            tool_part = "（" + " / ".join(name for name, _ms in tools) + "）"
        out.append({
            "type": "efficiency",
            "severity": "info",
            "title": TYPE_LABELS["efficiency"],
            "detail": f"AI 编程 {_fmt_hours(ai_ms)}{tool_part}，继续保持高效节奏，复杂改动记得复核",
        })

    # ---- 平衡：社交聊天时长 ----
    social_ms = int(by_category.get("社交聊天", 0) or 0)
    if social_ms > 0:
        out.append({
            "type": "balance",
            "severity": "info",
            "title": TYPE_LABELS["balance"],
            "detail": f"社交聊天 {_fmt_hours(social_ms)}，保持联系的同时记得给工作 / 学习留出整块时间",
        })

    # ---- 趋势：与昨日活跃时长对比 ----
    if isinstance(prev_agg, dict):
        prev_total = int(prev_agg.get("total_active_ms") or 0)
        if prev_total > 0 and total > 0:
            delta = (total - prev_total) / prev_total * 100.0
            direction = "多" if delta >= 0 else "少"
            out.append({
                "type": "trend",
                "severity": "info",
                "title": TYPE_LABELS["trend"],
                "detail": (
                    f"今天比昨天{direction} {abs(delta):.0f}% 活跃时长"
                    f"（今天 {_fmt_hours(total)}，昨天 {_fmt_hours(prev_total)}）"
                ),
            })

    return out


def _top_items(mapping: dict, limit: int) -> list[tuple[str, int]]:
    """按值降序取前 limit 项；值一律按毫秒解释。"""
    items = []
    for key, value in (mapping or {}).items():
        try:
            items.append((str(key), int(value or 0)))
        except (TypeError, ValueError):
            continue
    items.sort(key=lambda kv: -kv[1])
    return items[:max(0, limit)]


def build_ai_prompt(agg: dict, config: dict, prev_agg: dict | None, include_raw: bool) -> str:
    """构建发给 AI 的提示词。

    隐私过滤：默认只含聚合数字（日期、时长、会话数、主要活跃时段、分类/应用/
    AI工具/浏览器 Top 列表、联系人计数），不含窗口标题、URL、联系人名；
    include_raw=True 时才附加 Top 标题 / URL（联系人名仍然不上送）。
    """
    ins = _insights_config(config or {})
    language = str(ins["ai"].get("language") or "zh").lower()
    is_en = language.startswith("en")

    def fmt_min(ms: int | float) -> str:
        minutes = max(0, int(ms)) / 60000
        if is_en:
            return f"{minutes:.1f} min"
        return f"{minutes:.1f} 分钟"

    total = int(agg.get("total_active_ms") or 0)
    sessions = [s for s in (agg.get("sessions") or []) if isinstance(s, dict)]
    durations = [int(s.get("duration_ms") or 0) for s in sessions]
    longest_min = max(durations) / 60000 if durations else 0.0

    by_category = agg.get("by_category") if isinstance(agg.get("by_category"), dict) else {}
    by_app = agg.get("by_app") if isinstance(agg.get("by_app"), dict) else {}
    by_ai = agg.get("by_ai") if isinstance(agg.get("by_ai"), dict) else {}
    by_browser = agg.get("by_browser") if isinstance(agg.get("by_browser"), dict) else {}
    by_contact = agg.get("by_contact") if isinstance(agg.get("by_contact"), dict) else {}
    contact_count = sum(len(v) if isinstance(v, dict) else 0 for v in by_contact.values())
    hourly = agg.get("hourly_ms") if isinstance(agg.get("hourly_ms"), list) else []

    active_hours = [
        (h, int(hourly[h] or 0)) for h in range(min(24, len(hourly))) if int(hourly[h] or 0) > 0
    ]
    active_hours.sort(key=lambda kv: -kv[1])
    top_hours = active_hours[:3]

    if is_en:
        lines = [
            "You are a personal productivity analyst. Reply in the same language as the prompt.",
            f"Date: {agg.get('date', '')}",
            f"Total active time: {fmt_min(total)}",
            f"Sessions: {len(sessions)}",
            f"Longest continuous session: {longest_min:.1f} min",
            "Most active hours: " + (
                ", ".join(f"{h:02d}:00 ({fmt_min(ms)})" for h, ms in top_hours) or "none"
            ),
            "Time by category (top 6): " + (
                ", ".join(f"{k} {fmt_min(v)}" for k, v in _top_items(by_category, 6)) or "none"
            ),
            "Time by app (top 8): " + (
                ", ".join(f"{k} {fmt_min(v)}" for k, v in _top_items(by_app, 8)) or "none"
            ),
            "Time by AI tool (top 3): " + (
                ", ".join(f"{k} {fmt_min(v)}" for k, v in _top_items(by_ai, 3)) or "none"
            ),
            "Time by browser category: " + (
                ", ".join(f"{k} {fmt_min(v)}" for k, v in _top_items(by_browser, 10)) or "none"
            ),
            f"Contact count (names omitted for privacy): {contact_count}",
        ]
    else:
        lines = [
            "你是一名个人电脑使用情况分析师。请只依据下方聚合数据给出建议，不要编造数据。",
            f"日期：{agg.get('date', '')}",
            f"总活跃时长：{fmt_min(total)}",
            f"会话数：{len(sessions)}",
            f"最长连续会话：{longest_min:.1f} 分钟",
            "主要活跃时段：" + (
                "、".join(f"{h:02d}:00（{fmt_min(ms)}）" for h, ms in top_hours) or "无"
            ),
            "按类别时长（前 6）：" + (
                "、".join(f"{k} {fmt_min(v)}" for k, v in _top_items(by_category, 6)) or "无"
            ),
            "按应用时长（前 8）：" + (
                "、".join(f"{k} {fmt_min(v)}" for k, v in _top_items(by_app, 8)) or "无"
            ),
            "按 AI 工具时长（前 3）：" + (
                "、".join(f"{k} {fmt_min(v)}" for k, v in _top_items(by_ai, 3)) or "无"
            ),
            "浏览器分类时长：" + (
                "、".join(f"{k} {fmt_min(v)}" for k, v in _top_items(by_browser, 10)) or "无"
            ),
            f"联系人数量（出于隐私不上送联系人名）：{contact_count}",
        ]

    if isinstance(prev_agg, dict) and int(prev_agg.get("total_active_ms") or 0) > 0:
        prev_total = int(prev_agg.get("total_active_ms") or 0)
        if is_en:
            lines.append(
                f"Previous day active time: {fmt_min(prev_total)} "
                f"({int(prev_agg.get('session_count') or 0)} sessions)"
            )
        else:
            lines.append(
                f"昨日活跃时长：{fmt_min(prev_total)}（会话数 {int(prev_agg.get('session_count') or 0)}）"
            )

    if include_raw:
        raw_rows = []
        for s in sorted(sessions, key=lambda x: -int(x.get("duration_ms") or 0))[:10]:
            title = str(s.get("title") or "").strip()
            url = str(s.get("url") or "").strip()
            if not title and not url:
                continue
            raw_rows.append(
                f"{s.get('app') or s.get('exe') or ''} | {title} | {url}"
                if is_en else
                f"{s.get('app') or s.get('exe') or ''}｜标题：{title}｜URL：{url}"
            )
        if raw_rows:
            if is_en:
                lines.append("Raw sample (user explicitly enabled):\n" + "\n".join(raw_rows))
            else:
                lines.append("原始样本（用户明确开启后才会上送）：\n" + "\n".join(raw_rows))

    if is_en:
        lines.append(
            "Return ONLY a JSON array of 3-6 insights, each object shaped like "
            '{"type":"study|game|health|efficiency|balance|trend","title":"short title",'
            '"detail":"one-sentence actionable advice"}. No markdown, no extra text.'
        )
    else:
        lines.append(
            "请只返回一个 JSON 数组，包含 3-6 条洞察，每条对象格式为 "
            '{"type":"study|game|health|efficiency|balance|trend","title":"简短标题",'
            '"detail":"一句话可执行建议"}。不要输出 Markdown 或其他多余文字。'
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# AI 客户端 / 缓存 / 单飞锁
# ---------------------------------------------------------------------------
_AI_LOCK = threading.Lock()


def _cache_path(date_str: str, data_root: str) -> str:
    return os.path.join(data_root, date_str, "insights.json")


def _read_ai_cache(date_str: str, data_root: str) -> dict | None:
    """读取缓存；损坏/不存在返回 None。"""
    path = _cache_path(date_str, data_root)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("insights"), list):
            return data
    except Exception:  # noqa: BLE001 —— 缓存损坏不影响重新生成
        pass
    return None


def _write_ai_cache(date_str: str, data_root: str, payload: dict) -> None:
    """原子写缓存（仅在 AI 调用成功后写入）。"""
    path = _cache_path(date_str, data_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _parse_ai_response(text: str) -> list[dict]:
    """解析 AI 返回：优先 JSON 数组；失败则整段文本作为单条洞察。"""
    text = (text or "").strip()
    if not text:
        raise InsightsError("AI 服务返回了空内容")

    def _normalize(payload) -> list[dict]:
        items = payload if isinstance(payload, list) else [payload]
        normalized: list[dict] = []
        for item in items:
            if isinstance(item, str):
                normalized.append({
                    "type": "ai", "severity": "info",
                    "title": TYPE_LABELS["ai"], "detail": item.strip(),
                })
                continue
            if not isinstance(item, dict):
                continue
            itype = str(item.get("type") or "ai").strip().lower()
            if itype not in TYPE_LABELS:
                itype = "ai"
            severity = str(item.get("severity") or "info").strip().lower()
            if severity not in ("info", "warn", "alert"):
                severity = "info"
            detail = str(item.get("detail") or item.get("content") or "").strip()
            title = str(item.get("title") or "").strip() or TYPE_LABELS[itype]
            if not detail:
                detail = title
            normalized.append({
                "type": itype,
                "severity": severity,
                "title": title,
                "detail": detail,
            })
        return normalized

    candidates = [text]
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    if stripped and stripped != text:
        candidates.insert(0, stripped)
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if 0 <= start < end:
            candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if payload is None:
            continue
        items = _normalize(payload)
        if items:
            return items

    return [{
        "type": "ai",
        "severity": "info",
        "title": TYPE_LABELS["ai"],
        "detail": text,
    }]


def _pick_key(obj: dict, keys: tuple[str, ...], default=None):
    """大小写不敏感地取 obj 中的第一个匹配键。"""
    if not isinstance(obj, dict):
        return default
    lowered = {str(k).lower(): v for k, v in obj.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return default


def _opencode_provider_entry(data: dict, names: tuple[str, ...]) -> tuple[str, dict] | None:
    """从 opencode.json 中按优先级找 provider 条目。"""
    providers = data.get("providers")
    entries: dict = {}

    def _add(key, value):
        if isinstance(value, dict):
            entries[str(key).lower()] = value

    if isinstance(providers, dict):
        for key, value in providers.items():
            _add(key, value)
    elif isinstance(providers, list):
        for item in providers:
            if isinstance(item, dict):
                pid = item.get("id") or item.get("name")
                if pid:
                    _add(pid, item)
    provider = data.get("provider")
    if isinstance(provider, dict):
        for key, value in provider.items():
            _add(key, value)
    if isinstance(provider, list):
        for item in provider:
            if isinstance(item, dict):
                pid = item.get("id") or item.get("name")
                if pid:
                    _add(pid, item)

    for name in names:
        if name.lower() in entries:
            return name, entries[name.lower()]
    return None


def _opencode_models(entry: dict, data: dict) -> list[str]:
    """提取 provider 模型名列表（对象取键、列表取 id/name）。"""
    models = entry.get("models")
    out: list[str] = []
    if isinstance(models, dict):
        out.extend(str(k) for k in models.keys())
    elif isinstance(models, list):
        for item in models:
            if isinstance(item, dict):
                out.append(str(item.get("id") or item.get("name") or ""))
            else:
                out.append(str(item))
    if not out:
        top_models = data.get("models")
        if isinstance(top_models, dict):
            out.extend(str(k) for k in top_models.keys())
        elif isinstance(top_models, list):
            for item in top_models:
                out.append(str(item.get("id") or item.get("name") or "") if isinstance(item, dict) else str(item))
    return [m for m in out if m]


def _pick_model(models: list[str], preferred: str) -> str | None:
    for model in models:
        if model.lower() == preferred.lower():
            return model
    return models[0] if models else None


_DEFAULT_PROVIDER_URLS = {"opencodego": "https://opencode.ai/zen/go/v1"}


def _discover_ai_config(config: dict) -> dict | None:
    """自动发现 %USERPROFILE%\\.config\\opencode\\opencode.json 的 AI 配置。

    优先 provider "opencodego"（https://opencode.ai/zen/go/v1），回退
    "sensenova"；模型优先 deepseek-v4-flash，否则取该 provider 模型列表
    第一个。config.json 显式配置（base_url / api_key / model）优先于自动发现。
    """
    auto: dict | None = None
    try:
        path = os.path.join(os.path.expanduser("~"), ".config", "opencode", "opencode.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
    except Exception:  # noqa: BLE001 —— 无 opencode 配置时继续显式/回退路径
        data = {}

    found = _opencode_provider_entry(data, ("opencodego", "sensenova"))
    if found is not None:
        name, entry = found
        options = entry.get("options") if isinstance(entry.get("options"), dict) else {}
        base_url = (
            _pick_key(options, ("baseURL", "base_url"))
            or _pick_key(entry, ("baseURL", "base_url"))
            or _DEFAULT_PROVIDER_URLS.get(name.lower(), "")
        )
        api_key = (
            _pick_key(options, ("apiKey", "api_key"))
            or _pick_key(entry, ("apiKey", "api_key"))
            or _pick_key(data, ("apiKey", "api_key"))
        )
        models = _opencode_models(entry, data)
        if base_url:
            auto = {
                "provider": name,
                "base_url": str(base_url),
                "api_key": str(api_key or ""),
                "model": _pick_model(models, "deepseek-v4-flash") or "",
            }

    explicit = _insights_config(config or {})["ai"]
    base_url = str(explicit.get("base_url") or (auto or {}).get("base_url") or "")
    if not base_url:
        return None
    api_key = explicit.get("api_key")
    if api_key is None or str(api_key) == "":
        api_key = (auto or {}).get("api_key") or ""
    model = str(explicit.get("model") or (auto or {}).get("model") or "")
    try:
        timeout_s = float(explicit.get("timeout_s") or 60)
    except (TypeError, ValueError):
        timeout_s = 60.0
    return {
        "provider": str(explicit.get("provider") or (auto or {}).get("provider") or "custom"),
        "base_url": base_url,
        "api_key": str(api_key),
        "model": model,
        "timeout_s": max(1.0, timeout_s),
    }


def _chat_completion(cfg: dict, prompt: str) -> str:
    """OpenAI 兼容 chat/completions 调用（纯 urllib）。

    请求体固定 temperature=0.7、max_tokens=800；失败抛中文 InsightsError。
    """
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    if not base_url:
        raise InsightsError("未配置 AI base_url（config.json: insights.ai.base_url）")
    if base_url.endswith("/chat/completions"):
        url = base_url
    else:
        url = f"{base_url}/chat/completions"
    model = str(cfg.get("model") or "").strip()
    if not model:
        raise InsightsError("未配置 AI 模型（config.json: insights.ai.model）")

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 800,
    }
    headers = {"Content-Type": "application/json"}
    api_key = str(cfg.get("api_key") or "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST",
    )
    timeout = max(1.0, float(cfg.get("timeout_s") or 60))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        raise InsightsError(
            f"AI 服务返回 HTTP {exc.code}" + (f"：{detail}" if detail else "")
        ) from exc
    except urllib.error.URLError as exc:
        raise InsightsError(f"无法连接 AI 服务：{exc.reason}") from exc
    except TimeoutError as exc:
        raise InsightsError(f"AI 服务请求超时（>{timeout:g} 秒）") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InsightsError("AI 服务返回的不是有效 JSON") from exc
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise InsightsError("AI 服务响应缺少 choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise InsightsError("AI 服务响应缺少 message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        ]
        return "".join(parts).strip()
    raise InsightsError("AI 服务响应缺少 content")


def _ai_insights_locked(date_str: str, data_root: str, config: dict) -> dict:
    """单飞锁内实际执行：聚合 -> 提示词 -> 调用 -> 解析 -> 写缓存。"""
    cfg = _discover_ai_config(config)
    if cfg is None:
        return {
            "generated_at": None, "model": None, "insights": None,
            "error": "未发现可用 AI 配置：请配置 insights.ai.base_url/api_key/model，"
                     "或检查 %USERPROFILE%\\.config\\opencode\\opencode.json",
        }
    try:
        agg = report.aggregate(date_str, data_root)
        prev_day = (datetime.date.fromisoformat(date_str) - datetime.timedelta(days=1)).isoformat()
        prev_agg = report.aggregate(prev_day, data_root)
        prompt = build_ai_prompt(
            agg, config, prev_agg,
            include_raw=bool(_insights_config(config)["ai"].get("send_raw_titles")),
        )
        text = _chat_completion(cfg, prompt)
        insights_list = _parse_ai_response(text)
        payload = {
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "model": cfg.get("model") or "",
            "insights": insights_list,
            "error": None,
        }
        _write_ai_cache(date_str, data_root, payload)
        return dict(payload)
    except Exception as exc:  # noqa: BLE001 —— 任何失败都转为可展示的错误
        return {
            "generated_at": None,
            "model": cfg.get("model") or "",
            "insights": None,
            "error": str(exc),
        }


def ai_insights(date_str: str, data_root: str, config: dict, refresh: bool = False) -> dict:
    """生成/读取某天的 AI 洞察。

    返回 {generated_at, model, insights: [...]|None, error: str|None}。
    - 成功才写缓存 <data_root>/YYYY-MM-DD/insights.json
    - refresh=False 时优先读缓存；并发调用由模块级 threading.Lock 单飞
    """
    if not _DAY_RE.fullmatch(date_str or ""):
        return {"generated_at": None, "model": None, "insights": None, "error": f"非法日期: {date_str}"}
    ins = _insights_config(config or {})
    if not ins["ai"].get("enabled"):
        return {
            "generated_at": None, "model": None, "insights": None,
            "error": "AI 洞察未开启（config.json: insights.ai.enabled=false）",
        }

    if not refresh:
        cached = _read_ai_cache(date_str, data_root)
        if cached is not None:
            return {
                "generated_at": cached.get("generated_at"),
                "model": cached.get("model") or "",
                "insights": cached.get("insights"),
                "error": None,
            }

    with _AI_LOCK:
        if not refresh:
            cached = _read_ai_cache(date_str, data_root)
            if cached is not None:
                return {
                    "generated_at": cached.get("generated_at"),
                    "model": cached.get("model") or "",
                    "insights": cached.get("insights"),
                    "error": None,
                }
        return _ai_insights_locked(date_str, data_root, config)


def _prev_day(date_str: str) -> str:
    return (datetime.date.fromisoformat(date_str) - datetime.timedelta(days=1)).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="insights.py", description="电脑使用情况智能洞察（规则 + 可选 AI）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version.VERSION}")
    parser.add_argument("--day", metavar="YYYY-MM-DD", help="指定日期（默认今天）")
    parser.add_argument("--today", action="store_true", help="今天")
    parser.add_argument("--ai", action="store_true", help="同时生成/读取 AI 洞察")
    parser.add_argument("--refresh", action="store_true", help="与 --ai 连用：忽略缓存强制重新生成")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--data-root", default=None, help="数据根目录（默认取 config.json）")
    parser.add_argument("--config", default=None, help="config.json 路径")
    args = parser.parse_args(argv)

    try:
        import classifier  # noqa: PLC0415
        cfg = classifier.load_config(args.config)
        data_root = args.data_root or (cfg.get("data_root") or DEFAULT_DATA_ROOT)
    except Exception:  # noqa: BLE001
        cfg = {}
        data_root = args.data_root or DEFAULT_DATA_ROOT

    if args.today:
        date_str = datetime.date.today().isoformat()
    elif args.day:
        date_str = args.day
    else:
        date_str = datetime.date.today().isoformat()
    if not _DAY_RE.fullmatch(date_str):
        print(f"[insights] 日期格式错误: {date_str}（应为 YYYY-MM-DD）", file=sys.stderr)
        return 2

    agg = report.aggregate(date_str, data_root)
    prev_agg = report.aggregate(_prev_day(date_str), data_root)
    rules = rule_insights(agg, cfg, prev_agg)
    payload: dict = {"date": date_str, "rules": rules}
    if args.ai:
        payload["ai"] = ai_insights(date_str, data_root, cfg, refresh=args.refresh)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"# 智能洞察 {date_str}")
    if not rules:
        print("（今日暂无规则洞察）")
    for rule in rules:
        print(f"- [{rule['title']}] {rule['detail']}")
    if args.ai:
        ai = payload["ai"]
        print("")
        print("## AI 洞察")
        if ai.get("error"):
            print(f"错误：{ai['error']}")
        elif ai.get("insights"):
            for item in ai["insights"]:
                print(f"- [{item.get('title', 'AI')}] {item.get('detail', '')}")
        else:
            print("（无 AI 洞察）")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
