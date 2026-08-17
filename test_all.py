# -*- coding: utf-8 -*-
"""test_all.py — 电脑使用情况监控 完整集成测试（无头、确定性）。

对应《项目需求与开发文档》§14 测试方案，通过猴子补丁模拟前台窗口/空闲/进程树，
覆盖：切换计时、空闲不计时、微信联系人、浏览器分类、终端 AI 工具、跨天轮转、
隐私黑名单、暂停/继续、保留清理、AI 误伤防护、静止零写入、报表管线、清单扫描。

运行：python test_all.py   （全部通过打印 ALL TESTS PASSED）
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import os
import shutil
import sys
import threading
import time
import types

sys.path.insert(0, r"D:\电脑使用情况监控")

# 原地调整控制台编码（不要用 TextIOWrapper 换包装——旧包装被 GC 会关闭底层 buffer）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import win32core  # noqa: E402
import monitor  # noqa: E402
import classifier  # noqa: E402
import report  # noqa: E402
import inventory  # noqa: E402
import insights  # noqa: E402
import sqlite_store  # noqa: E402
import ai_sessions  # noqa: E402
import updater  # noqa: E402

TMP_ROOT = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "usage_monitor_tests")

PASSED = 0


def ok(name: str) -> None:
    global PASSED
    PASSED += 1
    print(f"  [PASS] {name}")


def fail(name: str, detail: str) -> None:
    print(f"  [FAIL] {name}: {detail}")
    raise AssertionError(f"{name}: {detail}")


def check(cond: bool, name: str, detail: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, detail)


def fresh_tmp(name: str) -> str:
    path = os.path.join(TMP_ROOT, name)
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    return path


class FG:
    def __init__(self, exe: str, title: str, pid: int = 999):
        self.exe = exe
        self.title = title
        self.pid = pid
        self.hwnd = 1


class P:
    def __init__(self, exe: str, ppid: int, pid: int):
        self.exe = exe
        self.ppid = ppid
        self.pid = pid


class FakeClock:
    """按轮询序号输出前台窗口与空闲秒数（最后一个元素重复）。

    每个轮询中 monitor 先调 idle_seconds() 再调 get_foreground_info()，
    因此 idle_now 不推进索引，fg_now 推进（两者对齐同一轮）。
    """

    def __init__(self, fg_list: list, idle_list: list | None = None):
        self.fg = fg_list
        self.idle = idle_list or [1.0]
        self.i = 0

    def fg_now(self):
        i = min(self.i, len(self.fg) - 1)
        self.i += 1
        return self.fg[i]

    def idle_now(self):
        i = min(self.i, len(self.idle) - 1)
        return self.idle[i]


def run_scenario(name: str, fg_list: list, idle_list=None, seconds: int = 9,
                 poll: int = 1, idle_threshold: int = 180,
                 process_tree: dict | None = None,
                 fg_pid_for_tree: int = 999) -> tuple[list[dict], str]:
    """在临时数据根目录跑一段 run_daemon，返回（写入记录列表, 数据根）。"""
    tmp = fresh_tmp(name)
    cfg = classifier.load_config()
    cfg["data_root"] = tmp
    cfg["poll_interval_s"] = poll
    cfg["idle_threshold_s"] = idle_threshold

    monitor.stop_event.clear()  # 防止前序测试残留的停止信号
    monitor.set_paused(False)

    clock = FakeClock(fg_list, idle_list)
    real_fg = win32core.get_foreground_info
    real_idle = win32core.idle_seconds
    real_procs = win32core.enum_processes
    win32core.get_foreground_info = clock.fg_now
    win32core.idle_seconds = clock.idle_now
    if process_tree is not None:
        win32core.enum_processes = lambda: dict(process_tree)
    try:
        recs = monitor.run_daemon(cfg, test_seconds=seconds, verbose=False)
    finally:
        win32core.get_foreground_info = real_fg
        win32core.idle_seconds = real_idle
        win32core.enum_processes = real_procs
    return recs, tmp


# ---------------------------------------------------------------------------
# §14-2 切换计时
# ---------------------------------------------------------------------------
def test_switch_timing():
    print("[test] 切换计时（3 个应用各停 3 轮，应产生 3 条会话）")
    fg = [FG("code.exe", "main.py - VS Code")] * 3 \
        + [FG("wechat.exe", "张三")] * 3 \
        + [FG("chrome.exe", "GitHub - 主页")] * 3
    recs, tmp = run_scenario("switch", fg, seconds=9)
    check(len(recs) == 3, "产生 3 条会话", f"实际 {len(recs)}: {[r['app'] for r in recs]}")
    check(recs[0]["app"] == "VS Code", "第 1 条 VS Code", recs[0]["app"])
    check(recs[1]["app"] == "微信" and recs[1]["contact"] == "张三", "第 2 条 微信/张三")
    check(recs[2]["app"] == "Chrome", "第 3 条 Chrome")
    for r in recs:
        check(1500 <= r["duration_ms"] <= 4000, "时长约 2-3 秒", str(r["duration_ms"]))
    check(recs[0]["end"] <= recs[1]["start"] and recs[1]["end"] <= recs[2]["start"], "时间连续不重叠")
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# §14-3 空闲不计时
# ---------------------------------------------------------------------------
def test_idle_truncation():
    print("[test] 空闲不计时（3 轮活动 -> 2 轮空闲 -> 恢复，空闲段必须被截断）")
    fg = [FG("code.exe", "main.py")] * 7
    idle = [1.0, 1.0, 1.0, 400.0, 400.0, 1.0, 1.0]
    recs, tmp = run_scenario("idle", fg, idle, seconds=7)
    check(len(recs) == 2, "空闲截断产生 2 条会话（恢复后新开）", f"实际 {len(recs)}")
    r0, r1 = recs[0], recs[1]
    check(r0["duration_ms"] < 3500, "第 1 条不含空闲段（<3.5s）", f"{r0['duration_ms']}ms")
    check(r0["duration_ms"] >= 1000, "第 1 条包含活动段（>=1s）", f"{r0['duration_ms']}ms")
    check(r1["start"] > r0["end"], "第 2 条在第 1 条之后开始（空闲段未被计入）")
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# §14-4 微信联系人
# ---------------------------------------------------------------------------
def test_contact_and_main_title():
    print("[test] 微信联系人（聊天窗口 -> 张三；主界面 -> 无联系人）")
    fg = [FG("wechat.exe", "张三")] * 3 + [FG("wechat.exe", "微信")] * 3
    recs, tmp = run_scenario("contact", fg, seconds=6)
    check(len(recs) == 2, "2 条会话", f"实际 {len(recs)}")
    check(recs[0]["contact"] == "张三" and recs[0]["category"] == "社交聊天", "聊天窗口解析出张三")
    check(recs[1]["contact"] is None, "主界面无联系人")
    check(recs[1]["app"] == "微信" and recs[1]["category"] == "社交聊天", "主界面仍计应用时长")
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# §14-5 浏览器分类
# ---------------------------------------------------------------------------
def test_browser_categories():
    print("[test] 浏览器分类（B站视频 / GitHub代码 / MOOC学习）")
    fg = [FG("chrome.exe", "bilibili - 视频")] * 3 \
        + [FG("chrome.exe", "GitHub - 主页")] * 3 \
        + [FG("chrome.exe", "中国大学MOOC - 课程")] * 3
    recs, tmp = run_scenario("browser", fg, seconds=9)
    check(len(recs) == 3, "3 条会话")
    check(recs[0]["browser_category"] == "视频", "B站 -> 视频", str(recs[0].get("browser_category")))
    check(recs[1]["browser_category"] == "代码", "GitHub -> 代码")
    check(recs[2]["browser_category"] == "学习", "MOOC -> 学习")
    for r in recs:
        check(r["category"] == "浏览器", "顶层类别为浏览器", r["category"])
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# §14-6 终端 AI 工具（进程树）
# ---------------------------------------------------------------------------
def test_ai_tool_detection():
    print("[test] 终端 AI 工具（wt 里跑 opencode -> ai_tool=opencode）")
    tree = {100: P("wt.exe", 0, 100), 200: P("opencode.exe", 100, 200), 300: P("python.exe", 200, 300)}
    fg = [FG("wt.exe", "opencode", pid=100)] * 3
    recs, tmp = run_scenario("aitool", fg, seconds=3, process_tree=tree, fg_pid_for_tree=100)
    check(len(recs) >= 1, "有会话")
    check(recs[0]["ai_tool"] == "opencode", "识别 opencode", str(recs[0].get("ai_tool")))
    # 验收口径（§14-6）：终端跑 opencode -> 日报记为 AI编程
    check(recs[0]["category"] == "AI编程", "终端 opencode 会话归入 AI编程", recs[0]["category"])
    shutil.rmtree(tmp, ignore_errors=True)


def test_ai_false_positive():
    print("[test] AI 误伤防护（wt 里只有 python/pip -> ai_tool=None）")
    tree = {100: P("wt.exe", 0, 100), 200: P("python.exe", 100, 200), 300: P("pip.exe", 200, 300)}
    fg = [FG("wt.exe", "python -m pip install", pid=100)] * 3
    recs, tmp = run_scenario("aifp", fg, seconds=3, process_tree=tree)
    check(len(recs) >= 1, "有会话")
    check(recs[0]["ai_tool"] is None, "python/pip 不误判为 pi agent", str(recs[0].get("ai_tool")))
    shutil.rmtree(tmp, ignore_errors=True)


def test_ai_tool_in_editor_terminal():
    print("[test] 编辑器集成终端 AI 工具（VS Code 里跑 opencode -> ai_tool=opencode, 类别=AI编程）")
    tree = {100: P("code.exe", 0, 100), 200: P("opencode.exe", 100, 200)}
    fg = [FG("code.exe", "main.py - Visual Studio Code", pid=100)] * 3
    recs, tmp = run_scenario("editorai", fg, seconds=3, process_tree=tree)
    check(len(recs) >= 1, "有会话")
    check(recs[0]["ai_tool"] == "opencode", "编辑器集成终端识别 opencode", str(recs[0].get("ai_tool")))
    check(recs[0]["category"] == "AI编程", "识别后类别归入 AI编程", recs[0]["category"])
    # 编辑器本身不承载 AI 工具时类别不受影响
    tree2 = {100: P("code.exe", 0, 100), 200: P("node.exe", 100, 200)}
    fg2 = [FG("code.exe", "main.py - Visual Studio Code", pid=100)] * 3
    recs2, tmp2 = run_scenario("editornoai", fg2, seconds=3, process_tree=tree2)
    check(len(recs2) >= 1, "有会话")
    check(recs2[0]["ai_tool"] is None, "普通开发会话 ai_tool 为空", str(recs2[0].get("ai_tool")))
    check(recs2[0]["category"] == "开发工具", "普通开发会话类别保持开发工具", recs2[0]["category"])
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(tmp2, ignore_errors=True)


# ---------------------------------------------------------------------------
# §14-7 跨天
# ---------------------------------------------------------------------------
def test_day_rollover():
    print("[test] 跨天（23:59:58 跨越 0 点 -> 两个日期文件夹 + 前一日日报）")
    real_datetime_mod = monitor.datetime

    class FakeDT(_dt.datetime):
        _cur = _dt.datetime(2026, 8, 8, 23, 59, 58)
        _step = _dt.timedelta(seconds=1)

        @classmethod
        def now(cls, tz=None):
            v = cls._cur
            cls._cur = v + cls._step
            return v

    fake_mod = types.ModuleType("datetime")
    fake_mod.datetime = FakeDT
    fake_mod.date = _dt.date
    fake_mod.timedelta = _dt.timedelta
    monitor.datetime = fake_mod
    try:
        fg = [FG("code.exe", "main.py")] * 6
        recs, tmp = run_scenario("rollover", fg, seconds=6, idle_threshold=999)
    finally:
        monitor.datetime = real_datetime_mod

    d1 = os.path.join(tmp, "2026-08-08")
    d2 = os.path.join(tmp, "2026-08-09")
    check(os.path.isdir(d1) and os.path.isdir(d2), "生成两个日期文件夹")
    check(os.path.isfile(os.path.join(d1, "report.md")), "前一日自动生成 report.md")
    check(os.path.isfile(os.path.join(d2, "usage.jsonl")), "新一天 usage.jsonl")
    recs_08 = [r for r in recs if r["start"].startswith("2026-08-08")]
    recs_09 = [r for r in recs if r["start"].startswith("2026-08-09")]
    check(len(recs_08) >= 1, "8 月 8 日有记录", str(len(recs_08)))
    check(len(recs_09) >= 1, "8 月 9 日有记录", str(len(recs_09)))
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# §14-8 黑名单
# ---------------------------------------------------------------------------
def test_title_blacklist():
    print("[test] 隐私黑名单（标题含'密码' -> [已隐藏]，且不产出 contact/browser_category）")
    fg = [FG("wechat.exe", "我的密码是abc")] * 3
    recs, tmp = run_scenario("blacklist", fg, seconds=3)
    check(len(recs) >= 1, "有会话")
    r = recs[0]
    check(r["title"] == "[已隐藏]", "标题被隐藏", repr(r["title"]))
    check(r["contact"] is None, "隐藏后不解析联系人", str(r["contact"]))
    check(r.get("browser_category") is None, "隐藏后不做浏览器分类")
    check(r["category"] == "社交聊天", "类别仍按 exe 归类", r["category"])
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 静止零写入
# ---------------------------------------------------------------------------
def test_static_zero_write():
    print("[test] 静止零写入（前台不变 8 轮 -> 只有退出时 1 条）")
    fg = [FG("code.exe", "main.py")] * 8
    recs, tmp = run_scenario("static", fg, seconds=8)
    check(len(recs) == 1, "仅最终关闭写 1 条", f"实际 {len(recs)}")
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 暂停/继续
# ---------------------------------------------------------------------------
def test_pause_resume():
    print("[test] 暂停/继续（暂停期间不写入）")
    tmp = fresh_tmp("pause")
    cfg = classifier.load_config()
    cfg["data_root"] = tmp
    cfg["poll_interval_s"] = 1
    cfg["idle_threshold_s"] = 180

    clock = FakeClock([FG("code.exe", "main.py")] * 30)
    real_fg = win32core.get_foreground_info
    real_idle = win32core.idle_seconds
    win32core.get_foreground_info = clock.fg_now
    win32core.idle_seconds = clock.idle_now
    monitor.stop_event.clear()
    monitor.set_paused(False)

    recs: list[dict] = []
    def runner():
        nonlocal recs
        recs = monitor.run_daemon(cfg, test_seconds=30)

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    time.sleep(2.5)
    monitor.set_paused(True)   # 暂停
    time.sleep(3.0)
    monitor.set_paused(False)  # 恢复
    time.sleep(2.5)
    monitor.stop_event.set()
    th.join(timeout=10)

    win32core.get_foreground_info = real_fg
    win32core.idle_seconds = real_idle

    check(not th.is_alive(), "守护线程正常退出")
    check(len(recs) == 2, "暂停截断 + 恢复后各 1 条", f"实际 {len(recs)}")
    if len(recs) == 2:
        gap = (recs[1]["start"] > recs[0]["end"])
        check(gap, "恢复后的会话在暂停之后开始")
        pause_gap = (_dt.datetime.fromisoformat(recs[1]["start"]) - _dt.datetime.fromisoformat(recs[0]["end"])).total_seconds()
        check(2.0 <= pause_gap <= 8.0, f"两条会话间隔约等于暂停时长（{pause_gap:.1f}s）")
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 保留清理
# ---------------------------------------------------------------------------
def test_retention():
    print("[test] 保留清理（超过保留期删除，只删 YYYY-MM-DD 目录）")
    tmp = fresh_tmp("retention")
    for d in ["2026-07-01", "2026-08-01", "2026-08-08"]:
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    os.makedirs(os.path.join(tmp, "backup_notes"), exist_ok=True)
    with open(os.path.join(tmp, "notes.txt"), "w") as fh:
        fh.write("keep")

    # 以 2026-08-08 为今天（monitor.retention_cleanup 用真实今天；直接调用并临时对齐）
    real_mod = monitor.datetime

    class FakeDate(_dt.date):
        @classmethod
        def today(cls):
            return _dt.date(2026, 8, 8)

    fake_mod = types.ModuleType("datetime")
    fake_mod.datetime = _dt.datetime
    fake_mod.date = FakeDate
    fake_mod.timedelta = _dt.timedelta
    monitor.datetime = fake_mod
    try:
        monitor.retention_cleanup(tmp, retention_days=7)
    finally:
        monitor.datetime = real_mod

    check(not os.path.isdir(os.path.join(tmp, "2026-07-01")), "7-01（超 7 天）已删除")
    check(os.path.isdir(os.path.join(tmp, "2026-08-01")), "8-01（恰好 7 天）保留")
    check(os.path.isdir(os.path.join(tmp, "2026-08-08")), "今天保留")
    check(os.path.isdir(os.path.join(tmp, "backup_notes")), "非日期目录不受影响")
    check(os.path.isfile(os.path.join(tmp, "notes.txt")), "普通文件不受影响")
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 报表管线
# ---------------------------------------------------------------------------
def test_report_pipeline():
    print("[test] 报表管线（run_daemon 后 generate_day_report -> report.md/csv 正确）")
    fg = [FG("wechat.exe", "张三")] * 3 + [FG("wt.exe", "opencode", pid=100)] * 3
    tree = {100: P("wt.exe", 0, 100), 200: P("opencode.exe", 100, 200)}
    recs, tmp = run_scenario("report_pipe", fg, seconds=6, process_tree=tree)
    check(len(recs) == 2, "2 条会话")
    day = recs[0]["start"][:10]
    report.generate_day_report(day, tmp)
    md_path = os.path.join(tmp, day, "report.md")
    csv_path = os.path.join(tmp, day, "report.csv")
    check(os.path.isfile(md_path), "report.md 生成")
    check(os.path.isfile(csv_path), "report.csv 生成")
    md = open(md_path, encoding="utf-8").read()
    check("## 总览" in md and "活跃时长" in md, "汇总日报含总览表")
    check("微信" in md and "张三" in md, "日报含 微信/张三")
    check("opencode" in md, "日报含 opencode")
    # 会话记录自洽：duration_ms == end - start
    import datetime as _dt2
    for r in recs:
        t0 = _dt2.datetime.fromisoformat(r["start"])
        t1 = _dt2.datetime.fromisoformat(r["end"])
        calc = int((t1 - t0).total_seconds() * 1000)
        check(calc == r["duration_ms"], f"duration_ms 与 start/end 自洽 ({r['app']})",
              f"{r['duration_ms']} vs {calc}")
    csv = open(csv_path, encoding="utf-8-sig").read()
    check("联系人:微信/张三" in csv, "CSV 含联系人汇总")
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 软件清单
# ---------------------------------------------------------------------------
def test_inventory():
    print("[test] 软件清单（注册表/进程扫描 -> 分类 -> 写 JSON/CSV）")
    cfg = classifier.load_config()
    inv = inventory.collect_inventory(cfg)
    check(inv["count"] >= 20, "扫描到至少 20 个应用", f"实际 {inv['count']}")
    cats = {a["category"] for a in inv["apps"]}
    check(cats <= set(classifier.CATEGORY_ORDER), "类别都在合法集合内", str(cats))
    tmp = fresh_tmp("inventory")
    written = inventory.write_inventory(tmp, cfg)
    check(os.path.isfile(os.path.join(tmp, "software_inventory.json")), "JSON 写出")
    check(os.path.isfile(os.path.join(tmp, "software_inventory.csv")), "CSV 写出")
    data = json.load(open(os.path.join(tmp, "software_inventory.json"), encoding="utf-8"))
    check(data["count"] == written["count"], "JSON 计数一致")
    check({"date", "scanned_at", "count", "apps"} <= set(data.keys()), "schema 字段齐全")
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 月度/JSON 导出
# ---------------------------------------------------------------------------
def test_month_and_json():
    print("[test] 月度汇总 + JSON 导出（--month / --json 逻辑）")
    tmp = fresh_tmp("month")
    day = "2026-08-08"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    lines = [
        {"start": f"{day}T10:00:00", "end": f"{day}T10:02:00", "duration_ms": 120000,
         "exe": "wechat.exe", "app": "微信", "title": "张三", "category": "社交聊天",
         "contact": "张三", "ai_tool": None, "active": True},
    ]
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for rec in lines:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    agg = report.aggregate_month("2026-08", tmp)
    check(agg["total_active_ms"] == 120000, "月度聚合时长正确", str(agg["total_active_ms"]))
    check(len(agg["per_day"]) == 1 and agg["per_day"][0]["date"] == day, "每日明细正确")
    md = report.generate_month_report_md("2026-08", tmp)
    check("电脑使用情况月报 2026-08" in md and "微信" in md and "张三" in md, "月报内容正确")
    j = json.loads(json.dumps(agg, ensure_ascii=False, default=str))
    check(j["by_contact"]["微信"]["张三"] == 120000, "JSON 导出结构正确")
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 浏览器 URL 级历史解析（Phase 3）
# ---------------------------------------------------------------------------
def _chrome_ft(offset_seconds: int = 0) -> int:
    """当前时刻的 Chrome FILETIME（微秒，1601 纪元）。"""
    return int((time.time() + offset_seconds + 11644473600) * 1e6)


def test_browser_history():
    print("[test] 浏览器历史（合成 Chromium SQLite：分类 + 黑名单掩蔽 + 停留时长）")
    import sqlite3
    import browser_history

    tmp = fresh_tmp("history")
    db = os.path.join(tmp, "History")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL,
            title TEXT, visit_count INTEGER DEFAULT 0, typed_count INTEGER DEFAULT 0,
            last_visit_time INTEGER, hidden INTEGER DEFAULT 0);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL,
            visit_time INTEGER NOT NULL, from_visit INTEGER, transition INTEGER,
            segment_id INTEGER, visit_duration INTEGER DEFAULT 0);
    """)
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)",
                 ("https://www.bilibili.com/video/av1", "测试视频页面"))
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)",
                 ("https://github.com/user/repo", "我的仓库"))
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)",
                 ("https://example.com/login?password=123", "密码修改页"))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (1, ?, 120000000)", (_chrome_ft(-120),))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (2, ?, 60000000)", (_chrome_ft(-60),))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (3, ?, 0)", (_chrome_ft(0),))
    conn.commit()
    conn.close()

    cfg = classifier.load_config()
    today = _dt.date.today().isoformat()
    data = browser_history.collect(today, tmp, cfg, db_paths=[db])
    check(data["count"] == 3, "提取 3 条访问", f"实际 {data['count']}")
    cats = {v["url"].split("?", 1)[0]: v["category"] for v in data["visits"]}
    check(cats.get("https://www.bilibili.com/video/av1") == "视频", "bilibili -> 视频", str(cats))
    check(cats.get("https://github.com/user/repo") == "代码", "github -> 代码", str(cats))
    masked = [v for v in data["visits"] if v["url"] == "[已隐藏]"]
    check(len(masked) == 1, "命中黑名单的 URL 掩蔽为 [已隐藏]")
    check(all(v["time"].startswith(today) for v in data["visits"]), "访问时间换算为本地时间")

    # 停留时长
    dur_by_url = {v["url"].split("?", 1)[0]: v["duration_s"] for v in data["visits"]}
    check(dur_by_url.get("https://www.bilibili.com/video/av1") == 120.0, "bilibili 停留 120 秒", str(dur_by_url))
    check(dur_by_url.get("https://github.com/user/repo") == 60.0, "github 停留 60 秒")
    check(data["total_duration_s"] == 180.0, "总停留 180 秒", str(data["total_duration_s"]))
    check(data["by_category_duration_s"].get("视频") == 120.0, "视频分类停留 120 秒", str(data.get("by_category_duration_s")))
    check(data["by_domain_duration_s"].get("www.bilibili.com") == 120.0, "域名停留聚合正确")

    section = browser_history.report_section(today, tmp, cfg, db_paths=[db])
    check(section is not None and "浏览器访问明细" in section, "日报章节可生成")
    check("bilibili" in section and "视频" in section, "章节含 URL 与分类")
    check("停留总时长" in section and "3 分钟" in section, "章节含停留时长汇总")
    check("密码" not in section and "example.com" not in section, "章节不含被掩蔽的敏感内容")

    # 无 visit_duration 列的兼容性（回退为 0）
    db2 = os.path.join(tmp, "History_old.db")
    conn2 = sqlite3.connect(db2)
    conn2.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL,
            visit_time INTEGER NOT NULL);
    """)
    conn2.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://www.icourse163.org/course/1", "MOOC课"))
    conn2.execute("INSERT INTO visits (url, visit_time) VALUES (1, ?)", (_chrome_ft(0),))
    conn2.commit()
    conn2.close()
    data2 = browser_history.collect(today, tmp, cfg, db_paths=[db2])
    check(data2["count"] == 1 and data2["visits"][0]["duration_s"] == 0.0, "旧 schema 无时长列时兼容")

    # 禁用开关
    cfg2 = dict(cfg)
    cfg2["browser_history_enabled"] = False
    data3 = browser_history.collect(today, tmp, cfg2, db_paths=[db])
    check(data3["enabled"] is False and data3["count"] == 0, "browser_history_enabled=false 时跳过")
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 自有窗口 AI 工具（Bug 2 回归：chatgpt.exe 前台 -> ai_tool=chatgpt）
# ---------------------------------------------------------------------------
def test_ai_own_window():
    print("[test] 自有窗口 AI 工具（ChatGPT 桌面版前台 -> ai_tool=chatgpt）")
    fg = [FG("chatgpt.exe", "New chat")] * 3
    recs, tmp = run_scenario("aiown", fg, seconds=3)
    check(len(recs) >= 1, "有会话")
    check(recs[0]["ai_tool"] == "chatgpt", "ai_tool=chatgpt", str(recs[0].get("ai_tool")))
    check(recs[0]["category"] == "AI编程", "类别=AI编程", recs[0]["category"])
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 联系人别名（Bug 3 回归：aliases.json 在日报聚合中生效）
# ---------------------------------------------------------------------------
def test_contact_aliases():
    print("[test] 联系人别名（aliases.json: aaa123 -> 张三）")
    tmp = fresh_tmp("aliases")
    with open(os.path.join(tmp, "aliases.json"), "w", encoding="utf-8") as fh:
        json.dump({"aaa123": "张三"}, fh, ensure_ascii=False)
    day = "2026-08-08"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "start": f"{day}T10:00:00", "end": f"{day}T10:02:00", "duration_ms": 120000,
            "exe": "wechat.exe", "app": "微信", "title": "aaa123", "category": "社交聊天",
            "contact": "aaa123", "ai_tool": None, "active": True,
        }, ensure_ascii=False) + "\n")
    agg = report.aggregate(day, tmp)
    check(agg["by_contact"].get("微信", {}).get("张三") == 120000, "聚合后显示别名张三",
          str(agg["by_contact"]))
    check("aaa123" not in agg["by_contact"].get("微信", {}), "原始 ID 不出现")
    md = report.generate_report_md(day, tmp)
    check("张三" in md, "日报显示别名")
    shutil.rmtree(tmp, ignore_errors=True)


def test_cross_day_isolation():
    print("[test] 跨天隔离（昨天打开的页面 -> 时长按日界分摊，绝不串天）")
    import sqlite3
    import browser_history

    tmp = fresh_tmp("crossday")
    db = os.path.join(tmp, "History")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL,
            visit_time INTEGER NOT NULL, visit_duration INTEGER DEFAULT 0);
    """)
    # 昨天 23:30 打开、时长 2 小时 -> 区间跨入今天 01:30
    y_2330 = _dt.datetime.combine(_dt.date.today() - _dt.timedelta(days=1), _dt.time(23, 30))
    ft_cross = int((time.mktime(y_2330.timetuple()) + 11644473600) * 1e6)
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)",
                 ("https://www.icourse163.org/course/cross", "跨天MOOC课"))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (1, ?, ?)",
                 (ft_cross, 2 * 3600 * 1_000_000))
    # 三天前打开、无时长 -> 不应进入任何一天的报表（防污染）
    old = _dt.datetime.combine(_dt.date.today() - _dt.timedelta(days=3), _dt.time(10, 0))
    ft_old = int((time.mktime(old.timetuple()) + 11644473600) * 1e6)
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://old.example.com/", "旧页面"))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (2, ?, 0)", (ft_old,))
    conn.commit()
    conn.close()

    cfg = classifier.load_config()
    today = _dt.date.today().isoformat()
    yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()

    t_data = browser_history.collect(today, tmp, cfg, db_paths=[db])
    cross = [v for v in t_data["visits"] if "cross" in v["url"]]
    check(len(cross) == 1, "跨天访问进入今天的报表")
    check(cross[0]["duration_s"] == 5400.0, "今天只算 00:00-01:30 的 1.5 小时", str(cross[0]["duration_s"]))
    check(cross[0]["time"].startswith(today), "跨天访问在今天的显示时间从 0 点起", cross[0]["time"])
    check(all("old.example.com" not in v["url"] for v in t_data["visits"]), "三天前的无时长访问不污染今天")
    check(t_data["total_duration_s"] == 5400.0, "今天总停留 = 5400 秒", str(t_data["total_duration_s"]))

    y_data = browser_history.collect(yesterday, tmp, cfg, db_paths=[db])
    cross_y = [v for v in y_data["visits"] if "cross" in v["url"]]
    check(len(cross_y) == 1, "跨天访问也进入昨天的报表")
    check(cross_y[0]["duration_s"] == 1800.0, "昨天只算 23:30-24:00 的 0.5 小时", str(cross_y[0]["duration_s"]))
    check(y_data["total_duration_s"] == 1800.0, "昨天总停留 = 1800 秒", str(y_data["total_duration_s"]))

    # 两天份额相加 = 原始时长，无丢失
    check(round(t_data["total_duration_s"] + y_data["total_duration_s"]) == 7200, "两天份额合计 = 2 小时")
    shutil.rmtree(tmp, ignore_errors=True)


