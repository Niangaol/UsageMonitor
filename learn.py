# -*- coding: utf-8 -*-
"""learn.py — 在线统计基线（v2.7「简单学习」，纯标准库，零第三方依赖）。

定位：在"零依赖"硬约束下做**能落地的最小学习**——不是深度学习，而是
在线统计学习：滑动窗口样本环 + z-score 异常检测。效果是"越用越懂你"：
对每个指标维护该用户自己的历史分布，当日值偏离常态时给出个性化洞察
（无需预设阈值，阈值由你自己的历史决定）。

为什么不是深度学习：
- torch/onnxruntime 均为第三方依赖，破坏"零依赖、单文件可跑"的定位；
- 个人场景样本量极小（每天 1 个样本），深度模型无数据红利且不可解释；
- 滑动窗口 z-score O(1) 写入、可解释（"偏离 2.3σ"）、可自愈。

语义要点：
- 打分先用**排除当日**的历史分布（当日不污染自身）；
- 同日重复调用按**覆盖重写**处理（日报 19:30 与仪表盘多次打开都安全，
  基线最终收敛到当日最终值，不会被半天数据污染）；
- 窗口上限 _MAX_DAYS（默认 180 天），习惯漂移自动跟随近期行为。

持久化：<data_root>/baselines.json（schema 版本化；坏档自动重建，自愈）。
"""

from __future__ import annotations

import json
import math
import os
import re

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SCHEMA = 1
_FILE = "baselines.json"
_MAX_DAYS = 180  # 样本环窗口上限（习惯漂移跟随期）

# 参与基线的指标（从 report.aggregate 提取）
METRICS = ("active_min", "coding_min", "sessions")

_METRIC_LABELS = {
    "active_min": "总活跃",
    "coding_min": "编码",
    "sessions": "会话数",
}
METRIC_LABELS = _METRIC_LABELS  # 公开别名（insights 层引用）

_DEFAULTS = {"enabled": True, "min_days": 7, "z_warn": 2.0, "z_alert": 3.0}


def baseline_config(config: dict | None) -> dict:
    """归一化 insights.baseline 配置段（缺省开启；老用户无该段也能跑）。"""
    ins = config.get("insights") if isinstance(config, dict) and isinstance(config.get("insights"), dict) else {}
    raw = ins.get("baseline") if isinstance(ins.get("baseline"), dict) else {}
    out = dict(_DEFAULTS)
    out["enabled"] = bool(raw.get("enabled", _DEFAULTS["enabled"]))
    for key in ("min_days", "z_warn", "z_alert"):
        try:
            out[key] = type(_DEFAULTS[key])(raw.get(key, _DEFAULTS[key]))
        except (TypeError, ValueError):
            continue
    out["min_days"] = max(3, min(90, int(out["min_days"])))
    out["z_warn"] = max(1.0, min(5.0, float(out["z_warn"])))
    out["z_alert"] = max(out["z_warn"], min(8.0, float(out["z_alert"])))
    return out


def extract_metrics(agg: dict) -> dict[str, float]:
    """从 report.aggregate 结果提取基线指标（分钟/个数为单位）。"""
    agg = agg if isinstance(agg, dict) else {}
    by_cat = agg.get("by_category") if isinstance(agg.get("by_category"), dict) else {}
    coding_ms = sum(int(by_cat.get(c, 0) or 0) for c in ("开发工具", "AI编程"))
    return {
        "active_min": int(agg.get("total_active_ms") or 0) / 60000.0,
        "coding_min": coding_ms / 60000.0,
        "sessions": float(int(agg.get("session_count") or 0)),
    }


def _state_path(root: str) -> str:
    return os.path.join(root or ".", _FILE)


def load_state(root: str) -> dict:
    """读基线状态；缺文件/坏 JSON/schema 不符 → 空状态（自愈）。"""
    try:
        with open(_state_path(root), encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict) and payload.get("schema") == _SCHEMA \
                and isinstance(payload.get("days"), dict):
            return payload
    except (OSError, ValueError):
        pass
    return {"schema": _SCHEMA, "days": {}}


def save_state(root: str, state: dict) -> None:
    """tmp + os.replace 原子写（避免半写文件）。"""
    try:
        os.makedirs(root or ".", exist_ok=True)
        tmp = _state_path(root) + f".tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        os.replace(tmp, _state_path(root))
    except OSError:
        pass


def _mean_std(values: list[float]) -> tuple[float, float, int]:
    """总体均值与标准差；空列表返回 (0, 0, 0)。"""
    n = len(values)
    if n == 0:
        return 0.0, 0.0, 0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return mean, math.sqrt(max(var, 1e-12)), n


def record(root: str, date: str, metrics: dict[str, float]) -> bool:
    """记录/覆盖某日指标样本（同日重写=覆盖），裁剪窗口并落盘。

    返回是否发生写入。date 非法时不动作。
    """
    if not _DAY_RE.fullmatch(date or ""):
        return False
    state = load_state(root)
    days = state["days"]
    days[date] = {k: round(float(metrics.get(k) or 0.0), 2) for k in METRICS}
    if len(days) > _MAX_DAYS:
        for stale in sorted(days)[:-_MAX_DAYS]:
            days.pop(stale, None)
    save_state(root, state)
    return True


def score(root: str, date: str, metrics: dict[str, float]) -> dict:
    """按**排除当日**的历史窗口给当日各指标打 z 分。

    返回 {"date", "n", "scores": {metric: {mean, std, z, level}}}。
    level: warming(样本<2) | normal(|z|<1) | notable(≥1) | unusual(≥2) | anomaly(≥3)。
    """
    state = load_state(root)
    history: dict[str, list[float]] = {k: [] for k in METRICS}
    for day, row in state["days"].items():
        if day == date or not isinstance(row, dict):
            continue
        for k in METRICS:
            try:
                history[k].append(float(row.get(k) or 0.0))
            except (TypeError, ValueError):
                continue
    n = len(history[METRICS[0]])
    scores: dict[str, dict] = {}
    for k in METRICS:
        x = float(metrics.get(k) or 0.0)
        mean, std, _ = _mean_std(history[k])
        z = (x - mean) / std if std > 1e-9 else 0.0
        z = max(-99.0, min(99.0, z))  # 显示上限：全同值历史后突变时 z 可能爆炸
        absz = abs(z)
        if n < 2:
            level = "warming"
        elif absz >= 3.0:
            level = "anomaly"
        elif absz >= 2.0:
            level = "unusual"
        elif absz >= 1.0:
            level = "notable"
        else:
            level = "normal"
        scores[k] = {"mean": round(mean, 1), "std": round(std, 1),
                     "z": round(z, 2), "level": level}
    return {"date": date, "n": n, "scores": scores}


def record_and_score_agg(root: str, date: str, agg: dict) -> dict:
    """便捷入口：从 aggregate 结果提取指标 → 先打分（排除当日）→ 再记录当日。"""
    metrics = extract_metrics(agg)
    result = score(root, date, metrics)
    result["recorded"] = record(root, date, metrics)
    return result
