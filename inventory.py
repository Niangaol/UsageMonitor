# -*- coding: utf-8 -*-
"""inventory.py — 软件清单扫描与自动分类。

扫描来源（尽力而为）：
1. 注册表卸载项（HKLM / WOW6432Node / HKCU）
2. 开始菜单 .lnk 快捷方式（纯 Python 二进制解析 LinkInfo->LocalBasePath）
3. 当前运行进程

输出：当日文件夹 software_inventory.json（+ software_inventory.csv）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import struct
import sys
import winreg
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import classifier  # noqa: E402
import win32core  # noqa: E402

UNINSTALL_SUBKEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
_START_MENU_ROOTS = [
    os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
    r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
]
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# 1) 注册表
# ---------------------------------------------------------------------------
def _reg_get_value(key, name):
    try:
        value, _ = winreg.QueryValueEx(key, name)
        return value
    except OSError:
        return None


def _iter_uninstall(root_hive, access) -> list[dict]:
    results: list[dict] = []
    try:
        with winreg.OpenKey(root_hive, UNINSTALL_SUBKEY, 0, winreg.KEY_READ | access) as key:
            index = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(key, index)
                    index += 1
                except OSError:
                    break
                try:
                    with winreg.OpenKey(key, sub_name) as sub:
                        display = _reg_get_value(sub, "DisplayName")
                        if not display or not isinstance(display, str):
                            continue
                        display = display.strip()
                        if not display:
                            continue
                        if display.startswith("KB") or display.startswith("Update for"):
                            continue
                        icon = _reg_get_value(sub, "DisplayIcon")
                        location = _reg_get_value(sub, "InstallLocation")
                        publisher = _reg_get_value(sub, "Publisher")
                        results.append({
                            "name": display,
                            "exe": _exe_from_icon_or_location(icon, location),
                            "publisher": publisher if isinstance(publisher, str) else None,
                            "source": ["registry"],
                        })
                except OSError:
                    continue
    except OSError:
        pass
    return results


def _exe_from_icon_or_location(icon, location) -> str | None:
    if icon and isinstance(icon, str):
        icon = icon.strip().strip('"')
        icon = icon.split(",", 1)[0].strip()
        if icon.lower().endswith(".exe"):
            return os.path.basename(icon).lower()
    if location and isinstance(location, str) and os.path.isdir(location):
        try:
            for entry in sorted(os.listdir(location)):
                if entry.lower().endswith(".exe") and os.path.isfile(os.path.join(location, entry)):
                    return entry.lower()
        except OSError:
            pass
    return None


def scan_registry() -> list[dict]:
    """扫描三个卸载项注册表视图并去重。"""
    found: dict[str, dict] = {}
    for hive, access in (
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, winreg.KEY_READ),
    ):
        for entry in _iter_uninstall(hive, access):
            key = (entry["name"] or "").lower()
            if key in found:
                continue
            found[key] = entry
    return list(found.values())


# ---------------------------------------------------------------------------
# 2) 开始菜单快捷方式
# ---------------------------------------------------------------------------
def _lnk_target_exe(path: str) -> str | None:
    """纯 Python 解析 .lnk：Header -> LinkInfo -> LocalBasePath（UTF-16LE 空终止）。"""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if len(data) < 76:
        return None
    try:
        flags = struct.unpack_from("<I", data, 0x14)[0]
        pos = 76
        if flags & 0x1:  # HasLinkTargetIDList（真实文件中 IDListSize 为 2 字节）
            idlist_size = struct.unpack_from("<H", data, pos)[0]
            pos += 2 + idlist_size
        if not (flags & 0x2) or pos + 28 > len(data):  # HasLinkInfo
            return None
        local_base_path_offset = struct.unpack_from("<I", data, pos + 0x10)[0]
        if local_base_path_offset == 0:
            return None
        start = pos + local_base_path_offset
        chunk = data[start:start + 1024]
        # Windows 实际写盘时 LocalBasePath 可能是 UTF-16LE 或 ANSI（单字节），双解码试探
        text16 = chunk.decode("utf-16-le", errors="ignore")
        text8 = chunk.decode("latin-1", errors="ignore")
        candidate = None
        for text in (text16, text8):
            end = text.find("\x00")
            s = (text[:end] if end >= 0 else text).strip()
            if s.lower().endswith(".exe") and ("\\" in s or ":" in s or "/" in s):
                candidate = s
                break
        if not candidate:
            return None
        exe = candidate.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        return exe if exe.endswith(".exe") else None
    except (struct.error, IndexError, ValueError):
        return None


def scan_start_menu() -> list[dict]:
    """递归扫描开始菜单 .lnk，尽力解析目标 exe。"""
    results: list[dict] = []
    for root in _START_MENU_ROOTS:
        if not os.path.isdir(root):
            continue
        try:
            for lnk in Path(root).rglob("*.lnk"):
                try:
                    exe = _lnk_target_exe(str(lnk))
                    results.append({
                        "name": lnk.stem.strip(),
                        "exe": exe,
                        "publisher": None,
                        "source": ["startmenu"],
                    })
                except OSError:
                    continue
        except OSError:
            continue
    return results


# ---------------------------------------------------------------------------
# 3) 运行进程
# ---------------------------------------------------------------------------
def scan_running_processes() -> list[dict]:
    """当前运行进程的 exe 名。"""
    results = []
    for info in win32core.enum_processes().values():
        exe = info.exe
        if not exe:
            continue
        stem = exe[:-4] if exe.endswith(".exe") else exe
        results.append({
            "name": stem.title(),
            "exe": exe,
            "publisher": None,
            "source": ["running"],
            "running": True,
        })
    return results


# ---------------------------------------------------------------------------
# 汇总 + 输出
# ---------------------------------------------------------------------------
def collect_inventory(config: dict) -> dict:
    """合并三类来源，去重（按 exe 小写），自动分类。"""
    merged: dict[str, dict] = {}
    for entry in scan_registry() + scan_start_menu() + scan_running_processes():
        exe = (entry.get("exe") or "").lower()
        key = exe or ("name:" + (entry.get("name") or "").lower())
        if key in merged:
            cur = merged[key]
            for src in entry["source"]:
                if src not in cur["source"]:
                    cur["source"].append(src)
            if entry.get("running") and not cur.get("running"):
                cur["running"] = True
            if not cur.get("exe") and exe:
                cur["exe"] = exe
            continue
        merged[key] = {
            "name": entry.get("name") or "",
            "exe": exe or None,
            "publisher": entry.get("publisher"),
            "source": list(entry["source"]),
            "running": bool(entry.get("running")),
        }

    apps = []
    for item in merged.values():
        name = item["name"] or (item["exe"] or "").replace(".exe", "")
        apps.append({
            "name": name,
            "exe": item["exe"],
            "category": classifier.classify_category(item["exe"] or "", name, config),
            "source": item["source"],
            "running": item["running"],
        })
    apps.sort(key=lambda a: (a["category"], (a["name"] or "").lower()))
    return {
        "date": datetime.date.today().isoformat(),
        "scanned_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "count": len(apps),
        "apps": apps,
    }


def write_inventory(date_dir: str, config: dict) -> dict:
    """写入 software_inventory.json 与 software_inventory.csv。"""
    os.makedirs(date_dir, exist_ok=True)
    inv = collect_inventory(config)
    json_path = os.path.join(date_dir, "software_inventory.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(inv, fh, ensure_ascii=False, indent=2)
    csv_path = os.path.join(date_dir, "software_inventory.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write("name,exe,category,source,running\n")
        for app in inv["apps"]:
            fh.write(
                f"{app['name']},{app['exe'] or ''},{app['category']},"
                f"{'|'.join(app['source'])},{int(app['running'])}\n"
            )
    return inv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inventory.py", description="软件清单扫描")
    parser.add_argument("--once", action="store_true", help="扫描并写入当日清单（默认行为）")
    parser.add_argument("--config", default=None, help="config.json 路径")
    parser.add_argument("--data-root", default=None, help="数据根目录（默认取 config.json）")
    args = parser.parse_args(argv)

    config = classifier.load_config(args.config)
    data_root = args.data_root or config.get("data_root") or "D:\\电脑使用情况监控"
    today = datetime.date.today().isoformat()
    date_dir = os.path.join(data_root, today)
    try:
        inv = write_inventory(date_dir, config)
    except Exception as exc:  # noqa: BLE001
        print(f"[inventory] 扫描失败: {exc}", file=sys.stderr)
        return 1
    print(f"scanned {inv['count']} apps -> {os.path.join(date_dir, 'software_inventory.json')}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