def test_reclassify():
    print("[test] 重分类（规则变更后修复历史记录：π 终端会话 -> pi agent）")
    tmp = fresh_tmp("reclassify")
    day = "2026-08-08"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    lines = [
        {"start": f"{day}T10:00:00", "end": f"{day}T10:02:00", "duration_ms": 120000,
         "exe": "wt.exe", "app": "Windows Terminal", "title": "π - niangao",
         "category": "开发工具", "contact": None, "ai_tool": None, "active": True},
        {"start": f"{day}T10:05:00", "end": f"{day}T10:06:00", "duration_ms": 60000,
         "exe": "tabbit browser.exe", "app": "Tabbit Browser", "title": "[已隐藏]",
         "category": "浏览器", "contact": None, "ai_tool": None, "active": True},
    ]
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for rec in lines:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    n = report.reclassify_day(day, tmp)
    check(n == 1, "1 条记录变更", str(n))
    recs = [json.loads(line) for line in open(os.path.join(tmp, day, "usage.jsonl"), encoding="utf-8") if line.strip()]
    pi = [r for r in recs if "π" in r["title"]][0]
    check(pi["ai_tool"] == "pi agent", "π 会话重分类为 pi agent", str(pi["ai_tool"]))
    check(pi["category"] == "AI编程", "π 会话类别重分类为 AI编程", pi["category"])
    hidden = [r for r in recs if r["title"] == "[已隐藏]"][0]
    check(hidden["ai_tool"] is None and hidden["category"] == "浏览器", "已隐藏记录不受影响")
    check(os.path.isfile(os.path.join(tmp, day, "usage.jsonl.bak")), "写回前已备份")
    shutil.rmtree(tmp, ignore_errors=True)


