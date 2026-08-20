# -*- coding: utf-8 -*-
"""tests/unit/test_applog.py — applog 日志读取（P9-3）单元测试。

覆盖 read_recent 与 read_errors：
- 文件不存在 / 空文件 → []
- n > 总行数 → 全部行；n < 总行数 → 恰好最近 n 行、顺序保持
- n = 0 / n 为负 → []（“最近 n 行”对非正 n 无意义）
- 非法 UTF-8 字节 → errors="replace" 降级为替换字符，不抛异常
- OSError（读失败）→ 降级返回 []
- 超大文件（20 万行）→ 只返回最近 n 行，内存不随总行数增长
- 流式证明：逐行迭代（deque），绝不调用 readlines() 全量读入
- read_errors：多天 errors.log 汇总、空行过滤、超限截尾

零第三方依赖、确定性、不读写真实 data_root。
"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import applog  # noqa: E402


# ---------------------------------------------------------------------------
# 构造助手
# ---------------------------------------------------------------------------
def _write_log(root, lines):
    """在临时目录写入 logs/app.log（真实文件），返回文件路径。"""
    logf = applog.log_path(root)
    os.makedirs(os.path.dirname(logf), exist_ok=True)
    with open(logf, "w", encoding="utf-8") as fh:
        fh.write("".join(lines))
    return logf


def _write_errors(root, day, lines):
    """在临时目录写入 <day>/errors.log，返回文件路径。"""
    p = os.path.join(root, day, "errors.log")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", errors="replace") as fh:
        fh.write("".join(lines))
    return p


class _StreamingFile:
    """只支持逐行迭代的假文件：断言实现未退化为 readlines() 全量读入。"""

    def __init__(self, lines):
        self._lines = list(lines)
        self.readlines_called = False
        self.iter_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        self.iter_calls += 1
        return iter(self._lines)

    def readlines(self):  # 若实现调用 readlines() 会被标记
        self.readlines_called = True
        return list(self._lines)


# ---------------------------------------------------------------------------
# 路径 / 文件不存在 / 空文件
# ---------------------------------------------------------------------------
def test_log_path_layout(tmp_path):
    assert applog.log_path(str(tmp_path)) == os.path.join(str(tmp_path), "logs", "app.log")


def test_read_recent_missing_file(tmp_path):
    assert applog.read_recent(str(tmp_path), 10) == []


def test_read_recent_empty_file(tmp_path):
    _write_log(tmp_path, [])
    assert applog.read_recent(str(tmp_path), 10) == []


# ---------------------------------------------------------------------------
# n 与总行数的关系（边界）
# ---------------------------------------------------------------------------
def test_read_recent_n_greater_than_total(tmp_path):
    _write_log(tmp_path, ["a\n", "b\n", "c\n"])
    got = applog.read_recent(str(tmp_path), 10)
    assert got == ["a", "b", "c"]


def test_read_recent_n_equal_total(tmp_path):
    _write_log(tmp_path, ["a\n", "b\n", "c\n", "d\n"])
    assert applog.read_recent(str(tmp_path), 4) == ["a", "b", "c", "d"]


def test_read_recent_n_smaller_than_total(tmp_path):
    _write_log(tmp_path, ["a\n", "b\n", "c\n", "d\n", "e\n", "f\n"])
    got = applog.read_recent(str(tmp_path), 3)
    assert got == ["d", "e", "f"]


def test_read_recent_single_line(tmp_path):
    _write_log(tmp_path, ["only\n"])
    assert applog.read_recent(str(tmp_path), 1) == ["only"]


def test_read_recent_nonpositive_n(tmp_path):
    _write_log(tmp_path, ["a\n", "b\n"])
    assert applog.read_recent(str(tmp_path), 0) == []
    assert applog.read_recent(str(tmp_path), -5) == []


# ---------------------------------------------------------------------------
# 编码 / 错误处理（保留原有行为）
# ---------------------------------------------------------------------------
def test_read_recent_invalid_utf8_replaced(tmp_path):
    logf = applog.log_path(tmp_path)
    os.makedirs(os.path.dirname(logf), exist_ok=True)
    with open(logf, "wb") as fh:
        fh.write("2026-08-20 [INFO] ok\n2026-08-20 [INFO] bad\xffbyte\nline3\n".encode("latin-1"))
    got = applog.read_recent(str(tmp_path), 10)
    assert len(got) == 3
    assert got[0] == "2026-08-20 [INFO] ok"
    assert "bad" in got[1] and "\ufffd" in got[1]
    assert got[2] == "line3"


def test_read_recent_oserror_returns_empty(tmp_path, monkeypatch):
    _write_log(tmp_path, ["a\n"])

    def _boom(*_args, **_kwargs):
        raise OSError("模拟读取失败")

    monkeypatch.setattr("builtins.open", _boom)
    assert applog.read_recent(str(tmp_path), 10) == []


# ---------------------------------------------------------------------------
# 超大文件 / 流式行为（deque 只保留最近 n 行）
# ---------------------------------------------------------------------------
def test_read_recent_large_file(tmp_path):
    """20 万行日志：只返回最近 n 行且顺序保持，证明内存与总行数无关。"""
    n, total = 300, 200_000
    logf = applog.log_path(tmp_path)
    os.makedirs(os.path.dirname(logf), exist_ok=True)
    with open(logf, "w", encoding="utf-8") as fh:
        for i in range(total):
            fh.write(f"line {i}\n")
    got = applog.read_recent(str(tmp_path), n)
    assert len(got) == n
    assert got[0] == f"line {total - n}"
    assert got[-1] == f"line {total - 1}"
    assert got == [f"line {i}" for i in range(total - n, total)]


def test_read_recent_is_streaming_no_readlines(tmp_path, monkeypatch):
    """证明实现为逐行流式：大输入下只迭代一次、绝不调用 readlines()。"""
    n = 50
    _write_log(tmp_path, ["dummy\n"])  # 仅让 os.path.isfile 通过
    lines = [f"line {i}\n" for i in range(10_000)]
    fake = _StreamingFile(lines)
    monkeypatch.setattr("builtins.open", lambda *_a, **_k: fake)
    got = applog.read_recent(str(tmp_path), n)
    assert got == [f"line {i}" for i in range(10_000 - n, 10_000)]
    assert fake.readlines_called is False
    assert fake.iter_calls == 1


# ---------------------------------------------------------------------------
# read_errors（同批回归，确认未受影响）
# ---------------------------------------------------------------------------
def test_read_errors_multi_day(tmp_path):
    _write_errors(tmp_path, "2026-08-01", ["err a\n", "\n", "err b\n"])
    _write_errors(tmp_path, "2026-08-02", ["err c\n"])
    got = applog.read_errors(str(tmp_path), ["2026-08-01", "2026-08-02"], 10)
    assert got == ["2026-08-01 err a", "2026-08-01 err b", "2026-08-02 err c"]


def test_read_errors_truncates_to_n(tmp_path):
    _write_errors(tmp_path, "2026-08-01", [f"e{i}\n" for i in range(5)])
    _write_errors(tmp_path, "2026-08-02", [f"e{i}\n" for i in range(5, 12)])
    got = applog.read_errors(str(tmp_path), ["2026-08-01", "2026-08-02"], 5)
    assert got == ["2026-08-02 e7", "2026-08-02 e8", "2026-08-02 e9", "2026-08-02 e10", "2026-08-02 e11"]
    assert len(got) == 5


def test_read_errors_missing_days_skipped(tmp_path):
    got = applog.read_errors(str(tmp_path), ["2026-08-01", "2026-08-02"], 10)
    assert got == []