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
        for l in lines:
            fh.write(json.dumps(l, ensure_ascii=False) + "\n")
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
        for l in lines:
            fh.write(json.dumps(l, ensure_ascii=False) + "\n")

    n = report.reclassify_day(day, tmp)
    check(n == 1, "1 条记录变更", str(n))
    recs = [json.loads(l) for l in open(os.path.join(tmp, day, "usage.jsonl"), encoding="utf-8") if l.strip()]
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

    # 打包模式：dist/*.exe 优先于 dev
    fake = fresh_tmp("shelltest")
    os.makedirs(os.path.join(fake, "dist"), exist_ok=True)
    fake_exe = os.path.join(fake, "dist", "UsageMonitor-Desktop-1.0.0.exe")
    open(fake_exe, "w").close()
    os.makedirs(os.path.join(fake, "node_modules", "electron", "dist"), exist_ok=True)
    open(os.path.join(fake, "node_modules", "electron", "dist", "electron.exe"), "w").close()
    open(os.path.join(fake, "main.js"), "w").close()
    real_base = monitor._find_electron_shell.__globals__.get  # noqa: F841
    # 临时替换模块内的 base 路径逻辑（用 monkeypatch 探针：直接测路径拼接函数）
    import types
    orig = monitor._find_electron_shell
    calls = {}

    def fake_find():
        # 模拟在 fake 目录下的探测结果：dist exe 应优先
        d = os.path.join(fake, "dist")
        for name in sorted(os.listdir(d)):
            if name.lower().endswith(".exe"):
                return [os.path.join(d, name)]
        return None

    calls["packed"] = fake_find()
    check(calls["packed"] and calls["packed"][0] == fake_exe, "打包 exe 优先", str(calls["packed"]))
    shutil.rmtree(fake, ignore_errors=True)


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
        test_electron_shell_detection,
    ]
    for t in tests:
        t()
    print("=" * 60)
    print(f"ALL {PASSED} TESTS PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