def test_dimension_refinements():
    print("[test] 维度细化（终端工具 / 窗口状态 / 子分类 / 会话URL关联）")
    import sqlite3
    import browser_history

    # 1) 终端 TUI 工具 + 子分类 + 窗口状态
    fg = [FG("wt.exe", "git status - niangao", pid=100)] * 3
    recs, tmp = run_scenario("dims", fg, seconds=3)
    r = recs[0]
    check(r.get("term_tool") == "git", "终端标题识别 git", str(r.get("term_tool")))
    check(r.get("subcategory") == "终端", "wt 子分类=终端", str(r.get("subcategory")))
    check(r.get("window_state") in ("normal", "maximized", "fullscreen"), "窗口状态字段存在", str(r.get("window_state")))
    shutil.rmtree(tmp, ignore_errors=True)

    # 2) 路径标题不误判（D:\git-stuff）
    fg2 = [FG("wt.exe", r"D:\git-stuff - pwsh", pid=100)] * 3
    recs2, tmp2 = run_scenario("dims2", fg2, seconds=3)
    check(recs2[0].get("term_tool") is None, "路径标题不误判 git", str(recs2[0].get("term_tool")))
    shutil.rmtree(tmp2, ignore_errors=True)

    # 3) 游戏顶级类别下的子分类（用户 config 中 游戏 已是独立大类）
    fg3 = [FG("steam.exe", "Steam")] * 3
    recs3, tmp3 = run_scenario("dims3", fg3, seconds=3)
    check(recs3[0].get("category") == "游戏", "steam 顶级类别=游戏", str(recs3[0].get("category")))
    check(recs3[0].get("subcategory") == "游戏平台", "steam 子分类=游戏平台", str(recs3[0].get("subcategory")))
    shutil.rmtree(tmp3, ignore_errors=True)

    # 4) 浏览器会话 subcategory=browser_category + URL 关联（monkeypatch 查找函数）
    real_fn = browser_history.find_url_for_session
    browser_history.find_url_for_session = lambda *a, **k: "https://www.bilibili.com/video/BV1test"
    try:
        fg4 = [FG("chrome.exe", "bilibili 视频 - 主页")] * 3
        recs4, tmp4 = run_scenario("dims4", fg4, seconds=3)
    finally:
        browser_history.find_url_for_session = real_fn
    r4 = recs4[0]
    check(r4.get("subcategory") == "视频", "浏览器子分类=视频", str(r4.get("subcategory")))
    check(r4.get("url") == "https://www.bilibili.com/video/BV1test", "会话关联 URL", str(r4.get("url")))
    shutil.rmtree(tmp4, ignore_errors=True)

    # 5) find_url_for_session 合成库直接测试（重叠匹配 / 无重叠 / 黑名单掩蔽）
    tmp5 = fresh_tmp("dims5")
    db = os.path.join(tmp5, "History")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL,
            visit_time INTEGER NOT NULL, visit_duration INTEGER DEFAULT 0);
    """)
    now = _dt.datetime.now()
    ft = int((now.timestamp() + 11644473600) * 1e6)
    conn.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://example.com/page", "页面"))
    conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (1, ?, 120000000)", (ft,))
    conn.commit()
    conn.close()
    cfg = classifier.load_config()
    hit = browser_history.find_url_for_session(
        now - _dt.timedelta(minutes=1), now, tmp5, cfg, db_paths=[db])
    check(hit == "https://example.com/page", "时间重叠匹配 URL", str(hit))
    miss = browser_history.find_url_for_session(
        now - _dt.timedelta(hours=3), now - _dt.timedelta(hours=2, minutes=59), tmp5, cfg, db_paths=[db])
    check(miss is None, "无重叠返回 None", str(miss))
    db2 = os.path.join(tmp5, "History2")
    conn2 = sqlite3.connect(db2)
    conn2.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL,
            visit_time INTEGER NOT NULL, visit_duration INTEGER DEFAULT 0);
    """)
    conn2.execute("INSERT INTO urls (url, title) VALUES (?, ?)", ("https://example.com/login?password=123", "登录"))
    conn2.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (1, ?, 120000000)", (ft,))
    conn2.commit()
    conn2.close()
    hit2 = browser_history.find_url_for_session(
        now - _dt.timedelta(minutes=1), now, tmp5, cfg, db_paths=[db2])
    check(hit2 == "[已隐藏]", "命中黑名单 URL 掩蔽", str(hit2))
    shutil.rmtree(tmp5, ignore_errors=True)


