# -*- coding: utf-8 -*-
"""ai_sessions.py — AI 会话深度统计（可选增强，§6.4.3）。

读取 opencode / ChatGPT / Claude / Cursor / Windsurf / Trae / DeepSeek /
Pi Agent / DSH 等工具的本地会话文件（JSON / JSONL），统计某天
“AI 交互轮数、生成行数/字符数”等指标。默认关闭，需在 config.json
显式开启 `ai_sessions.enabled=true`；路径可用 `ai_sessions.paths` 自定义，
未配置时自动探测常见目录。

设计原则：
- 纯标准库、零第三方依赖；
- 只读取用户配置/常见 AI 工具本地会话目录，**不会上传任何数据**；
- 解析失败/格式未知时静默跳过，不影响监控主流程；
- JSONL 仍是原始事实源，本模块只是附加统计。

CLI：
  python ai_sessions.py --day 2026-08-10 [--json] [--data-root ...] [--config ...]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_FILE_SIZE = 20 * 1024 * 1024  # 单文件最大 20MB，避免误扫大文件卡顿

# 常见本地会话目录（ai_sessions.paths 未配置时使用）
_DEFAULT_PATHS: dict[str, list[str]] = {
    "opencode": [
        "~/.local/share/opencode",
        "~/.config/opencode",
    ],
    "chatgpt": [
        "%APPDATA%/ChatGPT",
        "%LOCALAPPDATA%/ChatGPT",
        "~/.chatgpt",
    ],
    "claude": [
        "%APPDATA%/Claude",
        "%LOCALAPPDATA%/Claude",
        "~/.claude",
    ],
    "cursor": [
        "%APPDATA%/Cursor",
        "%LOCALAPPDATA%/Cursor",
        "~/.cursor",
    ],
    "windsurf": [
        "%APPDATA%/Windsurf",
        "%LOCALAPPDATA%/Windsurf",
        "~/.codeium/windsurf",
        "~/.windsurf",
    ],
    "trae": [
        "%APPDATA%/Trae",
        "%LOCALAPPDATA%/Trae",
        "~/.trae",
    ],
    "deepseek": [
        "%APPDATA%/DeepSeek",
        "%LOCALAPPDATA%/DeepSeek",
        "~/.deepseek",
    ],
    "pi_agent": [
        "~/.pi-agent",
        "~/.local/share/pi-agent",
        "%APPDATA%/pi-agent",
        "%LOCALAPPDATA%/pi-agent",
        "~/.config/pi-agent",
    ],
    "dsh": [
        "%DSH_DATA%",
        "%DSH_HOME%",
        "~/.dsh",
        "%LOCALAPPDATA%/dsh",
    ],
}

# 时间字段候选
_TIME_KEYS = ("timestamp", "created_at", "time", "date", "ts", "created")
# 角色字段候选
_ROLE_KEYS = ("role", "type", "author")
# 内容字段候选
_CONTENT_KEYS = ("content", "text", "message", "value")


def _expand(path: str) -> str:
    """展开 ~ 与 %VAR% 环境变量。"""
    path = os.path.expanduser(str(path or "").strip())
    path = os.path.expandvars(path)
    return path


def _default_tool_paths() -> dict[str, list[str]]:
    out = {}
    for tool, dirs in _DEFAULT_PATHS.items():
        expanded = [p for p in (_expand(d) for d in dirs) if p]
        if expanded:
            out[tool] = expanded
    return out


def _config_paths(config: dict) -> dict[str, list[str]]:
    """从 config 读取 ai_sessions.paths；未配置时返回默认探测路径。"""
    section = config.get("ai_sessions") if isinstance(config.get("ai_sessions"), dict) else {}
    raw_paths = section.get("paths")
    if isinstance(raw_paths, dict) and raw_paths:
        out = {}
        for tool, dirs in raw_paths.items():
            if isinstance(dirs, list):
                expanded = [p for p in (_expand(d) for d in dirs) if p]
                if expanded:
                    out[str(tool)] = expanded
        if out:
            return out
    return _default_tool_paths()


def _walk_files(dirs: list[str], max_files: int = 500) -> list[str]:
    """递归收集目录下 JSON/JSONL 文件（限制数量与单文件大小）。"""
    out: list[str] = []
    seen: set[str] = set()
    for base in dirs:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for name in files:
                if len(out) >= max_files:
                    return out
                if not name.lower().endswith((".json", ".jsonl", ".ndjson")):
                    continue
                path = os.path.join(root, name)
                try:
                    if os.path.getsize(path) > _MAX_FILE_SIZE:
                        continue
                except OSError:
                    continue
                real = os.path.normcase(os.path.abspath(path))
                if real not in seen:
                    seen.add(real)
                    out.append(path)
    return out


def _extract_timestamp(obj: dict) -> str | None:
    """从对象中提取可解析的本地时间字符串/时间戳。"""
    for key in _TIME_KEYS:
        val = obj.get(key)
        if val is None:
            continue
        if isinstance(val, (int, float)):
            # 秒级/毫秒级时间戳
            ts = float(val)
            if ts > 10_000_000_000:
                ts /= 1000.0
            try:
                return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")
            except (OSError, ValueError, OverflowError):
                continue
        text = str(val)
        if not text:
            continue
        # 去掉常见后缀 Z / 时区偏移，取前 19 位
        text = text.strip().replace("Z", "+00:00")
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(text[:19], fmt).isoformat(timespec="seconds")
            except ValueError:
                continue
    return None


_CONTAINER_KEYS = ("messages", "conversation", "history", "thread", "items", "turns",
                   "chat_messages", "entries", "conversations")
_DICT_CONTAINER_KEYS = ("conversations", "sessions", "threads")


def _extract_messages(obj, _depth: int = 0) -> list[dict]:
    """从 JSON/JSONL 对象中递归尽力提取消息列表（兼容多工具嵌套结构）。"""
    if _depth > 6:
        return []
    if isinstance(obj, dict):
        # 单条消息对象
        if any(k in obj for k in _ROLE_KEYS) and any(k in obj for k in _CONTENT_KEYS):
            return [obj]
        # 常见嵌套 message / data
        for key in ("message", "data"):
            inner = obj.get(key)
            if isinstance(inner, dict):
                sub = _extract_messages(inner, _depth + 1)
                if sub:
                    return sub
        # 列表容器
        for key in _CONTAINER_KEYS:
            val = obj.get(key)
            if isinstance(val, list):
                out: list[dict] = []
                for item in val:
                    if isinstance(item, dict):
                        out.extend(_extract_messages(item, _depth + 1))
                if out:
                    return out
        # 字典容器（conversations/sessions/threads）
        for key in _DICT_CONTAINER_KEYS:
            val = obj.get(key)
            if isinstance(val, dict):
                out = []
                for item in val.values():
                    if isinstance(item, dict):
                        out.extend(_extract_messages(item, _depth + 1))
                if out:
                    return out
    elif isinstance(obj, list):
        out = []
        for item in obj:
            if isinstance(item, dict):
                out.extend(_extract_messages(item, _depth + 1))
        return out
    return []


def _message_role(msg: dict) -> str | None:
    for key in _ROLE_KEYS:
        val = msg.get(key)
        if isinstance(val, str):
            return val.lower()
        if isinstance(val, dict):
            inner = val.get("role")
            if isinstance(inner, str):
                return inner.lower()
    return None


def _message_content(msg: dict) -> str:
    for key in _CONTENT_KEYS:
        val = msg.get(key)
        if isinstance(val, str):
            return val
        if isinstance(val, list):
            parts = []
            for part in val:
                if isinstance(part, dict):
                    if isinstance(part.get("text"), str):
                        parts.append(part["text"])
                    elif isinstance(part.get("content"), str):
                        parts.append(part["content"])
                elif isinstance(part, str):
                    parts.append(part)
            if parts:
                return "\n".join(parts)
        if isinstance(val, dict):
            text = val.get("text") or val.get("content")
            if isinstance(text, str):
                return text
    return ""


def _message_time(msg: dict) -> str | None:
    # 嵌套 message 对象优先
    for key in ("message", "data"):
        inner = msg.get(key)
        if isinstance(inner, dict):
            ts = _extract_timestamp(inner)
            if ts:
                return ts
    return _extract_timestamp(msg)


def parse_file(path: str) -> list[dict]:
    """解析单个会话文件，返回消息对象列表（含 timestamp/role/content 近似字段）。"""
    out: list[dict] = []
    try:
        if path.lower().endswith((".jsonl", ".ndjson")):
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for msg in _extract_messages(obj):
                        out.append(msg)
        else:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                data = json.load(fh)
            for msg in _extract_messages(data):
                out.append(msg)
    except Exception:  # noqa: BLE001 —— 格式未知/文件损坏时跳过
        return []
    return out


def _generated_lines(text: str) -> int:
    """生成行数：按换行符拆分，空内容返回 0。"""
    text = text or ""
    if not text.strip():
        return 0
    return len(text.splitlines())


def collect(date_str: str, config: dict) -> dict:
    """统计某天 AI 会话深度指标。

    返回结构：
    {
      "date": "YYYY-MM-DD",
      "enabled": bool,
      "found": bool,
      "tools": {tool: {"files": n, "turns": n, "user_messages": n,
                        "assistant_messages": n, "generated_lines": n,
                        "generated_chars": n}},
      "total": {...}
    }
    """
    section = config.get("ai_sessions") if isinstance(config.get("ai_sessions"), dict) else {}
    enabled = bool(section.get("enabled", False))
    if not enabled:
        return {"date": date_str, "enabled": False, "found": False,
                "tools": {}, "total": _empty_total()}

    tool_paths = _config_paths(config)
    tools: dict[str, dict] = {}
    parsed_paths: set[str] = set()
    for tool, dirs in tool_paths.items():
        stats = {"files": 0, "turns": 0, "user_messages": 0,
                 "assistant_messages": 0, "generated_lines": 0, "generated_chars": 0}
        for path in _walk_files(dirs):
            real = os.path.normcase(os.path.abspath(path))
            if real in parsed_paths:
                continue
            parsed_paths.add(real)
            messages = parse_file(path)
            hit_file = False
            for msg in messages:
                ts = _message_time(msg)
                if not ts or not ts.startswith(date_str):
                    continue
                role = _message_role(msg)
                content = _message_content(msg)
                hit_file = True
                stats["turns"] += 1
                if role in ("user", "human", "prompt"):
                    stats["user_messages"] += 1
                elif role in ("assistant", "ai", "bot", "model", "agent"):
                    stats["assistant_messages"] += 1
                    stats["generated_lines"] += _generated_lines(content)
                    stats["generated_chars"] += len(content or "")
            if hit_file:
                stats["files"] += 1
        if stats["files"] or stats["turns"]:
            tools[tool] = stats

    total = _empty_total()
    for stats in tools.values():
        for key in ("files", "turns", "user_messages", "assistant_messages",
                    "generated_lines", "generated_chars"):
            total[key] += stats[key]
    return {
        "date": date_str,
        "enabled": True,
        "found": bool(tools),
        "tools": tools,
        "total": total,
    }


def _empty_total() -> dict:
    return {"files": 0, "turns": 0, "user_messages": 0,
            "assistant_messages": 0, "generated_lines": 0, "generated_chars": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai_sessions.py", description="AI 会话深度统计（可选增强）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__import__('version').VERSION}")
    parser.add_argument("--day", metavar="YYYY-MM-DD", help="指定日期（默认今天）")
    parser.add_argument("--today", action="store_true", help="今天")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--data-root", default=None, help="数据根目录（默认取 config.json）")
    parser.add_argument("--config", default=None, help="config.json 路径")
    args = parser.parse_args(argv)

    try:
        import classifier  # noqa: PLC0415
        if args.config is None and args.data_root:
            args.config = os.path.join(args.data_root, "config.json")
        cfg = classifier.load_config(args.config)
    except Exception:  # noqa: BLE001
        cfg = {}

    if args.today:
        date_str = datetime.date.today().isoformat()
    elif args.day:
        date_str = args.day
    else:
        date_str = datetime.date.today().isoformat()
    if not _DAY_RE.fullmatch(date_str):
        print(f"[ai_sessions] 日期格式错误: {date_str}（应为 YYYY-MM-DD）", file=sys.stderr)
        return 2

    result = collect(date_str, cfg)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"# AI 会话深度统计 {date_str}")
    if not result["enabled"]:
        print("未启用：config.json 的 ai_sessions.enabled=false（默认关闭）")
        return 0
    if not result["found"]:
        print("（未发现该日期的本地 AI 会话记录；可配置 ai_sessions.paths 指向会话目录）")
        return 0
    for tool, s in result["tools"].items():
        print(f"- {tool}: 文件 {s['files']} 个，消息/轮数 {s['turns']}，"
              f"用户 {s['user_messages']} / 助手 {s['assistant_messages']}，"
              f"生成 {s['generated_lines']} 行 / {s['generated_chars']} 字符")
    print(f"合计: {result['total']['turns']} 条消息，生成 {result['total']['generated_lines']} 行")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
