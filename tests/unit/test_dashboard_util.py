# -*- coding: utf-8 -*-
"""tests/unit/test_dashboard_util.py — dashboard_util.py 外置纯函数的确定性单测。

覆盖从 dashboard.py 拆出的与 HTTP 无关纯函数：_agg_to_csv、_backup_zip/
_backup_entries、_safe_extract_zip、_available_days、_collect_known_apps、
_sanitize_csv、_month_days_for。
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import dashboard_util  # noqa: E402


# ---------------------------------------------------------------------------
# _agg_to_csv
# ---------------------------------------------------------------------------
def test_agg_to_csv_minimal():
    """空聚合只输出表头行。"""
    assert dashboard_util._agg_to_csv({}) == "类型,名称,时长秒\n"


def test_agg_to_csv_sections_sorted_by_duration():
    """各分类按时长降序渲染，格式 分类:名称,秒。"""
    agg = {
        "by_app": {"vscode": 3_000, "browser": 5_000},
        "by_category": {"coding": 8_000, "fun": 1_000},
        "by_contact": {"dev": {"alice": 2_000, "bob": 6_000}},
        "by_ai": {"claude": 4_000},
        "by_browser": {"docs": 7_000},
    }
    csv = dashboard_util._agg_to_csv(agg)
    lines = csv.strip().split("\n")
    assert lines[0] == "类型,名称,时长秒"
    assert lines[1] == "应用:browser,5"
    assert lines[2] == "应用:vscode,3"
    assert "类别:coding,8" in lines and "类别:fun,1" in lines
    assert "联系人:dev/bob,6" in lines and "联系人:dev/alice,2" in lines
    assert "AI工具:claude,4" in lines
    assert "浏览器:docs,7" in lines


def test_agg_to_csv_title_line():
    """标题行去掉 # 前缀与逗号，输出为 # 注释行 + 空行 + 表头。"""
    csv = dashboard_util._agg_to_csv({"by_app": {}}, "# 我的周报, 2026-08")
    assert csv.startswith("# 我的周报 2026-08\n\n类型,名称,时长秒\n")