def test_dashboard_api():
    print("[test] 仪表盘 API（端点 + 同源安全校验 + 错误码）")
    import http.client
    import threading
    import dashboard

    tmp = fresh_tmp("dashapi")
    day = "2026-08-08"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "start": f"{day}T10:00:00", "end": f"{day}T10:02:00", "duration_ms": 120000,
            "exe": "wechat.exe", "app": "微信", "title": "张三", "category": "社交聊天",
            "contact": "张三", "ai_tool": None, "active": True,
        }, ensure_ascii=False) + "\n")

    server = dashboard.create_server(tmp, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def req(method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request(method, path, headers=headers or {})
        r = conn.getresponse()
        body = r.read().decode("utf-8", errors="replace")
        conn.close()
        return r.status, body

    try:
        # 无浏览器上下文的请求（curl/脚本）放行
        s, _ = req("GET", "/api/dates")
        check(s == 200, "无 Origin 放行", str(s))
        # 恶意 Origin（跨站 fetch）拒绝
        s, _ = req("GET", "/api/dates", {"Origin": "https://evil.example"})
        check(s == 403, "恶意 Origin 拒绝", str(s))
        # 恶意 Referer 拒绝
        s, _ = req("GET", "/api/dates", {"Referer": "https://evil.example/page"})
        check(s == 403, "恶意 Referer 拒绝", str(s))
        # 合法 Origin 放行
        s, _ = req("GET", "/api/dates", {"Origin": f"http://127.0.0.1:{port}"})
        check(s == 200, "合法 Origin 放行", str(s))
        # 页面响应带安全头
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/")
        r = conn.getresponse()
        headers = dict(r.getheaders())
        conn.close()
        check(headers.get("X-Frame-Options") == "DENY", "X-Frame-Options: DENY")
        check("Content-Security-Policy" in headers, "CSP 存在")
        # 端点
        s, _ = req("GET", "/api/day?date=2026-08-08")
        check(s == 200, "api/day 正常", str(s))
        s, _ = req("GET", "/api/day?date=bad")
        check(s == 400, "非法日期 400", str(s))
        s, _ = req("GET", "/nope")
        check(s == 404, "未知路径 404", str(s))
        s, _ = req("POST", "/api/dates")
        check(s == 405, "POST 405", str(s))
        # 路径穿越被拒（不存在的日期 -> 400 而非泄露路径）
        s, _ = req("GET", "/api/day?date=../2026-08-08")
        check(s == 400, "路径穿越日期被拒", str(s))
    finally:
        server.shutdown()
        server.server_close()
    shutil.rmtree(tmp, ignore_errors=True)


def test_dashboard_update_api():
    print("[test] 仪表盘更新 API（status/check/download/apply 错误态）")
    import http.client
    import threading
    import dashboard

    tmp = fresh_tmp("dash_update")
    server = dashboard.create_server(tmp, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def req(method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        data = r.read().decode("utf-8", errors="replace")
        conn.close()
        return r.status, data

    try:
        s, body = req("GET", "/api/update/status")
        status = json.loads(body)
        check(s == 200 and status.get("state") == "idle" and status.get("dev") is True,
              "update/status 正常", body)
        import updater
        orig = updater.check_for_update
        updater.check_for_update = lambda *a, **k: {
            "current": "2.1.0", "latest": "", "has_update": False,
            "notes": "", "published_at": "", "url": "", "asset": None, "error": None,
        }
        try:
            s, body = req("GET", "/api/update/check")
            check(s == 200 and json.loads(body).get("has_update") is False,
                  "update/check 无更新", body)
            s, body = req("POST", "/api/update/download",
                          {"Content-Type": "application/json"}, "{}")
            check(s == 400 and "无需下载" in json.loads(body).get("error", ""),
                  "update/download 无更新拒绝", body)
            s, body = req("POST", "/api/update/apply",
                          {"Content-Type": "application/json"}, "{}")
            check(s == 400 and "没有已下载" in json.loads(body).get("error", ""),
                  "update/apply 未下载拒绝", body)
        finally:
            updater.check_for_update = orig
    finally:
        server.shutdown()
        server.server_close()
    shutil.rmtree(tmp, ignore_errors=True)


def test_electron_shell_detection():
    print("[test] Electron 桌面壳探测（dev 模式 / 打包模式 / 缺失回退）")
    import monitor

    # dev 模式：electron.exe + main.js 存在时返回可执行命令
    base = os.path.dirname(os.path.abspath(monitor.__file__))
    app_dir = os.path.join(base, "electron-app")
    electron_exe = os.path.join(app_dir, "node_modules", "electron", "dist", "electron.exe")
    if os.path.isfile(electron_exe):
        cmd = monitor._find_electron_shell()
        check(cmd is not None and len(cmd) >= 2, "dev 模式探测到 Electron 壳", str(cmd))
        check(os.path.isfile(cmd[0]), "返回的 electron.exe 存在")
        check(cmd[1].endswith("main.js"), "第二个参数是 main.js", str(cmd[1]))
    else:
        check(monitor._find_electron_shell() is None or True, "无 dev 环境时跳过（不失败）")

    # 打包模式：exe 位于 <root>/dist，electron-app 位于项目根 <root>（父目录）
    fake_root = fresh_tmp("shell_frozen")
    fake_dist = os.path.join(fake_root, "dist")
    os.makedirs(fake_dist, exist_ok=True)
    fake_app = os.path.join(fake_root, "electron-app")
    os.makedirs(os.path.join(fake_app, "node_modules", "electron", "dist"), exist_ok=True)
    fake_elec = os.path.join(fake_app, "node_modules", "electron", "dist", "electron.exe")
    open(fake_elec, "w").close()
    open(os.path.join(fake_app, "main.js"), "w").close()
    os.makedirs(os.path.join(fake_app, "dist"), exist_ok=True)
    fake_packed = os.path.join(fake_app, "dist", "UsageMonitor-Desktop-2.0.0.exe")
    open(fake_packed, "w").close()
    real_script_dir = monitor.paths.script_dir
    try:
        monitor.paths.script_dir = lambda: fake_dist
        cmd = monitor._find_electron_shell()
        check(cmd and cmd[0] == fake_packed, "打包 exe 优先（父目录项目根）", str(cmd))
    finally:
        monitor.paths.script_dir = real_script_dir
    shutil.rmtree(fake_root, ignore_errors=True)


def test_app_groups():
    print("[test] 应用分组自定义（覆盖层分类 + API 增删改移出）")
    import http.client
    import threading
    import dashboard

    tmp = fresh_tmp("groups")
    day = "2026-08-08"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "start": f"{day}T10:00:00", "end": f"{day}T10:02:00", "duration_ms": 120000,
            "exe": "steam.exe", "app": "Steam", "title": "Steam", "category": "游戏",
            "contact": None, "ai_tool": None, "active": True,
        }, ensure_ascii=False) + "\n")
    with open(os.path.join(tmp, day, "software_inventory.json"), "w", encoding="utf-8") as fh:
        json.dump({"date": day, "count": 2, "apps": [
            {"name": "Steam", "exe": "steam.exe", "category": "游戏", "source": ["registry"], "running": False},
            {"name": "WeChat", "exe": "wechat.exe", "category": "社交聊天", "source": ["registry"], "running": False},
        ]}, fh, ensure_ascii=False)

    cfg = classifier.load_config()
    cfg["data_root"] = tmp

    # 1) 覆盖层分类：steam -> 自定义分组
    classifier.save_app_groups(
        {"exe_groups": {"steam.exe": "我的分组"}, "custom_categories": ["我的分组"]}, tmp)
    check(classifier.classify_category("steam.exe", "", cfg) == "我的分组", "覆盖层优先", "游戏")
    check(classifier.classify_category("wechat.exe", "", cfg) == "社交聊天", "未覆盖应用不受影响")
    check("我的分组" in classifier.all_categories(cfg), "自定义分组出现在列表中")

    # 2) API：GET /api/groups
    server = dashboard.create_server(tmp, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def req(method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = conn.getresponse()
        data = json.loads(r.read().decode("utf-8"))
        conn.close()
        return r.status, data

    try:
        s, d = req("GET", "/api/groups")
        check(s == 200 and "apps" in d and "categories" in d, "GET /api/groups")
        check(any(a["exe"] == "steam.exe" and a["category"] == "我的分组" for a in d["apps"]),
              "API 反映覆盖层分类")
        check(any(a["exe"] == "wechat.exe" for a in d["apps"]), "已知应用列表含清单+usage exe")

        # 3) set 移出（恢复自动）
        s, d = req("POST", "/api/groups/set", {"exe": "steam.exe", "category": ""})
        check(s == 200 and d.get("ok") is True, "POST set 移出")
        check(classifier.classify_category("steam.exe", "", cfg) == "游戏", "移出后恢复自动分类")

        # 4) set 到内置分组
        req("POST", "/api/groups/set", {"exe": "steam.exe", "category": "影音娱乐"})
        check(classifier.classify_category("steam.exe", "", cfg) == "影音娱乐", "设置到内置分组")

        # 5) add 新分组（未知分组自动登记）
        s, d = req("POST", "/api/groups/add", {"name": "学习工具"})
        check(s == 200 and "学习工具" in d.get("categories", []), "新增分组")
        # 6) delete 分组（组内应用恢复自动）
        req("POST", "/api/groups/set", {"exe": "steam.exe", "category": "学习工具"})
        check(classifier.classify_category("steam.exe", "", cfg) == "学习工具", "移到新分组")
        req("POST", "/api/groups/delete", {"name": "学习工具"})
        check(classifier.classify_category("steam.exe", "", cfg) == "游戏", "删分组后恢复自动")
        s, d = req("GET", "/api/groups")
        check("学习工具" not in d["categories"], "分组已删除")

        # 7) 自定义显示名（客制化）
        s, d = req("POST", "/api/groups/rename", {"exe": "steam.exe", "display_name": "Steam 自定义名"})
        check(s == 200 and d.get("ok") is True, "重命名 API")
        s, d = req("GET", "/api/groups")
        steam = next(a for a in d["apps"] if a["exe"] == "steam.exe")
        check(steam["app"] == "Steam 自定义名", "显示名在列表中生效", str(steam))
        check(classifier.resolve_app_name("steam.exe", cfg) == "Steam 自定义名", "resolve_app_name 使用自定义名")

        # 8) 导出配置
        s, d = req("GET", "/api/groups/export")
        check(s == 200 and d.get("app_names", {}).get("steam.exe") == "Steam 自定义名",
              "导出包含自定义显示名", str(d))

        # 9) 导入配置（整份覆盖）
        import_groups = {
            "exe_groups": {"wechat.exe": "我的分组"},
            "custom_categories": ["我的分组"],
            "app_names": {"wechat.exe": "微信自定义"},
            "group_meta": {"我的分组": {"description": "测试描述"}},
        }
        s, d = req("POST", "/api/groups/import", import_groups)
        check(s == 200 and d.get("ok") is True, "导入 API")
        s, d = req("GET", "/api/groups")
        wechat = next(a for a in d["apps"] if a["exe"] == "wechat.exe")
        check(wechat["app"] == "微信自定义" and wechat["category"] == "我的分组",
              "导入后显示名/分组生效", str(wechat))
        check(d.get("group_meta", {}).get("我的分组", {}).get("description") == "测试描述",
              "导入后分组元数据生效", str(d.get("group_meta")))

        # 10) 恶意 Origin 对 POST 同样拒绝
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", "/api/groups/set", body='{"exe":"a","category":"b"}',
                     headers={"Content-Type": "application/json", "Origin": "https://evil.example"})
        r = conn.getresponse()
        check(r.status == 403, "POST 恶意 Origin 拒绝", str(r.status))
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
    shutil.rmtree(tmp, ignore_errors=True)


def test_report_balloon_once_per_day():
    print("[test] 日报生成托盘通知调度（一天一次 / 晚启动不补弹 / 时间门槛）")
    root = fresh_tmp("balloon")
    day = _dt.date.today().isoformat()
    d = os.path.join(root, day)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "report.md")

    def touch(t: _dt.datetime) -> None:
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# x")
        os.utime(p, (t.timestamp(), t.timestamp()))

    # 场景 1：19:30 前启动 -> 武装；报告生成后弹一次，且不重复
    monitor._report_notified_day = None
    monitor._report_armed = False
    check(monitor.check_report_balloon(root, lambda: None) is False, "启动首查不弹（武装）")
    check(monitor._report_armed is True, "武装状态登记")
    touch(_dt.datetime.combine(_dt.date.today(), _dt.time(19, 30)))
    fired: list[int] = []
    check(monitor.check_report_balloon(root, lambda: fired.append(1)) is True, "生成后首次发现弹一次")
    check(monitor.check_report_balloon(root, lambda: fired.append(2)) is False, "同一天不重复弹")
    check(fired == [1], "恰好弹一次", str(fired))

    # 场景 2：19:30 后启动（报告已存在）-> 只登记不补弹
    monitor._report_notified_day = None
    monitor._report_armed = False
    fired = []
    check(monitor.check_report_balloon(root, lambda: fired.append(1)) is False, "晚启动不补弹")
    check(fired == [], "晚启动零通知", str(fired))

    # 场景 3：时间门槛——早于 19:25 生成的报告不算"刚生成"
    touch(_dt.datetime.combine(_dt.date.today(), _dt.time(8, 0)))
    check(monitor._today_report_recent(root, day) is False, "早于 19:25 不算刚生成")
    touch(_dt.datetime.combine(_dt.date.today(), _dt.time(19, 30)))
    check(monitor._today_report_recent(root, day) is True, "19:30 生成识别为刚生成")
    monitor._report_notified_day = None
    monitor._report_armed = False
    shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 智能洞察模块（规则引擎 + AI 客户端 + 缓存 + 仪表盘/日报集成）
# ---------------------------------------------------------------------------
def _make_fake_agg(total_h: float = 6.0) -> dict:
    """构造足以触发全部规则类型的聚合结果。"""
    hour_ms = 3600000
    total_ms = int(total_h * hour_ms)
    return {
        "date": "2026-08-10",
        "session_count": 4,
        "total_active_ms": total_ms,
        "by_app": {"VS Code": 2 * hour_ms, "Steam": 3 * hour_ms, "微信": hour_ms},
        "by_category": {
            "办公学习": 1 * hour_ms,
            "游戏": 3 * hour_ms,
            "AI编程": hour_ms,
            "社交聊天": hour_ms,
            "浏览器": hour_ms,
        },
        "by_contact": {"微信": {"张三": 30 * 60000}},
        "by_ai": {"opencode": hour_ms},
        "by_browser": {"学习": 30 * 60000, "视频": 30 * 60000},
        "by_subcategory": {},
        "by_term_tool": {},
        "hourly_ms": [0] * 24,
        "sessions": [
            {"start": "2026-08-10T09:00:00", "end": "2026-08-10T10:40:00",
             "duration_ms": 100 * 60000, "app": "VS Code", "category": "办公学习"},
            {"start": "2026-08-10T14:00:00", "end": "2026-08-10T15:00:00",
             "duration_ms": 60 * 60000, "app": "Steam", "category": "游戏"},
        ],
    }


def test_insights_rules():
    print("[test] 智能洞察规则引擎（study/game/health/efficiency/balance/trend + 阈值/空数据）")
    agg = _make_fake_agg()
    agg["hourly_ms"][1] = 10 * 60000  # 深夜 01:00 仍有活动
    prev = {
        "date": "2026-08-09",
        "session_count": 3,
        "total_active_ms": 4 * 3600000,
        "by_category": {}, "by_app": {}, "by_ai": {}, "by_browser": {},
        "by_contact": {}, "hourly_ms": [0] * 24, "sessions": [],
    }
    cfg = classifier.load_config()
    rules = insights.rule_insights(agg, cfg, prev)
    types = {r["type"] for r in rules}
    check({"study", "game", "health", "efficiency", "balance", "trend"} <= types,
          "六类规则全部命中", str(types))
    check(all(r["severity"] in ("info", "warn", "alert") for r in rules), "severity 合法")
    study = next(r for r in rules if r["type"] == "study")
    check("今日学习" in study["detail"] and "网课" in study["detail"], "学习建议含网课时长",
          study["detail"])
    game = next(r for r in rules if r["type"] == "game")
    check(game["severity"] == "warn" and "劳逸结合" in game["detail"], "游戏超阈值 -> warn", game["detail"])
    health = [r for r in rules if r["type"] == "health"]
    check(any("最长连续使用" in r["detail"] for r in health), "长会话健康提醒")
    check(any("深夜" in r["detail"] for r in health), "深夜使用健康提醒")
    trend = next(r for r in rules if r["type"] == "trend")
    check("多 50%" in trend["detail"], "趋势对比 +50%", trend["detail"])

    # 阈值可配置：把提醒线提到 120 分钟后，100 分钟会话不再触发
    cfg2 = json.loads(json.dumps(cfg))
    cfg2["insights"]["rules"]["long_session_min"] = 120
    rules2 = insights.rule_insights(agg, cfg2, prev)
    health2 = [r for r in rules2 if r["type"] == "health"]
    check(all("最长连续使用" not in r["detail"] for r in health2), "阈值提升后长会话不触发")
    check(any("深夜" in r["detail"] for r in health2), "深夜提醒不受影响")

    # 空数据安全
    empty = {"date": "2026-08-10", "session_count": 0, "total_active_ms": 0,
             "by_app": {}, "by_category": {}, "by_ai": {}, "by_browser": {},
             "by_contact": {}, "hourly_ms": [0] * 24, "sessions": []}
    check(insights.rule_insights(empty, cfg) == [], "空数据返回空列表")


def test_insights_ai_prompt():
    print("[test] AI 提示词隐私过滤（默认无标题/URL/联系人名；开启后含标题/URL，联系人仍不上送）")
    agg = _make_fake_agg()
    agg["sessions"] = agg["sessions"] + [{
        "start": "2026-08-10T20:00:00", "end": "2026-08-10T20:30:00",
        "duration_ms": 30 * 60000, "app": "Chrome", "title": "秘密项目资料",
        "url": "https://secret.example.com/project?token=abc", "category": "浏览器",
    }]
    prev = {"date": "2026-08-09", "session_count": 1, "total_active_ms": 3600000,
            "by_category": {}, "by_app": {}, "by_ai": {}, "by_browser": {},
            "by_contact": {}, "hourly_ms": [0] * 24, "sessions": []}
    cfg = classifier.load_config()
    p_default = insights.build_ai_prompt(agg, cfg, prev, False)
    check("张三" not in p_default, "默认不上送联系人名")
    check("秘密项目资料" not in p_default, "默认不上送窗口标题")
    check("secret.example.com" not in p_default, "默认不上送 URL")
    check("联系人数量" in p_default and "总活跃时长" in p_default, "默认仍含聚合统计")
    check("昨日活跃时长" in p_default, "提示词含昨日对比")

    p_raw = insights.build_ai_prompt(agg, cfg, prev, True)
    check("秘密项目资料" in p_raw and "secret.example.com" in p_raw, "开启后上送标题/URL")
    check("张三" not in p_raw, "即使开启也不上送联系人名")
    check("top" not in p_raw.lower() or "原始样本" in p_raw, "原始样本段存在")


def test_insights_ai_call():
    print("[test] AI 调用（urllib 请求体/响应解析/HTTP 错误/超时）")
    import urllib.error
    import urllib.request

    class FakeResp:
        def __init__(self, payload: bytes):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self._payload

    calls: list[urllib.request.Request] = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        return FakeResp(json.dumps({
            "choices": [{"message": {"content": '[{"type":"health","title":"健康","detail":"记得休息"}]'}}],
        }).encode("utf-8"))

    orig = insights.urllib.request.urlopen
    insights.urllib.request.urlopen = fake_urlopen
    try:
        text = insights._chat_completion({
            "base_url": "https://ai.example.test/v1",
            "api_key": "sk-test",
            "model": "deepseek-v4-flash",
            "timeout_s": 60,
        }, "测试提示词")
        check(text == '[{"type":"health","title":"健康","detail":"记得休息"}]', "响应 content 解析")
        check(len(calls) == 1, "恰好调用一次", str(len(calls)))
        req = calls[0]
        check(req.full_url == "https://ai.example.test/v1/chat/completions", "URL 拼接正确", req.full_url)
        check(req.get_header("Authorization") == "Bearer sk-test", "Authorization 头正确")
        body = json.loads(req.data.decode("utf-8"))
        check(body["model"] == "deepseek-v4-flash" and body["temperature"] == 0.7
              and body["max_tokens"] == 800, "请求体参数正确", str(body))
        check(body["messages"][0]["content"] == "测试提示词", "提示词入消息体")
    finally:
        insights.urllib.request.urlopen = orig

    def fail_http(req, timeout=None):
        raise urllib.error.HTTPError("https://ai.example.test/v1/chat/completions",
                                     500, "Server Error", {}, io.BytesIO(b"oops"))
    insights.urllib.request.urlopen = fail_http
    try:
        try:
            insights._chat_completion({
                "base_url": "https://ai.example.test/v1", "api_key": "k", "model": "m",
            }, "x")
            fail("HTTP 错误未抛出", "expected InsightsError")
        except insights.InsightsError as exc:
            check("HTTP 500" in str(exc), "非 200 -> InsightsError(HTTP 500)", str(exc))
    finally:
        insights.urllib.request.urlopen = orig

    def fail_timeout(req, timeout=None):
        raise TimeoutError("timed out")
    insights.urllib.request.urlopen = fail_timeout
    try:
        try:
            insights._chat_completion({
                "base_url": "https://ai.example.test/v1", "api_key": "k", "model": "m",
            }, "x")
            fail("超时未抛出", "expected InsightsError")
        except insights.InsightsError as exc:
            check("超时" in str(exc), "超时 -> InsightsError", str(exc))
    finally:
        insights.urllib.request.urlopen = orig


def test_insights_provider_presets():
    print("[test] AI provider 预设与自定义（内置预设 / 显式覆盖 / 无端点安全返回 None）")
    cfg = classifier.load_config()
    cfg2 = json.loads(json.dumps(cfg))
    ai = cfg2["insights"]["ai"]

    # 内置 DeepSeek 预设：不填 base_url/model 也能自动补全
    ai.update({"enabled": True, "provider": "deepseek", "base_url": "",
               "api_key": "sk-test", "model": ""})
    d = insights._discover_ai_config(cfg2)
    check(d is not None and "api.deepseek.com" in d["base_url"], "DeepSeek 预设自动补 base_url",
          str(d))
    check(d["model"] == "deepseek-chat", "DeepSeek 预设自动补模型", str(d))

    # 显式 base_url/model 优先于预设
    ai.update({"provider": "custom", "base_url": "https://custom.test/v1", "model": "my-model"})
    d = insights._discover_ai_config(cfg2)
    check(d is not None and d["base_url"] == "https://custom.test/v1" and d["model"] == "my-model",
          "自定义 provider 显式覆盖", str(d))

    # 自定义但没有 base_url -> 无法使用
    ai.update({"provider": "custom", "base_url": "", "model": "my-model"})
    d = insights._discover_ai_config(cfg2)
    check(d is None, "自定义无 base_url 返回 None", str(d))

    # 预设列表包含常用 provider 与 custom
    presets = {p["id"]: p for p in insights.list_provider_presets()}
    check({"opencodego", "openai", "deepseek", "moonshot", "openrouter", "zhipu", "qwen", "custom"}
          <= set(presets.keys()), "内置预设齐全", str(presets.keys()))
    check(presets["custom"]["base_url"] == "" and presets["custom"]["model"] == "",
          "custom 预设为空模板")


def test_insights_cache():
    print("[test] AI 洞察缓存（成功写缓存 / 二次调用不重发 / refresh 重发 / 失败不写缓存）")
    tmp = fresh_tmp("insights_cache")
    day = "2026-08-10"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "start": f"{day}T10:00:00", "end": f"{day}T11:00:00",
            "duration_ms": 3600000, "exe": "code.exe", "app": "VS Code",
            "title": "main.py", "category": "办公学习", "active": True,
        }, ensure_ascii=False) + "\n")

    cfg = classifier.load_config()
    cfg["data_root"] = tmp
    cfg["insights"]["ai"].update({
        "enabled": True, "base_url": "https://ai.example.test/v1",
        "api_key": "sk-test", "model": "deepseek-v4-flash",
    })
    calls: list[int] = []

    def fake_chat(_cfg, _prompt, **_kw):
        calls.append(1)
        return '[{"type":"study","title":"学习","detail":"测试建议"}]'

    orig = insights._chat_completion
    insights._chat_completion = fake_chat
    try:
        r1 = insights.ai_insights(day, tmp, cfg)
        check(r1["error"] is None and len(r1["insights"]) == 1, "首次生成成功", str(r1))
        check(len(calls) == 1, "首次真实调用一次")
        cache_path = os.path.join(tmp, day, "insights.json")
        check(os.path.isfile(cache_path), "成功写缓存")

        r2 = insights.ai_insights(day, tmp, cfg)
        check(len(calls) == 1 and r2["generated_at"] == r1["generated_at"], "二次调用读缓存不重发")
        r3 = insights.ai_insights(day, tmp, cfg, refresh=True)
        check(len(calls) == 2 and r3["generated_at"] is not None, "refresh 强制重发")

        # 未开启：不读缓存、不请求
        cfg_off = json.loads(json.dumps(cfg))
        cfg_off["insights"]["ai"]["enabled"] = False
        r_off = insights.ai_insights(day, tmp, cfg_off)
        check("未开启" in (r_off["error"] or ""), "未开启返回错误态", str(r_off))

        # 失败：不写缓存
        day2 = "2026-08-11"

        def fail_chat(_cfg, _prompt, **_kw):
            raise insights.InsightsError("模拟失败")
        insights._chat_completion = fail_chat
        r_fail = insights.ai_insights(day2, tmp, cfg)
        check(r_fail["error"] == "模拟失败", "失败返回错误", str(r_fail))
        check(not os.path.isfile(os.path.join(tmp, day2, "insights.json")), "失败不写缓存")
    finally:
        insights._chat_completion = orig
    shutil.rmtree(tmp, ignore_errors=True)


