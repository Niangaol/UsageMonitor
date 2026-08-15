# -*- coding: utf-8 -*-
"""browser_history.py — 浏览器 URL 级历史解析（Phase 3，Chromium 系：Chrome/Edge；Firefox）。

功能：
- 读取浏览器 History SQLite（urls/visits 表）或 Firefox places.sqlite（moz_places/moz_historyvisits）
  得到某天访问的 URL 明细（时间/域名/标题/分类）；
- 文件锁安全：把 History 复制到临时目录后以 immutable 只读方式打开，绝不在浏览器锁上阻塞；
  复制失败时退回 sqlite3 备份 API；全程只读，绝不修改浏览器数据；
- 隐私：URL/标题经 title_blacklist 掩蔽为 [已隐藏]；
- 分类：域名+标题按 config browser_categories 规则归为 视频/代码/学习/其他，
  供日报追加"浏览器访问明细"章节。

用法：
    python browser_history.py --today
    python browser_history.py --day 2026-08-08
    python browser_history.py --list-browsers
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import classifier  # noqa: E402
import paths  # noqa: E402

_FILETIME_EPOCH_OFFSET = 11644473600  # 1601-01-01 -> 1970-01-01（秒）
_FILETIME_EPOCH_OFFSET_US = 11644473600000000  # 1601-01-01 -> 1970-01-01（微秒）
_MAX_REPORT_ROWS = 100
_MAX_VISIT_DURATION_S = 6 * 3600  # 单次访问停留上限 6 小时（防御历史脏数据）


# ---------------------------------------------------------------------------
# 浏览器发现
# ---------------------------------------------------------------------------
def _default_user_data(name: str) -> str | None:
    """浏览器的 user_data / Profiles 根目录默认路径（None = 不支持自动探测）。"""
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    table = {
        "chrome": os.path.join(local, r"Google\Chrome\User Data"),
        "edge": os.path.join(local, r"Microsoft\Edge\User Data"),
        "tabbit": os.path.join(local, r"Tabbit Browser\User Data"),
        # Firefox 的 Places 根目录是 %APPDATA%\Mozilla\Firefox\Profiles
        "firefox": os.path.join(roaming, r"Mozilla\Firefox\Profiles"),
    }
    return table.get(name)


def find_history_dbs(config: dict | None = None) -> list[dict]:
    """发现 Chromium 系 + Firefox 的浏览历史数据库。

    返回 [{"browser": "chrome", "profile": "Default", "db": "绝对路径"}]。
    config.browser_history 可覆盖 user_data / Profiles 根目录路径（None = 自动探测）。
    Firefox 用 %APPDATA%\\Mozilla\\Firefox\\Profiles，取最近修改的 places.sqlite profile。
    """
    if config is None:
        config = classifier.load_config()
    specs = config.get("browser_history", {})
    if not isinstance(specs, dict):
        specs = {}

    out: list[dict] = []
    skip_profiles = {"System Profile", "Guest Profile", "Default Wallet", "Snapshots", "GrShaderCache", "ShaderCache", "GraphiteDawnCache", "DawnGraphiteCache", "DawnWebGPUCache", "GPUCache"}
    # Firefox 走专门发现逻辑，不进 Chromium 式扫描
    firefox_spec = specs.get("firefox") if isinstance(specs, dict) else None
    firefox_root = (firefox_spec.get("user_data") if isinstance(firefox_spec, dict)
                    else None) or _default_user_data("firefox")
    for name, spec in specs.items():
        if name == "firefox":
            continue
        user_data = spec.get("user_data") if isinstance(spec, dict) else None
        root = user_data or _default_user_data(name)
        if not root or not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for entry in entries:
            if entry in skip_profiles:
                continue
            profile_dir = os.path.join(root, entry)
            if not os.path.isdir(profile_dir):
                continue
            db = os.path.join(profile_dir, "History")
            if os.path.isfile(db):
                out.append({"browser": name, "profile": entry, "db": db})

    # Firefox：Profiles 目录下找含 places.sqlite 的 profile，取最近修改者
    if firefox_root and os.path.isdir(firefox_root):
        best: tuple[float, str, str] | None = None
        try:
            entries = os.listdir(firefox_root)
        except OSError:
            entries = []
        for entry in entries:
            profile_dir = os.path.join(firefox_root, entry)
            db = os.path.join(profile_dir, "places.sqlite")
            if not os.path.isdir(profile_dir) or not os.path.isfile(db):
                continue
            try:
                mtime = os.path.getmtime(db)
            except OSError:
                mtime = 0.0
            if best is None or mtime > best[0]:
                best = (mtime, entry, db)
        if best is not None:
            out.append({"browser": "firefox", "profile": best[1], "db": best[2]})
    return out


# ---------------------------------------------------------------------------
# 锁安全读取
# ---------------------------------------------------------------------------
def _copy_db(db_path: str, tmpdir: str) -> str | None:
    """复制 History(+wal) 到临时目录，最多重试 3 次（间隔 0.1s）；失败返回 None。"""
    target = os.path.join(tmpdir, "History")
    for _ in range(3):
        try:
            shutil.copy2(db_path, target)
            for suffix in ("-wal",):
                side = db_path + suffix
                if os.path.isfile(side):
                    try:
                        shutil.copy2(side, target + suffix)
                    except OSError:
                        pass  # wal 复制失败也不致命（退化为 checkpoint 快照）
            return target
        except OSError:
            time.sleep(0.1)
    return None


def _open_ro(db_path: str) -> sqlite3.Connection:
    """immutable 只读打开原始数据库：不依赖任何锁，绝不等待浏览器。

    仅用于 _backup_read 读取浏览器正在使用的源文件（此时绝不可加锁/写）。
    """
    uri = f"file:{urllib.parse.quote(db_path)}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def _open_copy(db_copy_path: str) -> sqlite3.Connection:
    """普通方式打开复制到临时目录的副本。

    副本是我们自己刚复制出来的独立文件（可写、无锁竞争），普通连接会让
    SQLite 安全重放随副本一起复制的 -wal 日志（浏览器运行时 WAL 里可能还有
    未 checkpoint 的访问记录）。immutable 模式会直接忽略 WAL，导致丢数据。
    复制时若 WAL 正在被写入导致页不一致，SQLite 检测到损坏会安全回退主库。
    """
    return sqlite3.connect(db_copy_path)


def _backup_read(db_path: str, tmpdir: str) -> str | None:
    """兜底：sqlite3 备份 API 直接读取原始文件（浏览器允许共享读时可用）。"""
    try:
        target = os.path.join(tmpdir, "History_backup.db")
        src = sqlite3.connect(f"file:{urllib.parse.quote(db_path)}?mode=ro", uri=True)
        dst = sqlite3.connect(target)
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()
        return target
    except (sqlite3.Error, OSError):
        return None


# ---------------------------------------------------------------------------
# 时间换算
# ---------------------------------------------------------------------------
def _filetime_to_local(us: int) -> datetime.datetime:
    """Chrome visit_time（自 1601-01-01 UTC 起的微秒）-> 本地时间。"""
    epoch_seconds = us / 1e6 - _FILETIME_EPOCH_OFFSET
    return datetime.datetime.fromtimestamp(epoch_seconds)


def _local_day_ft_range(day_str: str) -> tuple[int, int]:
    """本地某天的 [起始, 结束) 对应 Chrome FILETIME 微秒区间。"""
    y, m, d = map(int, day_str.split("-"))
    start = datetime.datetime(y, m, d)
    end = start + datetime.timedelta(days=1)
    start_ft = int((time.mktime(start.timetuple()) + _FILETIME_EPOCH_OFFSET) * 1e6)
    end_ft = int((time.mktime(end.timetuple()) + _FILETIME_EPOCH_OFFSET) * 1e6)
    return start_ft, end_ft


# ---------------------------------------------------------------------------
# 提取与分类
# ---------------------------------------------------------------------------
def extract_visits(db_copy_path: str, day_str: str, config: dict | None = None,
                   browser: str = "chrome") -> list[dict]:
    """从历史数据库副本（History 或 places.sqlite）提取某天的访问记录（时间升序）。

    browser="firefox" 走 moz_historyvisits/moz_places 分支，其余按 Chromium urls/visits 解析。
    跨天隔离：访问的真实区间为 [visit_time, visit_time + visit_duration]，
    只把落在当天 [00:00, 24:00) 内的份额计入当天（按日界分摊）；
    前一天打开、时长延伸进当天的访问同样进入当天报表（份额正确）。
    duration_s 来自 Chromium visits.visit_duration（前台停留微秒，封顶 6 小时防脏数据）；
    firefox 无逐条时长，访问按时间点记入其所在当天。URL/标题命中黑名单时掩蔽为 [已隐藏]。
    """
    if config is None:
        config = classifier.load_config()
    if browser == "firefox":
        return _extract_firefox_visits(db_copy_path, day_str, config)
    start_ft, end_ft = _local_day_ft_range(day_str)
    day_start_epoch = start_ft / 1e6 - _FILETIME_EPOCH_OFFSET
    day_end_epoch = end_ft / 1e6 - _FILETIME_EPOCH_OFFSET
    # 查询下界放宽 6 小时（封顶时长内的跨天访问都能覆盖到）
    lower_ft = start_ft - _MAX_VISIT_DURATION_S * 1_000_000

    visits: list[dict] = []
    conn = _open_copy(db_copy_path)
    try:
        # 部分旧版/精简版库可能没有 visit_duration 列，探测后回退
        try:
            cur = conn.execute(
                "SELECT v.visit_time, v.visit_duration, u.url, u.title "
                "FROM visits v JOIN urls u ON v.url = u.id "
                "WHERE v.visit_time >= ? AND v.visit_time < ? "
                "ORDER BY v.visit_time",
                (lower_ft, end_ft),
            )
            has_duration = True
        except sqlite3.OperationalError:
            cur = conn.execute(
                "SELECT v.visit_time, 0, u.url, u.title "
                "FROM visits v JOIN urls u ON v.url = u.id "
                "WHERE v.visit_time >= ? AND v.visit_time < ? "
                "ORDER BY v.visit_time",
                (lower_ft, end_ft),
            )
            has_duration = False
        for visit_time, visit_duration, url, title in cur:
            start_epoch = int(visit_time) / 1e6 - _FILETIME_EPOCH_OFFSET
            if start_epoch >= day_end_epoch:
                continue
            raw_dur = 0.0
            if has_duration and visit_duration:
                raw_dur = min(int(visit_duration) / 1e6, _MAX_VISIT_DURATION_S)
            end_epoch = start_epoch + raw_dur

            # 跨天分摊：只取落在当天区间内的份额
            overlap_start = max(start_epoch, day_start_epoch)
            overlap_end = min(end_epoch, day_end_epoch)
            duration_s = round(max(0.0, overlap_end - overlap_start), 1)
            if start_epoch < day_start_epoch and duration_s <= 0:
                continue  # 前一天打开且当天无份额的访问，不进入当天

            visits.append(_build_visit(url, title, overlap_start, duration_s, config))
    finally:
        conn.close()
    visits.sort(key=lambda v: v["time"])
    return visits


def _build_visit(url: str | None, title: str | None, start_epoch: float,
                 duration_s: float, config: dict) -> dict:
    """把一条原始访问整理成统一输出结构（隐私掩蔽 + 分类），Chrome/Firefox 共用。"""
    url = url or ""
    title = title or ""
    # 隐私黑名单掩蔽（域名是 URL 的一部分：URL 掩蔽时域名一并隐藏）
    url_masked = classifier.is_blacklisted_title(url, config)
    url_disp = "[已隐藏]" if url_masked else url
    title_disp = "[已隐藏]" if classifier.is_blacklisted_title(title, config) else title
    domain = urllib.parse.urlparse(url).netloc.lower() if not url_masked else "-"
    category = classifier.classify_browser(f"{domain} {title}", config)
    return {
        "time": datetime.datetime.fromtimestamp(start_epoch).strftime("%Y-%m-%dT%H:%M:%S"),
        "url": url_disp,
        "title": title_disp,
        "domain": domain or "-",
        "category": category,
        "duration_s": round(duration_s, 1),
    }


def _extract_firefox_visits(db_copy_path: str, day_str: str, config: dict) -> list[dict]:
    """从 Firefox places.sqlite 副本提取某天的访问记录。

    Firefox 无逐条 visit_duration（有别于 Chromium），访问视为时间点，仅当该点落在
    当天 [00:00, 24:00) 内才计入当天。visit_date 为 PRTime（微秒自 1970-01-01），
    换算到与 Chromium 一致的 FILETIME 空间后复用 _local_day_ft_range 的日界。
    """
    start_ft, end_ft = _local_day_ft_range(day_str)
    day_start_epoch = start_ft / 1e6 - _FILETIME_EPOCH_OFFSET
    day_end_epoch = end_ft / 1e6 - _FILETIME_EPOCH_OFFSET
    # PRTime 空间 = FILETIME 空间 - 微秒偏移；firefox 无跨天时长，无需放宽下界
    lower = start_ft - _FILETIME_EPOCH_OFFSET_US
    upper = end_ft - _FILETIME_EPOCH_OFFSET_US

    visits: list[dict] = []
    conn = _open_copy(db_copy_path)
    try:
        cur = conn.execute(
            "SELECT h.visit_date, p.url, p.title "
            "FROM moz_historyvisits h JOIN moz_places p ON h.place_id = p.id "
            "WHERE h.visit_date >= ? AND h.visit_date < ? "
            "ORDER BY h.visit_date",
            (lower, upper),
        )
        for visit_date, url, title in cur:
            start_epoch = int(visit_date) / 1e6  # PRTime 微秒自 1970 -> 直接得 epoch 秒
            if start_epoch >= day_end_epoch:
                continue
            duration_s = 0.0  # firefox 无逐条时长，视为时间点
            visits.append(_build_visit(url, title, start_epoch, duration_s, config))
    finally:
        conn.close()
    visits.sort(key=lambda v: v["time"])
    return visits


def collect(day_str: str, data_root: str, config: dict | None = None,
            db_paths: list[str] | None = None) -> dict:
    """收集某天所有可用浏览器的访问明细。

    db_paths 提供时跳过自动发现（测试用）。
    """
    if config is None:
        config = classifier.load_config()
    if not config.get("browser_history_enabled", True):
        return {"enabled": False, "day": day_str, "count": 0, "visits": [], "errors": []}

    if db_paths is not None:
        dbs = [{"browser": "test", "profile": "Default", "db": p} for p in db_paths]
    else:
        dbs = find_history_dbs(config)

    visits: list[dict] = []
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="usagemon_hist_") as tmpdir:
        for item in dbs:
            db = item["db"]
            copy = _copy_db(db, tmpdir)
            if copy is None:
                copy = _backup_read(db, tmpdir)
            if copy is None:
                errors.append(f"{item['browser']}/{item['profile']}: 读取失败（文件被占用）")
                continue
            try:
                vs = extract_visits(copy, day_str, config, item["browser"])
                for v in vs:
                    v["browser"] = item["browser"]
                    v["profile"] = item["profile"]
                visits.extend(vs)
            except sqlite3.Error as exc:
                errors.append(f"{item['browser']}/{item['profile']}: {exc}")
    visits.sort(key=lambda v: v["time"])
    # 停留时长聚合（按分类 / 按域名）
    by_cat_dur: dict[str, float] = {}
    by_domain_dur: dict[str, float] = {}
    total_dur = 0.0
    for v in visits:
        d = v.get("duration_s") or 0.0
        total_dur += d
        by_cat_dur[v["category"]] = by_cat_dur.get(v["category"], 0.0) + d
        if v["domain"] != "-":
            by_domain_dur[v["domain"]] = by_domain_dur.get(v["domain"], 0.0) + d
    return {
        "enabled": True, "day": day_str, "count": len(visits), "visits": visits,
        "total_duration_s": round(total_dur, 1),
        "by_category_duration_s": {k: round(v, 1) for k, v in by_cat_dur.items()},
        "by_domain_duration_s": {k: round(v, 1) for k, v in by_domain_dur.items()},
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 会话 ↔ URL 关联（监控维度细化）
# ---------------------------------------------------------------------------
def find_url_for_session(start: datetime.datetime, end: datetime.datetime,
                         data_root: str, config: dict | None = None,
                         db_paths: list[str] | None = None) -> str | None:
    """在 [start-60s, end+60s] 窗口内寻找与会话时间重叠的访问 URL。

    用于浏览器会话落盘时关联"当时看的哪个页面"（尽力而为：浏览器历史
    可能有写入延迟，找不到返回 None，不影响会话本身）。
    命中黑名单的 URL 掩蔽为 [已隐藏]。
    """
    if config is None:
        config = classifier.load_config()
    if db_paths is not None:
        dbs = [{"browser": "test", "profile": "Default", "db": p} for p in db_paths]
    else:
        dbs = find_history_dbs(config)
    if not dbs:
        return None

    start_epoch = start.timestamp() - 60
    end_epoch = end.timestamp() + 60
    start_ft = int((start_epoch + _FILETIME_EPOCH_OFFSET) * 1e6)
    end_ft = int((end_epoch + _FILETIME_EPOCH_OFFSET) * 1e6)

    best_url: str | None = None
    best_overlap = 0.0
    with tempfile.TemporaryDirectory(prefix="usagemon_hist_") as tmpdir:
        for item in dbs:
            copy = _copy_db(item["db"], tmpdir)
            if copy is None:
                continue
            try:
                conn = _open_ro(copy)
                if item["browser"] == "firefox":
                    cur = conn.execute(
                        "SELECT h.visit_date, p.url "
                        "FROM moz_historyvisits h JOIN moz_places p ON h.place_id = p.id "
                        "WHERE h.visit_date >= ? AND h.visit_date <= ?",
                        (start_ft - _FILETIME_EPOCH_OFFSET_US, end_ft - _FILETIME_EPOCH_OFFSET_US),
                    )
                    for visit_date, url in cur:
                        s = int(visit_date) / 1e6  # PRTime -> epoch 秒
                        e = s  # firefox 无逐条时长，视为时间点
                        overlap = min(e, end_epoch) - max(s, start_epoch)
                        if overlap > 0 and overlap > best_overlap:
                            best_overlap = overlap
                            best_url = url or ""
                else:
                    try:
                        cur = conn.execute(
                            "SELECT v.visit_time, v.visit_duration, u.url "
                            "FROM visits v JOIN urls u ON v.url = u.id "
                            "WHERE v.visit_time >= ? AND v.visit_time <= ?",
                            (start_ft, end_ft),
                        )
                    except sqlite3.OperationalError:
                        cur = conn.execute(
                            "SELECT v.visit_time, 0, u.url "
                            "FROM visits v JOIN urls u ON v.url = u.id "
                            "WHERE v.visit_time >= ? AND v.visit_time <= ?",
                            (start_ft, end_ft),
                        )
                    for visit_time, visit_duration, url in cur:
                        s = int(visit_time) / 1e6 - _FILETIME_EPOCH_OFFSET
                        e = s + min(int(visit_duration or 0) / 1e6, _MAX_VISIT_DURATION_S)
                        overlap = min(e, end_epoch) - max(s, start_epoch)
                        if overlap > best_overlap:
                            best_overlap = overlap
                            best_url = url or ""
                conn.close()
            except sqlite3.Error:
                continue
    if best_url is None or best_url == "":
        return None
    if classifier.is_blacklisted_title(best_url, config):
        return "[已隐藏]"
    return best_url


# ---------------------------------------------------------------------------
# 日报章节
# ---------------------------------------------------------------------------
def _fmt_dur(seconds: float) -> str:
    """秒 -> 中文时长文本（用于浏览器停留时长展示）。"""
    total_s = int(seconds)
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} 小时")
    if m:
        parts.append(f"{m} 分钟")
    if s and not h:
        parts.append(f"{s} 秒")
    if not parts:
        return "0 秒"
    return " ".join(parts)


def report_section(day_str: str, data_root: str, config: dict | None = None,
                   db_paths: list[str] | None = None,
                   data: dict | None = None,
                   max_rows: int | None = None) -> str | None:
    """生成供 report.md 追加的"浏览器访问明细"Markdown 章节；无记录返回 None。

    含 URL 停留时长统计（visit_duration）：按分类/域名汇总"用了多久"。
    db_paths 提供时跳过自动发现（测试用）；data 提供时跳过重复收集（report 复用）。
    max_rows 覆盖默认 100 条上限（传 None 用默认值）。
    """
    if data is None:
        data = collect(day_str, data_root, config, db_paths=db_paths)
    if not data.get("enabled") or not data["visits"]:
        return None
    visits = data["visits"]
    limit = _MAX_REPORT_ROWS if max_rows is None else max(int(max_rows), 0)
    out = [f"## 浏览器访问明细（{data['count']} 条 URL 记录）", ""]

    # 停留时长总览（visit_duration 汇总）
    if data.get("total_duration_s"):
        out.append(f"URL 停留总时长：{_fmt_dur(data['total_duration_s'])}")
        by_cat = data.get("by_category_duration_s", {})
        if by_cat:
            sub = "；".join(f"{cat} {_fmt_dur(v)}" for cat, v in sorted(by_cat.items(), key=lambda kv: -kv[1]))
            out.append(f"分类停留：{sub}")
        by_domain = data.get("by_domain_duration_s", {})
        if by_domain:
            top = sorted(by_domain.items(), key=lambda kv: -kv[1])[:5]
            sub = "；".join(f"{d} {_fmt_dur(v)}" for d, v in top)
            out.append(f"域名停留 Top 5：{sub}")
        out.append("")
        out.append("注：停留时长来自浏览器前台标签计时（含空闲/挂机时间），为「浏览器侧」口径；"
                   "真实活跃时长以 monitor 会话统计为准。跨天访问按日界分摊，不会串天。")
        out.append("")

    # 分类条数小计
    by_cat_count: dict[str, int] = {}
    for v in visits:
        by_cat_count[v["category"]] = by_cat_count.get(v["category"], 0) + 1
    if by_cat_count:
        sub = "；".join(f"{cat} {n} 条" for cat, n in sorted(by_cat_count.items()))
        out.append(f"分类：{sub}")
        out.append("")

    rows = []
    for v in visits[:limit]:
        url_disp = v["url"] if v["url"] != "[已隐藏]" else "[已隐藏]"
        title_disp = v["title"]
        # URL 截断显示（隐私友好：不展示 query 参数）
        short_url = url_disp.split("?", 1)[0]
        if len(short_url) > 90:
            short_url = short_url[:90] + "…"
        label = f"[{title_disp}]({short_url})" if title_disp and title_disp != "[已隐藏]" else short_url
        dur = _fmt_dur(v.get("duration_s") or 0)
        rows.append([v["time"][11:], v["category"], v["domain"], dur, label])
    out.append("| 时间 | 分类 | 域名 | 停留 | 页面 |")
    out.append("|---|---|---|---|---|")
    for r in rows:
        out.append("| " + " | ".join(r) + " |")
    if data["count"] > limit:
        out.append("")
        out.append(f"（共 {data['count']} 条，仅显示前 {limit} 条）")
    for err in data["errors"]:
        out.append("")
        out.append(f"> {err}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="browser_history.py", description="浏览器 URL 级历史解析")
    parser.add_argument("--today", action="store_true", help="今天")
    parser.add_argument("--day", metavar="YYYY-MM-DD", help="指定日期")
    parser.add_argument("--list-browsers", action="store_true", help="列出发现的浏览器历史数据库")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")
    parser.add_argument("--config", default=None, help="config.json 路径")
    parser.add_argument("--data-root", default=None, help="数据根目录")
    args = parser.parse_args(argv)

    config = classifier.load_config(args.config)
    data_root = args.data_root or config.get("data_root") or paths.default_data_root()

    if args.list_browsers:
        dbs = find_history_dbs(config)
        if not dbs:
            print("未发现 Chrome/Edge/Firefox 历史数据库（可配置 browser_history.user_data / firefox.user_data 路径）")
            return 0
        for item in dbs:
            print(f"{item['browser']} [{item['profile']}]: {item['db']}")
        return 0

    if args.today:
        day_str = datetime.date.today().isoformat()
    elif args.day:
        try:
            datetime.datetime.strptime(args.day, "%Y-%m-%d")
        except ValueError:
            print(f"[browser_history] 日期格式错误: {args.day}", file=sys.stderr)
            return 2
        day_str = args.day
    else:
        parser.print_help()
        return 2

    if args.json:
        import json
        print(json.dumps(collect(day_str, data_root, config), ensure_ascii=False, indent=2, default=str))
        return 0

    section = report_section(day_str, data_root, config)
    if section is None:
        print(f"当日无浏览器历史记录：{day_str}（或浏览器历史解析未启用）")
        return 1
    print(section)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
