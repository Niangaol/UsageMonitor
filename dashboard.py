# -*- coding: utf-8 -*-
"""dashboard.py — 本地网页仪表盘（v4：周报/月报/导出/主题/口令/备份）。

- 仅监听 127.0.0.1（不做远程访问），默认端口 8765；
- 纯标准库（http.server），页面与图表内联，零外部依赖、离线可用；
- 数据全部来自本机日期文件夹，不产生任何新数据。

视图：
1. 今日概览：大数字卡片（总活跃/AI编程/社交/浏览器停留/会话数）+ 24 小时活跃分布
   + 14/30 天趋势 + 类别/应用分布 + AI 工具/联系人（鼠标悬停看详情）
2. 日报：选日期渲染当日 report.md（前端 mini-markdown，含表格/标题/列表/代码块）
3. 明细：会话明细与浏览器 URL 明细（均支持关键词过滤）
4. 周报：最近 7 个有数据日聚合回顾
5. 月报：按自然月聚合回顾
6. 洞察：离线规则洞察卡片 + 可选 AI 洞察面板
7. 设置：数据备份下载 / 恢复上传

安全与增强（v4）：
- 可选访问口令（config.json 的 dashboard_token，空/缺失=关闭；开启后所有 /api 需要
  X-Dashboard-Token 头，hmac.compare_digest 常量时间比较）
- 浅色/深色/自动 主题切换（localStorage 持久化 + 跟随系统 prefers-color-scheme）
- 一键导出 CSV/JSON（日报/周报/月报）、备份 zip 下载与回滚恢复

用法：
    python dashboard.py                # 启动，浏览器访问 http://127.0.0.1:8765
    python dashboard.py --port 9000    # 指定端口
    python dashboard.py --open         # 启动后自动打开浏览器
"""

from __future__ import annotations

import argparse
import datetime
import hmac
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import report  # noqa: E402
import version  # noqa: E402
import paths  # noqa: E402

DEFAULT_PORT = 8765
DEFAULT_DATA_ROOT = paths.default_data_root()

# API 日期参数白名单：防路径穿越（date=../../xxx 会拼进数据目录路径）
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# API 月份参数白名单（YYYY-MM）
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

_URL_MAX_ROWS = 200  # 浏览器明细最多回传条数

# 备份 zip 允许包含的顶层条目：日期目录 或 已知数据文件（其余一律拒绝，防解压注入）
_ALLOWED_ROOT_FILES = {
    "config.json", "app_groups.json", "aliases.json",
    "report_week.md", "report_month.json", "report_month.md",
}
# 备份 zip 打包时排除的大日志/临时/备份文件
_EXCLUDED_FILE_SUFFIXES = (".log", ".bak", ".bak_verify", ".tmp", ".pyc")


def _load_dashboard_token(data_root: str | None = None, config_path: str | None = None) -> str:
    """读取 dashboard_token（空/缺失 = 关闭口令）。

    优先数据根目录的 config.json（与仪表盘 data_root 语义一致，--data-root 场景正确）；
    其次回退 classifier.load_config()（默认/--config 路径，可移植性/深合并一致）。
    """
    if data_root:
        try:
            p = os.path.join(data_root, "config.json")
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as fh:
                    token = json.load(fh).get("dashboard_token")
                if token:
                    return str(token).strip()
        except Exception:  # noqa: BLE001 —— 数据根配置损坏时回退默认读取
            pass
    try:
        import classifier  # noqa: PLC0415
        cfg = classifier.load_config(config_path)
        token = cfg.get("dashboard_token")
        return str(token).strip() if token else ""
    except Exception:  # noqa: BLE001 —— 配置损坏时口令关闭（不阻断仪表盘）
        return ""


_token_cache: dict = {"key": None, "ts": 0.0, "token": ""}


def _required_token(config_path: str | None = None, data_root: str | None = None) -> str:
    """带短 TTL 的 token 缓存，避免每个请求都重读 config（改配置后 ~5s 生效）。"""
    key = data_root or config_path
    now = time.monotonic()
    if _token_cache["key"] != key or now - _token_cache["ts"] > 5.0:
        _token_cache["token"] = _load_dashboard_token(data_root, config_path)
        _token_cache["key"] = key
        _token_cache["ts"] = now
    return _token_cache["token"]


def _load_config_for_root(root: str, config_path: str | None = None) -> dict:
    """读取与数据根目录一致的完整配置（已深合并默认值）。

    优先级：显式 --config 路径 > <root>/config.json > 默认 config.json。
    """
    import classifier  # noqa: PLC0415
    if config_path:
        return classifier.load_config(config_path)
    local = os.path.join(root, "config.json")
    if os.path.isfile(local):
        return classifier.load_config(local)
    return classifier.load_config()


def _config_file_for_root(root: str, config_path: str | None = None) -> str:
    """设置页保存 AI 配置时实际写入的 config.json 路径。"""
    if config_path:
        return config_path
    return os.path.join(root, "config.json")


def _ai_settings_view(config: dict) -> dict:
    """把完整配置里的 AI 段转成前端可安全展示的结构（API Key 只给“是否已设置”）。"""
    ins = config.get("insights") if isinstance(config.get("insights"), dict) else {}
    ai = ins.get("ai") if isinstance(ins.get("ai"), dict) else {}
    return {
        "enabled": bool(ai.get("enabled")),
        "provider": str(ai.get("provider") or ""),
        "base_url": str(ai.get("base_url") or ""),
        "model": str(ai.get("model") or ""),
        "timeout_s": int(ai.get("timeout_s") or 60),
        "send_raw_titles": bool(ai.get("send_raw_titles")),
        "language": str(ai.get("language") or "zh"),
        "api_key_set": bool(ai.get("api_key")),
    }


def _save_ai_settings(root: str, config_path: str | None, payload: dict) -> dict:
    """把 AI 设置保存到 config.json（原子写），返回保存后的前端视图。

    api_key 为空字符串时保留原值（前端只显示“已设置/未设置”，不回显密钥）。
    """
    import classifier  # noqa: PLC0415
    path = _config_file_for_root(root, config_path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            cfg = {}
    except FileNotFoundError:
        cfg = {}
    except json.JSONDecodeError:
        cfg = {}
    cfg.setdefault("insights", {})
    ins = cfg["insights"]
    if not isinstance(ins, dict):
        ins = {}
        cfg["insights"] = ins
    ins.setdefault("ai", {})
    ai = ins["ai"]
    if not isinstance(ai, dict):
        ai = {}
        ins["ai"] = ai
    old_api_key = str(ai.get("api_key") or "")

    ai["enabled"] = bool(payload.get("enabled"))
    ai["provider"] = str(payload.get("provider") or "").strip()
    ai["base_url"] = str(payload.get("base_url") or "").strip()
    ai["model"] = str(payload.get("model") or "").strip()
    try:
        ai["timeout_s"] = max(1, min(600, int(payload.get("timeout_s") or 60)))
    except (TypeError, ValueError):
        ai["timeout_s"] = 60
    ai["send_raw_titles"] = bool(payload.get("send_raw_titles"))
    ai["language"] = str(payload.get("language") or "zh").strip() or "zh"
    # 选择内置预设且用户未手填时，把预设的 base_url/model 落盘，方便界面回显
    try:
        import insights  # noqa: PLC0415
        preset_map = {p["id"]: p for p in insights.list_provider_presets()}
        preset = preset_map.get(ai["provider"].lower(), {})
        if not ai["base_url"]:
            ai["base_url"] = preset.get("base_url", "")
        if not ai["model"]:
            ai["model"] = preset.get("model", "")
    except Exception:  # noqa: BLE001
        pass
    new_key = str(payload.get("api_key") or "").strip()
    if new_key:
        ai["api_key"] = new_key
    elif old_api_key:
        ai["api_key"] = old_api_key
    else:
        ai["api_key"] = ""

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    classifier.invalidate_config_cache(path)
    return _ai_settings_view(cfg)


def _month_days_for(data_root: str, month_str: str) -> list[str]:
    """返回某月内实际存在 usage.jsonl 的日期（升序），用于导出/统计。"""
    out = []
    for name in os.listdir(data_root) if os.path.isdir(data_root) else []:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name) and name.startswith(month_str + "-"):
            if os.path.isfile(os.path.join(data_root, name, "usage.jsonl")):
                out.append(name)
    return sorted(out)


def _agg_to_csv(agg: dict, title_line: str | None = None) -> str:
    """把任意聚合结果渲染成汇总 CSV（类型,名称,时长秒），周/月报通用。

    与 report.generate_report_csv 的口径一致：应用/类别/联系人/AI工具/浏览器分类。
    """
    lines: list[str] = []
    if title_line:
        lines.append("# " + title_line.replace("# ", "").replace(",", ""))
        lines.append("")
    lines.append("类型,名称,时长秒")
    for name, ms in sorted(agg.get("by_app", {}).items(), key=lambda kv: -kv[1]):
        lines.append(f"应用:{name},{int(ms // 1000)}")
    for cat, ms in sorted(agg.get("by_category", {}).items(), key=lambda kv: -kv[1]):
        lines.append(f"类别:{cat},{int(ms // 1000)}")
    for app, contacts in sorted(agg.get("by_contact", {}).items()):
        for contact, ms in sorted(contacts.items(), key=lambda kv: -kv[1]):
            lines.append(f"联系人:{app}/{contact},{int(ms // 1000)}")
    for tool, ms in sorted(agg.get("by_ai", {}).items(), key=lambda kv: -kv[1]):
        lines.append(f"AI工具:{tool},{int(ms // 1000)}")
    for label, ms in sorted(agg.get("by_browser", {}).items(), key=lambda kv: -kv[1]):
        lines.append(f"浏览器:{label},{int(ms // 1000)}")
    return "\n".join(lines) + "\n"


