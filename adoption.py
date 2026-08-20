# -*- coding: utf-8 -*-
"""adoption.py — v3.0 · P9 无插件采纳率/留存率近似归因（SPIKE 骨架）。

对应 docs/VIBECODING_IMPLEMENTATION_GUIDE.md §6.1 与 docs/ADOPTION_SPIKE.md。

**先 spike，失败就砍**：精确采纳率/留存率在无插件事件时**物理做不到**——既有会话文件
只带消息时间窗，没有「哪次编辑由谁/哪条消息生成」的 IDE 事件。本模块只做**近似归因**，
且**必须**显著标注误差（notice/disclaimer，UI 折叠展示），绝不声称精确。

**三信号启发式**（adoption_stats 判一个文件今天「疑似 AI 生成」）：
  1. `git_insights`：真实 Git 产出（当日 repo / file 级 added/deleted/churn、modify_ratio）；
  2. `ai_sessions.collect`：当日 AI 会话（conversations 带 first/last 时间窗 + project 归口、
     total.generated_lines 生成量估算——该值本身是换行数启发式，误差会传导到本模块）；
  3. **文件 mtime**：工作区文件最后写入时间；落在任一匹配项目的 AI 会话窗内 → 判为 AI 触碰。

**派生指标（全部「仅参考」）**：
  - per-file `ai_generated_ratio`：文件疑似 AI 生成行 / 文件新增行（项目 AI 行按会话 turns
    占比从 total.generated_lines 分摊，窗口内文件按 added 占比再分摊；added=0 → None）；
  - per-file `reworked_ratio`：deleted / (added + deleted)（「返工/重写」近似代理，逐文件
    modify_ratio 口径）；
  - per-project `approximate_retention` = lines_added / max(proj_ai_lines, 1)（AI 产出
    落进提交的比例；分母为 0 → None）；
  - per-project `approximate_acceptance` = 1 - modify_ratio（提交中未被删除的比例）。

**confidence 判定**：无 AI 数据 → "low"；join_rate（窗口内文件占比）≥ min_join_rate 且
AI 行估计 > 0 → "medium"；永远不给 "high"（缺 IDE 事件，物理天花板）。所有近似值强约束。

**铁律**：只读 import ai_sessions / git_insights，不修改任何既有模块；不写
usage.jsonl / usage.db；任何单源失败 → best-effort 降级（契约空态 200 可展示，绝不 500）。

依赖：仅项目内模块 + Python 标准库（零第三方运行时依赖）。

CLI：python adoption.py --day 2026-08-20 [--config path] [--data-root path] [--json]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

import ai_sessions  # 只读复用（collect → 会话窗 / generated_lines）
import git_insights  # 只读复用（analyze_repo → 真实 Git 变更）

# ---------------------------------------------------------------------------
# 默认配置（读 config.adoption；风格对齐 git_insights.git_config）
# ---------------------------------------------------------------------------
_DEFAULT_ADOPTION = {
    "enabled": True,
    "window_slack_s": 600,       # AI 会话窗前后宽容秒数（mtime 与消息时间的合理偏移）
    "top_files": 1_000_000,      # per-repo 全量文件（spike 需全量 numstat，不受 insights.git.top_files 限制）
    "min_join_rate": 0.30,       # join_rate 阈值：低于 → confidence=low（spike 判砍参考线）
}

# 近似归因强制免责声明（UI / 报告必须原样展示）
_NOTICE = (
    "无插件近似归因：非真实采纳率/留存率；基于 Git 当日变更 × AI 会话时间窗 × 文件 mtime "
    "启发式估算，误差可能很大，仅供参考。"
)


def adoption_config(config: dict) -> dict:
    """从完整 config 提取 adoption 段并补齐默认值（老用户 config.json 无该段也能跑）。"""
    raw = (config or {}).get("adoption")
    sec = raw if isinstance(raw, dict) else {}
    out = dict(_DEFAULT_ADOPTION)
    out["enabled"] = bool(sec.get("enabled", _DEFAULT_ADOPTION["enabled"]))
    try:
        out["window_slack_s"] = max(0.0, float(sec.get("window_slack_s",
                                                       _DEFAULT_ADOPTION["window_slack_s"])))
    except (TypeError, ValueError):
        pass
    try:
        out["top_files"] = max(1, int(sec.get("top_files", _DEFAULT_ADOPTION["top_files"])))
    except (TypeError, ValueError):
        pass
    try:
        out["min_join_rate"] = max(0.0, min(1.0, float(sec.get("min_join_rate",
                                                               _DEFAULT_ADOPTION["min_join_rate"]))))
    except (TypeError, ValueError):
        pass
    return out


def _empty_result(date: str) -> dict:
    """契约空态（200 可展示，非 500）。"""
    return {
        "date": date,
        "enabled": True,
        "found": False,
        "notice": _NOTICE,
        "summary": {"projects": 0, "files": 0, "ai_windows": 0, "join_rate": 0.0,
                    "approximate_retention": None, "approximate_acceptance": None},
        "projects": [],
    }


def _seconds(ts: str) -> float | None:
    """ISO 时间戳（YYYY-MM-DD[THH:MM:SS]）→ 本地 epoch 秒；解析失败返回 None。"""
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.strip().replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _fuzzy_match(a: str, b: str) -> bool:
    """双向子串匹配（大小写不敏感）：a 或 b 互为对方的子串。"""
    x, y = (a or "").strip().lower(), (b or "").strip().lower()
    return bool(x) and bool(y) and (x in y or y in x)


def _clamp01(v: float) -> float:
    """夹到 [0, 1]。"""
    if v <= 0:
        return 0.0
    if v >= 1:
        return 1.0
    return round(v, 4)


def _collect_ai(date: str, config: dict) -> tuple[dict, list[dict]]:
    """best-effort 拉取当日 AI 会话数据。

    返回 (total, windows)；任一失败 → (空 total, [])，不抛异常。
    windows = [{project, first, last, first_sec, last_sec, turns}]（collect 截断后的会话）。
    """
    try:
        col = ai_sessions.collect(date, config)
    except Exception:  # noqa: BLE001 —— 会话目录缺失/文件损坏等一律降级
        return {}, []
    total = col.get("total") or {}
    windows: list[dict] = []
    for conv in total.get("conversations") or []:
        first_s = _seconds(conv.get("first") or "")
        last_s = _seconds(conv.get("last") or "")
        if first_s is None or last_s is None:
            continue
        windows.append({
            "project": str(conv.get("project") or "未识别"),
            "first": conv.get("first") or "",
            "last": conv.get("last") or "",
            "first_sec": min(first_s, last_s),
            "last_sec": max(first_s, last_s),
            "turns": int(conv.get("turns") or 0),
        })
    return total, windows


def _in_window(mtime_s: float, windows: list[dict], slack_s: float) -> bool:
    """文件 mtime 是否落在任一会话窗 [first-slack, last+slack] 内。"""
    for w in windows:
        if (w["first_sec"] - slack_s) <= mtime_s <= (w["last_sec"] + slack_s):
            return True
    return False


def _repo_files(repo: dict, day: str, cfg: dict) -> list[dict]:
    """该仓库当天变更的全量文件（numstat，忽略/二进制不统计的自动跳过）。

    直接复用 git_insights.analyze_repo 并把 top_files 放到全量，避免重复实现
    git log 解析；非仓库 / git 缺失 / 超时 / 当天无提交 → 返回 []。
    """
    try:
        stats = git_insights.analyze_repo(
            repo, day, float(cfg.get("timeout_s", 10)), int(cfg.get("top_files", 1_000_000)))
    except Exception:  # noqa: BLE001
        return []
    if not stats:
        return []
    return [dict(f) for f in (stats.get("top_files") or [])]


def _file_mtime(repo_path: str, rel: str) -> float | None:
    """仓库内文件的最后写入时间（本地 epoch）；文件不存在/IO 错误 → None。"""
    try:
        return os.path.getmtime(os.path.join(repo_path, rel.replace("/", os.sep)))
    except (OSError, ValueError):
        return None


def _file_mtime_iso(repo_path: str, rel: str) -> str | None:
    sec = _file_mtime(repo_path, rel)
    if sec is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(sec).isoformat(timespec="seconds")
    except (OSError, ValueError, OverflowError):
        return None


def adoption_stats(date: str, data_root: str, config: dict) -> dict:
    """无插件采纳率/留存率近似归因（SPIKE 主入口）。

    返回 docs/ADOPTION_SPIKE.md §3 契约的 dict：
    {
      date, enabled, found,
      notice(强制免责声明),
      summary: {projects, files, ai_windows, join_rate,
                approximate_retention(None=无AI数据), approximate_acceptance},
      projects: [{
        project, repo, approximate_retention, approximate_acceptance, confidence,
        ai_generated_lines, lines_added, join_rate,
        files: [{path, added, deleted, churn, mtime, in_ai_window,
                 ai_generated_ratio, reworked_ratio}]
      }]
    }

    best-effort：git 数据失败 → 契约空态；AI 数据失败 → 仅 Git 侧（ratio 全 0/None，
    confidence=low）；单仓库失败仅跳过该仓库。绝不抛异常。
    """
    cfg = adoption_config(config)
    empty = _empty_result(date)
    if not cfg.get("enabled"):
        empty["enabled"] = False
        empty["notice"] = "近似归因已关闭（adoption.enabled=false）。"
        return empty

    # 1) Git 侧（真实产出；失败 → 空态）
    try:
        gc = git_insights.git_config(config)
        repos = gc.get("projects") or []
        repos = [r for r in repos if isinstance(r, dict) and r.get("path")]
    except Exception:  # noqa: BLE001
        return empty
    if not repos:
        empty["notice"] += " 未配置 Git 仓库（insights.git.projects）。"
        return empty

    # 2) AI 侧（会话窗 + 生成量估算；失败只降级不中断）
    total, windows = _collect_ai(date, config)
    total_turns = sum(w.get("turns") or 0 for w in windows)
    total_generated = int(total.get("generated_lines") or 0)

    projects: list[dict] = []
    files_total = 0
    in_window_total = 0
    ret_vals: list[float] = []
    acc_vals: list[float] = []

    for repo in repos:
        name = str(repo.get("name") or os.path.basename(repo.get("path", "").rstrip("\\/")))
        repo_path = str(repo.get("path") or "")
        files = _repo_files(repo, date, cfg)
        if not files:
            continue
        # 项目归口：仓库名与 AI 会话 project 双向子串匹配
        repo_windows = [w for w in windows if _fuzzy_match(name, w.get("project"))]
        proj_turns = sum(w.get("turns") or 0 for w in repo_windows)
        proj_ai_lines = (total_generated * proj_turns / total_turns) if total_turns > 0 else 0
        added_all = sum(int(f.get("added") or 0) for f in files)
        deleted_all = sum(int(f.get("deleted") or 0) for f in files)
        churn_all = added_all + deleted_all

        with_windows = [f for f in files if _in_window(
            _file_mtime(repo_path, str(f.get("path") or "")) or -1.0, repo_windows, cfg["window_slack_s"])]
        sum_added_win = sum(int(f.get("added") or 0) for f in with_windows)
        files_total += len(files)
        in_window_total += len(with_windows)

        per_files: list[dict] = []
        for f in files:
            rel = str(f.get("path") or "")
            added, deleted = int(f.get("added") or 0), int(f.get("deleted") or 0)
            churn = added + deleted
            mtime_s = _file_mtime(repo_path, rel)
            in_win = bool(mtime_s is not None and _in_window(mtime_s, repo_windows, cfg["window_slack_s"]))
            # ai_generated_ratio：窗口内文件按 added 占比分摊项目 AI 行；added=0 → None
            if added <= 0:
                ratio = None
            elif in_win and proj_ai_lines > 0 and sum_added_win > 0:
                ratio = _clamp01(proj_ai_lines * added / sum_added_win / added)
            else:
                ratio = 0.0
            per_files.append({
                "path": rel,
                "added": added,
                "deleted": deleted,
                "churn": churn,
                "mtime": _file_mtime_iso(repo_path, rel),
                "in_ai_window": in_win,
                "ai_generated_ratio": ratio,
                # 返工近似：deleted/(added+deleted)，逐文件口径同 modify_ratio
                "reworked_ratio": _clamp01(deleted / churn) if churn > 0 else 0.0,
            })

        join_rate = len(with_windows) / len(files) if files else 0.0
        retention = (added_all / proj_ai_lines) if proj_ai_lines > 0 else None
        acceptance = 1.0 - (deleted_all / churn_all) if churn_all > 0 else None
        # confidence：永远不给 high（缺 IDE 事件）；无 AI 或 join 低 → low
        if proj_ai_lines > 0 and files and join_rate >= cfg["min_join_rate"]:
            confidence = "medium"
        else:
            confidence = "low"
        if retention is not None and 0.0 <= retention <= 10.0:
            ret_vals.append(retention)
        if acceptance is not None:
            acc_vals.append(acceptance)

        projects.append({
            "project": name,
            "repo": repo_path,
            "approximate_retention": round(retention, 4) if retention is not None else None,
            "approximate_acceptance": round(acceptance, 4) if acceptance is not None else None,
            "confidence": confidence,
            "ai_generated_lines": int(round(proj_ai_lines)),
            "lines_added": added_all,
            "join_rate": round(join_rate, 4),
            "files": per_files,
        })

    if not projects:
        return empty

    summary = {
        "projects": len(projects),
        "files": files_total,
        "ai_windows": len(windows),
        "join_rate": round(in_window_total / files_total, 4) if files_total else 0.0,
        "approximate_retention": round(sum(ret_vals) / len(ret_vals), 4) if ret_vals else None,
        "approximate_acceptance": round(sum(acc_vals) / len(acc_vals), 4) if acc_vals else None,
    }
    return {
        "date": date,
        "enabled": True,
        "found": True,
        "notice": _NOTICE,
        "summary": summary,
        "projects": projects,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="无插件采纳率近似归因（SPIKE · 只读）")
    ap.add_argument("--day", required=True, help="日期 YYYY-MM-DD")
    ap.add_argument("--config", default=git_insights.DEFAULT_CONFIG, help="config.json 路径")
    ap.add_argument("--data-root", default="", help="数据根目录（当前仅契约占位）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()
    try:
        config = json.load(open(args.config, encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
    result = adoption_stats(args.day, args.data_root, config)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        s = result["summary"]
        print(f"found={result['found']} projects={s['projects']} files={s['files']} "
              f"join_rate={s['join_rate']} retention={s['approximate_retention']} "
              f"acceptance={s['approximate_acceptance']}")
        for p in result["projects"]:
            print(f"  {p['project']}: confidence={p['confidence']} join_rate={p['join_rate']} "
                  f"retention={p['approximate_retention']} acceptance={p['approximate_acceptance']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())