def test_dashboard_insights_api():
    print("[test] 仪表盘洞察 API（/api/insights 结构 + /api/insights/ai 错误态 + 非法日期 400）")
    import http.client
    import threading
    import dashboard

    tmp = fresh_tmp("dash_insights")
    day = "2026-08-10"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for row in (
            {"start": f"{day}T09:00:00", "end": f"{day}T10:00:00", "duration_ms": 3600000,
             "exe": "code.exe", "app": "VS Code", "title": "main.py",
             "category": "办公学习", "active": True},
            {"start": f"{day}T20:00:00", "end": f"{day}T23:00:00", "duration_ms": 3 * 3600000,
             "exe": "steam.exe", "app": "Steam", "title": "Steam",
             "category": "游戏", "active": True},
        ):
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    config_path = os.path.join(tmp, "config.json")
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({"insights": {
            "enabled": True, "in_report": True,
            "rules": {"long_session_min": 90, "late_night_hour": 23,
                      "game_alert_hours": 2, "study_goal_hours": 1, "game_ratio_warn": 0.4},
            "ai": {"enabled": False},
        }}, fh, ensure_ascii=False)
    classifier.invalidate_config_cache()

    server = dashboard.create_server(tmp, port=0, config_path=config_path)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def req(path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", path)
        r = conn.getresponse()
        data = json.loads(r.read().decode("utf-8", errors="replace"))
        conn.close()
        return r.status, data

    try:
        s, d = req(f"/api/insights?date={day}")
        check(s == 200, "/api/insights 200", str(s))
        check(d["date"] == day and isinstance(d["rules"], list) and len(d["rules"]) >= 2,
              "规则结构正确", str(d))
        check(d["ai_enabled"] is False and d["ai"] is None, "AI 关闭 -> ai_enabled=false / ai=null")
        check(any(r["type"] == "study" for r in d["rules"]), "规则含学习")
        check(any(r["type"] == "game" for r in d["rules"]), "规则含游戏")

        s, d = req(f"/api/insights/ai?date={day}")
        check(s == 200 and d["ai_enabled"] is False, "/api/insights/ai 未开启错误态 200")
        check("未开启" in (d.get("ai") or {}).get("error", ""), "错误态文案", str(d))

        s, _ = req("/api/insights?date=bad")
        check(s == 400, "/api/insights 非法日期 400", str(s))
        s, _ = req("/api/insights/ai?date=../2026-08-10")
        check(s == 400, "/api/insights/ai 路径穿越日期 400", str(s))
    finally:
        server.shutdown()
        server.server_close()
    shutil.rmtree(tmp, ignore_errors=True)


def test_dashboard_ai_settings_api():
    print("[test] 仪表盘 AI 设置 API（开关 + 预设 + 保存/保留密钥 + 校验）")
    import http.client
    import threading
    import dashboard

    tmp = fresh_tmp("dash_ai_settings")
    config_path = os.path.join(tmp, "config.json")
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({
            "insights": {
                "ai": {
                    "enabled": False, "provider": "opencodego",
                    "base_url": "", "api_key": "old-secret",
                    "model": "deepseek-v4-flash", "timeout_s": 60,
                    "send_raw_titles": False, "language": "zh",
                }
            }
        }, fh, ensure_ascii=False)
    classifier.invalidate_config_cache()
    server = dashboard.create_server(tmp, port=0, config_path=config_path)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def req(method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path,
                     body=json.dumps(body, ensure_ascii=False) if body is not None else None,
                     headers=headers)
        r = conn.getresponse()
        data = json.loads(r.read().decode("utf-8", errors="replace"))
        conn.close()
        return r.status, data

    try:
        s, d = req("GET", "/api/insights/settings")
        check(s == 200 and d["ai"]["enabled"] is False, "GET 设置返回当前关闭状态", str(d))
        check(d["ai"]["api_key_set"] is True, "已有 API Key 只返回已设置标志", str(d))
        check("api_key" not in d["ai"], "不回显真实 API Key", str(d))
        check(any(p["id"] == "deepseek" for p in d["presets"]), "预设列表含 DeepSeek")

        # 开启并选择 DeepSeek 预设，不填 base/model 也会自动落盘预设值；空 key 保留旧值
        s, d = req("POST", "/api/insights/settings", {
            "enabled": True, "provider": "deepseek", "base_url": "",
            "api_key": "", "model": "", "timeout_s": 90,
            "send_raw_titles": False, "language": "zh",
        })
        check(s == 200 and d.get("ok") is True, "保存 AI 设置成功", str(d))
        check(d["ai"]["enabled"] is True and d["ai"]["provider"] == "deepseek", "开关与 provider 已保存")
        check("api.deepseek.com" in d["ai"]["base_url"] and d["ai"]["model"] == "deepseek-chat",
              "预设 base/model 已落盘", str(d))
        check(d["ai"]["api_key_set"] is True, "空 API Key 保留旧值", str(d))

        # 开启自定义但没有 Base URL -> 400
        s, d = req("POST", "/api/insights/settings", {
            "enabled": True, "provider": "custom", "base_url": "",
            "api_key": "", "model": "m", "timeout_s": 60,
            "send_raw_titles": False, "language": "zh",
        })
        check(s == 400, "开启自定义无 Base URL 被拒", str(d))

        # 关闭开关允许空端点
        s, d = req("POST", "/api/insights/settings", {
            "enabled": False, "provider": "custom", "base_url": "",
            "api_key": "", "model": "", "timeout_s": 60,
            "send_raw_titles": False, "language": "zh",
        })
        check(s == 200 and d["ai"]["enabled"] is False, "关闭开关可保存", str(d))
        # 再次读取确认密钥仍在
        s, d = req("GET", "/api/insights/settings")
        check(d["ai"]["api_key_set"] is True, "关闭后密钥仍保留", str(d))
    finally:
        server.shutdown()
        server.server_close()
    shutil.rmtree(tmp, ignore_errors=True)


def test_report_insights_section():
    print("[test] 日报「今日建议」段（in_report=true 出现；false 不出现）")
    tmp = fresh_tmp("report_insights")
    day = "2026-08-10"
    os.makedirs(os.path.join(tmp, day), exist_ok=True)
    with open(os.path.join(tmp, day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for row in (
            {"start": f"{day}T09:00:00", "end": f"{day}T11:00:00", "duration_ms": 2 * 3600000,
             "exe": "code.exe", "app": "VS Code", "title": "main.py",
             "category": "办公学习", "active": True},
            {"start": f"{day}T20:00:00", "end": f"{day}T22:00:00", "duration_ms": 2 * 3600000,
             "exe": "steam.exe", "app": "Steam", "title": "Steam",
             "category": "游戏", "active": True},
        ):
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    config_path = os.path.join(tmp, "config.json")
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({"insights": {
            "enabled": True, "in_report": True,
            "rules": {"long_session_min": 90, "late_night_hour": 23,
                      "game_alert_hours": 2, "study_goal_hours": 1, "game_ratio_warn": 0.4},
            "ai": {"enabled": False},
        }}, fh, ensure_ascii=False)
    classifier.invalidate_config_cache()
    report.generate_day_report(day, tmp)
    md = open(os.path.join(tmp, day, "report.md"), encoding="utf-8").read()
    check("## 📌 今日建议" in md, "in_report=true 含今日建议段")
    check("- [学习]" in md and "今日学习" in md, "含学习建议")
    check("- [游戏]" in md, "含游戏建议")

    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({"insights": {"enabled": True, "in_report": False}}, fh, ensure_ascii=False)
    classifier.invalidate_config_cache()
    report.generate_day_report(day, tmp)
    md2 = open(os.path.join(tmp, day, "report.md"), encoding="utf-8").read()
    check("今日建议" not in md2, "in_report=false 无今日建议段")
    shutil.rmtree(tmp, ignore_errors=True)


def test_updater():
    print("[test] 更新模块（版本比较/检测/下载校验/脚本生成/信号）")
    check(updater.parse_version("v1.10.2-beta") == (1, 10, 2), "parse_version")
    check(updater.version_gt("1.6.0", "1.5.0") is True, "version_gt true")
    check(updater.version_gt("1.5.0", "1.6.0") is False, "version_gt false")

    import hashlib

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    fake_json = json.dumps({
        "tag_name": "v9.9.9", "name": "v9.9.9",
        "published_at": "2026-08-17T00:00:00Z",
        "html_url": "https://example.com/release", "body": "notes",
        "assets": [{
            "name": "UsageMonitor.exe", "size": 123,
            "browser_download_url": "https://example.com/exe",
        }],
    }).encode("utf-8")
    orig = updater.urllib.request.urlopen
    updater.urllib.request.urlopen = lambda req, timeout=None: FakeResp(fake_json)
    try:
        r = updater.check_for_update(current="1.6.0")
        check(r["has_update"] and r["latest"] == "9.9.9", "check_for_update 新版本", str(r))
    finally:
        updater.urllib.request.urlopen = orig

    tmp = fresh_tmp("updater_download")
    content = b"hello world\n"
    sha = hashlib.sha256(content).hexdigest()

    class FakeDLResp:
        headers = {"Content-Length": str(len(content))}

        def __init__(self):
            self._done = False

        def read(self, chunk=-1):
            if self._done:
                return b""
            self._done = True
            return content

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    updater.urllib.request.urlopen = lambda req, timeout=None: FakeDLResp()
    try:
        dest = os.path.join(tmp, "UsageMonitor.exe")
        updater.download("https://example.com/exe", dest,
                         expected_size=len(content), expected_digest="sha256:" + sha)
        check(open(dest, "rb").read() == content, "download 内容正确")
        try:
            updater.download("https://example.com/exe", os.path.join(tmp, "bad.exe"),
                             expected_size=999)
            fail("错误大小未抛异常", "expected UpdateError")
        except updater.UpdateError:
            ok("错误大小抛 UpdateError")
    finally:
        updater.urllib.request.urlopen = orig
    shutil.rmtree(tmp, ignore_errors=True)

    script = updater.build_update_script("C:/src/UsageMonitor.exe", "C:/dst/UsageMonitor.exe")
    check("Copy-Item" in script and "UsageMonitor" in script, "build_update_script 包含替换逻辑")
    tmp_apply = fresh_tmp("updater_apply")
    src = os.path.join(tmp_apply, "UsageMonitor.exe")
    dst = os.path.join(tmp_apply, "UsageMonitor_new.exe")
    with open(src, "wb") as fh:
        fh.write(b"x")
    with open(dst, "wb") as fh:
        fh.write(b"y")
    try:
        res = updater.apply_update(src, dst, dry_run=True)
        check(res.get("dry_run") is True and os.path.isfile(res.get("script", "")),
              "apply_update dry_run")
    finally:
        shutil.rmtree(tmp_apply, ignore_errors=True)

    tmp_signal = fresh_tmp("updater_signal")
    updater.request_update(tmp_signal)
    check(os.path.isfile(os.path.join(tmp_signal, updater.UPDATE_REQUEST_FILE)),
          "request_update 写信号")
    updater.clear_update_request(tmp_signal)
    check(not os.path.isfile(os.path.join(tmp_signal, updater.UPDATE_REQUEST_FILE)),
          "clear_update_request 清除信号")
    shutil.rmtree(tmp_signal, ignore_errors=True)


def test_ai_sessions():
    print("[test] AI 会话深度统计（JSONL/JSON 解析、按日过滤、关闭态）")
    tmp = fresh_tmp("ai_sessions")
    day = "2026-08-10"
    opencode_dir = os.path.join(tmp, "opencode")
    chatgpt_dir = os.path.join(tmp, "chatgpt")
    os.makedirs(opencode_dir, exist_ok=True)
    os.makedirs(chatgpt_dir, exist_ok=True)
    opencode_path = os.path.join(opencode_dir, "sessions.jsonl")
    with open(opencode_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "timestamp": f"{day}T10:00:00", "role": "user", "content": "帮我写代码",
        }, ensure_ascii=False) + "\n")
        fh.write(json.dumps({
            "timestamp": f"{day}T10:01:00", "role": "assistant", "content": "第一行\n第二行",
        }, ensure_ascii=False) + "\n")
        fh.write(json.dumps({
            "timestamp": "2026-08-11T10:00:00", "role": "assistant", "content": "不算今天",
        }, ensure_ascii=False) + "\n")
    chatgpt_path = os.path.join(chatgpt_dir, "conversations.json")
    with open(chatgpt_path, "w", encoding="utf-8") as fh:
        json.dump({
            "messages": [
                {"timestamp": f"{day}T11:00:00", "role": "user", "content": "你好"},
                {"timestamp": f"{day}T11:01:00", "role": "assistant", "content": "你好！"},
            ]
        }, fh, ensure_ascii=False)

    cfg = {
        "ai_sessions": {
            "enabled": True,
            "paths": {"opencode": [opencode_dir], "chatgpt": [chatgpt_dir]},
        }
    }
    result = ai_sessions.collect(day, cfg)
    check(result["enabled"] is True, "开启状态")
    check(result["found"] is True, "发现会话文件")
    check(result["total"]["turns"] == 4, "当天共 4 条消息", str(result["total"]))
    check(result["total"]["user_messages"] == 2, "用户消息 2 条", str(result["total"]))
    check(result["total"]["assistant_messages"] == 2, "助手消息 2 条", str(result["total"]))
    check(result["total"]["generated_lines"] == 3, "助手生成 3 行", str(result["total"]))
    check("opencode" in result["tools"] and "chatgpt" in result["tools"], "按工具分组")

    cfg_off = {"ai_sessions": {"enabled": False}}
    off = ai_sessions.collect(day, cfg_off)
    check(off["enabled"] is False and off["found"] is False, "默认关闭态")
    shutil.rmtree(tmp, ignore_errors=True)


def test_ai_sessions_more_tools():
    print("[test] AI 会话深度统计（Cursor 嵌套 / DSH 可配置路径）")
    tmp = fresh_tmp("ai_sessions_more")
    day = "2026-08-10"
    cursor_dir = os.path.join(tmp, "cursor")
    dsh_dir = os.path.join(tmp, "dsh")
    os.makedirs(cursor_dir, exist_ok=True)
    os.makedirs(dsh_dir, exist_ok=True)
    with open(os.path.join(cursor_dir, "conversations.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "conversations": {
                "c1": {
                    "messages": [
                        {"timestamp": f"{day}T12:00:00", "role": "user", "content": "hi"},
                        {"timestamp": f"{day}T12:01:00", "role": "assistant", "content": "a\nb"},
                    ]
                }
            }
        }, fh, ensure_ascii=False)
    with open(os.path.join(dsh_dir, "sessions.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": f"{day}T13:00:00", "role": "user", "content": "dsh q"},
                            ensure_ascii=False) + "\n")
        fh.write(json.dumps({"timestamp": f"{day}T13:01:00", "role": "assistant", "content": "dsh a"},
                            ensure_ascii=False) + "\n")

    cfg = {
        "ai_sessions": {
            "enabled": True,
            "paths": {"cursor": [cursor_dir], "dsh": [dsh_dir]},
        }
    }
    result = ai_sessions.collect(day, cfg)
    check("cursor" in result["tools"] and "dsh" in result["tools"],
          "识别 Cursor 与 DSH", str(list(result["tools"].keys())))
    check(result["total"]["turns"] == 4, "共 4 条消息", str(result["total"]))
    check(result["tools"]["cursor"]["generated_lines"] == 2, "Cursor 生成 2 行",
          str(result["tools"]["cursor"]))
    shutil.rmtree(tmp, ignore_errors=True)


