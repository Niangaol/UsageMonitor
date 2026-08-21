# -*- coding: utf-8 -*-
"""tests/api/test_goals_api.py — /api/goals 契约（进度读取 + 设置保存）。"""

from __future__ import annotations

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tests.conftest import make_record, seed_day  # noqa: E402

_DAY = "2099-03-15"


def test_goals_default_disabled(api_server):
    """功能默认关闭：GET 返回 enabled=false 空态，不产生错误。"""
    client, root = api_server
    s, d, _ = client.get(f"/api/goals?date={_DAY}")
    assert s == 200 and d["enabled"] is False and d["goals"] == []
    print("  [PASS] goals_default_disabled")


def test_goals_settings_save_and_progress(api_server):
    """POST 保存设置 → config.json 落盘 → GET 反映开启状态与进度。"""
    client, root = api_server
    seed_day(root, _DAY, [
        make_record(_DAY, 9, 120),
        make_record(_DAY, 14, 45, category="AI编程"),
    ])
    s, d, _ = client.post("/api/goals/settings", {
        "enabled": True, "daily_active_min": 60, "daily_coding_min": 30})
    assert s == 200 and d["ok"] is True, d
    # 落盘校验（原子写后的 config.json）
    with open(os.path.join(root, "config.json"), "r", encoding="utf-8") as fh:
        saved = json.load(fh)["goals"]
    assert saved == {"enabled": True, "daily_active_min": 60, "daily_coding_min": 30}
    # 进度：总活跃 165 分钟达标；编码 = 开发工具(120) + AI编程(45) = 165 分钟达标
    s, d, _ = client.get(f"/api/goals?date={_DAY}")
    assert s == 200 and d["enabled"] is True
    by_id = {g["id"]: g for g in d["goals"]}
    assert by_id["active"]["actual_min"] == 165 and by_id["active"]["met"] is True
    assert by_id["coding"]["actual_min"] == 165 and by_id["coding"]["met"] is True
    assert d["all_met"] is True and d["streak"]["today_met"] is True
    print("  [PASS] goals_settings_save_and_progress")


def test_goals_settings_validation_clamps(api_server):
    """非法/越界输入被夹取：负数→0、超上限→1440、非数字→0。"""
    client, root = api_server
    s, d, _ = client.post("/api/goals/settings", {
        "enabled": True, "daily_active_min": -10, "daily_coding_min": 99999})
    assert s == 200 and d["ok"]
    assert d["goals"]["daily_active_min"] == 0
    assert d["goals"]["daily_coding_min"] == 1440
    s, d, _ = client.post("/api/goals/settings", {"enabled": False, "daily_active_min": "abc"})
    assert s == 200 and d["goals"]["daily_active_min"] == 0
    print("  [PASS] goals_settings_validation_clamps")


def test_goals_invalid_date_falls_back_to_today(api_server):
    """date 非法时回退今天（不 400，概览页始终可拉取）。"""
    client, root = api_server
    s, d, _ = client.get("/api/goals?date=not-a-date")
    assert s == 200 and "date" in d and d["enabled"] is False
    print("  [PASS] goals_invalid_date_falls_back_to_today")