def _backup_zip(data_root: str) -> bytes:
    """把 data_root 内容打包为 zip 字节（日期目录 + 配置文件），排除大日志/临时/备份。

    用于 /api/backup 附件下载。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        entries = _backup_entries(data_root)
        for rel in entries:
            src = os.path.join(data_root, rel)
            if os.path.isfile(src):
                zf.write(src, rel)
            elif os.path.isdir(src):
                for dirpath, _dirnames, filenames in os.walk(src):
                    for fn in filenames:
                        full = os.path.join(dirpath, fn)
                        arch = os.path.join(rel, os.path.relpath(full, src))
                        zf.write(full, arch.replace("\\", "/"))
    return buf.getvalue()


def _backup_entries(data_root: str) -> list[str]:
    """枚举要打包的条目：日期目录 + 允许的根配置文件；排除日志/临时/备份文件。"""
    entries: list[str] = []
    if os.path.isdir(data_root):
        for name in sorted(os.listdir(data_root)):
            full = os.path.join(data_root, name)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name) and os.path.isdir(full):
                entries.append(name)
            elif name in _ALLOWED_ROOT_FILES and os.path.isfile(full):
                entries.append(name)
    return entries


def _safe_extract_zip(data_root: str, zip_bytes: bytes) -> str:
    """把备份 zip 解压到临时目录并校验（路径穿越/恶意条目拦截），返回临时目录路径。

    仅保留日期目录与白名单根文件；zip 外的其他条目一律丢弃（不覆盖攻击者文件）。
    """
    tmp = tempfile.mkdtemp(prefix="usemon_restore_")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            # 防路径穿越：拒绝绝对路径与 ../ 上级引用
            if name.startswith("/") or ".." in name.split("/"):
                continue
            top = name.split("/", 1)[0]
            if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", top) or top in _ALLOWED_ROOT_FILES):
                continue
            if info.is_dir():
                continue
            # 排除日志/临时/备份文件
            if any(name.lower().endswith(s) for s in _EXCLUDED_FILE_SUFFIXES):
                continue
            dest = os.path.normpath(os.path.join(tmp, name))
            if not dest.startswith(os.path.normpath(tmp) + os.sep) and dest != tmp:
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
    # 把白名单根文件的顶层名规范化后同 tmp 一起返回（由调用方合并到 data_root）
    return tmp


def _merge_restore(data_root: str, tmp: str) -> dict:
    """把临时解压目录合并覆盖到 data_root（逐日期目录 + 配置文件）。"""
    restored_days: list[str] = []
    restored_files: list[str] = []
    if not os.path.isdir(data_root):
        os.makedirs(data_root, exist_ok=True)
    for name in sorted(os.listdir(tmp)):
        src = os.path.join(tmp, name)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name) and os.path.isdir(src):
            dst = os.path.join(data_root, name)
            os.makedirs(dst, exist_ok=True)
            for fn in os.listdir(src):
                s = os.path.join(src, fn)
                if os.path.isfile(s) and not any(fn.lower().endswith(ext) for ext in _EXCLUDED_FILE_SUFFIXES):
                    shutil.copy2(s, os.path.join(dst, fn))
                    restored_files.append(f"{name}/{fn}")
            restored_days.append(name)
        elif name in _ALLOWED_ROOT_FILES and os.path.isfile(src):
            shutil.copy2(src, os.path.join(data_root, name))
            restored_files.append(name)
    return {"days": restored_days, "files": restored_files}


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UsageMonitor · 概览</title>
<style>
  :root{
    --bg:#101318; --surface:#14171e; --surface-2:#1a1e27; --surface-3:#20252f;
    --border:rgba(255,255,255,.07); --border-strong:rgba(255,255,255,.13);
    --text:#e8e6e1; --dim:#9aa0ab; --faint:#6b7280;
    --accent:#e0a53c; --accent-soft:rgba(224,165,60,.13);
    --danger:#e0533d; --warn:#d9a441; --ok:#7fb069;
    --code-bg:#0d1016; --md-h2:#e8b46a; --md-a:#8fb8ff; --url:#6b7280; --err-txt:#d98a7d;
    --grid-line:rgba(255,255,255,.06); --bar-empty:#232936; --chart-axis:#6b7280; --chart-bar:#e0a53c;
    --scroll-thumb:#2a303c; --scroll-thumb-hover:#39414f;
    --hm-1:#1c212b; --hm-2:#2c3342; --hm-3:#6b5323; --hm-4:#a06f24; --hm-5:#e0a53c;
    --tag-ai-text:#e8b46a; --tag-ai-border:rgba(232,180,106,.35); --tag-ai-bg:rgba(232,180,106,.08);
    --mono:ui-monospace,"Cascadia Code",Consolas,"Courier New",monospace;
    --radius:8px; --sidebar-w:216px;
    --ease:cubic-bezier(.22,.61,.36,1);
  }
  html[data-theme="light"]{
    --bg:#f4f5f7; --surface:#ffffff; --surface-2:#eef0f3; --surface-3:#e4e7ec;
    --border:rgba(20,25,36,.10); --border-strong:rgba(20,25,36,.18);
    --text:#1e232b; --dim:#5a6472; --faint:#89919d;
    --accent:#b47a1c; --accent-soft:rgba(180,122,28,.12);
    --danger:#c4422c; --warn:#96670f; --ok:#3e7736;
    --code-bg:#ffffff; --md-h2:#a06a12; --md-a:#2a6fd6; --url:#5a6472; --err-txt:#c4422c;
    --grid-line:rgba(20,25,36,.08); --bar-empty:#d9dde3; --chart-axis:#89919d; --chart-bar:#b47a1c;
    --scroll-thumb:#c3c9d2; --scroll-thumb-hover:#aab2bd;
    --hm-1:#dfe3ea; --hm-2:#c9d1e0; --hm-3:#d3ad5c; --hm-4:#c08a1f; --hm-5:#b47a1c;
    --tag-ai-text:#8a5a10; --tag-ai-border:rgba(180,122,28,.35); --tag-ai-bg:rgba(180,122,28,.08);
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%}
  body{background:var(--bg);color:var(--text);
    font-family:ui-sans-serif,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
    font-size:13px;line-height:1.55;-webkit-font-smoothing:antialiased}
  ::selection{background:var(--accent-soft)}
  ::-webkit-scrollbar{width:10px;height:10px}
  ::-webkit-scrollbar-thumb{background:var(--scroll-thumb);border-radius:5px;border:2px solid var(--bg)}
  ::-webkit-scrollbar-thumb:hover{background:var(--scroll-thumb-hover)}
  ::-webkit-scrollbar-track{background:transparent}
  button,input,select{font:inherit;color:inherit}
  .app{display:flex;min-height:100vh}

  /* ---------- 登录/口令遮罩 ---------- */
  .auth-mask{position:fixed;inset:0;background:var(--bg);z-index:200;display:none;
    align-items:center;justify-content:center;flex-direction:column;gap:14px;padding:20px}
  .auth-mask.show{display:flex}
  .auth-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
    padding:26px 28px;width:min(360px,92vw);text-align:center}
  .auth-card h3{font-size:16px;margin-bottom:6px}
  .auth-card p{font-size:12px;color:var(--dim);margin-bottom:16px}
  .auth-card .controls{justify-content:center}
  .auth-err{color:var(--danger);font-size:12px;min-height:16px;margin-top:8px}

  /* ---------- 侧边栏 ---------- */
  .sidebar{width:var(--sidebar-w);flex-shrink:0;background:var(--surface);
    border-right:1px solid var(--border);display:flex;flex-direction:column;
    position:fixed;top:0;bottom:0;left:0;z-index:50}
  .brand{display:flex;align-items:center;gap:10px;padding:18px 16px 14px;
    border-bottom:1px solid var(--border)}
  .brand svg{flex-shrink:0}
  .brand b{display:block;font-size:14px;letter-spacing:.2px}
  .brand span{display:block;font-size:11px;color:var(--faint);margin-top:1px}
  .nav{padding:10px 8px;flex:1;overflow-y:auto}
  .nav-item{display:flex;align-items:center;gap:10px;padding:8px 10px;margin:2px 0;
    border-radius:6px;color:var(--dim);text-decoration:none;position:relative;
    transition:background .18s var(--ease),color .18s var(--ease)}
  .nav-item svg{flex-shrink:0;opacity:.85}
  .nav-item:hover{background:var(--surface-2);color:var(--text)}
  .nav-item.active{color:var(--text);background:var(--accent-soft)}
  .nav-item.active::before{content:"";position:absolute;left:-8px;top:20%;bottom:20%;width:3px;
    border-radius:2px;background:var(--accent)}
  .side-foot{padding:14px 16px;border-top:1px solid var(--border);font-size:11px;color:var(--faint)}
  .side-foot .root{font-family:var(--mono);font-size:10px;word-break:break-all;color:var(--dim);
    margin-bottom:6px}
  .backdrop{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:40;opacity:0;pointer-events:none;
    transition:opacity .2s var(--ease)}
  .hamburger{display:none;position:fixed;top:12px;left:12px;z-index:60;width:36px;height:36px;
    border:1px solid var(--border);border-radius:6px;background:var(--surface);cursor:pointer;
    align-items:center;justify-content:center}

  /* ---------- 主内容 ---------- */
  .content{flex:1;margin-left:var(--sidebar-w);padding:26px 30px 60px;max-width:1240px}
  .page-head{display:flex;align-items:center;justify-content:space-between;gap:14px;
    flex-wrap:wrap;margin-bottom:22px}
  .page-head h1{font-size:19px;font-weight:600;letter-spacing:.1px}
  .page-head .sub{font-size:12px;color:var(--faint);margin-top:3px}
  .controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  select,input[type=text],input[type=month],input[type=password],input[type=file],button.btn{
    background:var(--surface);border:1px solid var(--border);
    border-radius:6px;padding:6px 10px;font-size:12.5px;color:var(--text);outline:none;
    transition:border-color .18s var(--ease),background .18s var(--ease)}
  input[type=file]{padding:4px 8px;width:220px}
  select:focus,input:focus,button.btn:focus-visible{border-color:var(--accent)}
  button.btn{cursor:pointer}
  button.btn:hover{background:var(--surface-2);border-color:var(--border-strong)}
  button.btn.primary{background:var(--accent);border-color:var(--accent);color:#141008;font-weight:600}
  html[data-theme="light"] button.btn.primary{color:#fff}
  button.btn.primary:hover{background:#eab356}

  /* ---------- 视图切换动画 ---------- */
  .view{display:none}
  .view.active{display:block;animation:viewIn .24s var(--ease) both}
  @keyframes viewIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}

  /* ---------- 卡片 ---------- */
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px;margin-bottom:18px}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
    padding:14px 16px;transition:border-color .18s var(--ease),transform .18s var(--ease)}
  .card:hover{border-color:var(--border-strong);transform:translateY(-1px)}
  .card .label{font-size:11.5px;color:var(--dim);display:flex;align-items:center;gap:6px}
  .card .value{font-size:23px;font-weight:600;margin-top:7px;font-variant-numeric:tabular-nums;
    letter-spacing:-.2px}
  .card .value small{font-size:12px;color:var(--faint);font-weight:400;margin-left:4px}
  .card .trend{font-size:11px;margin-top:4px;color:var(--faint)}

  .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  @media(max-width:960px){.grid{grid-template-columns:1fr}}
  .panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
    padding:16px 18px;margin-bottom:12px}
  .panel h2{font-size:12px;font-weight:600;letter-spacing:.6px;text-transform:uppercase;
    color:var(--faint);margin-bottom:14px;display:flex;align-items:center;justify-content:space-between}
  .panel h2 .hint{font-size:11px;text-transform:none;letter-spacing:0;font-weight:400}

  /* ---------- 统计行（类别/应用条） ---------- */
  .stat-row{margin-bottom:9px}
  .stat-row .top{display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px}
  .stat-row .top .name{color:var(--text)}
  .stat-row .top .val{color:var(--dim);font-variant-numeric:tabular-nums}
  .bar{height:6px;border-radius:3px;background:var(--surface-3);overflow:hidden}
  .bar > i{display:block;height:100%;border-radius:3px;background:var(--accent);
    width:0;transition:width .7s var(--ease)}

  /* ---------- 表格 ---------- */
  .tbl-wrap{overflow:auto;border:1px solid var(--border);border-radius:var(--radius)}
  table.tbl{width:100%;border-collapse:collapse;font-size:12.5px;min-width:640px}
  .tbl th{text-align:left;padding:8px 10px;color:var(--faint);font-weight:500;font-size:11px;
    text-transform:uppercase;letter-spacing:.5px;background:var(--surface-2);
    border-bottom:1px solid var(--border);position:sticky;top:0;white-space:nowrap}
  .tbl td{padding:7px 10px;border-bottom:1px solid var(--border);vertical-align:top}
  .tbl tbody tr{transition:background .12s var(--ease)}
  .tbl tbody tr:hover{background:var(--surface-2)}
  .tbl td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
  .tbl td.mono{font-family:var(--mono);font-size:11.5px}
  .tag{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;margin-right:5px;
    background:var(--surface-3);border:1px solid var(--border-strong);color:var(--dim);white-space:nowrap}
  .tag.ai{color:var(--tag-ai-text);border-color:var(--tag-ai-border);background:var(--tag-ai-bg)}
  .url-cell{font-family:var(--mono);font-size:11px;color:var(--url);max-width:280px;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;vertical-align:bottom}

  /* ---------- 日志 ---------- */
  .log-box{background:var(--code-bg);border:1px solid var(--border);border-radius:var(--radius);
    font-family:var(--mono);font-size:11.5px;line-height:1.7;padding:12px 14px;
    height:calc(100vh - 300px);min-height:280px;overflow:auto;white-space:pre-wrap;word-break:break-all}
  #view-log .grid{grid-template-columns:1fr}
  .log-line{display:flex;gap:10px}
  .log-line .ts{color:var(--faint);flex-shrink:0}
  .log-line .lv{flex-shrink:0;width:34px}
  .log-line .lv.INFO{color:var(--faint)}
  .log-line .lv.WARN{color:var(--warn)}
  .log-line .lv.ERRO{color:var(--danger)}
  .log-line .msg{color:var(--dim)}
  .log-line.err .msg{color:var(--err-txt)}

  /* ---------- 日报渲染 ---------- */
  .md h1{font-size:19px;border-bottom:1px solid var(--border);padding-bottom:10px;margin-bottom:16px}
  .md h2{font-size:14px;color:var(--md-h2);margin:20px 0 10px;letter-spacing:.3px}
  .md table{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0 16px}
  .md th,.md td{border:1px solid var(--border);padding:6px 10px;text-align:left}
  .md th{background:var(--surface-2);color:var(--faint);font-weight:500}
  .md a{color:var(--md-a);text-decoration:none}
  .md blockquote{color:var(--dim);border-left:3px solid var(--border-strong);padding-left:10px;margin:8px 0}
  .mdbar{height:6px;border-radius:3px;background:var(--surface-3);overflow:hidden;min-width:60px}
  .mdbar i{display:block;height:100%;background:var(--accent);border-radius:3px}
  .md p{font-size:12.5px;margin:6px 0}
  .md li{font-size:12.5px;margin:3px 0 3px 18px}

  /* ---------- 骨架屏 ---------- */
  .sk{position:relative;overflow:hidden;background:var(--surface-3);border-radius:6px}
  .sk::after{content:"";position:absolute;inset:0;transform:translateX(-100%);
    background:linear-gradient(90deg,transparent,rgba(255,255,255,.05),transparent);
    animation:shimmer 1.3s infinite}
  html[data-theme="light"] .sk::after{background:linear-gradient(90deg,transparent,rgba(20,25,36,.06),transparent)}
  @keyframes shimmer{to{transform:translateX(100%)}}
  .empty{padding:34px 0;text-align:center;color:var(--faint);font-size:12.5px}
  canvas{width:100%;display:block}

  /* ---------- 热力图 ---------- */
  .hm{display:flex;gap:2px;min-height:264px}
  .hm .hl-col{display:flex;flex-direction:column;width:22px;gap:2px;flex-shrink:0}
  .hm .hl-col span{font-family:var(--mono);font-size:8.5px;color:var(--faint);line-height:11px;height:11px}
  .hm .grid-col{display:flex;flex-direction:column;gap:2px;flex:1}
  .hm .cell{height:11px;border-radius:2px;transition:background .2s var(--ease)}
  .hm-legend{display:flex;align-items:center;gap:6px;font-size:10.5px;color:var(--faint);
    justify-content:flex-end;margin-top:8px}
  .hm-legend .sw{width:10px;height:10px;border-radius:2px}

  /* ---------- 设置区（备份/恢复/口令） ---------- */
  .set-group{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
    padding:16px 18px;margin-bottom:12px}
  .set-group h3{font-size:13px;margin-bottom:8px;display:flex;align-items:center;gap:8px}
  .set-group .desc{font-size:11.5px;color:var(--faint);margin-bottom:12px;line-height:1.6}
  .set-restore{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .set-note{font-size:11px;color:var(--faint);margin-top:8px}

  /* ---------- 响应式 ---------- */
  @media(max-width:920px){
    .sidebar{transform:translateX(-100%);transition:transform .26s var(--ease)}
    .sidebar.open{transform:none;box-shadow:0 0 40px rgba(0,0,0,.5)}
    .content{margin-left:0;padding:18px 16px 50px}
    .hamburger{display:flex}
    .backdrop.show{opacity:1;pointer-events:auto}
    .page-head h1{display:none}
  }
  @media(prefers-reduced-motion:reduce){
    *,*::before,*::after{animation:none!important;transition:none!important}
  }

</style>
</head>
<body>
<div class="auth-mask" id="authMask" style="display:none"></div>
<div class="app">
  <aside class="sidebar" id="sidebar">
    <div class="brand">
      <svg width="30" height="30" viewBox="0 0 30 30" fill="none" aria-hidden="true">
        <circle cx="15" cy="15" r="13" stroke="#e0a53c" stroke-width="2.4"/>
        <path d="M15 9v6.2l4.2 2.4" stroke="#e0a53c" stroke-width="2" stroke-linecap="round"/>
      </svg>
      <div><b>UsageMonitor</b><span>电脑使用情况监控</span></div>
    </div>
    <nav class="nav" id="nav">
      <a class="nav-item active" data-view="overview" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="1.5" y="1.5" width="5.6" height="5.6" rx="1"/><rect x="8.9" y="1.5" width="5.6" height="5.6" rx="1"/><rect x="1.5" y="8.9" width="5.6" height="5.6" rx="1"/><rect x="8.9" y="8.9" width="5.6" height="5.6" rx="1"/></svg>
        概览
      </a>
      <a class="nav-item" data-view="trends" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M1.5 13.5h13M3 11l3-4 2.5 2.5L13 4.5"/></svg>
        趋势
      </a>
      <a class="nav-item" data-view="report" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M3.5 1.5h6l3 3v10h-9z"/><path d="M9.5 1.5v3h3M5.5 8.5h5M5.5 11h5"/></svg>
        日报
      </a>
      <a class="nav-item" data-view="week" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2.5 3.5h11v9h-11z"/><path d="M2.5 7h11M5 2v2.5M11 2v2.5"/></svg>
        周报
      </a>
      <a class="nav-item" data-view="month" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2.5 2.5h11v11h-11z"/><path d="M2.5 5.5h11"/><path d="M5.5 1.5v2.5M10.5 1.5v2.5"/></svg>
        月报
      </a>
      <a class="nav-item" data-view="sessions" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2 4h12M2 8h12M2 12h7"/></svg>
        会话
      </a>
      <a class="nav-item" data-view="log" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/><path d="M5 6l2.2 2L5 10M9 10.5h2.5"/></svg>
        日志
      </a>
      <a class="nav-item" data-view="groups" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2 4.5h12v7H2z"/><path d="M2 7h12"/><path d="M6 4.5V9M10 4.5V9"/></svg>
        分组
      </a>
      <a class="nav-item" data-view="insights" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M8 1.6c3.2 0 5.4 2.1 5.4 4.9 0 2.1-1.2 3.3-2 4.4-.4.6-.8 1-.8 1.6H5.4c0-.6-.4-1-.8-1.6-.8-1.1-2-2.3-2-4.4 0-2.8 2.2-4.9 5.4-4.9z"/><path d="M6.9 14.5h2.2"/></svg>
        洞察
      </a>
      <a class="nav-item" data-view="settings" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3M3.4 3.4l2.1 2.1M10.5 10.5l2.1 2.1M12.6 3.4l-2.1 2.1M5.5 10.5l-2.1 2.1"/><circle cx="8" cy="8" r="2.2"/></svg>
        设置
      </a>
    </nav>
    <div class="side-foot">
      <div class="root" id="rootPath" title="数据目录"></div>
      <div>v1.0.0 · 仅监听 127.0.0.1 · 纯本地</div>
    </div>
  </aside>
  <div class="backdrop" id="backdrop"></div>
  <button class="hamburger" id="hamburger" aria-label="菜单">
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 4h12M2 8h12M2 12h12"/></svg>
  </button>

  <main class="content">
    <div class="page-head">
      <div><h1 id="pageTitle">概览</h1><div class="sub" id="pageSub"></div></div>
      <div class="controls" id="headControls"></div>
    </div>

    <!-- 概览 -->
    <section class="view active" id="view-overview">
      <div class="cards" id="ovCards"></div>
      <div class="grid">
        <div class="panel"><h2>最近 14 天活跃趋势</h2><canvas id="ovTrend" height="190"></canvas></div>
        <div class="panel"><h2>今日类别分布</h2><div id="ovCats"></div></div>
      </div>
      <div class="grid">
        <div class="panel"><h2>今日应用 Top 10</h2><div id="ovApps"></div></div>
        <div class="panel"><h2>AI 工具 / 联系人</h2><div id="ovMisc"></div></div>
      </div>
    </section>

    <!-- 趋势 -->
    <section class="view" id="view-trends">
      <div class="panel"><h2>活跃热力图（24 小时 × 最近 84 天）<span class="hint">行=小时 · 列=日期</span></h2>
        <div id="trHeatmap"></div>
        <div class="hm-legend">少 <span class="sw" style="background:var(--hm-1)"></span><span class="sw" style="background:var(--hm-2)"></span><span class="sw" style="background:var(--hm-4)"></span><span class="sw" style="background:var(--hm-5)"></span> 多</div>
      </div>
      <div class="panel"><h2>日活跃柱状图</h2>
        <div class="controls" style="margin-bottom:12px">
          <button class="btn" data-range="14">14 天</button>
          <button class="btn primary" data-range="30">30 天</button>
        </div>
        <canvas id="trBars" height="210"></canvas>
      </div>
    </section>

    <!-- 日报 -->
    <section class="view" id="view-report">
      <div class="panel"><h2>日报 <span class="hint">选日期渲染当日 report.md</span></h2>
        <div class="controls" style="margin-bottom:12px">
          <button class="btn" data-export="csv" data-scope="day">导出 CSV</button>
          <button class="btn" data-export="json" data-scope="day">导出 JSON</button>
        </div>
        <div class="md" id="rpMd"></div>
      </div>
    </section>

    <!-- 周报 -->
    <section class="view" id="view-week">
      <div class="panel"><h2>周报 <span class="hint">最近 7 个有数据日 · 自动</span></h2>
        <div class="controls" style="margin-bottom:12px">
          <button class="btn" data-export="csv" data-scope="week">导出 CSV</button>
          <button class="btn" data-export="json" data-scope="week">导出 JSON</button>
        </div>
        <div class="md" id="wkMd"></div>
      </div>
    </section>

    <!-- 月报 -->
    <section class="view" id="view-month">
      <div class="panel"><h2>月报 <span class="hint">按自然月汇总</span></h2>
        <div class="controls" style="margin-bottom:12px">
          <input type="month" id="moSel" value="">
          <button class="btn primary" id="moGo">查看</button>
          <button class="btn" data-export="csv" data-scope="month">导出 CSV</button>
          <button class="btn" data-export="json" data-scope="month">导出 JSON</button>
        </div>
        <div class="md" id="moMd"></div>
      </div>
    </section>

    <!-- 会话 -->
    <section class="view" id="view-sessions">
      <div class="panel">
        <div class="controls" style="margin-bottom:12px">
          <select id="ssCat"></select>
          <select id="ssApp"></select>
          <input type="text" id="ssSearch" placeholder="搜索标题 / 应用…" style="width:200px">
          <span class="hint" id="ssCount" style="color:var(--faint);font-size:11.5px"></span>
        </div>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>开始</th><th>时长</th><th>应用</th><th>标题</th><th>类别</th><th>备注</th></tr></thead>
          <tbody id="ssBody"></tbody>
        </table></div>
      </div>
    </section>

    <!-- 日志 -->
    <section class="view" id="view-log">
      <div class="grid">
        <div class="panel"><h2>运行日志（app.log）<span class="hint">自动刷新 15s</span></h2>
          <div class="log-box" id="lgEntries"></div>
        </div>
        <div class="panel"><h2>错误日志（errors.log · 最近 3 天）</h2>
          <div class="log-box" id="lgErrors"></div>
        </div>
      </div>
    </section>

    <!-- 分组管理 -->
    <section class="view" id="view-groups">
      <div class="panel">
        <div class="controls" style="margin-bottom:14px">
          <input type="text" id="grpSearch" placeholder="搜索应用…" style="width:200px">
          <input type="text" id="grpNewName" placeholder="新分组名称" style="width:150px" maxlength="20">
          <button class="btn primary" id="grpAdd">新增分组</button>
          <button class="btn" id="grpExport">导出配置</button>
          <input type="file" id="grpImportFile" accept=".json,application/json" style="width:170px">
          <button class="btn" id="grpImport">导入配置</button>
          <span id="grpStatus" style="color:var(--faint);font-size:11.5px"></span>
        </div>
        <div id="grpCats" style="margin-bottom:14px;line-height:2"></div>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>应用</th><th>显示名</th><th>当前分组</th><th>移动到</th></tr></thead>
          <tbody id="grpBody"></tbody>
        </table></div>
        <div id="grpCount" style="color:var(--faint);font-size:11.5px;margin-top:8px"></div>
        <div class="set-note" id="grpImportNote" style="margin-top:8px"></div>
      </div>
    </section>

    <!-- 智能洞察 -->
    <section class="view" id="view-insights">
      <div class="panel">
        <h2>规则洞察 <span class="hint">离线规则引擎 · 自动跟随所选日期</span></h2>
        <div class="controls" style="margin-bottom:12px">
          <button class="btn" id="inReload">刷新</button>
          <span class="hint">规则 100% 本地计算，不上送任何数据</span>
        </div>
        <div class="grid" id="inRules" style="margin-top:12px"></div>
      </div>
      <div class="panel">
        <h2>AI 洞察 <span class="hint">可选 · OpenAI 兼容 API · 默认关闭</span></h2>
        <div class="controls" style="margin-bottom:12px">
          <button class="btn primary" id="inGen">生成 AI 洞察</button>
          <span class="hint" id="inAiMeta"></span>
        </div>
        <div class="set-note" id="inAiError" style="display:none"></div>
        <div id="inAiCards" style="margin-top:12px"></div>
      </div>
    </section>

    <!-- 设置（备份/恢复/口令/主题） -->
    <section class="view" id="view-settings">
      <div class="set-group">
        <h3>✨ AI 洞察（可选功能）</h3>
        <div class="desc">AI 洞察默认关闭（隐私优先）。开启后，聚合统计会发送到你选择的 API 端点；
          规则洞察始终离线。可选用内置 provider 预设，或选「自定义」填写任意 OpenAI 兼容端点。</div>
        <div class="controls" style="margin:10px 0 14px">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="aiEnabled" style="width:auto">
            <b>启用 AI 洞察</b>
          </label>
        </div>
        <div class="ai-fields">
          <label style="display:block;margin:8px 0">Provider 预设
            <select id="aiProvider" style="width:min(420px,100%);margin-top:4px"></select>
          </label>
          <label style="display:block;margin:8px 0">Base URL
            <input type="text" id="aiBaseUrl" placeholder="https://api.example.com/v1" style="width:min(420px,100%);margin-top:4px">
          </label>
          <label style="display:block;margin:8px 0">API Key
            <input type="password" id="aiApiKey" placeholder="留空保持不变" autocomplete="off" style="width:min(420px,100%);margin-top:4px">
          </label>
          <label style="display:block;margin:8px 0">Model
            <input type="text" id="aiModel" placeholder="model-name" style="width:min(420px,100%);margin-top:4px">
          </label>
          <label style="display:block;margin:8px 0">超时（秒）
            <input type="number" id="aiTimeout" min="1" max="600" value="60" style="width:min(180px,100%);margin-top:4px">
          </label>
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;margin:8px 0">
            <input type="checkbox" id="aiSendRaw" style="width:auto"> 发送标题 / URL 样本（默认不发送，联系人名永不上送）
          </label>
        </div>
        <div class="controls" style="margin-top:12px">
          <button class="btn primary" id="aiSave">保存 AI 设置</button>
          <span class="set-note" id="aiSaveNote"></span>
        </div>
        <div class="set-note" id="aiCfgNote" style="margin-top:8px"></div>
      </div>
      <div class="set-group">
        <h3>🗄️ 数据备份</h3>
        <div class="desc">打包数据目录（各日期文件夹 + config.json / app_groups.json / aliases.json，
          已排除 .log / 临时 / 备份大文件）为 zip 一键下载，用于迁移或存档。</div>
        <div class="controls"><button class="btn primary" id="bkDownload">备份下载（zip）</button></div>
      </div>
      <div class="set-group">
        <h3>♻️ 数据恢复</h3>
        <div class="desc">选择上文备份生成的 zip 上传，解压校验后按日期目录/配置合并覆盖到当前数据目录。
          相同日期目录会被覆盖、缺失的会补齐；不会删除已有的其他数据。</div>
        <div class="set-restore">
          <input type="file" id="bkFile" accept=".zip,application/zip">
          <button class="btn primary" id="bkRestore">恢复上传</button>
        </div>
        <div class="set-note" id="bkNote"></div>
      </div>
      <div class="set-group">
        <h3>🔒 访问口令</h3>
        <div class="desc">是否启用口令由数据根目录 <span class="url-cell">config.json</span> 的
          <b>dashboard_token</b> 决定（缺失/为空 = 关闭）。开启后所有 API 需携带
          <span class="url-cell">X-Dashboard-Token</span> 请求头；本机浏览器首次会在右上角输入口令并记住。
          本页面不会显示/修改口令。</div>
        <div class="set-note" id="authNote"></div>
      </div>
    </section>
  </main>
</div>

<script>
"use strict";
const ROOT_DIR = DATA_ROOT;
const AUTH_REQUIRED = AUTH_FLAG;
const TITLES = {overview:"概览",trends:"趋势",report:"日报",week:"周报",month:"月报",
                sessions:"会话",log:"日志",groups:"分组",insights:"洞察",settings:"设置"};
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const state = { view:"overview", day:null, month:null, dates:[], loaded:{}, authed:!AUTH_REQUIRED };
const NO_ANIM = location.search.includes("static=1") || matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- 访问口令（P1-8） ---------- */
let authShown = false;
function tokenHeaders(){
  const t = localStorage.getItem("dash_token");
  return t ? {"X-Dashboard-Token": t} : {};
}
function failAuth(){
  if(authShown) return;
  authShown = true;
  buildAuthMask();
  $("#authMask").classList.add("show");
  window.__pendingNav = null;
}
function buildAuthMask(){
  const mask = $("#authMask");
  mask.style.cssText = "display:flex;position:fixed;inset:0;background:var(--bg);z-index:300;" +
    "align-items:center;justify-content:center;flex-direction:column;gap:14px";
  mask.innerHTML = '<div class="auth-card">' +
    '<h3>请输入访问口令</h3>' +
    '<p>仪表盘开启了口令保护，请输入后继续。</p>' +
    '<div class="controls"><input type="password" id="authInput" placeholder="访问口令" autofocus>' +
    '<button class="btn primary" id="authGo">解锁</button></div>' +
    '<div class="auth-err" id="authErr"></div></div>';
  $("#authGo").onclick = tryUnlock;
  $("#authInput").onkeydown = e => { if(e.key === "Enter") tryUnlock(); };
}
async function tryUnlock(){
  const val = $("#authInput").value.trim();
  if(!val) return;
  const saved = localStorage.getItem("dash_token");
  // 先尝试校验；成功则记录并继续
  state.authed = true;
  try{
    const r = await fetch("/api/dates", {headers:{"X-Dashboard-Token": val}});
    if(r.status === 401){
      state.authed = false;
      $("#authErr").textContent = "口令错误，请重试。";
      return;
    }
    if(!r.ok) throw new Error("HTTP " + r.status);
  }catch(e){
    // 网络错误也放行为本地状态，交由后续请求判断
  }
  localStorage.setItem("dash_token", val);
  authShown = false;
  $("#authMask").classList.remove("show");
  $("#authMask").style.display = "none";
  // 触发当前视图重载
  if(state.loaded[state.view]){ state.loaded[state.view] = false; }
  startApp();
}

/* ---------- 工具 ---------- */
async function api(path){
  const r = await fetch(path, {headers: tokenHeaders()});
  if(r.status === 401){ failAuth(); throw new Error("口令未授权"); }
  if(!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}
function esc(s){ return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function fmtMs(ms){
  if(ms == null || ms < 0) return "-";
  const s = Math.floor(ms/1000), h = Math.floor(s/3600), m = Math.floor(s%3600/60), sec = s%60;
  const p = [];
  if(h) p.push(h + " 小时"); if(m) p.push(m + " 分钟"); if(!h && !m && sec) p.push(sec + " 秒");
  return p.join(" ") || "0 秒";
}
function fmtDurS(sec){ return fmtMs(Math.round(sec)*1000); }
function fmtCompact(ms){
  if(ms == null || ms < 0) return "-";
  const s = Math.floor(ms/1000);
  if(s >= 3600) return (s/3600).toFixed(1) + "h";
  if(s >= 60) return Math.round(s/60) + "m";
  return s + "s";
}
function countUp(el, value, fmt, dur){
  if(!el) return;
  if(NO_ANIM){ el.textContent = fmt(value); return; }
  const t0 = performance.now(), durMs = dur || 500;
  function tick(t){
    const p = Math.min(1, (t - t0)/durMs), e = 1 - Math.pow(1-p, 3);
    const v = Math.min(value, Math.max(0, Math.round(value * e)));
    el.textContent = fmt(v);
    if(p < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}
function skeleton(rows){
  return '<div class="sk" style="height:' + (rows*14) + 'px;width:100%"></div>';
}
function statRows(data, fmt){
  const max = Math.max(1, ...Object.values(data));
  return Object.entries(data).sort((a,b)=>b[1]-a[1]).map(([k,v])=>{
    const pct = Math.max(2, Math.round(v/max*100));
    return '<div class="stat-row"><div class="top"><span class="name">' + esc(k) + '</span>' +
      '<span class="val">' + fmt(v) + '</span></div><div class="bar"><i style="width:' + pct + '%"></i></div></div>';
  }).join("") || '<div class="empty">（无数据）</div>';
}

/* ---------- 视图切换 ---------- */
function closeDrawer(){
  $("#sidebar").classList.remove("open");
  $("#backdrop").classList.remove("show");
}
function switchView(v, push){
  state.view = v;
  $$(".view").forEach(el => el.classList.toggle("active", el.id === "view-"+v));
  $$(".nav-item").forEach(a => a.classList.toggle("active", a.dataset.view === v));
  $("#pageTitle").textContent = TITLES[v];
  document.title = TITLES[v] + " · UsageMonitor";
  if(push !== false) history.replaceState(null, "", v === "overview" ? "/" : "/?view=" + v);
  closeDrawer();
  if(!state.loaded[v]){
    state.loaded[v] = true;
    Promise.resolve().then(async () => {
      try { await loaders[v](); }
      catch(err){ const box = $("#view-" + v); if(box) box.innerHTML = '<div class="empty">加载失败：' + esc(err.message) + '</div>'; }
    });
  }
}
const loaders = { overview:loadOverview, trends:loadTrends, report:loadReport,
                  week:loadWeek, month:loadMonth,
                  sessions:loadSessions, log:loadLog, groups:loadGroups,
                  insights:loadInsights, settings:loadSettings };

/* ---------- 头部控件（日期选择 + 主题切换） ---------- */
function buildHeadControls(){
  const c = $("#headControls");
  c.innerHTML = '<select id="daySel"></select><button class="btn" id="btnToday">今天</button>' +
    '<button class="btn" id="themeBtn" title="切换主题：自动/浅色/深色">🌗</button>';
  $("#btnToday").onclick = () => { pickDay(todayStr()); };
  $("#daySel").onchange = e => pickDay(e.target.value);
  $("#themeBtn").onclick = cycleTheme;
  updateThemeBtn();
}
function pickDay(d){
  state.day = d; $("#daySel").value = d;
  if(state.view === "overview") loadOverview();
  if(state.view === "report") loadReport();
  if(state.view === "sessions") loadSessions();
  if(state.view === "insights") loadInsights();
}
function todayStr(){ const d=new Date(); return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }

/* ---------- 主题切换（P1-7） ---------- */
const THEME_LABEL = {auto:"🌗 自动", light:"☀️ 浅色", dark:"🌙 深色"};
function currentTheme(){
  const pref = localStorage.getItem("dash_theme") || "auto";
  if(pref === "auto") return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  return pref;
}
function applyTheme(){
  const eff = currentTheme();
  document.documentElement.dataset.theme = eff;
  updateThemeBtn();
  // 重绘 canvas 图表以匹配新配色
  if(state.view === "overview" && state.loaded.overview) loadOverview();
  else if(state.view === "trends" && state.loaded.trends) loadTrends();
}
function cycleTheme(){
  const order = ["auto", "light", "dark"];
  const pref = localStorage.getItem("dash_theme") || "auto";
  const next = order[(order.indexOf(pref) + 1) % order.length];
  localStorage.setItem("dash_theme", next);
  applyTheme();
}
function updateThemeBtn(){
  const b = $("#themeBtn");
  if(b) b.textContent = THEME_LABEL[localStorage.getItem("dash_theme") || "auto"];
}
function canvasCssVar(name, fallback){
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/* ---------- 画布图表 ---------- */
function setupCanvas(cv){
  const dpr = window.devicePixelRatio || 1;
  let w = cv.clientWidth, h = cv.clientHeight;
  if(w < 20){ w = cv.parentElement ? cv.parentElement.clientWidth : 600; }
  if(h < 20){ h = parseInt(cv.getAttribute("height") || "200", 10); }
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext("2d"); ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,h);
  return {ctx, w, h};
}
function niceMax(v){
  if(v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / exp;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp;
}
function drawBarChart(cv, labels, values, fmt){
  const {ctx,w,h} = setupCanvas(cv);
  const padL = 44, padB = 22, padT = 14;
  const max = niceMax(Math.max(1, ...values));
  const n = values.length, bw = Math.max(6, (w-padL-8)/n);
  const labelStep = bw < 60 ? 2 : 1;
  const tickFmt = v => v >= 3600000 ? Math.round(v/3600000) + "h" : Math.round(v/60000) + "m";
  // 主题相关颜色：从 CSS 变量读取，切换主题后重绘即同步
  const gridColor = canvasCssVar("--grid-line", "rgba(255,255,255,.06)");
  const axisColor = canvasCssVar("--chart-axis", "#6b7280");
  const barColor = canvasCssVar("--chart-bar", "#e0a53c");
  const emptyColor = canvasCssVar("--bar-empty", "#232936");
  function paint(ease){
    ctx.clearRect(0,0,w,h);
    ctx.strokeStyle = gridColor; ctx.fillStyle = axisColor;
    ctx.font = "10px " + getComputedStyle(document.body).fontFamily;
    ctx.lineWidth = 1;
    for(let g=0; g<=4; g++){
      const y = padT + (h-padT-padB) * g/4;
      ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(w-4,y); ctx.stroke();
      ctx.textAlign = "right";
      ctx.fillText(tickFmt(Math.round(max * (1 - g/4))), padL-6, y+3);
    }
    values.forEach((v,i)=>{
      const bh = Math.max(2, (v/max) * (h-padT-padB) * ease);
      const x = padL + i*bw, y = h-padB-bh;
      ctx.fillStyle = v > 0 ? barColor : emptyColor;
      ctx.fillRect(x+2, y, bw-4, bh);
      if(i % labelStep === 0){
        ctx.fillStyle = axisColor; ctx.textAlign = "center";
        ctx.fillText(String(labels[i]).slice(5), x+bw/2, h-8);
      }
    });
  }
  if(NO_ANIM){ paint(1); return; }
  const t0 = performance.now();
  function frame(t){
    const p = Math.min(1, (t - t0)/650);
    paint(1 - Math.pow(1-p, 3));
    if(p < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

/* ---------- 概览 ---------- */
async function loadOverview(){
  const cards = $("#ovCards");
  cards.innerHTML = [0,1,2,3].map(()=>'<div class="card"><div class="label">…</div><div class="sk" style="height:26px;margin-top:7px"></div></div>').join("");
  const d = await api("/api/day?date=" + state.day);
  const a = d.aggregate;
  const ai = Object.values(a.by_ai||{}).reduce((s,v)=>s+v,0);
  const social = (a.by_category||{})["社交聊天"] || 0;
  cards.innerHTML =
    '<div class="card"><div class="label">总活跃时长</div><div class="value" id="cTotal"></div><div class="trend">'+a.session_count+' 个会话</div></div>' +
    '<div class="card"><div class="label">AI 编程时长</div><div class="value" id="cAi"></div></div>' +
    '<div class="card"><div class="label">社交聊天</div><div class="value" id="cSocial"></div></div>' +
    '<div class="card"><div class="label">浏览器停留</div><div class="value" id="cUrl">—</div><div class="trend">标签页口径 · 含挂机</div></div>';
  countUp($("#cTotal"), a.total_active_ms, fmtMs);
  countUp($("#cAi"), ai, fmtMs);
  countUp($("#cSocial"), social, fmtMs);
  try{
    const u = await api("/api/urls?date=" + state.day);
    countUp($("#cUrl"), (u.total_duration_s||0)*1000, fmtMs);
  }catch(e){ $("#cUrl").textContent = "-"; }
  $("#ovCats").innerHTML = skeleton(6);
  $("#ovCats").innerHTML = statRows(a.by_category, fmtMs);
  $("#ovApps").innerHTML = statRows(a.by_app, fmtMs);
  const aiBlock = '<div style="margin-bottom:16px"><b style="font-size:12px;color:var(--dim);letter-spacing:.4px">AI 工具</b>' +
    (Object.keys(a.by_ai||{}).length ? statRows(a.by_ai, fmtMs) : '<div class="empty">（无 AI 工具记录）</div>') + '</div>';
  const ctBlock = '<div><b style="font-size:12px;color:var(--dim);letter-spacing:.4px">联系人</b>' +
    ((a.by_contact && Object.keys(a.by_contact).length) ? statRows(Object.fromEntries(Object.entries(a.by_contact).flatMap(([app,cs])=>Object.entries(cs).map(([c,v])=>[app+"/"+c, v]))), fmtMs) : '<div class="empty">（无联系人记录）</div>') + '</div>';
  $("#ovMisc").innerHTML = aiBlock + ctBlock;
  const days = await api("/api/days?n=14");
  drawBarChart($("#ovTrend"), days.days.map(x=>x.date), days.days.map(x=>x.total_ms),
    v => v>=3600000 ? (v/3600000).toFixed(1)+"h" : Math.round(v/60000)+"m");
}

/* ---------- 趋势 ---------- */
async function loadTrends(){
  $("#trHeatmap").innerHTML = skeleton(20);
  $("#trBars").style.display = "none";
  const hm = await api("/api/heatmap?days=84");
  const maxH = Math.max(1, ...hm.days.flatMap(d=>d.hourly_ms));
  const box = $("#trHeatmap");
  const lvl = [
    canvasCssVar("--hm-1","#242a36"), canvasCssVar("--hm-2","#2f3a4d"),
    canvasCssVar("--hm-3","#6b5323"), canvasCssVar("--hm-4","#a06f24"), canvasCssVar("--hm-5","#e0a53c")];
  const nDays = hm.days.length;
  // 行=小时(24)，列=天数；flex 布局：标签列 + 每天一列
  const cols = [];
  for(let di=0; di<nDays; di++){
    const day = hm.days[di];
    let col = '<div class="grid-col" title="'+day.date+'">';
    for(let hi=0; hi<24; hi++){
      const ms = day.hourly_ms[hi] || 0;
      const s = ms>0 ? Math.max(1, Math.round(ms/maxH*3)) : 0;
      const delay = NO_ANIM ? 0 : (di*2+hi*0.3);
      col += '<div class="cell" style="background:'+lvl[s]+';opacity:1;transition-delay:'+delay+'ms" title="'+day.date+' '+String(hi).padStart(2,"0")+':00 — '+fmtMs(ms)+'"></div>';
    }
    col += '</div>';
    cols.push(col);
  }
  let labels = '<div class="hl-col">';
  for(let hi=0; hi<24; hi++){
    labels += '<span>'+(hi%4===0 ? String(hi).padStart(2,"0") : "")+'</span>';
  }
  labels += '</div>';
  box.innerHTML = labels + cols.join("");
  $("#trBars").style.display = "block";
  drawBarChart($("#trBars"), hm.days.slice(-30).map(d=>d.date), hm.days.slice(-30).map(d=>d.total_ms),
    v => v>=3600000 ? (v/3600000).toFixed(1)+"h" : Math.round(v/60000)+"m");
  $$("#view-trends [data-range]").forEach(b=>b.onclick = ()=>{
    $$("#view-trends [data-range]").forEach(x=>x.classList.remove("primary"));
    b.classList.add("primary");
    const n = +b.dataset.range;
    const days = hm.days.slice(-n);
    drawBarChart($("#trBars"), days.map(d=>d.date), days.map(d=>d.total_ms),
      v => v>=3600000 ? (v/3600000).toFixed(1)+"h" : Math.round(v/60000)+"m");
  });
}

/* ---------- Markdown 渲染（日报/周报/月报 通用） ---------- */
function md2html(src){
  const lines = (src||"").split("\n"); let out = [], inT = false;
  for(const line of lines){
    if(line.startsWith("|") && line.endsWith("|")){
      const cells = line.slice(1,-1).split("|").map(c=>c.trim());
      if(cells.length && cells.every(c => /^:?-+:?$/.test(c))) continue; // 表头分隔行，跳过
      if(!inT){ out.push("<table>"); inT = true; }
      const tag = "td";
      out.push("<tr>" + cells.map(c=>{
        const pure = c.replace(/[█▇▆▅▄▃▂▁\s]/g, "");
        if(pure === "" && /[█▇▆▅▄▃▂▁]/.test(c)){
          const n = (c.match(/[█▇▆▅▄▃▂▁]/g) || []).length;
          return "<" + tag + "><div class='mdbar'><i style='width:" + Math.min(100, n * 10) + "%'></i></div></" + tag + ">";
        }
        if(c.trim() === "-" || c.trim() === "--") return "<" + tag + "><span style='color:var(--faint)'>—</span></" + tag + ">";
        return "<" + tag + ">" + inline(c) + "</" + tag + ">";
      }).join("") + "</tr>");
      continue;
    }
    if(inT){ out.push("</table>"); inT = false; }
    if(/^#\s/.test(line)) out.push("<h1>" + inline(line.slice(2)) + "</h1>");
    else if(/^##\s/.test(line)) out.push("<h2>" + inline(line.slice(3)) + "</h2>");
    else if(/^>\s?/.test(line)) out.push("<blockquote>" + inline(line.replace(/^>\s?/,"")) + "</blockquote>");
    else if(/^-\s/.test(line)) out.push("<li>" + inline(line.slice(2)) + "</li>");
    else if(!line.trim()) out.push("");
    else out.push("<p>" + inline(line) + "</p>");
  }
  if(inT) out.push("</table>");
  return out.join("\n");
  function inline(s){
    let t = esc(s);
    t = t.replace(/\[([^\]]*)\]\(([^)]*)\)/g, (m,a,b)=>{ const u=b.split("?")[0]; return '<a href="'+esc(b)+'">'+esc(a)+'</a><span class="url-cell">'+esc(u.slice(0,80))+'</span>'; });
    t = t.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    if(/[█▇▆▅▄▃▂▁]/.test(t)) t = t.replace(/([█▇▆▅▄▃▂▁]+)/g, '<span style="color:var(--md-h2);letter-spacing:1px">$1</span>');
    return t;
  }
}
async function loadReport(){
  $("#rpMd").innerHTML = skeleton(18);
  const r = await api("/api/report?date=" + state.day);
  if(!r.exists){ $("#rpMd").innerHTML = '<div class="empty">当日无日报（守护进程跨天时自动生成，或运行 report.py --day '+state.day+' --write）</div>'; return; }
  $("#rpMd").innerHTML = md2html(r.markdown);
}

/* ---------- 周报 / 月报（P1-1） ---------- */
async function loadWeek(){
  $("#wkMd").innerHTML = skeleton(14);
  const r = await api("/api/week");
  if(!r.days || !r.days.length){
    $("#wkMd").innerHTML = '<div class="empty">近 7 天无数据</div>'; return;
  }
  $("#wkMd").innerHTML = md2html(r.markdown);
}
async function loadMonth(){
  $("#moMd").innerHTML = skeleton(14);
  if(!state.month) return;
  const r = await api("/api/month?month=" + state.month);
  if(!r.exists){ $("#moMd").innerHTML = '<div class="empty">当月无数据</div>'; return; }
  $("#moMd").innerHTML = md2html(r.markdown);
}
function monthInit(){
  const sel = $("#moSel");
  const now = new Date();
  const latest = availableMonths()[availableMonths().length-1] ||
    now.getFullYear() + "-" + String(now.getMonth()+1).padStart(2,"0");
  state.month = latest;
  if(sel){ sel.value = latest; sel.max = latest; }
  $("#moGo").onclick = ()=>{ state.month = sel.value; if(state.loaded.month){ state.loaded.month = false; } loadMonth(); };
}
function availableMonths(){
  const s = new Set();
  (state.dates || []).forEach(d => s.add(d.slice(0,7)));
  return [...s].sort();
}

/* ---------- 导出（P1-2） ---------- */
async function downloadToFile(url, filename){
  const r = await fetch(url, {headers: tokenHeaders()});
  if(r.status === 401){ failAuth(); throw new Error("口令未授权"); }
  if(!r.ok) throw new Error("HTTP " + r.status);
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(()=>URL.revokeObjectURL(a.href), 1000);
}
let exporting = false;
async function doExport(scope, type){
  if(exporting) return;
  exporting = true;
  try{
    let url = "/api/export?type=" + type + "&scope=" + scope;
    let name = "report";
    if(scope === "day"){ url += "&date=" + state.day; name = "report_" + state.day; }
    else if(scope === "week"){ name = "week_" + (state.dates.length ? state.dates[state.dates.length-1] : todayStr()); }
    else if(scope === "month"){ url += "&month=" + state.month; name = "month_" + state.month; }
    name += (type === "csv" ? ".csv" : ".json");
    await downloadToFile(url, name);
  }catch(e){
    alert("导出失败：" + e.message);
  }finally{
    exporting = false;
  }
}
function wireExportButtons(){
  $$("[data-export]").forEach(b => b.onclick = () => doExport(b.dataset.export, b.dataset.scope));
}

/* ---------- 备份/恢复（P1-4） ---------- */
async function bkDownload(){
  try{
    const name = "usagemonitor_backup_" + todayStr() + ".zip";
    await downloadToFile("/api/backup", name);
  }catch(e){ alert("备份下载失败：" + e.message); }
}
async function bkRestore(){
  const file = $("#bkFile").files[0];
  const note = $("#bkNote");
  if(!file){ note.textContent = "请先选择要恢复的 zip 备份文件。"; note.style.color = "var(--danger)"; return; }
  if(!confirm("确认恢复「" + file.name + "」？\n将把备份中的日期目录/配置合并覆盖到当前数据目录（缺失补齐、相同覆盖）。\n建议先「备份下载」留底。")) return;
  note.textContent = "正在上传与校验…"; note.style.color = "var(--dim)";
  try{
    const buf = await file.arrayBuffer();
    const r = await fetch("/api/backup/restore", {method:"POST",
      headers:Object.assign({"Content-Type":"application/octet-stream"}, tokenHeaders()),
      body: buf});
    if(r.status === 401){ failAuth(); note.textContent = ""; return; }
    const result = await r.json();
    if(!r.ok || !result.ok){ throw new Error(result.error || ("HTTP " + r.status)); }
    note.textContent = "恢复完成：" + (result.days||[]).length + " 个日期目录、配置 " +
      (result.files||[]).length + " 项（如需查看请刷新页面重新载入日期）。";
    note.style.color = "var(--ok)";
    $("#bkFile").value = "";
  }catch(e){
    note.textContent = "恢复失败：" + e.message;
    note.style.color = "var(--danger)";
  }
}
function fillAiProviderOptions(presets, selected){
  const sel = $("#aiProvider");
  if(!sel) return;
  const opts = (presets || []).map(p =>
    '<option value="'+esc(p.id)+'" data-base="'+esc(p.base_url || "")+'" data-model="'+esc(p.model || "")+'"' +
    (p.id === selected ? " selected" : "") + '>'+esc(p.name)+'</option>'
  ).join("");
  sel.innerHTML = opts;
  if(selected && !(presets || []).some(p => p.id === selected)){
    const opt = document.createElement("option");
    opt.value = selected; opt.textContent = "当前：" + selected;
    sel.appendChild(opt);
  }
}
function applyAiSettingsView(d){
  const ai = d.ai || {};
  const en = $("#aiEnabled");
  if(en) en.checked = !!ai.enabled;
  fillAiProviderOptions(d.presets || [], ai.provider);
  if($("#aiBaseUrl")) $("#aiBaseUrl").value = ai.base_url || "";
  if($("#aiModel")) $("#aiModel").value = ai.model || "";
  if($("#aiTimeout")) $("#aiTimeout").value = ai.timeout_s || 60;
  if($("#aiSendRaw")) $("#aiSendRaw").checked = !!ai.send_raw_titles;
  if($("#aiApiKey")){
    $("#aiApiKey").value = "";
    $("#aiApiKey").placeholder = ai.api_key_set ? "已设置，留空保持不变" : "未设置（本地端点可留空）";
  }
  const note = $("#aiCfgNote");
  if(!note) return;
  note.textContent = ai.enabled
    ? "AI 洞察已开启。" + (ai.api_key_set ? " API Key 已配置。" : " 尚未配置 API Key（部分本地端点可留空）。")
    : "AI 洞察当前关闭（可选功能，默认关闭）。规则洞察始终离线。";
  note.style.color = ai.enabled ? "var(--ok)" : "var(--dim)";
}
async function loadSettings(){
  $("#authNote").textContent = AUTH_REQUIRED ?
    "已开启：当前页面需要访问口令。" :
    "当前关闭：config.json 缺失 dashboard_token（或为空）。如需开启，在 config.json 增加 \"dashboard_token\":\"你的口令\" 后重启仪表盘。";
  try{
    const d = await api("/api/insights/settings");
    applyAiSettingsView(d);
    const note = $("#aiSaveNote");
    if(note){ note.textContent = ""; }
  }catch(e){
    const note = $("#aiCfgNote");
    if(note){
      note.textContent = "读取 AI 设置失败：" + e.message;
      note.style.color = "var(--danger)";
    }
  }
}
async function saveAiSettings(){
  const note = $("#aiSaveNote");
  if(!note) return;
  note.textContent = "保存中…";
  note.style.color = "var(--dim)";
  const payload = {
    enabled: $("#aiEnabled").checked,
    provider: $("#aiProvider").value,
    base_url: $("#aiBaseUrl").value.trim(),
    api_key: $("#aiApiKey").value.trim(),
    model: $("#aiModel").value.trim(),
    timeout_s: parseInt($("#aiTimeout").value || "60", 10),
    send_raw_titles: $("#aiSendRaw").checked,
    language: "zh"
  };
  try{
    const d = await postJson("/api/insights/settings", payload);
    applyAiSettingsView(d);
    note.textContent = "已保存。";
    note.style.color = "var(--ok)";
    if(state.loaded.insights){ state.loaded.insights = false; }
  }catch(e){
    note.textContent = "保存失败：" + e.message;
    note.style.color = "var(--danger)";
  }
}
function wireAiSettings(){
  const sel = $("#aiProvider");
  if(sel){
    sel.onchange = () => {
      const opt = sel.selectedOptions[0];
      if(opt && opt.dataset.base !== undefined){
        if(opt.dataset.base && $("#aiBaseUrl")) $("#aiBaseUrl").value = opt.dataset.base;
        if(opt.dataset.model && $("#aiModel")) $("#aiModel").value = opt.dataset.model;
      }
    };
  }
  const btn = $("#aiSave");
  if(btn) btn.onclick = saveAiSettings;
}

/* ---------- 会话 ---------- */
async function loadSessions(){
  const d = await api("/api/day?date=" + state.day);
  const a = d.aggregate, sessions = a.sessions || [];
  const catSel = $("#ssCat"), appSel = $("#ssApp");
  catSel.innerHTML = '<option value="">全部类别</option>' + Object.keys(a.by_category).map(c=>'<option>'+esc(c)+'</option>').join("");
  appSel.innerHTML = '<option value="">全部应用</option>' + Object.keys(a.by_app).map(c=>'<option>'+esc(c)+'</option>').join("");
  $("#ssSearch").value = "";
  renderSessions(sessions, a);
}
function renderSessions(sessions, agg){
  const cat = $("#ssCat").value, app = $("#ssApp").value, q = $("#ssSearch").value.toLowerCase();
  const rows = sessions.filter(s =>
    (!cat || s.category === cat) && (!app || (s.app||s.exe) === app) &&
    (!q || ((s.title||"")+(s.app||"")+(s.exe||"")).toLowerCase().includes(q))
  ).slice(0, 300);
  $("#ssCount").textContent = rows.length + " / " + sessions.length + " 条" + (sessions.length>300 ? "（仅显示前 300）" : "");
  $("#ssBody").innerHTML = rows.map(s=>{
    const tags = [];
    if(s.ai_tool) tags.push('<span class="tag ai">AI:'+esc(s.ai_tool)+'</span>');
    if(s.term_tool) tags.push('<span class="tag term">终端:'+esc(s.term_tool)+'</span>');
    if(s.contact) tags.push('<span class="tag contact">'+esc(s.contact)+'</span>');
    if(s.subcategory) tags.push('<span class="tag sub">'+esc(s.subcategory)+'</span>');
    if(s.window_state && s.window_state !== "normal") tags.push('<span class="tag ws">'+({fullscreen:"全屏",maximized:"最大化"}[s.window_state]||s.window_state)+'</span>');
    if(s.url) tags.push(s.url === "[已隐藏]" ? '<span class="tag">URL已隐藏</span>' : '<span class="url-cell" title="'+esc(s.url)+'">'+esc(s.url.split("?")[0].slice(0,56))+'</span>');
    return "<tr><td class='mono num'>"+(s.start||"").slice(11,19)+"</td>" +
      "<td class='num'>"+fmtCompact(s.duration_ms||0)+"</td>" +
      "<td>"+esc(s.app||s.exe||"")+"</td>" +
      "<td class='mono'>"+esc(s.title||"")+"</td>" +
      "<td>"+esc(s.category||"")+"</td>" +
      "<td>"+tags.join("")+"</td></tr>";
  }).join("") || '<tr><td colspan="6" class="empty">无匹配会话</td></tr>';
  $("#ssCat").onchange = ()=>renderSessions(sessions, agg);
  $("#ssApp").onchange = ()=>renderSessions(sessions, agg);
  $("#ssSearch").oninput = ()=>renderSessions(sessions, agg);
}

/* ---------- 日志 ---------- */
function logRow(line, isErr){
  const m = line.match(/^(\S+ \S+) \[([A-Z]+)\] \[([^\]]+)\] (.*)$/);
  if(!m) return '<div class="log-line'+(isErr?' err':'')+'"><span class="msg">'+esc(line)+'</span></div>';
  return '<div class="log-line'+(isErr?' err':'')+'"><span class="ts">'+esc(m[1])+'</span>' +
    '<span class="lv '+m[2]+'">'+esc(m[2])+'</span>' +
    '<span class="msg">['+esc(m[3])+'] '+esc(m[4])+'</span></div>';
}
async function loadLog(){
  const d = await api("/api/log");
  $("#lgEntries").innerHTML = d.entries.length
    ? d.entries.map(l=>logRow(l)).join("")
    : '<div class="empty">暂无运行日志</div>';
  $("#lgErrors").innerHTML = d.errors.length
    ? d.errors.map(l=>logRow(l,true)).join("")
    : '<div class="empty">无错误记录</div>';
  scrollLogBottom();
}
let logTimer = null;
function scrollLogBottom(){
  const el = $("#lgEntries");
  if(el) el.scrollTop = el.scrollHeight;
}
function armLogTimer(){
  if(logTimer) clearInterval(logTimer);
  logTimer = setInterval(()=>{ if(state.view==="log" && state.loaded.log && state.authed) loadLog(); }, 15000);
}

/* ---------- 分组管理 ---------- */
let grpFlashTimer = null;
function grpFlash(msg){
  const el = $("#grpStatus");
  el.textContent = msg;
  el.style.color = "var(--ok)";
  if(grpFlashTimer) clearTimeout(grpFlashTimer);
  grpFlashTimer = setTimeout(()=>{ el.textContent = ""; }, 3000);
}
async function postJson(url, obj){
  const r = await fetch(url, {method:"POST",
    headers:Object.assign({"Content-Type":"application/json"}, tokenHeaders()),
    body: JSON.stringify(obj)});
  if(r.status === 401){ failAuth(); throw new Error("口令未授权"); }
  if(!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}
async function loadGroups(){
  const d = await api("/api/groups");
  state.groups = d;
  $("#grpCats").innerHTML = d.categories.map(c=>{
    const custom = d.custom_categories.includes(c);
    return '<span class="tag" style="padding:4px 10px;margin-right:6px">'+esc(c)+
      (custom ? ' <a href="#" class="grp-del" data-name="'+esc(c)+'" title="删除分组" style="color:var(--danger);text-decoration:none;margin-left:4px">✕</a>' : '')+'</span>';
  }).join("");
  renderGroups();
}
function renderGroups(){
  const q = $("#grpSearch").value.toLowerCase();
  const d = state.groups || {apps:[]};
  const rows = d.apps.filter(a => (a.app+" "+a.exe).toLowerCase().includes(q));
  $("#grpCount").textContent = rows.length + " / " + d.apps.length + " 个应用（下拉选分组即时生效；清空=恢复自动分类；显示名可自定义）";
  $("#grpBody").innerHTML = rows.map(a=>{
    const opts = ['<option value="">自动分类</option>'].concat(
      d.categories.map(c=>'<option value="'+esc(c)+'"'+(a.overridden && a.category===c ? " selected" : "")+'>'+esc(c)+'</option>')
    ).join("");
    return "<tr><td>"+esc(a.app)+"<span class='url-cell' style='margin-left:8px'>"+esc(a.exe)+"</span></td>"+
      "<td><input class='grp-name' data-exe='"+esc(a.exe)+"' value='"+esc(a.app)+"' placeholder='默认' style='width:140px'></td>"+
      "<td>"+(a.overridden ? '<span class="tag ai">'+esc(a.category)+'</span>' : '<span style="color:var(--dim)">'+esc(a.category)+'</span>')+"</td>"+
      "<td><select data-exe='"+esc(a.exe)+"'>"+opts+"</select></td></tr>";
  }).join("") || '<tr><td colspan="4" class="empty">无匹配应用</td></tr>';
  $$("#grpBody select").forEach(sel=>{
    sel.onchange = async ()=>{
      try{
        await postJson("/api/groups/set", {exe: sel.dataset.exe, category: sel.value});
        grpFlash("已保存：" + sel.dataset.exe + " → " + (sel.value || "自动分类"));
        await loadGroups();
        if(state.loaded.overview) loadOverview();
      }catch(e){ grpFlash("保存失败：" + e.message); }
    };
  });
  $$("#grpBody .grp-name").forEach(inp=>{
    inp.onchange = async ()=>{
      const name = inp.value.trim();
      try{
        await postJson("/api/groups/rename", {exe: inp.dataset.exe, display_name: name});
        grpFlash("已更新显示名：" + (name || "恢复默认"));
        await loadGroups();
        if(state.loaded.overview) loadOverview();
      }catch(e){ grpFlash("保存失败：" + e.message); }
    };
  });
}
async function grpExport(){
  try{
    await downloadToFile("/api/groups/export", "app_groups.json");
  }catch(e){ alert("导出失败：" + e.message); }
}
async function grpImport(){
  const file = $("#grpImportFile").files[0];
  const note = $("#grpImportNote");
  if(!file){ note.textContent = "请先选择要导入的 app_groups.json 文件。"; note.style.color = "var(--danger)"; return; }
  try{
    const text = await file.text();
    const obj = JSON.parse(text);
    const d = await postJson("/api/groups/import", obj);
    note.textContent = "导入成功：" + Object.keys((d.groups && d.groups.exe_groups) || {}).length + " 个应用分组配置。";
    note.style.color = "var(--ok)";
    $("#grpImportFile").value = "";
    await loadGroups();
    if(state.loaded.overview) loadOverview();
  }catch(e){
    note.textContent = "导入失败：" + e.message;
    note.style.color = "var(--danger)";
  }
}
async function groupsInit(){
  $("#grpAdd").onclick = async ()=>{
    const name = $("#grpNewName").value.trim();
    if(!name){ grpFlash("请输入分组名称"); return; }
    try{
      await postJson("/api/groups/add", {name});
      $("#grpNewName").value = "";
      grpFlash("已新增分组：" + name);
      await loadGroups();
    }catch(e){ grpFlash("新增失败：" + e.message); }
  };
  $("#grpSearch").oninput = renderGroups;
  $("#grpCats").onclick = async (e)=>{
    const el = e.target.closest(".grp-del");
    if(!el) return;
    e.preventDefault();
    const name = el.dataset.name;
    if(!confirm("删除分组「" + name + "」？组内应用将恢复自动分类。")) return;
    try{
      await postJson("/api/groups/delete", {name});
      grpFlash("已删除分组：" + name);
      await loadGroups();
      if(state.loaded.overview) loadOverview();
    }catch(err){ grpFlash("删除失败：" + err.message); }
  };
  $("#grpExport").onclick = grpExport;
  $("#grpImport").onclick = grpImport;
}

/* ---------- 智能洞察（规则 + AI） ---------- */
const SEV_STYLE = {
  info:   {c:"var(--accent)", bg:"color-mix(in srgb, var(--accent) 10%, transparent)"},
  warn:   {c:"#e0a53c", bg:"rgba(224,165,60,.12)"},
  alert:  {c:"var(--danger)", bg:"rgba(239,68,68,.12)"}
};
function insightCardHTML(item){
  const s = SEV_STYLE[item.severity] || SEV_STYLE.info;
  const label = {info:"建议", warn:"注意", alert:"提醒"}[item.severity] || "建议";
  return '<div class="panel" style="padding:14px 16px;margin:0;border-left:3px solid '+s.c+'">' +
    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">' +
      '<b style="font-size:13px">'+esc(item.title || "AI")+'</b>' +
      '<span style="font-size:10.5px;padding:2px 8px;border-radius:999px;background:'+s.bg+';color:'+s.c+'">'+label+'</span>' +
    '</div>' +
    '<div style="color:var(--dim);font-size:12.5px;line-height:1.7">'+esc(item.detail || "")+'</div>' +
  '</div>';
}
async function loadInsights(){
  const box = $("#inRules");
  box.innerHTML = skeleton(6);
  $("#inAiError").style.display = "none";
  $("#inAiCards").innerHTML = "";
  let d;
  try{
    d = await api("/api/insights?date=" + state.day);
  }catch(err){
    box.innerHTML = '<div class="empty">加载失败：' + esc(err.message) + '</div>';
    return;
  }
  box.innerHTML = (d.rules && d.rules.length)
    ? d.rules.map(insightCardHTML).join("")
    : '<div class="empty">当日暂无规则洞察（数据为空或 insights.enabled=false）</div>';
  renderAiPanel(d);
}
function renderAiPanel(d){
  const btn = $("#inGen"), meta = $("#inAiMeta"), err = $("#inAiError"), cards = $("#inAiCards");
  if(btn) btn.disabled = false;
  if(!d.ai_enabled){
    meta.textContent = "未开启（config.json: insights.ai.enabled=false）";
    cards.innerHTML = '<div class="empty">AI 洞察未开启。开启后聚合统计会发送到你配置的 API 端点；规则洞察始终离线。</div>';
    return;
  }
  const ai = d.ai || {};
  const bits = [];
  if(ai.provider) bits.push("provider " + esc(ai.provider));
  if(ai.model) bits.push("模型 " + esc(ai.model));
  if(ai.generated_at) bits.push("上次生成 " + esc(String(ai.generated_at).replace("T", " ")));
  meta.textContent = bits.length ? bits.join(" · ") : "尚未生成（点击左侧按钮）";
  if(ai.error){
    err.style.display = "block";
    err.textContent = "生成失败：" + ai.error;
    err.style.color = "var(--danger)";
  }else{
    err.style.display = "none";
  }
  if(ai.insights && ai.insights.length){
    cards.innerHTML = ai.insights.map(insightCardHTML).join("");
  }else if(!ai.error){
    cards.innerHTML = '<div class="empty">暂无 AI 洞察缓存。</div>';
  }else{
    cards.innerHTML = "";
  }
}
let aiGenerating = false;
function wireInsights(){
  $("#inReload").onclick = () => { loadInsights(); };
  $("#inGen").onclick = async () => {
    if(aiGenerating) return;
    aiGenerating = true;
    const btn = $("#inGen");
    btn.disabled = true;
    btn.textContent = "生成中…（可能需数十秒）";
    $("#inAiError").style.display = "none";
    try{
      const d = await api("/api/insights/ai?date=" + state.day + "&refresh=1");
      renderAiPanel(d);
    }catch(err){
      $("#inAiError").style.display = "block";
      $("#inAiError").textContent = "生成失败：" + err.message;
      $("#inAiError").style.color = "var(--danger)";
    }finally{
      aiGenerating = false;
      const b = $("#inGen");
      if(b){ b.disabled = false; b.textContent = "重新生成 AI 洞察"; }
    }
  };
}

/* ---------- 初始化 ---------- */
function startApp(){
  buildHeadControls();
  applyTheme();
  backgroundWatchTheme();
  wireExportButtons();
  wireInsights();
  wireAiSettings();
  monthInit();
  $("#bkDownload").onclick = bkDownload;
  $("#bkRestore").onclick = bkRestore;

  // 导航
  $$(".nav-item").forEach(a => a.onclick = e => { e.preventDefault(); switchView(a.dataset.view); });
  // 移动端抽屉
  $("#hamburger").onclick = () => { $("#sidebar").classList.add("open"); $("#backdrop").classList.add("show"); };
  $("#backdrop").onclick = closeDrawer;

  // 加载日期
  $("#pageSub").textContent = "数据目录：" + ROOT_DIR;
  $("#rootPath").textContent = ROOT_DIR;
  Promise.all([api("/api/dates")]).then(async ([dates]) => {
    state.dates = dates.dates || [];
    $("#daySel").innerHTML = state.dates.map(d=>'<option value="'+d+'">'+d+'</option>').join("");
    state.day = state.dates.length ? state.dates[state.dates.length-1] : todayStr();
    if(!state.dates.includes(state.day)) {
      const opt = document.createElement("option");
      opt.value = state.day; opt.textContent = state.day + "（今天）";
      $("#daySel").appendChild(opt);
    }
    $("#daySel").value = state.day;

    // URL 视图
    const v = new URLSearchParams(location.search).get("view");
    const target = v && TITLES[v] ? v : "overview";
    groupsInit();
    armLogTimer();
    window.addEventListener("resize", ()=>{
      if(state.view==="overview") loadOverview();
      else if(state.view==="trends" && state.loaded.trends) loadTrends();
    });
    if(target !== "overview") switchView(target, false);
    else { state.loaded.overview = true; loadOverview(); }
  }).catch(err => {
    if(String(err.message).includes("口令")) return; // 认证流程已接管
    $("#daySel").innerHTML = '<option>加载失败</option>';
  });
}
let themeMedia = null;
function backgroundWatchTheme(){
  if(themeMedia) return;
  themeMedia = matchMedia("(prefers-color-scheme: light)");
  themeMedia.addEventListener("change", ()=>{
    if((localStorage.getItem("dash_theme") || "auto") === "auto") applyTheme();
  });
}

(async function init(){
  if(AUTH_REQUIRED){
    // 尝试用已存口令静默通过；失败则弹出口令框
    const t = localStorage.getItem("dash_token");
    if(t){
      try{
        const r = await fetch("/api/dates", {headers:{"X-Dashboard-Token": t}});
        if(r.ok){ state.authed = true; }
      }catch(e){ /* 忽略，稍后重试 */ }
    }
    if(state.authed) startApp();
    else { failAuth(); }
  }else{
    startApp();
  }
})();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "UsageMonitorDashboard/4.0"

    def log_message(self, fmt, *args):  # 静默，减少刷屏
        pass

    def _send_security_headers(self, extra: dict | None = None) -> None:
        """统一的隐私/安全响应头（CSP / X-Frame-Options 等）。"""
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "connect-src 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)

    def _send_json(self, obj: dict, status: int = 200) -> None:
        """发送 JSON 响应（带统一隐私/安全头）。"""
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_blob(self, data: bytes, content_type: str, filename: str) -> None:
        """发送附件下载（导出 CSV/JSON、备份 zip），带 Content-Disposition。"""
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition",
                         f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _valid_date(self, query: dict) -> str | None:
        """校验并返回日期参数；非法返回 None。"""
        date = query.get("date", [""])[0]
        return date if _DAY_RE.fullmatch(date) else None

    def _valid_month(self, query: dict) -> str | None:
        """校验并返回月份参数（YYYY-MM）；非法返回 None。"""
        month = query.get("month", [""])[0]
        if not _MONTH_RE.fullmatch(month):
            return None
        try:
            datetime.datetime.strptime(month, "%Y-%m")
            return month
        except ValueError:
            return None

    def _required_token(self) -> str:
        """当前生效的访问口令；'' = 未开启。"""
        config_path = self.server.config_path if hasattr(self.server, "config_path") else None
        return _required_token(config_path, data_root=self.server.data_root)

    def _auth_ok(self) -> bool:
        """校验 X-Dashboard-Token（未开启口令直接放行；开启则 hmac 常量时间比较）。"""
        token = self._required_token()
        if not token:
            return True
        provided = self.headers.get("X-Dashboard-Token", "")
        return hmac.compare_digest(provided, token)

    def _origin_allowed(self, headers) -> bool:
        """同源校验：Origin/Referer 存在时必须匹配本服务（防恶意网页偷读隐私数据）。

        浏览器跨站 fetch/资源请求必然携带 Origin 或 Referer（指向恶意站点），
        校验拒绝即可堵住 CSRF/localhost 数据泄露；curl/无头脚本等无浏览器
        上下文的请求不带这两个头，予以放行（不是攻击向量）。
        """
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        for header in ("Origin", "Referer"):
            value = headers.get(header)
            if not value:
                continue
            try:
                parsed = urllib.parse.urlparse(value.strip())
            except ValueError:
                return False
            if parsed.netloc not in allowed:
                return False
        return True

    # ---- 周报 / 月报 数据构造（复用 report.py 聚合） ----
    def _week_aggregate(self, root: str) -> dict:
        """最近 7 个有数据日聚合；返回 (agg, days)。"""
        days = _available_days(root)[-7:]
        return report._aggregate_days(days, root), days

    def _month_aggregate(self, root: str, month: str) -> dict | None:
        """月度聚合；当月无数据返回 None。"""
        agg = report.aggregate_month(month, root)
        if not agg.get("per_day"):
            return None
        return agg

    def _render_week_md(self, agg: dict) -> str:
        return report._report_from_agg(agg, "电脑使用情况周报（最近 7 个有数据日）")

    def _render_month_md(self, agg: dict) -> str:
        return report.generate_month_report_md(agg.get("month", ""), self.server.data_root)

    def _handle_export(self, query: dict, root: str) -> None:
        """/api/export：CSV/JSON 一键下载（day/week/month）。"""
        ftype = query.get("type", [""])[0]
        scope = query.get("scope", [""])[0]
        if ftype not in ("csv", "json"):
            self._send_json({"error": "invalid type"}, 400)
            return
        agg = None
        filename = "report"
        if scope == "day":
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            agg = report.aggregate(date, root)
            filename = f"report_{date}"
        elif scope == "week":
            a, days = self._week_aggregate(root)
            agg = a
            filename = f"week_{days[-1] if days else 'none'}"
        elif scope == "month":
            month = self._valid_month(query)
            if not month:
                self._send_json({"error": "invalid month"}, 400)
                return
            m = self._month_aggregate(root, month)
            if m is None:
                self._send_json({"error": "no data"}, 404)
                return
            agg = m
            filename = f"month_{month}"
        else:
            self._send_json({"error": "invalid scope"}, 400)
            return

        if ftype == "json":
            payload = json.dumps(agg, ensure_ascii=False, default=str).encode("utf-8")
            self._send_blob(payload, "application/json; charset=utf-8", f"{filename}.json")
        else:
            csv = _agg_to_csv(agg)
            # 简单安全清洗：去掉可能被当作公式的单元格前缀（CSV 注入防护）
            csv = _sanitize_csv(csv)
            self._send_blob(csv.encode("utf-8-sig"), "text/csv; charset=utf-8", f"{filename}.csv")

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        root = self.server.data_root

        # 同源校验：跨站请求直接拒绝（隐私数据防偷读）
        if not self._origin_allowed(self.headers):
            self._send_json({"error": "forbidden"}, 403)
            return

        # 访问口令：开启状态所有 /api 一致校验（P1-8）
        auth_enabled = bool(self._required_token())
        if path.startswith("/api/") and not self._auth_ok():
            self._send_json({"error": "unauthorized"}, 401)
            return

        if path == "/" or path == "/index.html":
            html = (PAGE_TEMPLATE
                    .replace("DATA_ROOT", json.dumps(root).replace("$", "\\$"))
                    .replace("AUTH_FLAG", "true" if auth_enabled else "false"))
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/dates":
            days = _available_days(root)
            self._send_json({"dates": days})
            return

        if path == "/api/days":
            n = max(1, min(90, int(query.get("n", ["14"])[0])))
            days = _available_days(root)[-n:]
            out = []
            for d in days:
                agg = report.aggregate(d, root)
                out.append({"date": d, "total_ms": agg["total_active_ms"], "count": agg["session_count"]})
            self._send_json({"days": out})
            return

        if path == "/api/day":
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            self._send_json({"date": date, "aggregate": report.aggregate(date, root)})
            return

        if path == "/api/insights":
            # 规则即时计算（离线）；AI 只读缓存/按需生成（成功才写缓存）
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            config = _load_config_for_root(root, self.server.config_path)
            try:
                import insights  # noqa: PLC0415 —— 惰性导入
                prev_day = (datetime.date.fromisoformat(date)
                            - datetime.timedelta(days=1)).isoformat()
                agg = report.aggregate(date, root)
                prev_agg = report.aggregate(prev_day, root)
                rules = insights.rule_insights(agg, config, prev_agg)
                ins_cfg = config.get("insights") if isinstance(config.get("insights"), dict) else {}
                ai_cfg = ins_cfg.get("ai") if isinstance(ins_cfg.get("ai"), dict) else {}
                ai_enabled = bool(ins_cfg.get("enabled", True) and ai_cfg.get("enabled"))
                ai = None
                if ai_enabled:
                    ai = insights.ai_insights(date, root, config, refresh=False)
                    ai["provider"] = str(ai_cfg.get("provider") or "")
                self._send_json({
                    "date": date, "rules": rules,
                    "ai_enabled": ai_enabled, "ai": ai,
                })
            except Exception as exc:  # noqa: BLE001 —— 洞察失败不拖垮仪表盘
                self._send_json({"error": f"insights unavailable: {exc}"}, 500)
            return

        if path == "/api/insights/settings":
            # AI 设置（可选功能开关 + provider 预设 + 自定义端点）
            config = _load_config_for_root(root, self.server.config_path)
            try:
                import insights  # noqa: PLC0415
                self._send_json({
                    "ai": _ai_settings_view(config),
                    "presets": insights.list_provider_presets(),
                })
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"insights settings unavailable: {exc}"}, 500)
            return

        if path == "/api/insights/ai":
            # AI 洞察：refresh=1 强制重生成；未开启时返回可展示的错误态
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            config = _load_config_for_root(root, self.server.config_path)
            try:
                import insights  # noqa: PLC0415
                ins_cfg = config.get("insights") if isinstance(config.get("insights"), dict) else {}
                ai_cfg = ins_cfg.get("ai") if isinstance(ins_cfg.get("ai"), dict) else {}
                ai_enabled = bool(ins_cfg.get("enabled", True) and ai_cfg.get("enabled"))
                if not ai_enabled:
                    self._send_json({
                        "date": date, "ai_enabled": False,
                        "ai": {
                            "generated_at": None, "model": None, "insights": None,
                            "error": "AI 洞察未开启（config.json: insights.ai.enabled=false）",
                            "provider": str(ai_cfg.get("provider") or ""),
                        },
                    })
                    return
                refresh = query.get("refresh", [""])[0] in ("1", "true", "yes")
                ai = insights.ai_insights(date, root, config, refresh=refresh)
                ai["provider"] = str(ai_cfg.get("provider") or "")
                self._send_json({"date": date, "ai_enabled": True, "ai": ai})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"insights ai unavailable: {exc}"}, 500)
            return

        if path == "/api/hourly":
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            agg = report.aggregate(date, root)
            self._send_json({"date": date, "hourly_ms": agg.get("hourly_ms", [0] * 24)})
            return

        if path == "/api/urls":
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            try:
                import browser_history  # noqa: PLC0415 —— 惰性导入
                config = browser_history.classifier.load_config()
                data = browser_history.collect(date, root, config)
                visits = data.get("visits", [])[:_URL_MAX_ROWS]
                self._send_json({
                    "date": date,
                    "count": data.get("count", 0),
                    "total_duration_s": data.get("total_duration_s", 0),
                    "by_category_duration_s": data.get("by_category_duration_s", {}),
                    "by_domain_duration_s": data.get("by_domain_duration_s", {}),
                    "visits": visits,
                })
            except Exception:  # noqa: BLE001 —— 浏览器数据失败不影响页面其他部分
                self._send_json({"date": date, "count": 0, "total_duration_s": 0,
                                 "by_category_duration_s": {}, "by_domain_duration_s": {}, "visits": []})
            return

        if path == "/api/report":
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            md_path = os.path.join(root, date, "report.md")
            if os.path.isfile(md_path):
                try:
                    with open(md_path, "r", encoding="utf-8-sig") as fh:
                        self._send_json({"date": date, "exists": True, "markdown": fh.read()})
                        return
                except OSError:
                    pass
            self._send_json({"date": date, "exists": False, "markdown": ""})
            return

        if path == "/api/week":
            # 周报：最近 7 个有数据日聚合（复用 report._aggregate_days/_report_from_agg）
            a, days = self._week_aggregate(root)
            payload = {
                "days": days,
                "total_ms": a["total_active_ms"],
                "count": a["session_count"],
                "aggregate": a,
                "markdown": self._render_week_md(a),
            }
            self._send_json(payload)
            return

        if path == "/api/month":
            month = self._valid_month(query)
            if not month:
                self._send_json({"error": "invalid month"}, 400)
                return
            a = self._month_aggregate(root, month)
            if a is None:
                self._send_json({"month": month, "exists": False,
                                 "markdown": "", "aggregate": None})
                return
            self._send_json({
                "month": month, "exists": True,
                "total_ms": a["total_active_ms"],
                "active_days": len(a.get("per_day", [])),
                "count": a["session_count"],
                "aggregate": a,
                "markdown": self._render_month_md(a),
            })
            return

        if path == "/api/export":
            self._handle_export(query, root)
            return

        if path == "/api/backup":
            if not os.path.isdir(root):
                self._send_json({"error": "no data"}, 404)
                return
            try:
                data = _backup_zip(root)
                stamp = datetime.date.today().isoformat()
                self._send_blob(data, "application/zip", f"usagemonitor_backup_{stamp}.zip")
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"backup failed: {exc}"}, 500)
            return

        if path == "/api/heatmap":
            # 热力图数据：最近 N 天（默认 84 = 12 周）的每日总活跃 + 24 小时分布
            try:
                n = max(7, min(90, int(query.get("days", ["84"])[0])))
            except ValueError:
                n = 84
            days = _available_days(root)[-n:]
            out = []
            for d in days:
                agg = report.aggregate(d, root)
                out.append({
                    "date": d,
                    "total_ms": agg["total_active_ms"],
                    "hourly_ms": agg.get("hourly_ms", [0] * 24),
                })
            self._send_json({"days": out})
            return

        if path == "/api/log":
            # 统一运行日志 + 最近几天 errors.log（仪表盘「日志」视图）
            try:
                n = max(10, min(500, int(query.get("n", ["200"])[0])))
            except ValueError:
                n = 200
            try:
                import applog  # noqa: PLC0415
                entries = applog.read_recent(root, n)
                err_days = _available_days(root)[-3:]
                errors = applog.read_errors(root, err_days, n)
            except Exception:  # noqa: BLE001
                entries, errors = [], []
            self._send_json({"entries": entries, "errors": errors})
            return

        if path == "/api/groups":
            # 应用分组管理：内置+自定义分组、全部已知应用及其当前分类
            try:
                import classifier as _clf  # noqa: PLC0415
                config = _clf.load_config()
                config["data_root"] = root
                groups = _clf.load_app_groups(root)
                cats = _clf.all_categories(config, groups)
                known = _collect_known_apps(root)
                custom_names = groups.get("app_names", {})
                entries = []
                for exe, name in sorted(known.items(), key=lambda kv: kv[1].lower()):
                    entries.append({
                        "exe": exe,
                        "app": custom_names.get(exe) or name,
                        "category": _clf.classify_category(exe, "", config),
                        "overridden": exe in groups["exe_groups"],
                    })
                self._send_json({
                    "exe_groups": groups["exe_groups"],
                    "custom_categories": groups["custom_categories"],
                    "app_names": groups.get("app_names", {}),
                    "group_meta": groups.get("group_meta", {}),
                    "categories": cats,
                    "apps": entries,
                })
            except Exception:  # noqa: BLE001
                self._send_json({"error": "groups unavailable"}, 500)
            return

        if path == "/api/groups/export":
            # 导出应用分组配置（app_groups.json 完整内容，含分组/显示名/元数据）
            try:
                import classifier as _clf  # noqa: PLC0415
                groups = _clf.load_app_groups(root)
                data = json.dumps(groups, ensure_ascii=False, indent=2).encode("utf-8")
                self._send_blob(data, "application/json; charset=utf-8", "app_groups.json")
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"export failed: {exc}"}, 500)
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        self._send_json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        root = self.server.data_root
        # 同源校验（与 GET 一致）
        if not self._origin_allowed(self.headers):
            self._send_json({"error": "forbidden"}, 403)
            return
        # 访问口令：所有 POST 一致校验
        if path.startswith("/api/") and not self._auth_ok():
            self._send_json({"error": "unauthorized"}, 401)
            return

        # 恢复上传：二进制 zip（Content-Type: application/octet-stream）
        if path == "/api/backup/restore":
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            if length <= 0 or length > 200 * 1024 * 1024:
                self._send_json({"error": "bad body"}, 400)
                return
            data = self.rfile.read(length)
            tmp_dir = None
            try:
                tmp_dir = _safe_extract_zip(root, data)
                result = _merge_restore(root, tmp_dir)
                self._send_json({"ok": True, "days": result["days"], "files": result["files"]})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"restore failed: {exc}"}, 400)
            finally:
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        # 其余 POST 为 JSON 请求体
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            else:
                body = {}
            if not isinstance(body, dict):
                body = {}
        except Exception:  # noqa: BLE001
            body = {}

        try:
            import classifier as _clf  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            self._send_json({"error": "unavailable"}, 500)
            return

        if path == "/api/insights/settings":
            # AI 可选功能设置：开关 + provider 预设 + 自定义端点（API Key 空=保留原值）
            enabled = bool(body.get("enabled"))
            provider = str(body.get("provider") or "").strip()
            base_url = str(body.get("base_url") or "").strip()
            model = str(body.get("model") or "").strip()
            try:
                import insights  # noqa: PLC0415
                preset_map = {p["id"]: p for p in insights.list_provider_presets()}
                preset = preset_map.get(provider.lower(), {})
                eff_base = base_url or preset.get("base_url") or ""
                eff_model = model or preset.get("model") or ""
                if enabled and (not eff_base or not eff_model):
                    self._send_json({
                        "error": "开启 AI 需要可用的 Base URL 和 Model（请选择预设或填写自定义端点）",
                    }, 400)
                    return
                ai = _save_ai_settings(root, self.server.config_path, body)
                self._send_json({"ok": True, "ai": ai, "presets": insights.list_provider_presets()})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"save failed: {exc}"}, 400)
            return

        if path == "/api/groups/set":
            # 设置/移出应用分组：{"exe": "steam.exe", "category": "游戏"}；category 为空=移出（自动分类）
            exe = str(body.get("exe", "")).lower()
            cat = str(body.get("category", "")).strip()
            if not exe:
                self._send_json({"error": "exe required"}, 400)
                return
            groups = _clf.load_app_groups(root)
            if cat:
                groups["exe_groups"][exe] = cat
                # 未知分组自动登记为自定义分组
                if cat not in _clf.all_categories(_clf.load_config(), groups):
                    groups["custom_categories"].append(cat)
            else:
                groups["exe_groups"].pop(exe, None)
            _clf.save_app_groups(groups, root)
            self._send_json({"ok": True})
            return

        if path == "/api/groups/rename":
            # 客制化显示名：{"exe":"steam.exe","display_name":"Steam 自定义名"}；display_name 为空=恢复默认
            exe = str(body.get("exe", "")).lower()
            display_name = str(body.get("display_name", "")).strip()
            if not exe:
                self._send_json({"error": "exe required"}, 400)
                return
            groups = _clf.load_app_groups(root)
            groups.setdefault("app_names", {})
            if display_name:
                groups["app_names"][exe] = display_name
            else:
                groups["app_names"].pop(exe, None)
            _clf.save_app_groups(groups, root)
            self._send_json({"ok": True, "app": display_name})
            return

        if path == "/api/groups/import":
            # 导入应用分组配置：可传 {"groups": {...}} 或直接传 app_groups.json 对象
            data = body.get("groups") if isinstance(body.get("groups"), dict) else body
            if not isinstance(data, dict):
                self._send_json({"error": "invalid groups payload"}, 400)
                return
            groups = {
                "exe_groups": data.get("exe_groups", {}),
                "custom_categories": data.get("custom_categories", []),
                "app_names": data.get("app_names", {}),
                "group_meta": data.get("group_meta", {}),
            }
            _clf.save_app_groups(groups, root)
            self._send_json({"ok": True, "groups": _clf.load_app_groups(root)})
            return

        if path == "/api/groups/add":
            name = str(body.get("name", "")).strip()
            if not name:
                self._send_json({"error": "name required"}, 400)
                return
            groups = _clf.load_app_groups(root)
            cats = _clf.all_categories(_clf.load_config(), groups)
            if name not in cats:
                groups["custom_categories"].append(name)
                _clf.save_app_groups(groups, root)
            self._send_json({"ok": True, "categories": _clf.all_categories(_clf.load_config(), groups)})
            return

        if path == "/api/groups/delete":
            name = str(body.get("name", "")).strip()
            if not name:
                self._send_json({"error": "name required"}, 400)
                return
            groups = _clf.load_app_groups(root)
            groups["custom_categories"] = [x for x in groups["custom_categories"] if x != name]
            groups["exe_groups"] = {k: v for k, v in groups["exe_groups"].items() if v != name}
            _clf.save_app_groups(groups, root)
            self._send_json({"ok": True})
            return

        self._send_json({"error": "method not allowed"}, 405)


def _available_days(data_root: str) -> list[str]:
    """列出数据根目录下所有 YYYY-MM-DD 文件夹（升序）。"""
    if not os.path.isdir(data_root):
        return []
    days = []
    for name in os.listdir(data_root):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            days.append(name)
    return sorted(days)


def _collect_known_apps(data_root: str) -> dict[str, str]:
    """收集"已知应用"：软件清单 exe + 最近 14 天 usage.jsonl 出现的 exe。

    返回 {exe(小写): 显示名}（供分组管理界面列出全部可分组应用）。
    """
    known: dict[str, str] = {}

    def _add(exe: str, name: str = "") -> None:
        exe = (exe or "").lower()
        if not exe:
            return
        if exe not in known or not known[exe]:
            known[exe] = name or exe

    # 1) 软件清单（今日 + 最近几天）
    for day in _available_days(data_root)[-7:]:
        inv_path = os.path.join(data_root, day, "software_inventory.json")
        if not os.path.isfile(inv_path):
            continue
        try:
            with open(inv_path, "r", encoding="utf-8") as fh:
                inv = json.load(fh)
            for app in inv.get("apps", []):
                if isinstance(app, dict):
                    _add(app.get("exe"), app.get("name", ""))
        except Exception:  # noqa: BLE001
            continue
    # 2) 最近 14 天 usage.jsonl
    for day in _available_days(data_root)[-14:]:
        usage_path = os.path.join(data_root, day, "usage.jsonl")
        if not os.path.isfile(usage_path):
            continue
        try:
            with open(usage_path, "r", encoding="utf-8-sig") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if isinstance(rec, dict):
                            _add(rec.get("exe"), rec.get("app", ""))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return known


def _sanitize_csv(csv_text: str) -> str:
    """CSV 注入防护：把以 = + - @ 或 tab 开头的单元格值前缀为 '（防 Excel 公式执行）。"""
    def clean(field: str) -> str:
        f = field.strip()
        if f[:1] in ("=", "+", "-", "@", "\t"):
            return "'" + field
        return field
    out = []
    for line in csv_text.split("\n"):
        if line.startswith("#"):
            out.append(line)
            continue
        out.append(",".join(clean(c) for c in line.split(",")))
    return "\n".join(out)


