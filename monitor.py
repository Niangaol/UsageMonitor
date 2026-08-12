# -*- coding: utf-8 -*-
"""monitor.py — 电脑使用情况监控守护进程。

- 每 5 秒轮询前台窗口（Win32），会话状态变化才写一条（静止零写入）；
- 空闲/锁屏不计时（会话在最后一次输入处截断）；
- vibe coding 进程树识别（终端里运行的 opencode / pi agent / claude 等）；
- 微信/QQ/钉钉联系人解析、浏览器 视频/代码/学习 分类；
- 跨天自动生成前一日 report.md / report.csv，并按保留天数清理过期文件夹；
- 支持 --test N（跑 N 秒后退出打印汇总）、--tray（托盘图标）、--foreground。

纯标准库实现，pythonw 静默运行兼容。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import classifier  # noqa: E402
import report  # noqa: E402
import version  # noqa: E402
import win32core  # noqa: E402

_pause = threading.Event()      # 暂停监控（托盘使用）
stop_event = threading.Event()  # 停止守护（托盘退出时置位）

DEFAULT_POLL_INTERVAL = 5
DEFAULT_IDLE_THRESHOLD = 180
DEFAULT_RETENTION = 90


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def load_config(config_path: str | None = None) -> dict:
    """读取 config.json（缺失时 classifier 使用默认配置）。"""
    return classifier.load_config(config_path)


def set_paused(paused: bool) -> None:
    """暂停/恢复监控（托盘菜单调用）。"""
    if paused:
        _pause.set()
    else:
        _pause.clear()


def is_paused() -> bool:
    return _pause.is_set()


# ---------------------------------------------------------------------------
# 日志与写入
# ---------------------------------------------------------------------------
def _log_error(data_root: str, day_str: str, exc: BaseException, context: str = "") -> None:
    """把错误写入当日 errors.log（守护进程静默运行，不打印）。"""
    try:
        day_dir = os.path.join(data_root, day_str or datetime.date.today().isoformat())
        os.makedirs(day_dir, exist_ok=True)
        with open(os.path.join(day_dir, "errors.log"), "a", encoding="utf-8") as fh:
            fh.write(
                f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {context}: {exc}\n"
            )
            # 仅当确实有活动异常时写堆栈（主动记录如 single-instance 拒绝不写）
            if sys.exc_info()[0] is not None:
                fh.write(traceback.format_exc() + "\n")
    except Exception:  # noqa: BLE001
        pass


def append_session_record(day_str: str, record: dict, data_root: str) -> None:
    """JSON Lines 追加写一条会话记录到 当日文件夹/usage.jsonl。"""
    day_dir = os.path.join(data_root, day_str)
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, "usage.jsonl"), "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _round_sec(dt: datetime.datetime) -> datetime.datetime:
    """四舍五入到整秒（写入 ISO 的 start/end 与 duration_ms 严格自洽）。"""
    return (dt + datetime.timedelta(milliseconds=500)).replace(microsecond=0)


def make_record(session: dict, end_dt: datetime.datetime) -> dict | None:
    """会话 dict -> JSON 记录（时长 <= 0 返回 None）。

    start/end 四舍五入到整秒后写入，duration_ms 用同一组值计算，
    保证文件内 duration_ms == end - start 严格相等。
    """
    start_r = _round_sec(session["start"])
    end_r = _round_sec(end_dt)
    duration_ms = int((end_r - start_r).total_seconds() * 1000)
    if duration_ms <= 0:
        return None
    rec = {
        "start": start_r.isoformat(),
        "end": end_r.isoformat(),
        "duration_ms": duration_ms,
        "exe": session["exe"],
        "app": session["app"],
        "title": session["title"],
        "category": session["category"],
        "contact": session["contact"],
        "ai_tool": session["ai_tool"],
        "active": True,
    }
    if session.get("browser_category"):
        rec["browser_category"] = session["browser_category"]
    # 监控维度细化字段（仅在存在时写入）
    for key in ("subcategory", "term_tool", "window_state", "url"):
        if session.get(key):
            rec[key] = session[key]
    return rec


def _close_session(session: dict, end_dt: datetime.datetime, data_root: str,
                   day_str: str, config: dict | None = None) -> dict | None:
    """关闭会话并写入；返回记录（未写入时返回 None）。

    浏览器会话落盘时尽力关联 URL（会话 ↔ 历史时间重叠），失败不影响写入。
    """
    # 浏览器会话：尝试关联当时访问的 URL（维度细化，best-effort）
    if config is not None and session.get("exe") in config.get("browser_exes", []) \
            and not session.get("url"):
        try:
            import browser_history  # noqa: PLC0415 —— 惰性导入
            url = browser_history.find_url_for_session(
                session["start"], end_dt, data_root, config)
            if url:
                session["url"] = url
        except Exception:  # noqa: BLE001 —— URL 关联失败不影响会话写入
            pass
    rec = make_record(session, end_dt)
    if rec is None:
        return None
    append_session_record(day_str, rec, data_root)
    return rec


# ---------------------------------------------------------------------------
# 跨天/清理
# ---------------------------------------------------------------------------
def retention_cleanup(data_root: str, retention_days: int) -> None:
    """删除超过保留期的日期文件夹（仅匹配 YYYY-MM-DD 目录名，避免误删）。"""
    today = datetime.date.today()
    if not os.path.isdir(data_root):
        return
    for name in os.listdir(data_root):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            continue
        try:
            folder_date = datetime.date.fromisoformat(name)
        except ValueError:
            continue
        if (today - folder_date).days > retention_days:
            try:
                shutil.rmtree(os.path.join(data_root, name))
            except OSError:
                pass


def _refresh_inventory(data_root: str, config: dict) -> None:
    """守护启动/跨天时刷新当日软件清单（文档 §6.1：每次启动刷新一次，含新软件补录）。

    全链路扫描约 25ms，可忽略；失败只记日志不影响守护。
    """
    try:
        import inventory  # noqa: PLC0415 —— 惰性导入，避免拖慢启动
        day = datetime.date.today().isoformat()
        inventory.write_inventory(os.path.join(data_root, day), config)
    except Exception as exc:  # noqa: BLE001
        _log_error(data_root, datetime.date.today().isoformat(), exc, "inventory refresh")


def finalize_day(day_str: str, data_root: str, retention_days: int) -> None:
    """生成某天 report.md/report.csv；顺带做一次保留期清理。"""
    try:
        report.generate_day_report(day_str, data_root)
    except Exception as exc:  # noqa: BLE001
        _log_error(data_root, day_str, exc, f"finalize report {day_str}")
    try:
        retention_cleanup(data_root, retention_days)
    except Exception as exc:  # noqa: BLE001
        _log_error(data_root, day_str, exc, "retention cleanup")


# ---------------------------------------------------------------------------
# 会话采集
# ---------------------------------------------------------------------------
def _open_session(fg, config: dict, processes: dict, now: datetime.datetime) -> dict:
    """根据前台窗口信息构建会话候选（含分类/联系人/AI工具识别）。"""
    title = fg.title
    hidden = classifier.is_blacklisted_title(title, config)
    if hidden:
        title = "[已隐藏]"
    app = classifier.resolve_app_name(fg.exe, config)
    category = classifier.classify_category(fg.exe, title, config)

    browser_category = None
    contact = None
    if not hidden:  # 标题已隐藏时不做基于标题的深度分类（隐私优先）
        if fg.exe in config.get("browser_exes", []):
            browser_category = classifier.classify_browser(title, config)
        if fg.exe in config.get("social_apps", {}):
            contact = classifier.extract_contact(fg.exe, title, config)

    ai_tool = None
    # 终端 / 编辑器集成终端：需要完整进程树才能识别里面跑的 AI CLI 工具
    if (fg.exe in config.get("terminal_exes", [])
            or fg.exe in config.get("editor_exes", [])):
        ai_tool = classifier.detect_ai_tool(fg.pid, processes, title, config)
    else:
        # 自有窗口的 AI 工具（ChatGPT/Cursor/Windsurf 桌面版等）：
        # 把前台进程自身交给识别器，保证 ai_tool 字段不遗漏
        ai_tool = classifier.detect_ai_tool(
            fg.pid,
            {fg.pid: types.SimpleNamespace(exe=fg.exe, ppid=0, pid=fg.pid)},
            title, config,
        )

    # 进程树/标题已识别出 AI 工具时，类别统一归为 AI编程（与终端场景口径一致）：
    # 例如 VS Code 集成终端里跑 opencode，窗口标题不含关键词，但 ai_tool 已命中。
    if ai_tool is not None and category != "AI编程":
        category = "AI编程"

    # 维度细化：窗口状态 / 二级子分类 / 终端 TUI 工具
    window_state = win32core.get_window_state(fg.hwnd)
    subcategory = browser_category if category == "浏览器" and browser_category else None
    if subcategory is None:
        subcategory = classifier.classify_subcategory(category, fg.exe, title, config)
    term_tool = None
    if ai_tool is None and (fg.exe in config.get("terminal_exes", [])
                            or fg.exe in config.get("editor_exes", [])):
        term_tool = classifier.detect_term_tool(title, config)

    return {
        "start": now,
        "exe": fg.exe,
        "app": app,
        "title": title,
        "category": category,
        "contact": contact,
        "ai_tool": ai_tool,
        "browser_category": browser_category,
        "subcategory": subcategory,
        "window_state": window_state,
        "term_tool": term_tool,
        "last_active": now,
        "signature": (fg.exe, title, category, contact, ai_tool, browser_category),
    }


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------
def run_daemon(config: dict, test_seconds: int | None = None, verbose: bool = False) -> list[dict]:
    """守护主循环。test_seconds 非空时跑 N 秒后返回本次写入的记录列表。

    仅在 (exe, 标题, 类别, 联系人, AI工具, 浏览器分类) 变化时写一条；静止零写入。
    """
    data_root = config.get("data_root") or "D:\\电脑使用情况监控"
    poll_interval = max(1, int(config.get("poll_interval_s", DEFAULT_POLL_INTERVAL)))
    idle_threshold = max(0, int(config.get("idle_threshold_s", DEFAULT_IDLE_THRESHOLD)))
    retention = max(0, int(config.get("retention_days", DEFAULT_RETENTION)))
    os.makedirs(data_root, exist_ok=True)

    session: dict | None = None
    current_day: str | None = None
    test_records: list[dict] = []
    start_mono = time.monotonic()

    while True:
        try:
            now = datetime.datetime.now()
            day_str = now.strftime("%Y-%m-%d")

            # 跨天 / 首次启动
            if current_day is None:
                current_day = day_str
                yesterday = (now.date() - datetime.timedelta(days=1)).isoformat()
                if not os.path.isfile(os.path.join(data_root, yesterday, "report.md")):
                    finalize_day(yesterday, data_root, retention)
                _refresh_inventory(data_root, config)  # 启动时刷新今日软件清单
            elif day_str != current_day:
                if session is not None:
                    rec = _close_session(session, session["last_active"], data_root, current_day, config)
                    if rec and test_seconds:
                        test_records.append(rec)
                    session = None
                finalize_day(current_day, data_root, retention)
                _refresh_inventory(data_root, config)  # 跨天：新一天清单
                current_day = day_str

            # 暂停
            if _pause.is_set():
                if session is not None:
                    rec = _close_session(session, session["last_active"], data_root, current_day, config)
                    if rec and test_seconds:
                        test_records.append(rec)
                    session = None
                _pause.wait(poll_interval)
                continue

            idle_s = win32core.idle_seconds()

            # 空闲：在最后一次输入处截断会话
            if session is not None and idle_s >= idle_threshold:
                rec = _close_session(session, session["last_active"], data_root, current_day, config)
                if rec and test_seconds:
                    test_records.append(rec)
                session = None

            fg = win32core.get_foreground_info()
            if fg is None:
                if session is not None:
                    end = session["last_active"] if idle_s >= idle_threshold else now
                    rec = _close_session(session, end, data_root, current_day, config)
                    if rec and test_seconds:
                        test_records.append(rec)
                    session = None
            else:
                processes: dict = {}
                # 终端 / 编辑器集成终端：需要进程树才能识别里面跑的 AI CLI 工具
                # （编辑器如 VS Code 的集成终端里跑 opencode 同样可识别）
                if (fg.exe in config.get("terminal_exes", [])
                        or fg.exe in config.get("editor_exes", [])):
                    processes = win32core.enum_processes()
                cand = _open_session(fg, config, processes, now)

                if session is not None and cand["signature"] != session["signature"]:
                    end = session["last_active"] if idle_s >= idle_threshold else now
                    rec = _close_session(session, end, data_root, current_day, config)
                    if rec and test_seconds:
                        test_records.append(rec)
                    if verbose and rec:
                        print(f"[monitor] {rec['start']} {rec['app']} {rec['duration_ms']}ms")
                    session = None

                if session is None and idle_s < idle_threshold:
                    session = cand
                elif session is not None and idle_s < idle_threshold:
                    session["last_active"] = now

            if test_seconds is not None and (time.monotonic() - start_mono) >= test_seconds:
                break
            if stop_event.is_set():
                break
            time.sleep(poll_interval)

        except Exception as exc:  # noqa: BLE001 —— 单次轮询失败不中断守护
            _log_error(data_root, current_day or day_str, exc, "poll")
            time.sleep(poll_interval)

    if session is not None:
        rec = _close_session(session, session["last_active"], data_root, current_day, config)
        if rec and test_seconds:
            test_records.append(rec)
    return test_records


# ---------------------------------------------------------------------------
# 今日概览（托盘使用）
# ---------------------------------------------------------------------------
def overview_text(data_root: str | None = None) -> str:
    """生成"今日概览"文本：按应用聚合今天已记录的活跃时长。"""
    root = data_root or (load_config().get("data_root") or "D:\\电脑使用情况监控")
    today = datetime.date.today().isoformat()
    by_app: dict[str, int] = {}
    for s in report.read_sessions(today, root):
        dur = int(s.get("duration_ms") or 0)
        if not s.get("active", True):
            continue
        app = s.get("app") or s.get("exe") or "未知"
        by_app[app] = by_app.get(app, 0) + dur
    lines = [f"今日概览 {today}", f"总活跃：{sum(by_app.values()) // 60000} 分钟"]
    for app, ms in sorted(by_app.items(), key=lambda kv: -kv[1])[:8]:
        lines.append(f"  {app}  {ms // 60000} 分钟")
    if not by_app:
        lines.append("  （暂无数据）")
    return "\n".join(lines)


def open_dashboard(data_root: str, port: int = 8765, view: str | None = None) -> None:
    """打开本地仪表盘（幂等）。

    端口未被占用 -> 在后台线程启动 dashboard 服务器并打开浏览器；
    端口已被占用（已有实例）-> 直接打开浏览器。
    view 指定初始视图（overview / report / detail），为空用默认视图。
    """
    import socket
    import webbrowser

    url = f"http://127.0.0.1:{port}/"
    if view:
        url += f"?view={view}"
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        # 已有 dashboard 实例在跑
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        sock.close()

    def _serve() -> None:
        try:
            import dashboard  # noqa: PLC0415 —— 惰性导入
            server = dashboard.create_server(data_root, port)
            server.serve_forever()
        except Exception as exc:  # noqa: BLE001
            _log_error(data_root, datetime.date.today().isoformat(), exc, "dashboard serve")

    threading.Thread(target=_serve, daemon=True).start()
    time.sleep(0.4)  # 等服务器绑定端口
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
# exe 多工具分派：单文件 UsageMonitor.exe 内同时提供 monitor / report / dashboard
# 三个子工具（PyInstaller 打包入口是 monitor.py，通过参数前缀分派到对应模块）。
_REPORT_FLAGS = {
    "--day", "--today", "--week", "--month", "--reclassify",
    "--json", "--write", "--full",
}
_DASHBOARD_FLAGS = {"--dashboard", "--port", "--open"}


def _dispatch(argv: list[str] | None) -> str | None:
    """检测参数是否属于 report / dashboard 子工具；命中返回子工具名。"""
    args_list = list(argv) if argv is not None else sys.argv[1:]
    if not args_list:
        return None
    first = args_list[0]
    if first == "--report":
        return "report"
    if first == "--dashboard":
        return "dashboard"
    # 兼容直接传 report 专属参数（python monitor.py --today 也走 report）
    if first in _REPORT_FLAGS:
        return "report"
    if first in _DASHBOARD_FLAGS:
        return "dashboard"
    return None


def main(argv: list[str] | None = None) -> int:
    sub = _dispatch(argv)
    if sub == "report":
        args_list = list(argv) if argv is not None else sys.argv[1:]
        if args_list and args_list[0] == "--report":
            args_list = args_list[1:]
        return report.main(args_list)
    if sub == "dashboard":
        args_list = list(argv) if argv is not None else sys.argv[1:]
        if args_list and args_list[0] == "--dashboard":
            args_list = args_list[1:]
        import dashboard  # noqa: PLC0415
        return dashboard.main(args_list)

    parser = argparse.ArgumentParser(prog="monitor.py", description="电脑使用情况监控守护进程")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version.VERSION}")
    parser.add_argument("--test", type=int, metavar="N", help="测试模式：运行 N 秒后退出并打印汇总")
    parser.add_argument("--tray", action="store_true", help="启用托盘图标（不可用时降级为静默守护）")
    parser.add_argument("--foreground", action="store_true", help="前台模式：把写入记录打印到控制台")
    parser.add_argument("--config", default=None, help="config.json 路径")
    parser.add_argument("--data-root", default=None, help="数据根目录（默认取 config.json）")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.data_root:
        config["data_root"] = args.data_root
    data_root = config.get("data_root") or "D:\\电脑使用情况监控"
    os.makedirs(data_root, exist_ok=True)

    # 单实例保护：守护模式（非 --test）下已有实例在运行则直接退出，
    # 避免多个 monitor 同时写 usage.jsonl 造成重复记录。
    if not args.test and not win32core.acquire_single_instance("UsageMonitorMutex"):
        _log_error(data_root, datetime.date.today().isoformat(),
                   RuntimeError("another instance is running"), "single-instance")
        return 0

    if args.test:
        records = run_daemon(config, test_seconds=max(1, args.test), verbose=args.foreground)
        print(f"--test 结束：本次运行写入 {len(records)} 条会话记录")
        by_app: dict[str, int] = {}
        for r in records:
            app = r.get("app") or r.get("exe") or "未知"
            by_app[app] = by_app.get(app, 0) + r["duration_ms"]
        for app, ms in sorted(by_app.items(), key=lambda kv: -kv[1]):
            print(f"  {app}: {ms // 1000}s")
        return 0

    # 无参数（双击 exe / 默认运行）时自动启用托盘：桌面环境不可用时降级静默守护。
    # 显式 --foreground 保留纯控制台行为。
    use_tray = args.tray or (not args.foreground)
    if use_tray:
        try:
            import tray  # noqa: PLC0415 —— 惰性导入，托盘不可用时降级
            thread = threading.Thread(
                target=run_daemon, args=(config,), daemon=True
            )
            thread.start()
            tray.run(
                config,
                overview_fn=lambda: overview_text(data_root),
                set_paused_fn=set_paused,
                is_paused_fn=is_paused,
                open_dashboard_fn=lambda view=None: open_dashboard(data_root, view=view),
                stop_event=stop_event,
            )
            return 0
        except Exception as exc:  # noqa: BLE001
            _log_error(data_root, datetime.date.today().isoformat(), exc, "tray init (degraded)")

    run_daemon(config, verbose=args.foreground)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
