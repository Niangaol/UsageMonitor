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
import threading
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
        custom = insights.load_ai_custom(root)
        preset_map = {p["id"]: p for p in
                      insights.list_provider_presets(custom.get("providers"))}
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


# ---------------------------------------------------------------------------
# 软件更新（updater.py 的仪表盘侧状态与辅助）
# ---------------------------------------------------------------------------
_UPDATE_CHECK_CACHE: dict = {"ts": 0.0, "result": None}
_UPDATE_STATE: dict = {
    "state": "idle",           # idle | downloading | ready | error | applying
    "downloaded": 0,
    "total": 0,
    "path": None,
    "latest": "",
    "error": None,
}
_UPDATE_LOCK = threading.Lock()


def _update_api_base(config: dict) -> str | None:
    """config.json 的 update.api_base（空则用默认 GitHub API）。"""
    up = config.get("update") if isinstance(config.get("update"), dict) else {}
    return str(up.get("api_base") or "").strip() or None


def _update_progress(got: int, total: int | None) -> None:
    with _UPDATE_LOCK:
        _UPDATE_STATE["downloaded"] = int(got or 0)
        _UPDATE_STATE["total"] = int(total or 0)


def _run_download(asset: dict, dest: str) -> None:
    try:
        import updater  # noqa: PLC0415 —— 惰性导入
        updater.download(
            str(asset.get("url") or ""), dest,
            expected_size=int(asset.get("size") or 0) or None,
            expected_digest=str(asset.get("digest") or "") or None,
            progress=_update_progress,
        )
        with _UPDATE_LOCK:
            _UPDATE_STATE.update(state="ready", path=dest, error=None)
    except Exception as exc:  # noqa: BLE001 —— 下载失败转为可展示状态
        with _UPDATE_LOCK:
            _UPDATE_STATE.update(state="error", path=None, error=str(exc))


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


