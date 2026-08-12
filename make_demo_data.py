# -*- coding: utf-8 -*-
"""make_demo_data.py — 生成演示数据（截图/试用用，全部为虚构内容，无任何真实数据）。

用法：
    python make_demo_data.py [输出目录]     # 默认 ./demo_data

生成：
    <out>/2026-08-12/usage.jsonl            完整演示日会话（14 个典型场景）
    <out>/2026-07-30 ~ 2026-08-11/          前 13 天轻量会话（趋势图用）
    <out>/2026-08-12/software_inventory.json 演示软件清单
    <out>/history/Default/History            演示浏览器历史（SQLite）
    <out>/config.json                        演示配置（data_root=输出目录，浏览器历史指向演示库）

配合截图脚本可生成 docs/screenshots/ 下的项目截图。
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import sqlite3
import sys
import time

DAY = "2026-08-12"
_OFFSET = 11644473600


def _iso(h: int, m: int = 0, s: int = 0) -> str:
    return f"{DAY}T{h:02d}:{m:02d}:{s:02d}"


def _sec(a: tuple, b: tuple) -> int:
    """两个 (h,m,s) 之间的秒数。"""
    t0 = datetime.datetime(2026, 8, 12, *a)
    t1 = datetime.datetime(2026, 8, 12, *b)
    return int((t1 - t0).total_seconds())


def _session(h0, m0, h1, m1, exe, app, title, category, **extra):
    start = _iso(h0, m0)
    end = _iso(h1, m1)
    rec = {
        "start": start, "end": end,
        "duration_ms": _sec((h0, m0, 0), (h1, m1, 0)) * 1000,
        "exe": exe, "app": app, "title": title, "category": category,
        "contact": None, "ai_tool": None, "active": True,
    }
    rec.update(extra)
    return rec


def make_sessions() -> list[dict]:
    """演示日的典型会话（覆盖各分类/维度字段）。"""
    return [
        # 上午：写代码
        _session(9, 0, 10, 30, "Code.exe", "VS Code", "main.py - 演示项目 - Visual Studio Code",
                 "开发工具", subcategory="编辑器"),
        # 学习：B 站 C 语言教程
        _session(10, 30, 11, 0, "chrome.exe", "Chrome",
                 "【合集】C语言入门到精通 - bilibili", "浏览器",
                 browser_category="视频", subcategory="视频",
                 url="https://www.bilibili.com/video/BV1Demo4X"),
        # 社交：微信联系张三
        _session(11, 0, 11, 20, "wechat.exe", "微信", "张三", "社交聊天",
                 contact="张三", subcategory="社交聊天"),
        # AI 编程：终端里跑 π（pi agent）
        _session(11, 20, 12, 0, "wt.exe", "Windows Terminal", "π - 演示项目 - niangao",
                 "AI编程", ai_tool="pi agent", subcategory="终端", window_state="maximized"),
        # 代码：GitHub
        _session(12, 0, 12, 30, "chrome.exe", "Chrome", "GitHub - demo/usage-monitor",
                 "浏览器", browser_category="代码", subcategory="代码",
                 url="https://github.com/demo/usage-monitor"),
        # 下午：MOOC 课程
        _session(13, 0, 14, 30, "chrome.exe", "Chrome",
                 "C语言程序设计_中国大学MOOC(慕课)", "浏览器",
                 browser_category="学习", subcategory="学习",
                 url="https://www.icourse163.org/course/DEMO001"),
        # AI 编程：opencode 桌面版
        _session(14, 30, 15, 0, "opencode.exe", "opencode", "OpenCode",
                 "AI编程", ai_tool="opencode", subcategory="AI编程"),
        # 娱乐：Steam 游戏平台
        _session(15, 0, 16, 0, "steam.exe", "Steam", "Steam", "游戏",
                 subcategory="游戏平台", window_state="normal"),
        # 办公：Word 整理错题
        _session(16, 0, 16, 30, "winword.exe", "Word", "C语言错题本_合并版.docx - Word",
                 "办公学习", subcategory="文档办公"),
        # 社交：钉钉李四
        _session(16, 30, 16, 45, "dingtalk.exe", "钉钉", "与 李四 的会话", "社交聊天",
                 contact="李四", subcategory="社交聊天"),
        # 学习：知乎
        _session(16, 45, 17, 0, "chrome.exe", "Chrome", "如何系统学习 C 语言？ - 知乎",
                 "浏览器", browser_category="学习", subcategory="学习",
                 url="https://www.zhihu.com/question/demo-c"),
        # 终端：npm 构建
        _session(17, 0, 17, 20, "wt.exe", "Windows Terminal", "npm run build - niangao",
                 "开发工具", subcategory="终端", term_tool="npm"),
        # 系统：文件资源管理器
        _session(17, 20, 17, 35, "explorer.exe", "文件资源管理器",
                 "演示项目 - 文件资源管理器", "系统", subcategory="系统"),
    ]


def make_light_sessions(day: str) -> list[dict]:
    """前 13 天的轻量会话（每天 2-4 条，趋势图用）。"""
    templates = [
        (9, 12, "Code.exe", "VS Code", "main.py - 演示项目", "开发工具", {"subcategory": "编辑器"}),
        (13, 14, "chrome.exe", "Chrome", "C语言程序设计_中国大学MOOC(慕课)", "浏览器",
         {"browser_category": "学习", "subcategory": "学习"}),
        (15, 16, "chrome.exe", "Chrome", "GitHub - demo/usage-monitor", "浏览器",
         {"browser_category": "代码", "subcategory": "代码"}),
        (20, 21, "steam.exe", "Steam", "Steam", "游戏", {"subcategory": "游戏平台"}),
    ]
    import random
    rng = random.Random(hash(day) & 0xFFFF)
    chosen = [t for t in templates if rng.random() < 0.75]
    recs = []
    for h0, h1, exe, app, title, cat, extra in chosen:
        m0 = rng.randint(0, 40)
        m1 = min(59, m0 + rng.randint(15, 50))
        rec = _session(h0, m0, h0, m1, exe, app, title, cat, **extra)
        rec["start"] = rec["start"].replace(DAY, day)
        rec["end"] = rec["end"].replace(DAY, day)
        recs.append(rec)
    return recs


def make_inventory() -> dict:
    """演示软件清单（虚构）。"""
    apps = [
        ("Visual Studio Code", "Code.exe", "开发工具"), ("Windows Terminal", "wt.exe", "开发工具"),
        ("PyCharm", "pycharm64.exe", "开发工具"), ("Docker Desktop", "docker desktop.exe", "开发工具"),
        ("Git", "git.exe", "开发工具"), ("opencode", "opencode.exe", "AI编程"),
        ("ChatGPT", "chatgpt.exe", "AI编程"), ("Trae", "trae.exe", "AI编程"),
        ("Chrome", "chrome.exe", "浏览器"), ("Edge", "msedge.exe", "浏览器"),
        ("Tabbit Browser", "tabbit browser.exe", "浏览器"),
        ("微信", "wechat.exe", "社交聊天"), ("QQ", "qq.exe", "社交聊天"),
        ("钉钉", "dingtalk.exe", "社交聊天"), ("Telegram", "telegram.exe", "社交聊天"),
        ("Word", "winword.exe", "办公学习"), ("Excel", "excel.exe", "办公学习"),
        ("WPS Office", "wps.exe", "办公学习"), ("Obsidian", "obsidian.exe", "办公学习"),
        ("Notion", "Notion.exe", "办公学习"), ("MarkText", "marktext.exe", "办公学习"),
        ("Steam", "steam.exe", "游戏"), ("WeGame", "wegame.exe", "游戏"),
        ("赛博朋克2077", "cyberpunk2077.exe", "游戏"), ("英雄联盟", "leagueclient.exe", "游戏"),
        ("PotPlayer", "PotPlayerMini64.exe", "影音娱乐"), ("VLC", "vlc.exe", "影音娱乐"),
        ("QQ音乐", "QQMusic.exe", "影音娱乐"), ("网易云音乐", "cloudmusic.exe", "影音娱乐"),
        ("B站客户端", "bilibili.exe", "影音娱乐"), ("Spotify", "Spotify.exe", "影音娱乐"),
        ("文件资源管理器", "explorer.exe", "系统"), ("任务管理器", "Taskmgr.exe", "系统"),
        ("设置", "SystemSettings.exe", "系统"),
    ]
    return {
        "date": DAY, "scanned_at": f"{DAY}T08:00:00", "count": len(apps),
        "apps": [{"name": n, "exe": e.lower(), "category": c, "source": ["registry"], "running": False}
                 for n, e, c in apps],
    }


def make_history(db_path: str) -> None:
    """演示浏览器历史：与演示日会话时间对齐的访问记录。"""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT,
            visit_count INTEGER DEFAULT 1, typed_count INTEGER DEFAULT 0, last_visit_time INTEGER, hidden INTEGER DEFAULT 0);
        CREATE TABLE visits (id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL,
            visit_time INTEGER NOT NULL, from_visit INTEGER, transition INTEGER,
            segment_id INTEGER, visit_duration INTEGER DEFAULT 0);
    """)
    visits = [
        # (时刻, 停留秒, url, title)
        (("10:31", 1500), "https://www.bilibili.com/video/BV1Demo4X", "【合集】C语言入门到精通 - bilibili"),
        (("10:57", 120), "https://www.bilibili.com/", "哔哩哔哩 (゜-゜)つロ 干杯~"),
        (("12:01", 1200), "https://github.com/demo/usage-monitor", "GitHub - demo/usage-monitor"),
        (("12:21", 300), "https://github.com/demo/usage-monitor/issues/1", "Issue: 支持自定义分类 - demo/usage-monitor"),
        (("13:05", 4500), "https://www.icourse163.org/course/DEMO001", "C语言程序设计_中国大学MOOC(慕课)"),
        (("14:15", 300), "https://www.icourse163.org/spoc/learn/DEMO001", "第一章 概述 - 中国大学MOOC"),
        (("16:45", 700), "https://www.zhihu.com/question/demo-c", "如何系统学习 C 语言？ - 知乎"),
    ]
    for (hms, dur), url, title in visits:
        hh, mm = map(int, hms.split(":"))
        ts = datetime.datetime(2026, 8, 12, hh, mm)
        ft = int((time.mktime(ts.timetuple()) + _OFFSET) * 1e6)
        conn.execute("INSERT INTO urls (url, title, last_visit_time) VALUES (?, ?, ?)", (url, title, ft))
        conn.execute("INSERT INTO visits (url, visit_time, visit_duration) VALUES (?, ?, ?)",
                     (conn.execute("SELECT last_insert_rowid()").fetchone()[0], ft, dur * 1_000_000))
    conn.commit()
    conn.close()


