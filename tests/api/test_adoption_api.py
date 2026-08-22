# -*- coding: utf-8 -*-
"""tests/api/test_adoption_api.py — /api/adoption 端点契约（Git 侧采纳率代理指标）。

覆盖：
  契约空态（未配置 Git 仓库 → found=False，200 可展示，绝不 500）；
  非法/缺失日期 → 400；统一安全头（CSP）与会话不落地；
  同源校验（恶意 Origin → 403）、访问口令（开 token → 401/200）；
  有真实数据路径（monkeypatch git_insights）→ found=True、retention/reworked 正确、
  confidence 仅 low/medium 绝不 high、免责声明原样返回。
"""

from __future__ import annotations

import json
import os
import sys
import threading

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import dashboard  # noqa: E402
import git_insights  # noqa: E402
from tests.conftest import ApiClient  # noqa: E402

_DAY = "2099-01-10"


@pytest.fixture
def adoption_server(tmp_path, request):
    """起一个 /api/adoption 专用服务器；request.param 可覆盖 config.json（如开 token）。"""
    root = str(tmp_path / "adopt_root")
    os.makedirs(root, exist_ok=True)
    cfg = {"update": {"api_base": "http://127.0.0.1:1"},
           "ai_sessions": {"enabled": False}}
    overrides = getattr(request, "param", None)
    if isinstance(overrides, dict):
        cfg.update(overrides)
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    dashboard.invalidate_days_cache()
    server = dashboard.create_server(root, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = ApiClient(port)
    try:
        yield client, root
    finally:
        server.shutdown()
        server.server_close()
        dashboard.invalidate_days_cache()


def test_empty_state_contract(adoption_server):
    """未配置 Git 仓库 → 契约空态 200（found=False + 免责声明 + low confidence）。"""
    client, _ = adoption_server
    s, d, hdr = client.get(f"/api/adoption?date={_DAY}")
    assert s == 200, f"/api/adoption status {s}: {d}"
    assert d["date"] == _DAY
    assert d["enabled"] is True
    assert d["found"] is False
    assert d["confidence"] == "low"
    assert d["projects"] == []
    assert "非真实采纳率" in d["notice"] and "仅供参考" in d["notice"]
    assert d["summary"]["retention"] is None
    assert d["summary"]["reworked_ratio"] is None
    # 统一安全头
    assert hdr.get("Content-Type", "").startswith("application/json")
    assert hdr.get("Content-Security-Policy", "")


def test_invalid_date_returns_400(adoption_server):
    """缺失/非法日期 → 400（与既有日期端点约定一致），绝不 500。"""
    client, _ = adoption_server
    for path in ("/api/adoption",
                 "/api/adoption?date=nope",
                 "/api/adoption?date=20/01/2026",
                 "/api/adoption?date=../etc"):
        s, d, _ = client.get(path)
        assert s == 400, f"{path} → {s}: {d}"


def test_never_500(adoption_server):
    """任意输入都不 500：端点异常一律契约空态 200 可展示。"""
    client, _ = adoption_server
    s, d, _ = client.get(f"/api/adoption?date={_DAY}&x=1")
    assert s == 200 and isinstance(d, dict) and "found" in d


def test_origin_forbidden(adoption_server):
    """跨站 Origin → 403（隐私数据防偷读）。"""
    client, _ = adoption_server
    s, _, _ = client.get(f"/api/adoption?date={_DAY}",
                         headers={"Origin": "http://evil.example"})
    assert s == 403


@pytest.mark.parametrize("adoption_server", [{"dashboard_token": "sekret-123"}], indirect=True)
def test_auth_required_when_token_set(adoption_server):
    """开启访问口令：无 token → 401；带正确 token → 200。"""
    client, _ = adoption_server
    s, _, _ = client.get(f"/api/adoption?date={_DAY}")
    assert s == 401
    s, d, _ = client.get(f"/api/adoption?date={_DAY}",
                         headers={"X-Dashboard-Token": "sekret-123"})
    assert s == 200 and "found" in d


def test_found_with_patched_git(adoption_server, monkeypatch):
    """有真实 Git 数据路径：found=True、指标正确、confidence 仅 low/medium。"""
    client, _ = adoption_server
    monkeypatch.setattr(
        git_insights, "git_config",
        lambda config: {"enabled": True,
                        "projects": [{"name": "ProjA", "path": r"D:\projA"}],
                        "timeout_s": 10, "top_files": 5})

    def fake_analyze(repo, day, timeout, top_files):
        return {"name": "ProjA", "path": repo["path"], "commit_count": 2,
                "lines_added": 300, "lines_deleted": 100, "churn": 400, "files": 3,
                "top_files": [{"path": "a.py", "added": 200, "deleted": 50},
                              {"path": "b.py", "added": 100, "deleted": 50}],
                "authors": ["t"], "modify_ratio": 0.25}

    monkeypatch.setattr(git_insights, "analyze_repo", fake_analyze)

    s, d, _ = client.get(f"/api/adoption?date={_DAY}")
    assert s == 200, f"/api/adoption status {s}: {d}"
    assert d["found"] is True
    assert d["confidence"] == "medium"
    assert d["summary"]["retention"] == pytest.approx(0.75, abs=1e-4)
    assert d["summary"]["reworked_ratio"] == pytest.approx(0.25, abs=1e-4)
    proj = d["projects"][0]
    assert proj["retention"] == pytest.approx(0.75, abs=1e-4)
    assert proj["reworked_ratio"] == pytest.approx(0.25, abs=1e-4)
    assert proj["confidence"] in ("low", "medium")
    # 永不 high
    assert d["confidence"] != "high"
    assert all(p["confidence"] != "high" for p in d["projects"])
    assert "非真实采纳率" in d["notice"]
