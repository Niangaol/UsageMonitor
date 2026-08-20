# -*- coding: utf-8 -*-
"""timeline.py — Vibe 时间轴回放（v2.5 · P2 预研骨架 → P3 完整实现）。

对应 docs/TIMELINE_P2_DESIGN.md（即 VIBECODING_IMPLEMENTATION_GUIDE.md §4.2
「功能 B：Vibe Coding 时间轴回放」）。

目标：把某一天「什么时间在哪个 AI 工具/项目上干活、花多少 token/钱、产出
多少行、何时提交」还原成一条按时间递增的可回放时间轴。

三源输入（全部已存在，本模块只读复用）：
  - report.aggregate()            -> sessions（start/end/category/ai_tool）→ session 事件
  - ai_sessions.collect()         -> conversations（first/last/tool/model/...）→ ai_session 事件
  - git_insights（底层 git log）    -> per-commit（date/hash/added/deleted）→ git_commit 事件

设计铁律（对齐 §7 数据模型矩阵）：
  - usage.jsonl 永不因派生逻辑修改；
  - 本模块是纯派生，全部内存计算，不落盘；每次请求重算。
  - 不触碰 report/ai_sessions/git_insights 的核心逻辑。
  - 任一源失败降级（该源返回空），时间轴整体不抛异常（best-effort）。

对外 API：
  - timeline_events(date_str, data_root, config, project=None) -> list[dict]
     按时间升序的事件流；每项 {"time": "HH:MM:SS", "type", "title", "detail"}，
     type ∈ {session, ai_session, git_commit}。
  - build_timeline(date, data_root, config, project=None) -> dict
     {"date", "events", "summary"}；events 即 timeline_events 的输出，
     summary 为 {ai_minutes, commit_count, churn, total_cost, ai_blocks, conversations}。
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import time as _time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 配置段（对齐 git_insights.git_config 的风格，git_insights.py:37）
# P1 的 config_schema_version 迁移会把它并入 config.default.json；
# 这里自带默认值兜底，老用户 config.json 不加字段也能正常跑。
# ---------------------------------------------------------------------------
_DEFAULT_TIMELINE = {
    "enabled": True,
    "merge_gap_s": 120,   # 相邻 AI 会话段合并阈值（秒），超此间隔视为两块
}

_EPOCH = _dt.datetime(1970, 1, 1)


def timeline_config(config: dict) -> dict:
    """从完整 config 提取 vibe_timeline 段并补齐默认值。

    返回值：{"enabled": bool, "merge_gap_s": int}，字段缺失均回退默认。
    """
    vt = config.get("vibe_timeline")
    if not isinstance(vt, dict):
        vt = {}
    enabled = bool(vt.get("enabled", _DEFAULT_TIMELINE["enabled"]))
    try:
        merge_gap_s = max(1, int(vt.get("merge_gap_s", _DEFAULT_TIMELINE["merge_gap_s"]) or 1))
    except (TypeError, ValueError):
        merge_gap_s = _DEFAULT_TIMELINE["merge_gap_s"]
    return {"enabled": enabled, "merge_gap_s": merge_gap_s}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Evt:
    """时间轴内部事件（统一按 ts 排序）。

    kind: session | ai_session | git_commit；payload 为展示字段扁平 dict。
    对外输出时统一序列化为 {"time", "type", "title", "detail"}（见 _to_events）。
    """
    kind: str
    ts: _dt.datetime | None
    payload: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 时间戳归一化
# ---------------------------------------------------------------------------
# "YYYY-MM-DDTHH:MM:SS" / "YYYY-MM-DD HH:MM:SS" [+08:00 | +0800]
_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
    r"(?:[ ]?([+-])(\d{2}):?(\d{2}))?$"
)
_DAY_ONLY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _norm_dt(ts) -> _dt.datetime | None:
    """三源时间戳 -> naive datetime（统一排序 key）。

    支持 "YYYY-MM-DDTHH:MM:SS" / "YYYY-MM-DD HH:MM:SS[ +0800]" / 纯日期 /
    datetime 对象 / epoch 秒。git commit 的 iso date（"2026-08-20 09:48:30 +0800"）
    先转 UTC epoch 再转本机本地 naive，与 report/ai_sessions 的 naive 本地时间对齐。
    解析失败返回 None。
    """
    if ts is None:
        return None
    if isinstance(ts, _dt.datetime):
        return ts.replace(tzinfo=None)
    if isinstance(ts, (int, float)):
        try:
            return _dt.datetime.fromtimestamp(float(ts))
        except (OSError, ValueError, OverflowError):
            return None
    text = str(ts).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")  # 常见 UTC 后缀
    m = _TS_RE.match(text)
    if m is None:
        m2 = _DAY_ONLY_RE.match(text)
        if m2:
            try:
                return _dt.datetime(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
            except ValueError:
                return None
        return None
    try:
        dt = _dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                          int(m.group(4)), int(m.group(5)), int(m.group(6)))
    except ValueError:
        return None
    if not m.group(7):
        return dt
    # 带时区（git --date=iso 输出）：转本地 naive，避免与其它源混排错位。
    sign = 1 if m.group(7) == "+" else -1
    offset_s = sign * (int(m.group(8)) * 3600 + int(m.group(9)) * 60)
    try:
        utc = dt - _dt.timedelta(seconds=offset_s)
        epoch = (utc - _EPOCH).total_seconds()
        lt = _time.localtime(epoch)
        return _dt.datetime(lt.tm_year, lt.tm_mon, lt.tm_mday,
                            lt.tm_hour, lt.tm_min, lt.tm_sec)
    except (OverflowError, ValueError, OSError):
        return dt


# ---------------------------------------------------------------------------
# 三源采集（best-effort：任一源失败/缺失 → 空列表，绝不抛异常）
# ---------------------------------------------------------------------------
def _collect_ai_sessions(date: str, data_root: str, config: dict) -> list[dict]:
    """取当日 AI 相关前台会话段（category==AI编程 或 ai_tool 非空）。

    内部调用 report.aggregate(date, data_root)（report.py:171）取其 agg["sessions"]。
    """
    try:
        import report  # noqa: PLC0415 —— 惰性导入避免循环依赖
        agg = report.aggregate(date, data_root)
    except Exception:  # noqa: BLE001 —— 聚合失败降级
        return []
    sessions = agg.get("sessions") or []
    out = []
    for s in sessions:
        if not isinstance(s, dict):
            continue
        if not (s.get("category") == "AI编程" or s.get("ai_tool")):
            continue
        out.append({k: s.get(k) for k in (
            "start", "end", "duration_ms", "app", "exe", "title",
            "category", "ai_tool")})
    return out


def _collect_conversations(date: str, config: dict) -> list[dict]:
    """取当日 ai_sessions.collect() 的会话深度明细（total.conversations）。"""
    try:
        import ai_sessions  # noqa: PLC0415
        result = ai_sessions.collect(date, config)
    except Exception:  # noqa: BLE001 —— 会话深度失败降级
        return []
    if not isinstance(result, dict):
        return []
    total = result.get("total")
    if not isinstance(total, dict):
        return []
    convs = total.get("conversations")
    return convs if isinstance(convs, list) else []


def _collect_git_commits(config: dict, day: str) -> list[dict]:
    """产出当天 per-commit 事件 [{date, hash, project, added, deleted, author}]。

    关键：git_insights.analyze_repo（git_insights.py:153）只返回当日聚合、不暴露
    单条 commit 时间戳；此处复用 git_insights._run_git（:112）+ _parse_numstat
    （:204）自行跑 git log 拿 per-commit。git 缺失/失败 -> 返回 []（降级）。
    """
    try:
        import git_insights  # noqa: PLC0415
        gc = git_insights.git_config(config)
    except Exception:  # noqa: BLE001
        return []
    if not gc.get("enabled") or not gc.get("projects"):
        return []
    run = getattr(git_insights, "_run_git", None)
    parse = getattr(git_insights, "_parse_numstat", None)
    if run is None or parse is None:
        return []

    commits: list[dict] = []
    timeout = gc.get("timeout_s", 10)
    for proj in gc["projects"]:
        path = proj.get("path")
        if not path:
            continue
        try:
            if not os.path.isdir(path):
                continue
            raw = run(["log", f"--since={day} 00:00:00", f"--until={day} 23:59:59",
                       "--date=iso", "--pretty=format:%x1e%H%x1f%ad%x1f%an",
                       "--numstat"], path, timeout)
        except Exception:  # noqa: BLE001 —— 单仓库失败不影响其它仓库
            continue
        if not raw:
            continue
        for c in parse(raw):
            files = c.get("files") or []
            commits.append({
                "date": str(c.get("date") or ""),
                "hash": str(c.get("hash") or ""),
                "project": proj.get("name") or os.path.basename(path.rstrip("\\/")),
                "added": sum(f.get("added") or 0 for f in files),
                "deleted": sum(f.get("deleted") or 0 for f in files),
                "author": str(c.get("author") or ""),
            })
    return commits


# ---------------------------------------------------------------------------
# 合并与叠加
# ---------------------------------------------------------------------------
def _merge_blocks(sessions: list[dict], gap_s: int) -> list[dict]:
    """把相邻 AI 会话段按「同 ai_tool + 间隔<gap_s」合并为粗粒度 AI 工作块。

    输入无需有序（内部先按 start 排序）；输出块按 start 升序。
    每块：{tool, start, end, sessions, duration_ms}。
    时间戳缺失/乱序的会话按单段成块（不崩、不丢失）。
    """
    ordered = sorted(
        ((s, _norm_dt(s.get("start"))) for s in sessions if isinstance(s, dict)),
        key=lambda x: x[1] if x[1] is not None else _dt.datetime.max,
    )
    blocks: list[dict] = []
    for s, start in ordered:
        if start is None:
            # 无 start 的会话单独成块（best-effort 不丢）
            blocks.append({
                "tool": str(s.get("ai_tool") or ""), "start": start, "end": start,
                "sessions": 1, "duration_ms": int(s.get("duration_ms") or 0),
            })
            continue
        end = _norm_dt(s.get("end")) or start
        if end < start:
            end = start
        tool = str(s.get("ai_tool") or "")
        if blocks and blocks[-1]["start"] is not None and blocks[-1]["tool"] == tool \
                and (start - blocks[-1]["end"]).total_seconds() < gap_s:
            b = blocks[-1]
            b["end"] = max(b["end"], end)
            b["sessions"] += 1
            b["duration_ms"] += int(s.get("duration_ms") or 0)
        else:
            blocks.append({
                "tool": tool, "start": start, "end": end,
                "sessions": 1, "duration_ms": int(s.get("duration_ms") or 0),
            })
    return blocks


def _attach_conversations(blocks: list[dict], conversations: list[dict], gap_s: int) -> list[dict]:
    """把 ai_sessions 的 conversations 叠加回对应块。

    归属判定：conversation 的 first 落在块窗口 [start-gap_s, end+gap_s]；
    命中即在块上累加 conversations/tokens_total/cost_total/generated_lines。
    一个 conversation 只归第一个命中的块（时间有序时即最近块）。
    """
    for b in blocks:
        b.setdefault("conversations", 0)
        b.setdefault("tokens_total", 0)
        b.setdefault("cost_total", 0.0)
        b.setdefault("generated_lines", 0)
    for c in conversations or []:
        if not isinstance(c, dict):
            continue
        first = _norm_dt(c.get("first"))
        if first is None:
            continue
        for b in blocks:
            if b["start"] is None:
                continue
            lo = b["start"] - _dt.timedelta(seconds=gap_s)
            hi = b["end"] + _dt.timedelta(seconds=gap_s)
            if lo <= first <= hi:
                b["conversations"] += 1
                b["tokens_total"] += int(c.get("tokens_total") or 0)
                b["cost_total"] += float(c.get("cost_total") or 0.0)
                b["generated_lines"] += int(c.get("generated_lines") or 0)
                break
    return blocks


# ---------------------------------------------------------------------------
# 事件化 & 汇总
# ---------------------------------------------------------------------------
def _block_conv_n(ts: _dt.datetime | None, blocks: list[dict], gap_s: int) -> int:
    """session 时间戳命中的块的 conversation 数（±gap_s 容差），未命中返回 0。"""
    if ts is None:
        return 0
    for b in blocks:
        if b["start"] is None:
            continue
        if b["start"] - _dt.timedelta(seconds=gap_s) <= ts <= b["end"] + _dt.timedelta(seconds=gap_s):
            return int(b.get("conversations") or 0)
    return 0


def _project_match(payload: dict, project: str) -> bool:
    """模糊项目过滤（substring，大小写不敏感）。会话事件回退到标题/应用名匹配。"""
    needle = (project or "").strip().lower()
    if not needle:
        return True
    proj = payload.get("project")
    if isinstance(proj, str) and proj and needle in proj.lower():
        return True
    hay = " ".join(str(payload.get(k) or "") for k in ("title", "app", "exe"))
    return needle in hay.lower()


def _to_events(raw: list[Evt], project: str | None) -> list[dict]:
    """Evt 列表 → 对外事件列表（time/type/title/detail），project 过滤 + 升序排序。

    时间戳解析失败的事件丢弃（不崩、不占位）。同一天内 HH:MM:SS 等宽，
    字典序即时间序，排序键用 time 字符串即可（输入乱序仍输出有序）。
    """
    events: list[dict] = []
    for ev in raw:
        if ev.ts is None:
            continue
        payload = ev.payload or {}
        if not _project_match(payload, project or ""):
            continue
        if ev.kind == "session":
            title = payload.get("app") or payload.get("exe") or "AI 会话"
            if payload.get("title"):
                title = f"{title} · {payload['title']}"
            detail = {k: payload[k] for k in payload if payload[k] is not None}
        elif ev.kind == "ai_session":
            tool = payload.get("tool") or "AI 工具"
            model = payload.get("model") or "未知模型"
            title = f"{tool} · {model}"
            detail = {k: payload.get(k) for k in (
                "tool", "model", "project", "tokens_total", "cost_total",
                "generated_lines", "turns", "rounds", "first", "last", "id")}
        else:  # git_commit
            h = str(payload.get("hash") or "")[:8]
            title = f"commit {h} · {payload.get('project') or '未知名项目'}"
            detail = {k: payload.get(k) for k in (
                "hash", "project", "added", "deleted", "author", "date")}
        events.append({"time": ev.ts.strftime("%H:%M:%S"), "type": ev.kind,
                       "title": title, "detail": detail})
    events.sort(key=lambda e: e["time"])
    return events


def _summarize(events: list[dict]) -> dict:
    """汇总 ai_minutes / commit_count / churn / total_cost 等。"""
    ai_minutes = 0
    commit_count = 0
    churn = 0
    total_cost = 0.0
    ai_blocks = 0
    conversations = 0
    for e in events:
        d = e.get("detail") or {}
        if e["type"] == "session":
            ai_blocks += 1
            ai_minutes += int(d.get("duration_ms") or 0) / 60000.0
        elif e["type"] == "git_commit":
            commit_count += 1
            churn += int(d.get("added") or 0) + int(d.get("deleted") or 0)
        elif e["type"] == "ai_session":
            conversations += 1
            total_cost += float(d.get("cost_total") or 0.0)
    return {
        "ai_minutes": round(ai_minutes, 1),
        "commit_count": commit_count,
        "churn": churn,
        "total_cost": round(total_cost, 4),
        "ai_blocks": ai_blocks,
        "conversations": conversations,
    }


def _empty(date: str) -> dict:
    """返回空态（无 AI 会话 / disabled）。events 空 + summary 归零。"""
    return {
        "date": date,
        "events": [],
        "summary": {
            "ai_minutes": 0, "commit_count": 0, "churn": 0,
            "total_cost": 0.0, "ai_blocks": 0, "conversations": 0,
        },
    }


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------
def timeline_events(date_str: str, data_root: str, config: dict,
                    project: str | None = None) -> list[dict]:
    """某天的时间轴事件流（按时间升序），合并三源，纯派生、不落盘。

    事件类型：session（usage.jsonl 的 AI 相关前台会话段）、
    ai_session（ai_sessions.collect 的会话深度）、
    git_commit（git_insights 底层 git log 的 per-commit）。

    每条事件：{"time": "HH:MM:SS", "type", "title", "detail"}。
    任一源失败/缺失自动降级为空；整体不抛异常：失败返回 []。
    """
    try:
        cfg = timeline_config(config)
        if not cfg["enabled"]:
            return []
        sessions = _collect_ai_sessions(date_str, data_root, config)
        conversations = _collect_conversations(date_str, config)
        commits = _collect_git_commits(config, date_str)

        # 块归属统计（session 事件的 detail 标注命中几个 AI 会话深度记录）
        blocks = _attach_conversations(
            _merge_blocks(sessions, cfg["merge_gap_s"]),
            conversations, cfg["merge_gap_s"])

        raw: list[Evt] = []
        for s in sessions:
            raw.append(Evt(kind="session", ts=_norm_dt(s.get("start")), payload={
                "app": s.get("app") or s.get("exe") or "",
                "exe": s.get("exe") or "",
                "title": s.get("title") or "",
                "category": s.get("category") or "",
                "duration_ms": int(s.get("duration_ms") or 0),
                "start": s.get("start") or "",
                "end": s.get("end") or "",
                "ai_tool": s.get("ai_tool") or "",
                "ai_convs": _block_conv_n(_norm_dt(s.get("start")), blocks, cfg["merge_gap_s"]),
            }))
        for c in conversations:
            project_name = c.get("project") or "未识别"
            raw.append(Evt(kind="ai_session", ts=_norm_dt(c.get("first")), payload={
                "tool": c.get("tool") or "",
                "model": c.get("model") or "",
                "project": project_name,
                "tokens_total": int(c.get("tokens_total") or 0),
                "cost_total": float(c.get("cost_total") or 0.0),
                "generated_lines": int(c.get("generated_lines") or 0),
                "turns": int(c.get("turns") or 0),
                "rounds": int(c.get("rounds") or 0),
                "first": c.get("first") or "",
                "last": c.get("last") or "",
                "id": c.get("id") or "",
            }))
        for c in commits:
            raw.append(Evt(kind="git_commit", ts=_norm_dt(c.get("date")), payload={
                "hash": c.get("hash") or "",
                "project": c.get("project") or "",
                "added": int(c.get("added") or 0),
                "deleted": int(c.get("deleted") or 0),
                "author": c.get("author") or "",
                "date": c.get("date") or "",
            }))
        return _to_events(raw, project)
    except Exception:  # noqa: BLE001 —— 纯派生视图，任何异常降级为空
        return []


def build_timeline(date: str, data_root: str, config: dict,
                   project: str | None = None) -> dict:
    """构建某天的时间轴事件 + 摘要（纯派生，不落盘）。

    参数：
      date      日期，YYYY-MM-DD
      data_root 数据根目录（含 <date>/usage.jsonl）
      config    完整配置（读 vibe_timeline 段）
      project   可选 project 模糊过滤

    返回：
      {"date": str, "events": [...], "summary": {...}}，见设计文档 §2.3。

    鲁棒性：任一源失败降级，不抛异常；输入乱序仍输出有序（幂等）。
    """
    cfg = timeline_config(config)
    if not cfg["enabled"]:
        return _empty(date)
    try:
        events = timeline_events(date, data_root, config, project=project)
        return {"date": date, "events": events, "summary": _summarize(events)}
    except Exception:  # noqa: BLE001 —— 兜底：空态而非 500
        return _empty(date)


if __name__ == "__main__":
    # 手工验证（不依赖真实数据）：空态与配置兜底。
    import json
    import sys
    try:
        import classifier  # noqa: PLC0415
        cfg = classifier.load_config()
    except Exception:  # noqa: BLE001
        cfg = {}
    print(json.dumps(build_timeline("2099-01-01", ".", cfg), ensure_ascii=False))
    sys.exit(0)