def make_config(out_dir: str) -> dict:
    """演示配置：data_root=输出目录，浏览器历史指向演示库（隔离真实数据）。"""
    cfg = {
        "poll_interval_s": 5, "idle_threshold_s": 180, "retention_days": 90,
        "data_root": out_dir,
        "browser_history_enabled": True,
        "browser_history": {
            "chrome": {"user_data": os.path.join(out_dir, "history")},
        },
    }
    return cfg


def main(argv: list[str] | None = None) -> int:
    out = argv[0] if argv else os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_data")
    out = os.path.abspath(out)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)

    # 完整演示日 + 前 13 天轻量
    sessions = make_sessions()
    for i in range(1, 14):
        d = (datetime.date(2026, 8, 12) - datetime.timedelta(days=i)).isoformat()
        day_dir = os.path.join(out, d)
        os.makedirs(day_dir, exist_ok=True)
        with open(os.path.join(day_dir, "usage.jsonl"), "w", encoding="utf-8") as fh:
            for rec in make_light_sessions(d):
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    day_dir = os.path.join(out, DAY)
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, "usage.jsonl"), "w", encoding="utf-8") as fh:
        for rec in sessions:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with open(os.path.join(day_dir, "software_inventory.json"), "w", encoding="utf-8") as fh:
        json.dump(make_inventory(), fh, ensure_ascii=False, indent=2)

    make_history(os.path.join(out, "history", "Default", "History"))
    with open(os.path.join(out, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(make_config(out), fh, ensure_ascii=False, indent=2)

    print(f"演示数据已生成: {out}")
    print(f"  会话: {out}\\{DAY}\\usage.jsonl（{len(sessions)} 条）")
    print(f"  清单: {out}\\{DAY}\\software_inventory.json")
    print(f"  历史: {out}\\history\\Default\\History")
    print(f"  配置: {out}\\config.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
