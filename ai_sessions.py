# -*- coding: utf-8 -*-
"""ai_sessions.py — AI 会话深度统计（默认开启，§6.4.3）。

读取 opencode / ChatGPT / Claude / Cursor / Windsurf / Trae / DeepSeek /
Pi Agent / DSH 等工具的本地会话文件（JSON / JSONL），统计某天
“AI 交互轮数、对话轮次、生成行数/字符数、Token 用量估算、按模型/项目拆分”
等指标；并可从浏览器访问明细（browser_history 输出）深度解析 Web AI 会话
（ChatGPT/Claude/Gemini 等聊天页面的会话轮次推断）。默认开启；可在 config.json
显式设 `ai_sessions.enabled=false` 关闭（仪表盘概览始终展示该维度）。
路径可用 `ai_sessions.paths` 自定义，未配置时自动探测常见目录。

实现要点（docs/ROADMAP.md Phase 1）：
- **对话轮次追踪**：本地会话文件内按 user→assistant 配对数计 `rounds`；
  浏览器历史里同一聊天会话页面的多次访问视为页面刷新轮次（best-effort）。
- **Token 用量估算**：`token_estimation`（默认开）。CJK 字符按 1 Token/字，
  其余按 4 字符/Token 折算输入/输出 Token。
- **按模型拆分**：从消息 `model` 字段或内容中的模型名正则提取。
- **按项目拆分**：从消息 `cwd/project/repo/...` 字段或会话文件目录推断。

设计原则：
- 纯标准库、零第三方依赖；
- 只读取用户配置/常见 AI 工具本地会话目录，**不会上传任何数据**；
- 解析失败/格式未知时静默跳过，不影响监控主流程；
- JSONL 仍是原始事实源，本模块只是附加统计。

CLI：
  python ai_sessions.py --day 2026-08-10 [--web] [--json] [--data-root ...] [--config ...]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse

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

# 用户 / 助手 角色白话集合
_USER_ROLES = ("user", "human", "prompt", "client", "user_msg")
_ASSISTANT_ROLES = ("assistant", "ai", "bot", "model", "agent", "assistant_msg", "response")

# 模型字段候选（含嵌套 response/model；不含通用 name，避免误把用户名/函数名当模型）
_MODEL_KEYS = ("model", "model_name", "model_id", "modelId", "model_id_str")
# 已知模型名正则（内容里的模型名识别按贪婪先后顺序；大小写不敏感）
_KNOWN_MODEL_RE = re.compile(
    r"(?:claude[- ][34](?:\.[0-9])?(?:-[\w.-]+)?"
    r"|claude-(?:opus|sonnet|haiku)[- 0-9.-]*"
    r"|gpt[-_ ]?(?:5|4o|4\.5|4|3\.5|3)[\w.-]*"
    r"|o[134](?:-mini)?[\w.-]*"
    r"|deepseek[-_ ]?(?:chat|reasoner|r1|v3?|coder|math)[\w.-]*"
    r"|gemini[- ][0-9](?:\.[0-9])?[\w.-]*"
    r"|qwen(?:\d+-)?[\w.-]*"
    r"|llama[- ][0-9][\w.-]*"
    r"|mistral(?:-large|-medium|-small|-nemo)?[\w.-]*"
    r"|codestral[\w.-]*"
    r"|moonshot[\w.-]*"
    r"|kimi[\w.-]*"
    r"|glm[-_ ]?[0-9][\w.-]*"
    r"|doubao[\w.-]*"
    r"|spark[\w.-]*"
    r"|ernie[\w.-]*"
    r"|command(?:-r|-a)?[\w.-]*"
    r"|codex(?:[- ][\w.-]+)?"
    r"|grok[- ][0-9][\w.-]*"
    r"|llama[\w.-]*"
    r"|gemma[\w.-]*)",
    re.IGNORECASE,
)

# 项目字段候选（cwd 等取最后一段作为项目名，避免路径噪声）
_PROJECT_KEYS = ("project", "cwd", "repo", "repository", "directory", "folder",
                 "workspace", "project_name", "git_repo", "worktree")
# 会话标识字段候选（同一文件内多会话时按此分组；缺失则整个文件视为一个会话）
_CONV_KEYS = ("conversation_id", "session_id", "thread_id", "chat_id", "conversationId",
              "sessionId", "threadId", "conversation", "session", "thread")

# CJK 字符（中文/日文/韩文）按 1 Token/字 折算
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")

# 内置主流模型定价表（USD / 百万 Token：(输入价, 输出价)）。
# 仅作“量级参考”；实际费用以各厂商实时报价为准，可用
# config 的 ai_sessions.costs.model_pricing 覆盖/补充（键为模型名子串，小写）。
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    # —— Anthropic（USD/百万 Token：输入, 输出）——
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus": (5.0, 25.0),          # Opus 4.x
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4": (3.0, 15.0),      # 4.5/4.6
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku": (0.25, 1.25),
    # —— OpenAI ——
    "gpt-5.5": (5.0, 30.0),
    "gpt-5.4-mini": (0.75, 4.5),
    "gpt-5.4": (2.5, 15.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5": (1.25, 10.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.5, 10.0),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-4": (30.0, 60.0),
    "gpt-3.5": (0.5, 1.5),
    "o4-mini": (1.1, 4.4),
    "o3-mini": (1.1, 4.4),
    "o3-pro": (20.0, 80.0),
    "o3": (2.0, 8.0),
    "o1": (15.0, 60.0),
    "codex": (1.75, 14.0),               # gpt-5.x-codex
    # —— DeepSeek（2026 V4）——
    "deepseek-v4-pro": (0.435, 0.87),
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v3": (0.27, 1.10),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-r1": (0.55, 2.19),
    # —— Google Gemini ——
    "gemini-3.6-flash": (1.5, 7.5),
    "gemini-3.1-pro": (2.0, 12.0),
    "gemini-3-flash-lite": (0.30, 2.50),
    "gemini-3-flash": (0.50, 3.0),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.0),
    # —— 国内模型（约合 USD）——
    "qwen-max": (1.6, 6.4),
    "qwen3-max": (0.50, 5.0),
    "qwen-plus": (0.8, 2.0),
    "qwen-turbo": (0.3, 0.6),
    "glm-5": (0.85, 3.4),
    "glm-4": (0.50, 1.40),
    "kimi": (1.0, 3.0),
    "moonshot": (1.0, 3.0),
    "doubao": (0.30, 0.60),
    "ernie": (0.57, 2.57),
    "hunyuan": (0.20, 0.90),
    # —— 其他 ——
    "grok-4": (1.25, 2.5),
    "grok-3": (3.0, 15.0),
    "mistral-large": (2.0, 6.0),
    "codestral": (0.30, 0.90),
    "mistral-small": (0.20, 0.60),
    "llama-4": (0.20, 0.40),
    "llama-3": (0.50, 0.75),
    "gemma": (0.20, 0.60),
    "command-r": (0.15, 0.60),
    # 定价随时变动，以上为“量级参考”。请用 config 的
    # ai_sessions.costs.model_pricing 或数据目录 ai_pricing.json 覆盖。
}
_WEB_AI_TOOLS: dict[str, tuple[str, ...]] = {
    "chatgpt": ("chatgpt.com", "chat.openai.com"),
    "claude": ("claude.ai",),
    "gemini": ("gemini.google.com",),
    "perplexity": ("perplexity.ai",),
    "deepseek": ("chat.deepseek.com",),
    "kimi": ("kimi.moonshot.cn", "kimi.com"),
    "copilot": ("copilot.microsoft.com",),
    "cursor": ("chat.cursor.com", "cursor.com"),
    "qwen": ("chat.qwen.ai", "tongyi.aliyun.com"),
    "metaso": ("metaso.cn",),
    "doubao": ("doubao.com",),
}
_WEB_CONV_PATTERNS = (
    re.compile(r"/c/([A-Za-z0-9_~-]{3,64})"),
    re.compile(r"/chat/([A-Za-z0-9_~-]{8,64})"),
    re.compile(r"/app/([A-Za-z0-9_~-]{8,64})"),
    re.compile(r"/conversations?/([A-Za-z0-9_~-]{8,64})"),
    re.compile(r"/share/([A-Za-z0-9_~-]{3,64})"),
    re.compile(r"/session/([A-Za-z0-9_~-]{3,64})"),
    re.compile(r"/thread/([A-Za-z0-9_~-]{8,64})"),
    re.compile(r"/inbox/([A-Za-z0-9_~-]{8,64})"),
)
_WEB_CONV_QUERY_RE = re.compile(r"[?&]c=([A-Za-z0-9_~-]{8,64})")

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



def estimate_tokens(text: str) -> int:
    """Token 量粗略估算（零依赖启发式）。

    CJK 字符按 1 Token/字，其余字符按 4 字符/Token（进一法）；空文本返回 0。
    仅用于“量级”参考，非精确计费。
    """
    text = text or ""
    if not text.strip():
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    return cjk + (other + 3) // 4


def _cost_section(config: dict) -> dict:
    """读取 ai_sessions.costs 配置段（空则返回空 dict）。"""
    section = config.get("ai_sessions") if isinstance(config.get("ai_sessions"), dict) else {}
    costs = section.get("costs") if isinstance(section.get("costs"), dict) else {}
    return costs


def _merge_pricing(table: dict[str, tuple[float, float]], raw: object) -> None:
    """把一段定价覆盖（{model: {"input":..,"output":..} 或 [in,out]}）并入表。"""
    if not isinstance(raw, dict):
        return
    for key, val in raw.items():
        if isinstance(val, dict):
            try:
                i = float(val.get("input", 0) or 0)
                o = float(val.get("output", 0) or 0)
            except (TypeError, ValueError):
                continue
        elif isinstance(val, (list, tuple)) and len(val) >= 2:
            try:
                i, o = float(val[0]), float(val[1])
            except (TypeError, ValueError):
                continue
        else:
            continue
        table[str(key).lower()] = (i, o)


def _pricing_file(config: dict) -> str | None:
    """用户自定义定价文件路径：<data_root>/ai_pricing.json（存在才返回）。"""
    root = config.get("data_root") or ""
    if not root:
        return None
    candidate = os.path.join(str(root), "ai_pricing.json")
    return candidate if os.path.isfile(candidate) else None


def _pricing_table(config: dict) -> dict[str, tuple[float, float]]:
    """合并内置定价表 + config 的 model_pricing + 数据目录 ai_pricing.json。

    优先级（后者覆盖前者）：内置默认 < config.ai_sessions.costs.model_pricing
    < <data_root>/ai_pricing.json。键为小写模型名子串。
    """
    table: dict[str, tuple[float, float]] = dict(_DEFAULT_PRICING)
    overrides = _cost_section(config).get("model_pricing")
    if isinstance(overrides, dict):
        _merge_pricing(table, overrides)
    fpath = _pricing_file(config)
    if fpath:
        try:
            with open(fpath, "r", encoding="utf-8-sig") as fh:
                raw = json.load(fh)
            _merge_pricing(table, raw)
        except Exception:  # noqa: BLE001 —— 定价文件损坏时忽略，不影响主流程
            pass
    return table


def _model_price(table: dict[str, tuple[float, float]], model: str) -> tuple[float, float]:
    """按模型名匹配（进/出 USD 每百万 Token）。未匹配返回 (0.0, 0.0)。"""
    m = (model or "").lower()
    if not m:
        return (0.0, 0.0)
    if m in table:
        return table[m]
    for key in sorted(table, key=len, reverse=True):
        if key in m:
            return table[key]
    return (0.0, 0.0)


def _fmt_cost(usd: float) -> str:
    """费用格式化：美元，小于 1 分显示 4 位小数，其余按需。"""
    usd = float(usd or 0)
    if usd == 0:
        return "$0"
    if usd < 0.01:
        return f"${usd:.4f}"
    if usd < 1:
        return f"${usd:.3f}"
    return f"${usd:.2f}"


def _message_model(msg: dict) -> str:
    """尽力提取模型名。优先级：直接字段 > 嵌套 response/model/delta > 内容正则。"""
    for key in _MODEL_KEYS:
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return _clean_model(val.strip())
        if isinstance(val, dict):  # 可能 {model: "..."} / {id: "..."}
            for inner_key in ("model", "name", "id"):
                inner = val.get(inner_key)
                if isinstance(inner, str) and inner.strip():
                    return _clean_model(inner.strip())
    for key in ("message", "data", "response", "result", "delta"):
        inner = msg.get(key)
        if isinstance(inner, dict):
            for ik in _MODEL_KEYS:
                iv = inner.get(ik)
                if isinstance(iv, str) and iv.strip():
                    return _clean_model(iv.strip())
    content = _message_content(msg)
    m = _KNOWN_MODEL_RE.search(content)
    if m:
        return _clean_model(m.group(0))
    return "未识别"


def _clean_model(raw: str) -> str:
    """归一化模型名（去掉引号/换行/前后空格，截断过长值）。"""
    name = re.sub(r"[\"'`\r\n]+", " ", raw).strip()
    return name[:48] or "未识别"


def _message_project(msg: dict) -> str | None:
    """从消息字段提取项目名（取路径最后一段）；没有返回 None。"""
    for key in _PROJECT_KEYS:
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            p = os.path.basename(val.rstrip("/\\"))
            if p and p not in ("", ".", ".."):
                return p[:64]
    for key in ("message", "data"):
        inner = msg.get(key)
        if isinstance(inner, dict):
            sub = _message_project(inner)
            if sub:
                return sub
    return None


def _message_conv_id(msg: dict, file_path: str) -> str:
    """会话标识：优先消息里的会话/线程字段；缺失则退回「文件名」整体为一会话。"""
    for key in _CONV_KEYS:
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:48]
        if isinstance(val, dict):
            for ik in _CONV_KEYS:
                iv = val.get(ik)
                if isinstance(iv, str) and iv.strip():
                    return iv.strip()[:48]
    basename = os.path.basename(file_path)
    return f"file:{basename[:40]}" if basename else "file:<unknown>"


def _count_rounds(msgs: list[dict]) -> int:
    """对话轮次：一次 user 提问后（在下一个 user 提问前）收到至少一条 assistant
    回复，记为一轮（Q/A 配对完成）。消息按文件顺序判定，尽力而为。
    """
    rounds = 0
    expecting = False
    for msg in msgs:
        role = _message_role(msg)
        if role in _USER_ROLES:
            expecting = True
        elif role in _ASSISTANT_ROLES and expecting:
            rounds += 1
            expecting = False
    return rounds


def _web_tool(domain: str) -> str | None:
    """从域名识别 Web AI 工具名；不是 AI 聊天域名返回 None。"""
    domain = (domain or "").lower()
    for tool, subs in _WEB_AI_TOOLS.items():
        for sub in subs:
            if sub in domain:
                return tool
    return None


def _web_conv_id(url: str) -> str | None:
    """从聊天页面 URL 提取会话 ID；非会话页（首页/新建）返回 None。"""
    url = str(url or "")
    parsed = urllib.parse.urlparse(url)
    for rx in _WEB_CONV_PATTERNS:
        m = rx.search(parsed.path)
        if m:
            return m.group(1)[:48]
    m = _WEB_CONV_QUERY_RE.search(url)
    if m:
        return m.group(1)[:48]
    return None


def web_ai_sessions(visits: list[dict]) -> dict:
    """从浏览器访问明细深度解析 Web AI 会话（对话轮次追踪的浏览器侧）。

    输入为 browser_history.collect() 的 visits 条目（含 domain/url/time/title）。
    同一聊天会话 URL 的每次访问视为一次页面刷新 ≈ 一轮；按 (工具, 会话ID) 分组。
    返回结构：
    {
      "found": bool, "turns": int, "conversations": int, "browsing_visits": int,
      "by_tool": {tool: {"conversations": int, "turns": int}},
      "sessions": [ {tool, id, title, visits, first, last} ... ]（按访问次数倒序，上限 20）
    }
    """
    per: dict[tuple[str, str], dict] = {}
    browsing = 0
    for v in visits or []:
        tool = _web_tool(v.get("domain", ""))
        if not tool:
            continue
        conv = _web_conv_id(v.get("url", ""))
        if not conv:
            browsing += 1
            continue
        key = (tool, conv)
        entry = per.get(key)
        if entry is None:
            title = v.get("title") or ""
            if title in ("[已隐藏]", ""):
                title = ""
            entry = {"tool": tool, "id": conv, "title": title, "visits": 0,
                     "first": v.get("time") or "", "last": v.get("time") or ""}
            per[key] = entry
        entry["visits"] += 1
        if not entry.get("first") or (v.get("time") or "") < entry["first"]:
            entry["first"] = v.get("time") or ""
        if not entry.get("last") or (v.get("time") or "") > entry["last"]:
            entry["last"] = v.get("time") or ""
        if not entry["title"] and v.get("title"):
            entry["title"] = v["title"]

    by_tool: dict[str, dict] = {}
    turns = 0
    for (_tool, _conv), entry in per.items():
        turns += entry["visits"]
        agg = by_tool.setdefault(entry["tool"], {"conversations": 0, "turns": 0})
        agg["conversations"] += 1
        agg["turns"] += entry["visits"]
    sessions = sorted(per.values(), key=lambda e: e["visits"], reverse=True)[:20]
    return {
        "found": bool(per),
        "turns": turns,
        "conversations": len(per),
        "browsing_visits": browsing,
        "by_tool": by_tool,
        "sessions": sessions,
    }


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


def collect(date_str: str, config: dict, web_visits: list[dict] | None = None) -> dict:
    """统计某天 AI 会话深度指标（ROADMAP Phase 1）。

    返回结构（向后兼容旧的 tools/total 标量字段，新增维度）：
    {
      "date": "YYYY-MM-DD",
      "enabled": bool,
      "found": bool,
      "tools": {tool: {"files", "turns", "rounds", "user_messages",
                        "assistant_messages", "generated_lines", "generated_chars",
                        "tokens_in", "tokens_out", "tokens_total",
                        "by_model": {model: {...}}, "by_project": {project: {...}},
                        "conversations": [ {...} ]}},
      "total": {同上但聚合全部工具，另含 by_model / by_project / conversations 汇总},
      "web_ai": web_ai_sessions(web_visits)（web_visits 为空或 web_ai.enabled=false 时 found=false）
    }
    """
    section = config.get("ai_sessions") if isinstance(config.get("ai_sessions"), dict) else {}
    enabled = bool(section.get("enabled", True))
    token_est = bool(section.get("token_estimation", True))
    costs_enabled = bool(_cost_section(config).get("enabled", True))
    pricing = _pricing_table(config) if costs_enabled else {}
    empty_web = {"found": False, "turns": 0, "conversations": 0, "browsing_visits": 0,
                 "by_tool": {}, "sessions": []}
    if not enabled:
        return {"date": date_str, "enabled": False, "found": False,
                "tools": {}, "total": _empty_total(), "web_ai": empty_web}

    tool_paths = _config_paths(config)
    tools: dict[str, dict] = {}
    parsed_paths: set[str] = set()
    for tool, dirs in tool_paths.items():
        stats = _empty_tool_stats()
        conv_buckets: dict[str, list[dict]] = {}
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
                is_user = role in _USER_ROLES
                is_assistant = role in _ASSISTANT_ROLES
                if is_user:
                    stats["user_messages"] += 1
                elif is_assistant:
                    stats["assistant_messages"] += 1
                    stats["generated_lines"] += _generated_lines(content)
                    stats["generated_chars"] += len(content or "")
                model = _message_model(msg)
                tokens = estimate_tokens(content) if token_est else 0
                if is_assistant:
                    stats["tokens_out"] += tokens
                else:
                    stats["tokens_in"] += tokens
                stats["tokens_total"] += tokens
                # 成本估算（按角色 × 模型单价；未识模型/未开启时为 0）
                c_in = c_out = 0.0
                if costs_enabled:
                    p_in, p_out = _model_price(pricing, model)
                    if is_assistant:
                        c_out = tokens * p_out / 1e6
                        stats["cost_out"] += c_out
                    else:
                        c_in = tokens * p_in / 1e6
                        stats["cost_in"] += c_in
                stats["cost_total"] += c_in + c_out
                # 模型维度（按消息归属）
                _add_dim(stats["by_model"], model, is_user, is_assistant, tokens, c_in, c_out)
                # 会话分组（轮次 / 详情 / 项目以此为准）
                conv_id = _message_conv_id(msg, path)
                conv_buckets.setdefault(conv_id, []).append({
                    "msg": msg, "role": role, "tokens": tokens,
                    "model": model, "project": _message_project(msg),
                    "cost_in": c_in, "cost_out": c_out,
                })
            if hit_file:
                stats["files"] += 1
        # 会话轮次统计 + 项目维度 + 详情（项目按会话归口，避免工具目录名污染）
        for conv_id, items in conv_buckets.items():
            detail = _conversation_summary(conv_id, tool, items, token_est)
            if detail is None:
                continue
            stats["rounds"] += detail["rounds"]
            pe = stats["by_project"].setdefault(
                detail["project"],
                {"turns": 0, "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
                 "cost_in": 0.0, "cost_out": 0.0, "cost_total": 0.0},
            )
            pe["turns"] += detail["turns"]
            pe["tokens_in"] += detail["tokens_in"]
            pe["tokens_out"] += detail["tokens_out"]
            pe["tokens_total"] += detail["tokens_total"]
            pe["cost_in"] += detail["cost_in"]
            pe["cost_out"] += detail["cost_out"]
            pe["cost_total"] += detail["cost_total"]
            stats["conversations"].append(detail)
        stats["conversations"].sort(key=lambda c: c["turns"], reverse=True)
        stats["conversations"] = stats["conversations"][:20]
        if stats["files"] or stats["turns"]:
            tools[tool] = stats

    total = _empty_total()
    for stats in tools.values():
        for key in ("files", "turns", "rounds", "user_messages", "assistant_messages",
                    "generated_lines", "generated_chars", "tokens_in", "tokens_out",
                    "tokens_total", "cost_in", "cost_out", "cost_total"):
            total[key] += stats[key]
        _merge_dim(total["by_model"], stats["by_model"])
        _merge_dim(total["by_project"], stats["by_project"])
        total["conversations"].extend(stats["conversations"])
    total["conversations"].sort(key=lambda c: c["turns"], reverse=True)
    total["conversations"] = total["conversations"][:20]

    # Web AI 会话（浏览器历史深度解析）
    web_ai = empty_web
    if bool(section.get("web_ai", {}).get("enabled", True)) and web_visits:
        web_ai = web_ai_sessions(web_visits)
    return {
        "date": date_str,
        "enabled": True,
        "found": bool(tools) or bool(web_ai["found"]),
        "tools": tools,
        "total": total,
        "web_ai": web_ai,
    }


def _empty_tool_stats() -> dict:
    return {"files": 0, "turns": 0, "rounds": 0, "user_messages": 0,
            "assistant_messages": 0, "generated_lines": 0, "generated_chars": 0,
            "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
            "cost_in": 0.0, "cost_out": 0.0, "cost_total": 0.0,
            "by_model": {}, "by_project": {}, "conversations": []}


def _add_dim(dim: dict, key: str, is_user: bool, is_assistant: bool, tokens: int,
                c_in: float = 0.0, c_out: float = 0.0) -> None:
    """累计 by_model 维度（tokens 与成本 c_in/c_out 已在调用方按角色算好）。"""
    e = dim.setdefault(key, {"turns": 0, "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
                             "cost_in": 0.0, "cost_out": 0.0, "cost_total": 0.0})
    e["turns"] += 1
    if is_user:
        e["tokens_in"] += tokens
    elif is_assistant:
        e["tokens_out"] += tokens
    else:
        e["tokens_in"] += tokens
    e["tokens_total"] += tokens
    e["cost_in"] += c_in
    e["cost_out"] += c_out
    e["cost_total"] += c_in + c_out


def _merge_dim(target: dict, src: dict) -> None:
    """把 src 维度聚合并入 target。"""
    for key, e in src.items():
        t = target.setdefault(key, {"turns": 0, "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
                                    "cost_in": 0.0, "cost_out": 0.0, "cost_total": 0.0})
        t["turns"] += e["turns"]
        t["tokens_in"] += e["tokens_in"]
        t["tokens_out"] += e["tokens_out"]
        t["tokens_total"] += e["tokens_total"]
        t["cost_in"] += e["cost_in"]
        t["cost_out"] += e["cost_out"]
        t["cost_total"] += e["cost_total"]


def _conversation_summary(conv_id: str, tool: str, items: list[dict],
                          token_est: bool) -> dict | None:
    """把一个会话的消息桶汇总成会话详情（轮次/Token/主导模型/项目）。

    items 为 [{msg, role, tokens, model, project}]（其中 project 为显式字段值或 None）：
    - rounds 由原始消息序列按 user→assistant 配对计算；
    - project 取该会话里显式字段（cwd/project/repo...）的众数，缺失则「未识别」；
    - model 取消息序列的众数。
    """
    if not items:
        return None
    model_counter: dict[str, int] = {}
    project_counter: dict[str, int] = {}
    user_n = assistant_n = tokens_out = tokens_total = 0
    cost_in_sum = cost_out_sum = 0.0
    first = last = ""
    for it in items:
        role = it.get("role")
        ts = _message_time(it["msg"]) or ""
        tokens = it.get("tokens") or 0
        c_in = it.get("cost_in") or 0.0
        c_out = it.get("cost_out") or 0.0
        if role in _USER_ROLES:
            user_n += 1
        elif role in _ASSISTANT_ROLES:
            assistant_n += 1
            tokens_out += tokens
        tokens_total += tokens
        cost_in_sum += c_in
        cost_out_sum += c_out
        model_counter[it.get("model") or "未识别"] = model_counter.get(it.get("model") or "未识别", 0) + 1
        proj = it.get("project")
        if proj:
            project_counter[proj] = project_counter.get(proj, 0) + 1
        if not first or (ts and ts < first):
            first = ts
        if not last or (ts and ts > last):
            last = ts
    project = max(project_counter, key=project_counter.get) if project_counter else "未识别"
    model = max(model_counter, key=model_counter.get)
    return {
        "id": conv_id,
        "tool": tool,
        "model": model,
        "project": project,
        "turns": len(items),
        "rounds": _count_rounds([it["msg"] for it in items]),
        "user_messages": user_n,
        "assistant_messages": assistant_n,
        "tokens_in": tokens_total - tokens_out,
        "tokens_out": tokens_out,
        "tokens_total": tokens_total,
        "cost_in": round(cost_in_sum, 8),
        "cost_out": round(cost_out_sum, 8),
        "cost_total": round(cost_in_sum + cost_out_sum, 8),
        "first": first,
        "last": last,
    }


def _empty_total() -> dict:
    return {"files": 0, "turns": 0, "rounds": 0, "user_messages": 0,
            "assistant_messages": 0, "generated_lines": 0, "generated_chars": 0,
            "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
            "cost_in": 0.0, "cost_out": 0.0, "cost_total": 0.0,
            "by_model": {}, "by_project": {}, "conversations": []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai_sessions.py", description="AI 会话深度统计（可选增强）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__import__('version').VERSION}")
    parser.add_argument("--day", metavar="YYYY-MM-DD", help="指定日期（默认今天）")
    parser.add_argument("--today", action="store_true", help="今天")
    parser.add_argument("--web", action="store_true", help="同时解析浏览器访问明细中的 Web AI 会话（对话轮次追踪）")
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

    web_visits: list[dict] | None = None
    if args.web:
        try:
            import browser_history  # noqa: PLC0415
            data_root = args.data_root or cfg.get("data_root") or "."
            web_visits = browser_history.collect(date_str, data_root, cfg).get("visits") or []
        except Exception:  # noqa: BLE001 —— Web 解析失败不影响本地统计
            web_visits = []
    result = collect(date_str, cfg, web_visits=web_visits or None)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"# AI 会话深度统计 {date_str}")
    if not result["enabled"]:
        print("未启用：config.json 的 ai_sessions.enabled=false")
        return 0
    if not result["found"]:
        print("（未发现该日期的本地 AI 会话记录；可配置 ai_sessions.paths 指向会话目录。"
              "浏览器 Web AI 会话可用 --web 附带统计）")
        return 0
    for tool, s in result["tools"].items():
        print(f"- {tool}: 文件 {s['files']} 个，消息 {s['turns']}（轮次 {s['rounds']}），"
              f"用户 {s['user_messages']} / 助手 {s['assistant_messages']}，"
              f"生成 {s['generated_lines']} 行 / {s['generated_chars']} 字符，"
              f"Token 进 {s['tokens_in']} / 出 {s['tokens_out']}，"
              f"费用 {_fmt_cost(s.get('cost_total', 0))}")
    print(f"合计: {result['total']['turns']} 条消息（轮次 {result['total']['rounds']}），"
          f"生成 {result['total']['generated_lines']} 行，Token 进 {result['total']['tokens_in']} / "
          f"出 {result['total']['tokens_out']}，"
          f"费用 {_fmt_cost(result['total']['cost_total'])}")
    if result["total"]["by_model"]:
        top_model = sorted(result["total"]["by_model"].items(),
                           key=lambda kv: kv[1]["turns"], reverse=True)[:5]
        print("模型分布: " + "；".join(f"{m} {v['turns']} 条 / {_fmt_cost(v['cost_total'])}" for m, v in top_model))
    if result["total"]["by_project"]:
        top_proj = sorted(result["total"]["by_project"].items(),
                          key=lambda kv: kv[1]["turns"], reverse=True)[:5]
        print("项目分布: " + "；".join(f"{p} {v['turns']} 条 / {_fmt_cost(v['cost_total'])}" for p, v in top_proj))
    web = result.get("web_ai") or {}
    if web.get("found"):
        print(f"Web AI 会话: {web['conversations']} 个会话，{web['turns']} 次页面访问"
              + ("（按工具: " +
                 "；".join(f"{t} {a['conversations']} 会话/{a['turns']} 次"
                           for t, a in web["by_tool"].items()) + "）" if web["by_tool"] else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