def create_server(data_root: str, port: int = DEFAULT_PORT,
                  config_path: str | None = None) -> ThreadingHTTPServer:
    """创建仪表盘服务器（绑定 127.0.0.1）。

    config_path 可指定 config.json 路径（测试用）；缺省由 _required_token 用默认路径。
    """
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.data_root = data_root
    server.config_path = config_path
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dashboard.py", description="本地网页仪表盘（仅 127.0.0.1）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version.VERSION}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--data-root", default=None, help="数据根目录（默认取 config.json）")
    parser.add_argument("--config", default=None, help="config.json 路径（默认取 data_root/config.json）")
    args = parser.parse_args(argv)

    try:
        import classifier  # noqa: PLC0415
        cfg = classifier.load_config(args.config)
        data_root = args.data_root or (cfg.get("data_root") or DEFAULT_DATA_ROOT)
    except Exception:  # noqa: BLE001
        data_root = args.data_root or DEFAULT_DATA_ROOT

    server = create_server(data_root, args.port, config_path=args.config)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[dashboard] 数据目录: {data_root}")
    base_domain = url.rstrip("/")
    if bool(_required_token(args.config)):
        print("[dashboard] 访问口令：已开启（config.json 的 dashboard_token）")
    else:
        print("[dashboard] 访问口令：关闭")
    print(f"[dashboard] 仪表盘已启动: {url}  （Ctrl+C 退出，可带 /?view=week|month|settings）")
    try:
        import applog  # noqa: PLC0415
        applog.configure(data_root)
        applog.get_logger("dashboard").info("仪表盘启动 %s (data_root=%s)", url, data_root)
    except Exception:  # noqa: BLE001
        pass
    del base_domain
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] 已退出")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
