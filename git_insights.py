# -*- coding: utf-8 -*-
"""git_insights.py — Git 代码变更分析（ROADMAP Phase 2 · 质量与效率）。

离线、只读、零网络请求：对用户配置的本地 Git 仓库，用 `git log --numstat`
统计指定日期内的提交/增删行/改动文件，从而衡量“代码产出”与“改写/返工”
（修改率 = 删除行 / (新增 + 删除)）——这是 Phase 2「采纳率/留存率/修改率」
中无需 IDE 插件即可离线落地的部分（Git 集成 · 代码变更分析）。

设计原则：
- 纯只读 git 命令（log / rev-parse），绝不改动仓库状态；
- git 缺失、仓库未配置、当日无提交或缺 data 时优雅降级（found=False）；
- 所有命令都有 timeout，异常不影响日报/仪表盘主流程。

CLI：python git_insights.py --day 2026-08-18 [--config path] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import classifier  # noqa: E402
import paths  # noqa: E402

DEFAULT_CONFIG = os.path.join(paths.default_data_root(), "config.json")

# git 默认配置（合并到 insights.git）
_DEFAULT_GIT = {
    "enabled": True,
    "projects": [],        # [path] 或 {name: path}
    "timeout_s": 10,       # 每个仓库的超时（秒）
    "top_files": 5,        # 每个仓库按变更量展示的文件数
}


def git_config(config: dict) -> dict:
    """从完整 config 提取 insights.git 段并补齐默认值。"""
    ins = (config or {}).get("insights")
    git = ins.get("git") if isinstance(ins, dict) and isinstance(ins.get("git"), dict) else {}
    enabled = bool(git.get("enabled", _DEFAULT_GIT["enabled"]))
    if isinstance(ins, dict):
        enabled = enabled and bool(ins.get("enabled", True))
    out = dict(_DEFAULT_GIT)
    out["enabled"] = enabled
    try:
        out["timeout_s"] = max(1.0, float(git.get("timeout_s", _DEFAULT_GIT["timeout_s"]) or 1.0))
    except (TypeError, ValueError):
        pass
    try:
        out["top_files"] = max(1, int(git.get("top_files", _DEFAULT_GIT["top_files"]) or 1))
    except (TypeError, ValueError):
        pass
    out["projects"] = _normalize_projects(git.get("projects"))
    return out


def _normalize_projects(raw) -> list[dict]:
    """把 projects 归一化为 [{name, path}]；支持 list[str] 或 {name: path}。"""
    projects: list[dict] = []
    if isinstance(raw, dict):
        for name, path in raw.items():
            p = str(path or "").strip().strip('"')
            if p:
                projects.append({"name": str(name or "").strip() or os.path.basename(p.rstrip("\\/")), "path": p})
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                p = item.strip().strip('"')
                projects.append({"name": os.path.basename(p.rstrip("\\/")), "path": p})
            elif isinstance(item, dict) and item.get("path"):
                p = str(item["path"]).strip().strip('"')
                if p:
                    projects.append({"name": str(item.get("name") or "").strip() or os.path.basename(p.rstrip("\\/")),
                                     "path": p})
    # 去重（按 path）
    seen: set[str] = set()
    out = []
    for proj in projects:
        key = os.path.normcase(os.path.abspath(proj["path"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(proj)
    return out


def _is_repo(path: str) -> bool:
    """path 是否为 git 仓库（含 .git 目录或 .git 文件 / 子模块）。"""
    if not os.path.isdir(path):
        return False
    gitmark = os.path.join(path, ".git")
    return os.path.isdir(gitmark) or os.path.isfile(gitmark)


def _run_git(args: list[str], cwd: str, timeout: float) -> str | None:
    """只读运行 git，成功返回 stdout（str），失败/缺失/超时返回 None。"""
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _parse_numstat(raw: str) -> list[dict]:
    """解析 `git log --pretty=... --numstat` 输出为提交列表。

    期望每段以 \x1e 开头：
      \x1e<hash>\x1f<date>\x1f<author>
      <add>\t<del>\t<file>
      ...
      （空行分隔）
    返回 [{hash, date, author, files:[{path, added, deleted}]}]。
    """
    commits: list[dict] = []
    if not raw:
        return commits
    blocks = [b for b in raw.split("\x1e") if b.strip()]
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        header = lines[0]
        parts = header.split("\x1f")
        if len(parts) < 3:
            continue
        commit: dict = {"hash": parts[0].strip(), "date": parts[1].strip(),
                        "author": parts[2].strip(), "files": []}
        for ln in lines[1:]:
            fields = ln.split("\t")
            if len(fields) < 3:
                continue
            add_s, del_s, fpath = fields[0], fields[1], "\t".join(fields[2:])
            # 二进制/重命名等 git 以 - 表示无需统计
            if add_s == "-" and del_s == "-":
                continue
            try:
                added = int(add_s)
                deleted = int(del_s)
            except (TypeError, ValueError):
                continue
            commit["files"].append({"path": fpath, "added": added, "deleted": deleted})
        commits.append(commit)
    return commits


def analyze_repo(repo: dict, day_str: str, timeout: float, top_files: int) -> dict:
    """统计单个仓库在 day_str 当天（本地时区 00:00:00–23:59:59）的提交与变更。

    返回含 commit_count / lines_added / lines_deleted / churn / files /
    top_files / authors / modify_ratio 的 dict；非仓库或失败时返回 None。
    """
    path = repo["path"]
    if not _is_repo(path):
        return None
    since = f"{day_str} 00:00:00"
    until = f"{day_str} 23:59:59"
    args = ["log", f"--since={since}", f"--until={until}",
            "--date=iso", "--pretty=format:%x1e%H%x1f%ad%x1f%an", "--numstat"]
    out = _run_git(args, path, timeout)
    if out is None:
        return None
    commits = _parse_numstat(out)
    added = sum(f["added"] for c in commits for f in c["files"])
    deleted = sum(f["deleted"] for c in commits for f in c["files"])
    churn = added + deleted
    file_map: dict[str, dict] = {}
    for c in commits:
        for f in c["files"]:
            entry = file_map.setdefault(
                f["path"], {"path": f["path"], "added": 0, "deleted": 0, "churn": 0})
            entry["added"] += f["added"]
            entry["deleted"] += f["deleted"]
            entry["churn"] += f["added"] + f["deleted"]
    top = sorted(file_map.values(), key=lambda e: -e["churn"])[:top_files]
    authors = sorted({c["author"] for c in commits if c.get("author")})
    modify_ratio = (deleted / churn) if churn > 0 else 0.0
    return {
        "name": repo["name"],
        "path": path,
        "commit_count": len(commits),
        "lines_added": added,
        "lines_deleted": deleted,
        "churn": churn,
        "files": len(file_map),
        "top_files": top,
        "authors": authors,
        "modify_ratio": round(modify_ratio, 2),
    }


def git_insights(config: dict, day_str: str) -> dict:
    """汇总指定日期的 Git 产出（ROADMAP Phase 2 · 代码变更分析）。"""
    gc = git_config(config)
    empty = {"enabled": gc["enabled"], "found": False, "repos": [],
             "total": {"commit_count": 0, "lines_added": 0, "lines_deleted": 0,
                       "churn": 0, "files": 0, "modify_ratio": 0.0},
             "notice": "未配置 Git 仓库（insights.git.projects）或已关闭"}
    if not gc["enabled"]:
        empty["notice"] = "Git 代码分析已关闭（insights.enabled=false 或 insights.git.enabled=false）"
        return empty
    if not gc["projects"]:
        return empty

    repos: list[dict] = []
    for proj in gc["projects"]:
        stats = analyze_repo(proj, day_str, gc["timeout_s"], gc["top_files"])
        if stats is not None and stats["commit_count"] > 0:
            repos.append(stats)

    if not repos:
        return {"enabled": True, "found": False, "repos": [],
                "total": {"commit_count": 0, "lines_added": 0, "lines_deleted": 0,
                          "churn": 0, "files": 0, "modify_ratio": 0.0},
                "notice": "已配置 Git 仓库，但当天没有本地提交"}
    total = {
        "commit_count": sum(r["commit_count"] for r in repos),
        "lines_added": sum(r["lines_added"] for r in repos),
        "lines_deleted": sum(r["lines_deleted"] for r in repos),
        "churn": sum(r["churn"] for r in repos),
        "files": sum(r["files"] for r in repos),
    }
    total["modify_ratio"] = round(
        total["lines_deleted"] / total["churn"], 2) if total["churn"] > 0 else 0.0
    return {"enabled": True, "found": True, "repos": repos, "total": total, "notice": ""}


def main() -> int:
    ap = argparse.ArgumentParser(description="Git 代码变更分析（只读 · 本地）")
    ap.add_argument("--day", required=True, help="日期 YYYY-MM-DD")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="config.json 路径")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()
    config = classifier.load_config(args.config)
    result = git_insights(config, args.day)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"enabled={result['enabled']} found={result['found']}")
        for r in result["repos"]:
            print(f"  {r['name']}: {r['commit_count']} commits, "
                  f"+{r['lines_added']}/-{r['lines_deleted']} ({r['churn']} churn, "
                  f"{r['files']} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
