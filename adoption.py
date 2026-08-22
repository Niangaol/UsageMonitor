# -*- coding: utf-8 -*-
"""adoption.py — v3.0 · P9 采纳率「Git 侧」代理指标（只读，折叠+免责展示）。

对应 docs/ADOPTION_SPIKE.md（§5 结论）与 docs/VIBECODING_IMPLEMENTATION_GUIDE.md §6.1。

**背景（spike 结论）**：无 IDE 插件事件时「AI 侧」per-file 归因（mtime × AI 会话窗）在
真实数据上 join 命中率 = 0%，判砍。本模块**只保留 Git 侧代理指标**，不掺 AI 会话时间窗、
不读取文件 mtime、不产生任何 AI 生成行归因。所有数值都是「当日 Git 变更的粗代理」，
不是真实采纳率，必须带强制免责声明，UI 折叠 + 灰色降权展示，confidence 永不等于 "high"。

**指标（每仓库 / 每日 global 口径）**：
  - `retention`（保留率粗代理）= lines_added / (lines_added + lines_deleted)；
  - `reworked_ratio`（返工粗代理）= lines_deleted / (lines_added + lines_deleted)
    == git_insights.modify_ratio；
两者互补且恒 ∈ [0,1]；churn == 0（如纯二进制提交）→ 该仓库两值为 None。

**数据来源**：只读复用 `git_insights.analyze_repo`（内部走 `git log --numstat` /
`_parse_numstat`），聚合口径总是全量（lines_added/lines_deleted 不受 top_files 限制），
`top_files` 仅限制每仓库 per-file 明细条数。

**confidence 规则**：found 且当日确有文本变更（总 churn > 0）→ "medium"（git 记录真实、
公式透明）；无数据/关闭/全部仓库跳过 → "low"。**永不返回 "high"**（缺 IDE 事件，物理天花板）。

**铁律**：只读 import git_insights，不改任何既有模块、不写 usage.jsonl/usage.db；
`git_config` 或整源失败 → 契约空态（found=False，200 可展示，绝不 500）；
单仓库失败仅跳过该仓库（best-effort）。本模块不 import ai_sessions。

依赖：仅项目内模块 + Python 标准库（零第三方运行时依赖）。

CLI：python adoption.py --day 2026-08-20 [--config path] [--data-root path] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import git_insights  # 只读复用（analyze_repo → 当日真实 Git 变更）

# ---------------------------------------------------------------------------
# 默认配置（读 config.adoption；风格对齐 git_insights.git_config）
# ---------------------------------------------------------------------------
_DEFAULT_ADOPTION = {
    "enabled": True,
    "top_files": 100,  # 每仓库 per-file 明细展示上限（聚合口径不受限，总是全量）
}

# 代理指标强制免责声明（UI / 报告必须原样展示）
_NOTICE = (
    "无插件 Git 侧代理指标（非真实采纳率）：仅按当日 Git 变更全局口径估算 "
    "「保留率 = 新增行/(新增+删除)」与「返工代理 = 删除行/(新增+删除)」，"
    "未关联 AI 会话/文件归属，误差可能很大，仅供参考。"
)


def adoption_config(config: dict) -> dict:
    """从完整 config 提取 adoption 段并补齐默认值（老用户 config.json 无该段也能跑）。"""
    raw = (config or {}).get("adoption")
    sec = raw if isinstance(raw, dict) else {}
    out = dict(_DEFAULT_ADOPTION)
    out["enabled"] = bool(sec.get("enabled", _DEFAULT_ADOPTION["enabled"]))
    try:
        out["top_files"] = max(1, int(sec.get("top_files", _DEFAULT_ADOPTION["top_files"])))
    except (TypeError, ValueError):
        pass
    return out


def _empty_result(date: str, enabled: bool = True, notice: str = _NOTICE) -> dict:
    """契约空态（200 可展示，非 500）。"""
    return {
        "date": date,
        "enabled": enabled,
        "found": False,
        "notice": notice,
        "confidence": "low",
        "summary": {"projects": 0, "files": 0, "commit_count": 0,
                    "lines_added": 0, "lines_deleted": 0, "churn": 0,
                    "retention": None, "reworked_ratio": None},
        "projects": [],
    }


def _clamp01(v: float) -> float:
    """夹到 [0, 1] 并保留 4 位小数。"""
    if v <= 0:
        return 0.0
    if v >= 1:
        return 1.0
    return round(v, 4)


def _repo_stats(repo: dict, date: str, cfg: dict) -> dict | None:
    """只读拉取单个仓库当日 Git 统计；非仓库 / git 缺失 / 超时 / 异常 → None。

    调用方据此**仅跳过该仓库**，不影响其他仓库。
    """
    try:
        return git_insights.analyze_repo(
            repo, date, float(cfg.get("timeout_s", 10)), int(cfg.get("top_files", 100)))
    except Exception:  # noqa: BLE001 —— 单仓库失败仅跳过
        return None


def adoption_stats(date: str, data_root: str, config: dict) -> dict:
    """Git 侧采纳率代理指标（SPIKE §5.3 收敛后的主入口）。

    返回契约：
    {
      date, enabled, found,
      notice(强制免责声明), confidence(仅 low/medium，永不 high),
      summary: {projects, files, commit_count, lines_added, lines_deleted, churn,
                retention(None=无文本变更), reworked_ratio},
      projects: [{
        project, repo, commit_count, lines_added, lines_deleted, churn, files,
        retention, reworked_ratio, confidence,
        top_files: [{path, added, deleted, churn, reworked_ratio}]
      }]
    }

    best-effort：git_config 失败/未配置仓库 → 契约空态；单仓库失败仅跳过该仓库；
    当日无有效提交 → 契约空态。绝不抛异常（端点侧也不会 500）。
    """
    cfg = adoption_config(config)
    empty = _empty_result(date)
    if not cfg["enabled"]:
        empty["enabled"] = False
        empty["notice"] = "Git 侧采纳率代理指标已关闭（adoption.enabled=false）。"
        return empty

    # 只读 Git 侧（单源失败 → 空态 200，绝不 500）
    try:
        gc = git_insights.git_config(config)
        repos = [r for r in (gc.get("projects") or []) if isinstance(r, dict) and r.get("path")]
    except Exception:  # noqa: BLE001
        return empty
    if not repos:
        empty["notice"] = _NOTICE + " 未配置 Git 仓库（insights.git.projects），无法计算代理指标。"
        return empty

    projects: list[dict] = []
    totals = {"files": 0, "commits": 0, "added": 0, "deleted": 0, "churn": 0}
    ret_vals: list[float] = []
    rework_vals: list[float] = []

    for repo in repos:
        name = str(repo.get("name") or os.path.basename(repo.get("path", "").rstrip("\\/")))
        stats = _repo_stats(repo, date, cfg)
        # 单仓库失败 / 当天无提交 → 仅跳过该仓库
        if not stats or int(stats.get("commit_count") or 0) <= 0:
            continue

        added = int(stats.get("lines_added") or 0)
        deleted = int(stats.get("lines_deleted") or 0)
        churn = added + deleted
        n_files = int(stats.get("files") or 0)
        commits = int(stats.get("commit_count") or 0)

        retention = _clamp01(added / churn) if churn > 0 else None
        reworked = _clamp01(deleted / churn) if churn > 0 else None

        totals["files"] += n_files
        totals["commits"] += commits
        totals["added"] += added
        totals["deleted"] += deleted
        totals["churn"] += churn
        if retention is not None:
            ret_vals.append(retention)
        if reworked is not None:
            rework_vals.append(reworked)

        top_files: list[dict] = []
        for f in stats.get("top_files") or []:
            pa, pd = int(f.get("added") or 0), int(f.get("deleted") or 0)
            pc = pa + pd
            top_files.append({
                "path": str(f.get("path") or ""),
                "added": pa,
                "deleted": pd,
                "churn": pc,
                "reworked_ratio": _clamp01(pd / pc) if pc > 0 else 0.0,
            })

        projects.append({
            "project": name,
            "repo": str(stats.get("path") or repo.get("path") or ""),
            "commit_count": commits,
            "lines_added": added,
            "lines_deleted": deleted,
            "churn": churn,
            "files": n_files,
            "retention": retention,
            "reworked_ratio": reworked,
            "confidence": "medium" if churn > 0 else "low",
            "top_files": top_files,
        })

    if not projects:
        empty["notice"] = _NOTICE + " 已配置 Git 仓库，但当日无有效提交（或全部仓库读取失败），无法计算代理指标。"
        return empty

    total_churn = totals["churn"]
    summary = {
        "projects": len(projects),
        "files": totals["files"],
        "commit_count": totals["commits"],
        "lines_added": totals["added"],
        "lines_deleted": totals["deleted"],
        "churn": total_churn,
        "retention": round(sum(ret_vals) / len(ret_vals), 4) if ret_vals else None,
        "reworked_ratio": round(sum(rework_vals) / len(rework_vals), 4) if rework_vals else None,
    }
    return {
        "date": date,
        "enabled": True,
        "found": True,
        "notice": _NOTICE,
        "confidence": "medium" if total_churn > 0 else "low",
        "summary": summary,
        "projects": projects,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Git 侧采纳率代理指标（只读 · SPIKE §5.3）")
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
        print(f"found={result['found']} confidence={result['confidence']} "
              f"projects={s['projects']} files={s['files']} commits={s['commit_count']} "
              f"+{s['lines_added']}/-{s['lines_deleted']} churn={s['churn']} "
              f"retention={s['retention']} reworked={s['reworked_ratio']}")
        for p in result["projects"]:
            print(f"  {p['project']}: confidence={p['confidence']} "
                  f"retention={p['retention']} reworked={p['reworked_ratio']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