# ---------------------------------------------------------------------------
# _backup_entries / _backup_zip / _safe_extract_zip
# ---------------------------------------------------------------------------
def test_backup_entries_filters(tmp_path):
    """只枚举日期目录与白名单根文件，排除日志/临时等。"""
    root = str(tmp_path / "d1")
    os.makedirs(os.path.join(root, "2026-08-01"), exist_ok=True)
    os.makedirs(os.path.join(root, "2026-08-02"), exist_ok=True)
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        fh.write("{}")
    with open(os.path.join(root, "app.log"), "w", encoding="utf-8") as fh:
        fh.write("x")
    entries = dashboard_util._backup_entries(root)
    assert "2026-08-01" in entries and "2026-08-02" in entries
    assert "config.json" in entries
    assert "app.log" not in entries


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_backup_zip_roundtrip_excludes_suffixes(tmp_path):
    """备份 zip 后安全解压：数据与白名单文件在，.log/.tmp 被排除。"""
    root = str(tmp_path / "d2")
    day = os.path.join(root, "2026-08-01")
    os.makedirs(day, exist_ok=True)
    with open(os.path.join(day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    with open(os.path.join(day, "debug.log"), "w", encoding="utf-8") as fh:
        fh.write("log")
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        fh.write("{}")

    data = dashboard_util._backup_zip(root)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    assert "2026-08-01/usage.jsonl" in names
    assert "config.json" in names
    # 打包按目录全量收录（含 .log）；排除后缀在解压阶段生效
    assert "2026-08-01/debug.log" in names

    tmp = dashboard_util._safe_extract_zip(root, data)
    try:
        assert os.path.isfile(os.path.join(tmp, "2026-08-01", "usage.jsonl"))
        assert os.path.isfile(os.path.join(tmp, "config.json"))
        assert not os.path.exists(os.path.join(tmp, "2026-08-01", "debug.log"))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_safe_extract_zip_blocks_traversal_and_unknown(tmp_path):
    """路径穿越与非白名单顶层条目一律丢弃，白名单条目保留。"""
    root = str(tmp_path / "d3")
    os.makedirs(root, exist_ok=True)
    data = _zip_bytes({
        "config.json": "{}",
        "2026-08-01/usage.jsonl": "{}\n",
        "../evil.txt": "pwn",           # 上级引用
        "/etc/passwd": "pwn",           # 绝对路径
        "random.exe": "pwn",            # 非白名单顶层
    })
    tmp = dashboard_util._safe_extract_zip(root, data)
    try:
        assert os.path.isfile(os.path.join(tmp, "config.json"))
        assert os.path.isfile(os.path.join(tmp, "2026-08-01", "usage.jsonl"))
        for bad in ("evil.txt", "passwd", "random.exe"):
            assert not os.path.exists(os.path.join(tmp, bad)), bad
        # 穿越文件不得落在 tmp 外
        assert not os.path.exists(os.path.join(os.path.dirname(tmp), "evil.txt"))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# _available_days
# ---------------------------------------------------------------------------
def test_available_days_sorted_filtered(tmp_path):
    """只认 YYYY-MM-DD 名称且升序，忽略非日期目录/文件。"""
    root = str(tmp_path / "d4")
    os.makedirs(root, exist_ok=True)
    for d in ["2026-08-12", "2026-08-10", "2026-08-11"]:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    os.makedirs(os.path.join(root, "2026-08"), exist_ok=True)  # 月目录不算
    with open(os.path.join(root, "note.txt"), "w", encoding="utf-8") as fh:
        fh.write("not a date")

    dashboard_util.invalidate_days_cache()
    days = dashboard_util._available_days(root)
    assert days == ["2026-08-10", "2026-08-11", "2026-08-12"]


def test_available_days_missing_root(tmp_path):
    """目录不存在返回空列表且不抛异常。"""
    dashboard_util.invalidate_days_cache()
    assert dashboard_util._available_days(str(tmp_path / "nope")) == []


# ---------------------------------------------------------------------------
# _collect_known_apps
# ---------------------------------------------------------------------------
def test_collect_known_apps(tmp_path):
    """从软件清单与 usage.jsonl 汇总已知应用（exe 小写 -> 显示名）。"""
    root = str(tmp_path / "d5")
    day = os.path.join(root, "2026-08-01")
    os.makedirs(day, exist_ok=True)
    with open(os.path.join(day, "software_inventory.json"), "w", encoding="utf-8") as fh:
        json.dump({"apps": [{"exe": "Code.EXE", "name": "Visual Studio Code"}]}, fh)
    with open(os.path.join(day, "usage.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"exe": "msedge.exe", "app": "Edge"}) + "\n")
        fh.write("not json\n")

    dashboard_util.invalidate_days_cache()
    known = dashboard_util._collect_known_apps(root)
    assert known["code.exe"] == "Visual Studio Code"
    assert known["msedge.exe"] == "Edge"


# ---------------------------------------------------------------------------
# _sanitize_csv
# ---------------------------------------------------------------------------
def test_sanitize_csv_prefixes_formula_chars():
    """以 = + - @ 开头（strip 后判定）的单元格加 ' 前缀；普通/注释行原样保留。"""
    src = "# 注释\n普通,=SUM(A1:A2),+1,-2,@x\n"
    out = dashboard_util._sanitize_csv(src)
    lines = out.split("\n")
    assert lines[0] == "# 注释"
    assert lines[1] == "普通,'=SUM(A1:A2),'+1,'-2,'@x"


def test_sanitize_csv_strips_tab_before_check():
    """tab 开头字段先 strip 再判定，故不会被前缀（既有行为，保持原样）。"""
    assert dashboard_util._sanitize_csv("\tx,1") == "\tx,1"


def test_sanitize_csv_leaves_safe_lines():
    """不含公式字符的 CSV 原样返回。"""
    src = "a,b,c\n1,2,3\n"
    assert dashboard_util._sanitize_csv(src) == src


# ---------------------------------------------------------------------------
# _month_days_for
# ---------------------------------------------------------------------------
def test_month_days_for_filters_month_and_usage(tmp_path):
    """只返回指定月份且含 usage.jsonl 的日期，升序。"""
    root = str(tmp_path / "d6")
    for d in ["2026-08-01", "2026-08-03", "2026-09-01"]:
        os.makedirs(os.path.join(root, d), exist_ok=True)
        with open(os.path.join(root, d, "usage.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
    os.makedirs(os.path.join(root, "2026-08-02"), exist_ok=True)  # 无 usage.jsonl
    days = dashboard_util._month_days_for(root, "2026-08")
    assert days == ["2026-08-01", "2026-08-03"]
    assert dashboard_util._month_days_for(str(tmp_path / "nope"), "2026-08") == []