def test_sqlite_store():
    print("[test] SQLite 后端（写入/回填/幂等/重建/查询）")
    tmp = fresh_tmp("sqlite_store")
    day = "2026-08-10"
    day_dir = os.path.join(tmp, day)
    os.makedirs(day_dir, exist_ok=True)
    recs = [
        {"start": f"{day}T10:00:00", "end": f"{day}T10:05:00", "duration_ms": 300000,
         "exe": "code.exe", "app": "VS Code", "title": "a", "category": "开发工具", "active": True},
        {"start": f"{day}T11:00:00", "end": f"{day}T11:10:00", "duration_ms": 600000,
         "exe": "opencode.exe", "app": "OpenCode", "title": "b", "category": "AI编程",
         "ai_tool": "opencode", "active": True},
    ]
    with open(os.path.join(day_dir, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    st0 = sqlite_store.status(tmp)
    check(not st0["exists"], "初始无 usage.db", str(st0))
    result = sqlite_store.backfill(tmp)
    check(result["inserted"] == 2 and result["days"] == 1, "回填 2 条", str(result))
    st1 = sqlite_store.status(tmp)
    check(st1["exists"] and st1["rows"] == 2, "回填后 rows=2", str(st1))
    rows = sqlite_store.read_day(tmp, day)
    check(len(rows) == 2 and rows[0]["exe"] == "code.exe", "read_day 返回数据", str(rows))
    result2 = sqlite_store.backfill(tmp)
    check(result2["inserted"] == 0 and result2["skipped"] == 2, "重复回填幂等", str(result2))

    day2 = "2026-08-11"
    monitor.append_session_record(day2, {
        "start": f"{day2}T09:00:00", "end": f"{day2}T09:01:00", "duration_ms": 60000,
        "exe": "chatgpt.exe", "app": "ChatGPT", "title": "c", "category": "AI编程",
        "ai_tool": "chatgpt", "active": True,
    }, tmp, sqlite_enabled=True)
    rows2 = sqlite_store.read_day(tmp, day2)
    check(len(rows2) == 1 and rows2[0]["ai_tool"] == "chatgpt", "monitor 同步写 SQLite", str(rows2))

    month_agg = report.aggregate_month("2026-08", tmp)
    check(month_agg["session_count"] == 3 and month_agg["total_active_ms"] == 960000,
          "SQLite 月聚合（不再逐日扫 JSONL）", str(month_agg))

    week_agg = report.aggregate_days([day, day2], tmp)
    check(week_agg["session_count"] == 3 and week_agg["total_active_ms"] == 960000,
          "SQLite 周聚合（多日范围一次查询）", str(week_agg))

    # 制造一条只写 JSONL 不写 SQLite 的记录，verify 应发现差异
    extra = {
        "start": f"{day}T20:00:00", "end": f"{day}T20:01:00", "duration_ms": 60000,
        "exe": "manual.exe", "app": "Manual", "title": "m", "category": "其他",
        "active": True,
    }
    with open(os.path.join(tmp, day, "usage.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(extra, ensure_ascii=False) + "\n")
    v = sqlite_store.verify(tmp)
    check(len(v["mismatches"]) == 1 and v["mismatches"][0]["day"] == day,
          "verify 发现 JSONL/SQLite 差异", str(v))

    result3 = sqlite_store.rebuild(tmp)
    check(result3["inserted"] == 4 and result3["skipped"] == 0, "重建全量回填", str(result3))
    shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("=" * 60)
    print("电脑使用情况监控 · 完整集成测试")
    print("=" * 60)
    tests = [
        test_switch_timing, test_idle_truncation, test_contact_and_main_title,
        test_browser_categories, test_ai_tool_detection, test_ai_false_positive,
        test_ai_own_window, test_day_rollover, test_title_blacklist,
        test_static_zero_write, test_pause_resume, test_retention,
        test_report_pipeline, test_inventory, test_month_and_json,
        test_contact_aliases, test_browser_history, test_cross_day_isolation,
        test_reclassify, test_dimension_refinements, test_dashboard_api,
        test_dashboard_update_api,
        test_electron_shell_detection, test_app_groups, test_report_balloon_once_per_day,
        test_insights_rules, test_insights_ai_prompt, test_insights_ai_call,
        test_insights_provider_presets, test_insights_cache,
        test_dashboard_insights_api, test_dashboard_ai_settings_api,
        test_report_insights_section,
        test_updater,
        test_ai_sessions,
        test_ai_sessions_more_tools,
        test_sqlite_store,
    ]
    for t in tests:
        t()
    print("=" * 60)
    print(f"ALL {PASSED} TESTS PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