# ---------------------------------------------------------------------------
# 页面模板：外置 assets/dashboard.html（ROADMAP §9.2 #1）
# 运行时加载，兼容源码运行（paths.script_dir()/assets）与 PyInstaller 打包
# （sys._MEIPASS/assets，spec 的 datas 已包含该文件）。文件缺失/读取失败时
# 回退到极简内联兜底页，保证仪表盘不白屏（best-effort，不抛异常）。
# 带 mtime/size 缓存：开发时改 HTML 免重启即生效，生产下等价一次性加载。
# ---------------------------------------------------------------------------
_TEMPLATE_NAME = "dashboard.html"
_FALLBACK_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>VibeTrace</title></head><body style="font-family:sans-serif;padding:32px">
<h1>VibeTrace</h1>
<p>页面模板 assets/dashboard.html 缺失或不可读，仪表盘前端无法加载。</p>
<p>数据接口仍可用，例如 <code>/api/dates</code>、<code>/api/day?date=YYYY-MM-DD</code>。</p>
</body></html>
"""

_template_cache: dict = {"path": None, "mtime": None, "size": None, "data": None}


def template_paths() -> list[str]:
    """页面模板候选路径（按优先级）：打包解压目录 > 程序目录 > 本文件目录。"""
    candidates: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(str(meipass), "assets", _TEMPLATE_NAME))
    candidates.append(os.path.join(paths.script_dir(), "assets", _TEMPLATE_NAME))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "assets", _TEMPLATE_NAME))
    out: list[str] = []
    for c in candidates:
        if c not in out:
            out.append(c)
    return out


def load_page_template() -> str:
    """读取页面模板（mtime/size 缓存）；全部候选不可用时返回内联兜底页。"""
    for path in template_paths():
        try:
            st = os.stat(path)
        except OSError:
            continue
        cache = _template_cache
        if (cache["data"] is not None and cache["path"] == path
                and cache["mtime"] == st.st_mtime and cache["size"] == st.st_size):
            return cache["data"]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = fh.read()
        except OSError:
            continue
        _template_cache.update(path=path, mtime=st.st_mtime, size=st.st_size, data=data)
        return data
    return _FALLBACK_TEMPLATE


def _page_html(root: str, auth_enabled: bool) -> str:
    """把数据根目录 / 鉴权标记 / 版本号注入模板（与原内联替换逻辑等价）。"""
    return (load_page_template()
            .replace("DATA_ROOT", json.dumps(root).replace("$", "\\$"))
            .replace("AUTH_FLAG", "true" if auth_enabled else "false")
            .replace("APP_VERSION", version.VERSION))




class Handler(BaseHTTPRequestHandler):
    server_version = "VibeTraceDashboard/4.0"

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
        return report.aggregate_days(days, root), days

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
            html = _page_html(root, auth_enabled)
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
                # 单日聚合失败不拖垮整个趋势（返回 0，时间轴保持连续）
                try:
                    agg = report.aggregate(d, root)
                    out.append({"date": d, "total_ms": agg["total_active_ms"], "count": agg["session_count"]})
                except Exception:  # noqa: BLE001
                    out.append({"date": d, "total_ms": 0, "count": 0})
            self._send_json({"days": out})
            return

        if path == "/api/day":
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            self._send_json({"date": date, "aggregate": report.aggregate(date, root)})
            return

        if path == "/api/ai-sessions":
            # AI 会话深度（默认开启，数据源为本地 AI 会话文件 + 浏览器 Web AI 会话）
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            config = _load_config_for_root(root, self.server.config_path)
            try:
                import ai_sessions  # noqa: PLC0415
                web_visits = None
                try:
                    import browser_history  # noqa: PLC0415
                    bh = browser_history.collect(date, root, config)
                    web_visits = bh.get("visits") or []
                except Exception:  # noqa: BLE001 —— Web 解析失败不影响本地统计
                    web_visits = None
                data = ai_sessions.collect(date, config, web_visits=web_visits)
                self._send_json({"date": date, "ai_sessions": data})
            except Exception as exc:  # noqa: BLE001 —— 会话深度失败不拖垮概览
                self._send_json({"error": f"ai-sessions unavailable: {exc}"}, 500)
            return

        if path == "/api/timeline":
            # Vibe 时间轴回放（v2.5）：三源合并（前台 AI 会话 + AI 会话深度 + Git 提交）
            # 纯派生、best-effort：无数据返回 200 空态；缓存复用 report._agg_cache（usage.jsonl mtime/size 失效）。
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            project = (query.get("project") or [None])[0] or None
            config = _load_config_for_root(root, self.server.config_path)
            try:
                import timeline  # noqa: PLC0415 —— 惰性导入，失败只影响本端点
                data = timeline.build_timeline(date, root, config, project=project)
                self._send_json({"date": date, "events": data.get("events") or [],
                                 "summary": data.get("summary") or {}})
            except Exception as exc:  # noqa: BLE001 —— 时间轴失败不拖垮仪表盘
                self._send_json({"error": f"timeline unavailable: {exc}"}, 500)
            return

        if path in ("/api/trend", "/api/growth"):
            # v2.6 P7：能力成长曲线（周均值快照）。纯派生 + 持久化快照（growth_baseline.json）：
            # 首次/坏档全量现算（自愈），此后增量跳过重算；weeks 为最近 N 周（默认 8，1..52）。
            try:
                weeks = int((query.get("weeks") or ["8"])[0])
            except (TypeError, ValueError):
                weeks = 8
            if not (1 <= weeks <= 52):
                self._send_json({"error": "invalid weeks"}, 400)
                return
            config = _load_config_for_root(root, self.server.config_path)
            try:
                import growth  # noqa: PLC0415 —— 惰性导入，失败只影响本端点
                data = growth.growth_snapshot(root, config)
                data["weeks"] = data.get("weeks") or []
                if weeks < len(data["weeks"]):
                    data["weeks"] = data["weeks"][-weeks:]
                self._send_json(data)
            except Exception as exc:  # noqa: BLE001 —— 成长曲线失败不拖垮仪表盘
                self._send_json({"error": f"trend unavailable: {exc}"}, 500)
            return

        if path in ("/api/ai-compare", "/api/tool-compare"):
            # v2.6 P6：多工具横向对比（纯派生、best-effort）。
            # start/end 必填且全匹配 YYYY-MM-DD；end<start 或范围非 1..90 天 → 400；
            # project 可选模糊过滤；无数据 → 200 空态；内部异常降级 500 不拖垮仪表盘。
            start = (query.get("start") or [""])[0]
            end = (query.get("end") or [""])[0]
            if not _DAY_RE.fullmatch(start) or not _DAY_RE.fullmatch(end):
                self._send_json({"error": "invalid date"}, 400)
                return
            try:
                d0 = datetime.date.fromisoformat(start)
                d1 = datetime.date.fromisoformat(end)
            except ValueError:
                self._send_json({"error": "invalid date"}, 400)
                return
            if d1 < d0 or (d1 - d0).days + 1 > 90:
                self._send_json({"error": "invalid range"}, 400)
                return
            project = (query.get("project") or [None])[0] or None
            config = _load_config_for_root(root, self.server.config_path)
            try:
                import tool_compare  # noqa: PLC0415 —— 惰性导入，失败只影响本端点
                days = [(d0 + datetime.timedelta(days=i)).isoformat()
                        for i in range((d1 - d0).days + 1)]
                data = tool_compare.compare_tools(days, root, config, project=project)
                self._send_json(data)
            except ValueError:
                self._send_json({"error": "invalid range"}, 400)
            except Exception as exc:  # noqa: BLE001 —— 对比失败不拖垮仪表盘
                self._send_json({"error": f"ai-compare unavailable: {exc}"}, 500)
            return

        if path == "/api/query":
            # v2.6 P7：受限模板查询（非 LLM，docs/VIBECODING_IMPLEMENTATION_GUIDE.md §6.2）。
            # 两种入口：?q=<自然语言模板>（如「昨天 opencode 花了多少钱」「本周哪个项目成本最高」）
            # 或指南兼容的 ?tpl=q1&start=...&end=...（显式模板 ID + 日期参数）。
            # 只做固定模板匹配，不接受任意自由文本；参数白名单校验（周期词表 + YYYY-MM-DD），
            # 未命中/非法参数 → 400；内部异常降级 500 不拖垮仪表盘；无数据 → 200 空态 + 文案。
            q_text = (query.get("q") or [""])[0].strip()
            config = _load_config_for_root(root, self.server.config_path)
            try:
                import query as _qmod  # noqa: PLC0415 —— 惰性导入，失败只影响本端点
                if q_text:
                    result = _qmod.run_query(q_text, root, config)
                else:
                    tpl_id = (query.get("tpl") or [""])[0].strip()
                    if not tpl_id:
                        self._send_json({"error": "missing q or tpl"}, 400)
                        return
                    result = _qmod.run_template(tpl_id, query, root, config)
            except Exception as exc:  # noqa: BLE001 —— 查询解析/执行失败不拖垮仪表盘
                self._send_json({"error": f"query unavailable: {exc}"}, 500)
                return
            if not result.get("ok"):
                self._send_json({"error": result.get("error") or "bad query"}, 400)
                return
            self._send_json(result)
            return

        if path == "/api/budget":
            # v2.6 P3：成本预算状态（默认关闭）。纯派生、best-effort：
            # period=daily|monthly；date 用 YYYY-MM-DD（daily）或 YYYY-MM（monthly），
            # 缺省按日期粒度推断；配置未开启/无效/异常 → 200 空态，不拖垮概览。
            period = (query.get("period") or [""])[0].strip().lower()
            if period not in ("", "daily", "monthly"):
                self._send_json({"error": "invalid period"}, 400)
                return
            config = _load_config_for_root(root, self.server.config_path)
            if period == "monthly":
                m = self._valid_month({"month": query.get("date", [""])})
                if not m:
                    self._send_json({"error": "invalid date"}, 400)
                    return
                date = m
            elif period == "daily":
                d = self._valid_date(query)
                if not d:
                    self._send_json({"error": "invalid date"}, 400)
                    return
                date = d
            else:
                d = self._valid_date(query)
                if d:
                    date, period = d, "daily"
                else:
                    m = self._valid_month({"month": query.get("date", [""])})
                    if not m:
                        self._send_json({"error": "invalid date"}, 400)
                        return
                    date, period = m, "monthly"
            try:
                import budget  # noqa: PLC0415 —— 惰性导入，失败只影响本端点
                self._send_json(budget.budget_status(date, root, config, period=period))
            except Exception as exc:  # noqa: BLE001 —— 预算异常降级为关闭态，不 500 拖垮概览
                self._send_json({"error": f"budget unavailable: {exc}"}, 500)
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
                behavior = insights.behavior_insights(agg, config)
                persona = insights.persona_insights(agg, config)
                time_saved = insights.time_saved_insights(agg, config)
                import git_insights  # noqa: PLC0415 —— 只读本地 Git 分析
                git = git_insights.git_insights(config, date)
                # v2.5：AI 会话质量卡片（纯离线派生；失败不影响既有洞察）
                ai_quality = []
                try:
                    import ai_sessions as _ai_mod  # noqa: PLC0415
                    ai_quality = insights.conversation_quality_insights(
                        _ai_mod.collect(date, config))
                except Exception:  # noqa: BLE001
                    ai_quality = []
                self._send_json({
                    "date": date, "rules": rules,
                    "ai_enabled": ai_enabled, "ai": ai,
                    "behavior": behavior,
                    "persona": persona,
                    "time_saved": time_saved,
                    "git": git,
                    "ai_quality": ai_quality,
                })
            except Exception as exc:  # noqa: BLE001 —— 洞察失败不拖垮仪表盘
                self._send_json({"error": f"insights unavailable: {exc}"}, 500)
            return

        if path == "/api/insights/settings":
            # AI 设置（可选功能开关 + provider 预设 + 自定义端点）；预设含 ai_custom.json 自定义项
            config = _load_config_for_root(root, self.server.config_path)
            try:
                import insights  # noqa: PLC0415
                custom = insights.load_ai_custom(root)
                ins_cfg = config.get("insights") if isinstance(config.get("insights"), dict) else {}
                ai_cfg = ins_cfg.get("ai") if isinstance(ins_cfg.get("ai"), dict) else {}
                ai_enabled = bool(ins_cfg.get("enabled", True) and ai_cfg.get("enabled"))
                self._send_json({
                    "ai": _ai_settings_view(config),
                    "ai_enabled": ai_enabled,
                    "presets": insights.list_provider_presets(custom.get("providers")),
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

        if path == "/api/insights/ollama/models":
            # 读取 Ollama 本地模型列表（供设置页下拉/校验；失败返回可展示错误）
            config = _load_config_for_root(root, self.server.config_path)
            try:
                import insights  # noqa: PLC0415
                ins_cfg = config.get("insights") if isinstance(config.get("insights"), dict) else {}
                ai_cfg = ins_cfg.get("ai") if isinstance(ins_cfg.get("ai"), dict) else {}
                base_url = str(ai_cfg.get("base_url") or "").strip()
                models = insights.ollama_models(base_url or None)
                self._send_json({"models": models, "error": None})
            except Exception as exc:  # noqa: BLE001 —— 连接失败不拖垮设置页
                self._send_json({"models": [], "error": str(exc)})
            return

        if path == "/api/ai/module":
            # AI 洞察客制化模块（自定义 provider + 提示词定制），持久化于 <root>/ai_custom.json
            try:
                import insights  # noqa: PLC0415
                custom = insights.load_ai_custom(root)
                self._send_json({
                    "custom": custom,
                    "sections": insights.PROMPT_SECTION_ITEMS,
                    "presets": insights.list_provider_presets(custom.get("providers")),
                })
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"ai module unavailable: {exc}"}, 500)
            return

        if path == "/api/ai/module/export":
            # 导出 AI 客制化模块配置（ai_custom.json 完整内容）
            try:
                import insights  # noqa: PLC0415
                custom = insights.load_ai_custom(root)
                data = json.dumps(custom, ensure_ascii=False, indent=2).encode("utf-8")
                self._send_blob(data, "application/json; charset=utf-8", "ai_custom.json")
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"ai module export failed: {exc}"}, 500)
            return

        if path == "/api/pricing":
            # AI 模型定价：内置默认计数 + 用户 <root>/ai_pricing.json 覆盖（USD/百万 Token）
            try:
                import ai_sessions  # noqa: PLC0415
                builtin = dict(ai_sessions._DEFAULT_PRICING)
                custom: dict = {}
                fp = os.path.join(root, "ai_pricing.json")
                if os.path.isfile(fp):
                    try:
                        with open(fp, "r", encoding="utf-8-sig") as fh:
                            loaded = json.load(fh)
                        if isinstance(loaded, dict):
                            custom = loaded
                    except Exception:  # noqa: BLE001
                        custom = {}
                self._send_json({
                    "builtin_count": len(builtin),
                    "builtin": {k: list(v) for k, v in sorted(builtin.items())},
                    "custom": custom,
                })
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"pricing read failed: {exc}"}, 500)
            return

        if path == "/api/update/check":
            # 新版本检测（GitHub Releases API，结果缓存 5 分钟）
            config = _load_config_for_root(root, self.server.config_path)
            try:
                import updater  # noqa: PLC0415 —— 惰性导入
                now = time.monotonic()
                if now - _UPDATE_CHECK_CACHE["ts"] > 300 or _UPDATE_CHECK_CACHE["result"] is None:
                    _UPDATE_CHECK_CACHE["result"] = updater.check_for_update(
                        api_base=_update_api_base(config), timeout=8.0)
                    _UPDATE_CHECK_CACHE["ts"] = now
                self._send_json(dict(_UPDATE_CHECK_CACHE["result"]))
            except Exception as exc:  # noqa: BLE001
                self._send_json({
                    "current": version.VERSION, "latest": "", "has_update": False,
                    "notes": "", "published_at": "", "url": "", "asset": None,
                    "error": f"检查更新失败：{exc}",
                })
            return

        if path == "/api/update/status":
            # 更新下载/应用状态（前端轮询）
            try:
                import updater  # noqa: PLC0415
                frozen = updater.is_frozen()
            except Exception:  # noqa: BLE001
                frozen = False
            with _UPDATE_LOCK:
                state = dict(_UPDATE_STATE)
            self._send_json({
                "current": version.VERSION,
                "frozen": frozen,
                "dev": not frozen,
                "state": state["state"],
                "downloaded": state["downloaded"],
                "total": state["total"],
                "latest": state["latest"],
                "error": state["error"],
            })
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
                # 单日聚合失败不拖垮热力图/总活跃（以 0 兜底，时间轴保持连续）
                try:
                    agg = report.aggregate(d, root)
                    out.append({
                        "date": d,
                        "total_ms": agg["total_active_ms"],
                        "hourly_ms": agg.get("hourly_ms", [0] * 24),
                    })
                except Exception:  # noqa: BLE001
                    out.append({"date": d, "total_ms": 0, "hourly_ms": [0] * 24})
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
                groups = _clf.sanitize_groups(config, groups)  # 剔除孤儿分组（如遗留的 AI工具）
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
                custom = insights.load_ai_custom(root)
                preset_map = {p["id"]: p for p in
                              insights.list_provider_presets(custom.get("providers"))}
                preset = preset_map.get(provider.lower(), {})
                eff_base = base_url or preset.get("base_url") or ""
                eff_model = model or preset.get("model") or ""
                if enabled and (not eff_base or not eff_model):
                    self._send_json({
                        "error": "开启 AI 需要可用的 Base URL 和 Model（请选择预设或填写自定义端点）",
                    }, 400)
                    return
                ai = _save_ai_settings(root, self.server.config_path, body)
                self._send_json({
                    "ok": True, "ai": ai,
                    "presets": insights.list_provider_presets(custom.get("providers")),
                })
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"save failed: {exc}"}, 400)
            return

        if path == "/api/pricing":
            # 保存用户模型定价覆盖到 <root>/ai_pricing.json（{model:[in,out]} 或 {model:{input,output}}）
            data = body.get("pricing") if isinstance(body.get("pricing"), dict) else body
            clean: dict = {}
            for k, v in (data or {}).items():
                if isinstance(v, (list, tuple)) and len(v) >= 2:
                    try:
                        clean[str(k)] = [float(v[0]), float(v[1])]
                    except (TypeError, ValueError):
                        pass
                elif isinstance(v, dict) and "input" in v and "output" in v:
                    try:
                        clean[str(k)] = {"input": float(v["input"]), "output": float(v["output"])}
                    except (TypeError, ValueError):
                        pass
            try:
                fp = os.path.join(root, "ai_pricing.json")
                tmp = fp + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(clean, fh, ensure_ascii=False, indent=2)
                os.replace(tmp, fp)
                self._send_json({"ok": True, "count": len(clean)})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"pricing save failed: {exc}"}, 400)
            return

        if path == "/api/ai/module":
            # 保存 AI 洞察客制化模块（providers + prompt 定制）
            if not (isinstance(body.get("providers"), list)
                    or isinstance(body.get("prompt"), dict)):
                self._send_json({"error": "invalid ai module payload"}, 400)
                return
            try:
                import insights  # noqa: PLC0415
                custom = insights.save_ai_custom(root, body)
                self._send_json({
                    "ok": True, "custom": custom,
                    "sections": insights.PROMPT_SECTION_ITEMS,
                    "presets": insights.list_provider_presets(custom.get("providers")),
                })
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"save failed: {exc}"}, 400)
            return

        if path == "/api/ai/module/import":
            # 导入 AI 洞察客制化模块：可传 {"custom": {...}} 或直接传 ai_custom.json 对象
            data = body.get("custom") if isinstance(body.get("custom"), dict) else body
            if not (isinstance(data, dict)
                    and (isinstance(data.get("providers"), list)
                         or isinstance(data.get("prompt"), dict))):
                self._send_json({"error": "invalid ai module payload"}, 400)
                return
            try:
                import insights  # noqa: PLC0415
                custom = insights.save_ai_custom(root, data)
                self._send_json({
                    "ok": True, "custom": custom,
                    "sections": insights.PROMPT_SECTION_ITEMS,
                    "presets": insights.list_provider_presets(custom.get("providers")),
                })
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"import failed: {exc}"}, 400)
            return

        if path == "/api/update/download":
            # 下载最新版 exe 到 %TEMP%（后台线程，前端轮询 /api/update/status）
            config = _load_config_for_root(root, self.server.config_path)
            with _UPDATE_LOCK:
                if _UPDATE_STATE["state"] == "downloading":
                    self._send_json({"error": "正在下载中，请稍候"}, 409)
                    return
            try:
                import updater  # noqa: PLC0415
                result = updater.check_for_update(api_base=_update_api_base(config), timeout=8.0)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"检查更新失败：{exc}"}, 400)
                return
            if result.get("error"):
                self._send_json({"error": result["error"]}, 400)
                return
            if not result.get("has_update") or not result.get("asset"):
                self._send_json({"error": "已是最新版本，无需下载"}, 400)
                return
            asset = result["asset"]
            dest_dir = os.path.join(tempfile.gettempdir(), "usagemonitor-update")
            dest = os.path.join(dest_dir, f"VibeTrace-{result['latest']}.exe")
            with _UPDATE_LOCK:
                _UPDATE_STATE.update(state="downloading", downloaded=0, total=0,
                                     path=None, error=None, latest=str(result["latest"]))
            threading.Thread(target=_run_download, args=(asset, dest), daemon=True).start()
            self._send_json({"ok": True})
            return

        if path == "/api/update/apply":
            # 应用已下载的更新：写信号让 monitor 优雅退出，启动更新脚本替换 exe 并重启。
            # dryrun=true（仅测试/预览）只生成脚本不执行。
            dryrun = bool(body.get("dryrun"))
            with _UPDATE_LOCK:
                state = dict(_UPDATE_STATE)
            if state.get("state") != "ready" or not state.get("path"):
                self._send_json({"error": "没有已下载的更新（请先下载）"}, 400)
                return
            try:
                import updater  # noqa: PLC0415
                if not dryrun:
                    updater.request_update(root)  # 通知 monitor 优雅退出
                result = updater.apply_update(state["path"], dry_run=dryrun)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"应用更新失败：{exc}"}, 400)
                return
            if not dryrun:
                with _UPDATE_LOCK:
                    _UPDATE_STATE.update(state="applying")
                # 响应发出后关闭仪表盘服务（更新脚本会等待全部进程退出后替换 exe）
                threading.Timer(2.5, lambda: self.server.shutdown()).start()
            self._send_json({"ok": True, "dry_run": dryrun, "script": result.get("script", "")})
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
            # 导入时同样剔除孤儿分组，保证分组系统自洽
            config = _clf.load_config()
            config["data_root"] = root
            groups = _clf.sanitize_groups(config, groups)
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


# ---------------------------------------------------------------------------
# _available_days 结果缓存（按 data_root 分桶）
# 范式同 report._agg_cache / classifier.load_config：目录 mtime 变化（新增/
# 删除日期文件夹必更新父目录 mtime）或超过 TTL（5s，同 _aliases_cache）时
# 重扫，避免单次请求内多次调用（/api/days + /api/dates、_collect_known_apps
# 两次切片）与长历史安装（数百日期文件夹）重复 os.listdir。返回浅拷贝，
# 避免调用方修改（如 /api/dates 外部消费）污染缓存。
# ---------------------------------------------------------------------------
_days_cache: dict[str, dict] = {}  # normcase(abs root) -> {"mtime", "ts", "data"}
_DAYS_TTL = 5.0  # 秒：mtime 未变化时也强制重扫的最长时间


def _days_cache_key(data_root: str) -> str:
    """规范化数据根目录为缓存键（相对/绝对归一化，Windows 忽略大小写）。"""
    return os.path.normcase(os.path.abspath(data_root))


def _days_mtime(data_root: str) -> float:
    """取数据根目录 mtime；目录不存在返回 0.0（之后重建 mtime 变化即可感知）。"""
    try:
        return os.path.getmtime(data_root)
    except OSError:
        return 0.0


def invalidate_days_cache(data_root: str | None = None) -> None:
    """强制丢弃日期列表缓存；data_root 为空时清空全部。供写盘场景与测试调用。"""
    global _days_cache
    if data_root is None:
        _days_cache.clear()
    else:
        _days_cache.pop(_days_cache_key(data_root), None)


def _available_days(data_root: str) -> list[str]:
    """列出数据根目录下所有 YYYY-MM-DD 文件夹（升序），带 mtime/TTL 缓存。

    目录 mtime 变化或距上次扫描超过 _DAYS_TTL 秒时重扫；否则返回缓存浅拷贝。
    新增日期文件夹会更新目录 mtime，因此缓存失效后可感知新数据。
    """
    key = _days_cache_key(data_root)
    now = time.monotonic()
    entry = _days_cache.get(key)
    if entry is not None and now - entry["ts"] < _DAYS_TTL:
        if _days_mtime(data_root) == entry["mtime"]:
            return list(entry["data"])
        _days_cache.pop(key, None)  # mtime 变化：丢弃旧缓存，走下方重扫

    days: list[str] = []
    if os.path.isdir(data_root):
        for name in os.listdir(data_root):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
                days.append(name)
    days.sort()
    _days_cache[key] = {"mtime": _days_mtime(data_root), "ts": now, "data": days}
    return list(days)


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
