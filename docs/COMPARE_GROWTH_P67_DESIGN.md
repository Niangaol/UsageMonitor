# VibeTrace v2.6 · P6/P7 预研：多工具横向对比 + 能力成长曲线 —— 骨架设计文档

> 对应 `docs/VIBECODING_IMPLEMENTATION_GUIDE.md` §5.2「功能 B：多工具横向对比」（PR 表 P6）与 §5.3「功能 C：能力基线 / 成长曲线」（PR 表 P7）。
> 本阶段只交付**设计文档 + 空模块骨架**，不实现完整功能、不改核心逻辑（`dashboard.py` / `report.py` / `ai_sessions.py` / `insights.py` / `git_insights.py` 一概不动）。
> 实现时按本设计落地即可直接编码。

---

## 0. 命名约定（重要）

| 指导文档建议 | 本设计（用户任务）命名 | 说明 |
|---|---|---|
| `tool_compare.py` + `/api/tool-compare`（§5.2） | 模块保持 **`tool_compare.py`**；端点按任务命名 **`/api/ai-compare`** | 模块名与端点名解耦；实现阶段可在同路由块内做 `/api/tool-compare` 别名（同一 handler），非强制 |
| `growth.py` + `/api/growth`（§5.3） | 模块 **`growth.py`**；端点按任务命名 **`/api/trend`** | 同上，`weeks` 参数沿用 §5.3；`growth_baseline.json` 快照文件名不变 |

- 二者对应关系在 §11 执行清单里以 `(P6)/(P7)` 标注，回滚时删除新模块 + 新端点即可，历史数据零影响。
- 参照先例：`TIMELINE_P2_DESIGN.md` 中 §4.2 建议 `vibe_timeline.py`，实际交付按任务要求为 `timeline.py`——本次同样「指导文档=契约，任务=命名」，以本表为准。

---

## 1. 目标与边界

**P6 多工具对比**：回答「opencode vs chatgpt vs claude，哪个性价比/产出/质量好」。数据已按工具/模型/项目结构化（`ai_sessions.collect().tools`），缺的是**归一化对比维度**（把不同工具的 token/成本/产出放到同一把尺子上比）。

**P7 成长曲线**：回答「我这个月有没有在变强」。复用全部既有派生指标（focus_score / quality_avg / 产出 / 修改率 / time_saved），做**周粒度快照对比**，并持久化周均值快照避免每周全量重算。

**设计铁律**（对齐指导文档 §7 数据矩阵）：
- `usage.jsonl` 永不因派生逻辑修改；
- `tool_compare.py` **实时派生**（每请求重算，可加进程内缓存），不落盘；
- `growth.py` 产出**持久化周均值快照** `growth_baseline.json`（只存周均值，不存明细，隐私友好），明细永远现算；
- 所有「估算/近似」指标带 `notice`/`仅参考` 标注（对齐 §12 风险矩阵 R1）；
- 新端点全部走既有 `_origin_allowed` + `_auth_ok` 三件套（`dashboard.py:2543/2551`），无新安全代码。

---

## 2. `tool_compare.py` —— 数据结构与算法

模块层级：顶层纯函数模块，import `ai_sessions` / `report` / `datetime`。

### 2.1 输入源与字段映射（两漏斗互补）

`ai_sessions.collect()` 的 tool stats **没有时长字段**（只有 turns/rounds/tokens/cost/generated_chars），
而 `report.aggregate().by_ai` 有**前台窗口级 AI 时长**。二者漏斗口径不同，本设计**同时消费两源**补全指标：

| 指标 | 来源 | 口径说明 |
|---|---|---|
| `sessions` | `collect().tools[tool]["conversations"]` 条数 | 会话深度漏斗：有本地会话文件支撑的会话数 |
| `minutes` | `report.aggregate(date).by_ai[tool]` 毫秒 → 分钟 | 前台漏斗：窗口级 AI 编程时长（含无会话文件的时段） |
| `tokens_total` / `cost_total` | `collect().tools[tool]` | 深度漏斗 |
| `generated_chars` / `generated_lines` | `collect().tools[tool]` | 深度漏斗 |
| `quality_avg` | `collect().tools[tool]["conversations"][*]["quality_score"]` 均值 | 会话级质量分（v2.5 已落地，`ai_sessions.py:714 _quality_summary` 同源逻辑）|
| `grade_dist` | 同上，按 `quality_grade` 分档计数 | 优/良/中/待优化 |

> ⚠️ **关键设计发现**：`minutes` **必须**来自 `report.by_ai` 而非 `conversations.first/last` 差值——
> 后者只覆盖「有会话文件」的时间，会系统性低估（参考 TIMELINE 先例对口径差异的处理方式）。
> 两漏斗差异在 UI/notice 里明示，不做隐藏合并。

### 2.2 归一化派生指标（全部带 `仅参考` 标注）

| 指标 | 公式 | 除零兜底 |
|---|---|---|
| `cost_per_1k_tokens` | `cost_total / tokens_total * 1000` | `tokens_total==0 → None` |
| `chars_per_dollar` | `generated_chars / cost_total` | `cost_total<=1e-9 → None`（`None` 排最后）|
| `chars_per_session` | `generated_chars / sessions` | `sessions==0 → 0` |
| `tokens_per_session` | `tokens_total / sessions` | `sessions==0 → 0` |
| `share_pct` | `{cost, sessions, tokens}` 各自占总量的比例 | 总量为 0 → 该维度全 0 |
| `quality_avg` | 会话 quality_score 均值 | 无 scored 会话 → `None` |

**排序**：默认按 `chars_per_dollar` 降序（`None` 排最后）；配置 `sort_by` 可切换（§6）。

### 2.3 对外响应契约（`/api/ai-compare`）

```jsonc
// GET /api/ai-compare?start=2026-08-10&end=2026-08-20
{
  "start": "2026-08-10", "end": "2026-08-20",
  "days": 11,
  "notice": "仅参考：token/成本为本地会话文件估算，非官方账单；minutes 为前台窗口级 AI 时长，与深度漏斗口径不同",
  "tools": [
    { "tool": "opencode",
      "sessions": 42, "minutes": 320.5,
      "tokens_total": 183000, "cost_total": 4.12,
      "generated_chars": 120000, "generated_lines": 1800,
      "quality_avg": 72, "grade_dist": {"优": 5, "良": 12, "中": 3, "待优化": 1},
      "cost_per_1k_tokens": 0.0225, "chars_per_dollar": 29126, "chars_per_session": 2857,
      "share_pct": {"cost": 0.63, "sessions": 0.58, "tokens": 0.66}
    }
  ],
  "summary": { "tools": 3, "total_sessions": 72, "total_cost": 6.54, "total_minutes": 520.0 }
}
```

错误语义（对齐 §8 新端点规则）：
- `400 {"error":"invalid date"}` —— start/end 缺失或非法（`_valid_date`）
- `400 {"error":"invalid range"}` —— `end < start` 或范围天数 > 上限（默认 90，防 DoS）
- `500 {"error":"ai-compare unavailable: <detail>"}` —— 内部异常（仿 `/api/ai-sessions` 降级）
- **无数据 → 200 空态**：`tools: []`、`summary` 归零、`notice` 保留

### 2.4 函数签名（骨架见 `tool_compare.py`）

```python
def compare_config(config: dict) -> dict:
    """读 config.tool_compare，带默认 {enabled, sort_by, top, min_sessions, max_days}；风格对齐 git_insights.git_config（git_insights.py:37）。"""

def compare_tools(days: list[str], data_root: str, config: dict) -> dict:
    """跨 N 天聚合工具对比（纯派生，不落盘）。days 为升序 YYYY-MM-DD 列表。
    返回 §2.3 契约的 dict；enabled=false 或 days 为空 → 空态。"""

def _merge_tool_stats(rows: list[dict]) -> dict:
    """把某工具跨天的 collect().tools[tool] stats 逐字段求和（keys 见 ai_sessions._empty_tool_stats ai_sessions.py:852）。"""

def _derive_metrics(merged: dict, totals: dict) -> dict:
    """按 §2.2 表派生归一化指标 + share_pct + 排序键。纯函数，除零兜底，天然可单测。"""
```

---

## 3. `growth.py` —— 数据结构与算法

模块层级：顶层纯函数模块，import `report` / `ai_sessions` / `insights` / `git_insights` / `paths` / `os`。

### 3.1 周粒度定义

- 用 `datetime.date.isocalendar()` 的 `(year, week)` → `f"{year}-W{week:02d}"`（如 `2026-W33`），
  与 Python `datetime.isocalendar()` 一致，避免 locale 差异（对齐文档 §10 边界：时间戳/日期归一化）。
- 每周的日期列表：`{d: d.isocalendar()[:2] == (y, w)}` 扫 `_available_days(data_root)`（`dashboard.py:3440`）过滤。

### 3.2 周指标来源（全复用，不新发明指标）

| 周指标 | 来源（每日） | 周聚合 |
|---|---|---|
| `focus_score` | `insights.behavior_insights(agg, config)["focus_score"]`（`insights.py:590`） | 均值（round 取整）|
| `quality_avg` | `ai_sessions.collect(date).total["quality_summary"]["avg"]`（`ai_sessions.py:747`） | 均值（仅统计 `sessions_scored>0` 的天）|
| `generated_lines` | `collect().total["generated_lines"]` | **周总和** |
| `lines_added` | `git_insights.git_insights(config, date)["total"]["lines_added"]` | 周总和 |
| `modify_ratio` | 同上 `["total"]["modify_ratio"]` | 有 git 数据天数的均值，无则 `None` |
| `ai_minutes` | `report.aggregate(date).by_category["AI编程"]` | 周总和（分钟）|
| `saved_minutes` | `insights.time_saved_insights(agg, config)["saved_ms"]`（`insights.py:687`） | 周总和（分钟，round）|

> ⚠️ **关键设计发现**：`quality_summary.avg` 在没有会话的天是 0（`_quality_summary` 空态 `ai_sessions.py:717`）——
> **不能在分母里混入 0 分天**，否则周均值虚低。本设计只统计 `sessions_scored>0` 的天，并在周条目里暴露 `days`/`scored_days` 两个计数便于前端判断可信度。

### 3.3 快照格式（`<data_root>/growth_baseline.json`）

```jsonc
{
  "schema": 1,
  "updated_at": "2026-08-25T00:05:00",
  "metrics": ["focus_score", "quality_avg", "generated_lines", "modify_ratio",
              "lines_added", "ai_minutes", "saved_minutes"],
  "weeks": [
    { "week": "2026-W33", "days": 5, "scored_days": 4,
      "focus_score": 72, "quality_avg": 68, "generated_lines": 1200,
      "lines_added": 800, "modify_ratio": 0.20,
      "ai_minutes": 300, "saved_minutes": 150 }
  ],
  "trend": [
    { "metric": "quality_avg", "from": 68, "to": 74, "slope": "+8.8%", "dir": "up" },
    { "metric": "modify_ratio", "from": 0.20, "to": 0.15, "slope": "-25.0%", "dir": "down" }
  ]
}
```

**写策略（幂等，核心验收）**：
1. `growth_snapshot(data_root, config, force=False)`：读现有快照（无/坏 → 全量现算）。
2. 对每个**已完成周**（ISO 周末 < 今天）与**当前周**分别聚合；当前周只算到「昨天」止（避免当日半截数据抖动）。
3. 与快照比对：已存在的周（`week` key 相同且字节等价）**跳过重算**；缺失/变化周增量更新。
4. 写文件用 **tmp + `os.replace` 原子替换**（避免半写）；单进程（dashboard 唯一写者）。
5. `weeks` 周数不足（`min_days_per_week` 默认 3，指导文档验收「造 2 周各 3 天」）→ 该周丢弃，避免噪音周进趋势。

**slope/dir 判定**（指导文档 §5.3）：`dir = up if rel>=flat else down if rel<=-flat else flat`，`flat=0.03`
（`<3%` 视为 flat 防噪声）；`rel = (cur - prev) / max(abs(prev), MIN_EPS)`；`MIN_EPS=1e-9`
（`prev==0` 时：`cur>0 → up(100%)`，`cur==0 → flat`）。`modify_ratio` 为**反向指标**：`dir` 语义反转（降=变好 `up_good`），
用 `"dir": "down"` + `"good_dir": true` 标记，前端着色用。

### 3.4 对外响应契约（`/api/trend`）

```jsonc
// GET /api/trend?weeks=8
{
  "weeks": [ /* §3.3 的 weeks 数组，按周升序，最多 weeks 条 */ ],
  "trend":  [ /* §3.3 的 trend 数组，按 metric 分组 */ ],
  "updated_at": "2026-08-25T00:05:00",
  "source": "snapshot" | "fresh",   // 命中快照 or 本次现算写入
  "notice": "仅参考：周均值为本地估算（focus/quality 为规则打分，git 未配置时 modiy_ratio 缺失）"
}
```

错误语义：
- `400 {"error":"invalid weeks"}` —— weeks 非 1..52 的整数或缺失（默认 8）
- `500 {"error":"trend unavailable: <detail>"}` —— 内部异常（含快照写失败）
- **无数据 → 200 空态**：`weeks: []`、`trend: []`、`source: "fresh"`

### 3.5 函数签名（骨架见 `growth.py`）

```python
def growth_config(config: dict) -> dict:
    """读 config.growth，带默认 {enabled, weeks, min_days_per_week, flat_threshold}。"""

def _week_key(d: datetime.date) -> str:
    """YYYY-MM-DD → 'YYYY-Www'（isocalendar）。"""

def _week_days(weeks: tuple[int,int], day_list: list[str]) -> list[str]:
    """从升序日期列表筛出属于某 ISO 周的天。"""

def _aggregate_week(days: list[str], data_root: str, config: dict) -> dict | None:
    """按 §3.2 表聚合一周（days 不足 min_days_per_week → None）。纯函数，monkeypatch 可测。"""

def _slope(cur: float | None, prev: float | None, flat: float) -> dict:
    """按 §3.3 判定，返回 {from, to, slope, dir}；任一为 None → {"dir":"flat"}（无对比不渲染）。"""

def _read_snapshot(data_root: str) -> dict | None:
    """读 growth_baseline.json；缺文件/坏 JSON → None（自愈重算）。"""

def _write_snapshot(data_root: str, payload: dict) -> None:
    """tmp + os.replace 原子写。"""

def growth_snapshot(data_root: str, config: dict, force: bool = False) -> dict:
    """主入口：增量更新快照并返回 §3.4 契约（weeks 截断 + trend）。幂等：重跑不翻倍、不重复追加。"""
```

---

## 4. `dashboard.py` —— 端点设计（实现阶段粘贴，本阶段不动文件）

### 4.1 插入位置

在 `do_GET` 内 `/api/timeline` 路由块之后插入（`dashboard.py:2837` 附近，即 timeline 块 `:2807` 结束后的下一处）。
既有守卫链路已统一覆盖：`_origin_allowed`（`dashboard.py:2551`）→ `_auth_ok`（`:2543`）→ 每端点 `_valid_date`（`:2615`）→ `_send_json`（`:2594`）。

### 4.2 待粘贴代码（骨架，实现阶段填 `days` 生成与快照写）

```python
# --- P6: /api/ai-compare（tool-compare 别名可选）---
if path in ("/api/ai-compare", "/api/tool-compare"):
    start = self._valid_date(query)
    end = self._valid_date(query)
    if not start or not end:
        self._send_json({"error": "invalid date"}, 400)
        return
    config = _load_config_for_root(root, self.server.config_path)
    try:
        import tool_compare  # noqa: PLC0415
        days = _date_range(start, end)          # 实现阶段在 dashboard_util.py 或本模块加纯函数
        data = tool_compare.compare_tools(days, root, config)
        self._send_json(data)
    except ValueError:
        self._send_json({"error": "invalid range"}, 400)
    except Exception as exc:  # noqa: BLE001
        self._send_json({"error": f"ai-compare unavailable: {exc}"}, 500)
    return

# --- P7: /api/trend（growth 别名可选）---
if path in ("/api/trend", "/api/growth"):
    try:
        weeks = int((query.get("weeks") or ["8"])[0])
    except (TypeError, ValueError):
        weeks = 8
    if not (1 <= weeks <= 52):
        self._send_json({"error": "invalid weeks"}, 400)
        return
    config = _load_config_for_root(root, self.server.config_path)
    try:
        import growth  # noqa: PLC0415
        data = growth.growth_snapshot(root, config)
        data["weeks"] = data.get("weeks") or []
        if weeks < len(data["weeks"]):
            data["weeks"] = data["weeks"][-weeks:]
        self._send_json(data)
    except Exception as exc:  # noqa: BLE001
        self._send_json({"error": f"trend unavailable: {exc}"}, 500)
    return
```

### 4.3 缓存策略

| 层级 | 端点 | 策略 | 依据 |
|---|---|---|---|
| 默认 | `/api/ai-compare` | 每请求重算 | 派生廉价、范围受限（≤90 天）|
| 可选 | `/api/ai-compare` | 进程内 LRU `_compare_cache`（key=`(start,end,data_root)`，失效=日期目录 mtime/size 拼接）| 复用 `report._agg_cache`（`report.py:38`）思路；骨架预留命名 |
| 持久化 | `/api/trend` | `growth_baseline.json` 周快照 + 每请求增量比对 | 避免每周 7 天 × 多源重算（指导文档 §7 矩阵：成长快照=持久化）|
| 不落盘 | 两者 | 不写 `usage.jsonl` / `usage.db` | 铁律：派生与事实隔离 |

---

## 5. 前端原型（骨架页，本阶段交付）

不侵入 `dashboard.py` 的 `PAGE_TEMPLATE` 内联 JS，新增两个可打开的演示页（参照 `assets/timeline_preview.html`）：

### 5.1 `assets/compare_preview.html`
- 输入：start / end / 端口 / Token（可选）；
- `fetch("/api/ai-compare?start=&end=")` 调通，处理 400 / 200 空态；
- **表格**：工具 | 会话 | 分钟 | token | 成本 | chars/$ | chars/会话 | 质量均分 | 占比 → 按 `chars_per_dollar` 降序；
- **雷达图**：手写 `<canvas>`（零依赖），顶部取 top N 工具（默认 5），五维 = `chars_per_dollar` / `cost_per_1k_tokens`（反向 1−x）/ `quality_avg` / `turns 密度` / `sessions 占比`，各维 0–1 归一化；`None` 值记为 0 并在图例标灰；
- 底部 notice 行。

### 5.2 `assets/growth_preview.html`
- 输入：weeks（默认 8）/ 端口 / Token；
- `fetch("/api/trend?weeks=")` 调通；
- **折线图**：`<canvas>` 多序列折线（focus_score / quality_avg，双轴 0–100），`generated_lines` / `lines_added` 共轴柱状叠加（周总和可能量级不同，用右轴或对数显示）；
- **趋势卡**：每 metric 一行「方向箭头 + slope 文本」，`modify_ratio` 显示 good_dir 反转语义；
- 快照来源标注（`source: snapshot/fresh`）。

实现阶段把两个原型页的逻辑内联进 `PAGE_TEMPLATE` 对应视图（`nav-item data-view` 新增 `compare` / `growth` 两项，`switchView` 注册表 `dashboard.py:1167` 加 key）。

---

## 6. 配置 schema（`config.default.json` 建议新增，实现阶段加；骨架由模块 `config()` 兜底）

```jsonc
"tool_compare": {
  "enabled": true,
  "sort_by": "chars_per_dollar",   // chars_per_dollar | cost_per_1k_tokens | quality_avg | sessions
  "top": 10,                        // 对比表最多行；0=全部
  "min_sessions": 1,                // 少于该会话数的工具行剔除（防噪声）
  "max_days": 90                    // start~end 范围上限（DoS 防护）
},
"growth": {
  "enabled": true,
  "weeks": 8,                       // /api/trend 默认返回周数
  "min_days_per_week": 3,           // 低于该天数的周丢弃（对齐验收「造 2 周各 3 天」）
  "flat_threshold": 0.03            // slope 判定阈值（<3% 视为 flat）
}
```

老用户 `config.json` 无这些字段 → 模块内 `compare_config()` / `growth_config()` 带默认值兜底（增量生效，
对指导文档 §9 的 config 迁移机制零依赖，P1 的 `config_schema_version` 不需为本功能改动）。

---

## 7. 测试清单（分层，对齐指导文档 §10）

### 7.1 Unit（新增）

`tests/unit/test_tool_compare.py`：
| 用例 | 断言 |
|---|---|
| 两个工具跨 2 天聚合 | `tokens/cost/generated_chars` 求和正确、`sessions` 为会话条数 |
| `cost=0` / `tokens=0` / `sessions=0` | 不抛异常；`chars_per_dollar`/`cost_per_1k_tokens` → `None`、其余 0/中性值 |
| `share_pct` | 与总额比例正确；总量为 0 → 全 0 |
| 排序 | 默认按 `chars_per_dollar` 降序，`None` 排最后 |
| 空 days / enabled=false | 返回空态（§2.3）且不崩 |
| `days` 非升序传入 | 内部仍按升序聚合（幂等）|

`tests/unit/test_growth.py`：
| 用例 | 断言 |
|---|---|
| 周均值聚合（monkeypatch 三源） | `focus_score/quality_avg` 均值、`generated_lines` 周总和正确 |
| `scored_days` 过滤 | 无会话天不把 `quality_avg=0` 混入分母 |
| 缺周（< min_days_per_week） | 该周丢弃 → `weeks` 不含它 |
| slope/dir 判定 | `+8.8% → up`、`-1% → flat`、`prev=0,cur>0 → up(100%)`、`None → flat` |
| 快照读写幂等 | 重跑 `growth_snapshot` 两次：`weeks` 不重复、字节等价（`_read_snapshot` 比对）|
| 坏快照自愈 | 删文件 / 写垃圾 JSON → 全量重算成功 |
| `modify_ratio` 反向指标 | `dir=down` 且 `good_dir=true` |

### 7.2 API（新增）

`tests/api/test_dashboard_compare_api.py`：非法 date 400、`end<start` 400 invalid range、无数据 200 空态、有数据 200 契约字段齐、token 开启时 401/200（复用 `test_dashboard_timeline_api.py` 的 `_setup`/`_server` 模式）；
`tests/api/test_dashboard_trend_api.py`：weeks 缺失默认 8、`weeks=0/53` 400、无数据 200 空态、快照文件生成且第二次请求 `source=snapshot`。

### 7.3 Security / Performance（并入或新增）

- `tests/security/test_compare_trend_privacy.py`：快照文件只含周均值，**无会话标题/路径明细**；两端点带 Referer/Origin/token 校验通过、缺失拒绝（对齐 §8 三件套）。
- `tests/performance/test_growth_io.py`：构造 8 周 × 7 天数据，`growth_snapshot` 全量现算 < 限时（建议 3s）；`compare_tools` 90 天 < 5s。

### 7.4 验收命令

```powershell
python -m pytest tests/unit/test_tool_compare.py tests/unit/test_growth.py -q
python -m pytest tests/api/test_dashboard_compare_api.py tests/api/test_dashboard_trend_api.py -q
python -m pytest tests/ -q          # 全量回归（旧用例零破坏）
ruff check .
coverage run -m pytest tests/ -q && coverage report -m
```

---

## 8. 涉及文件与接口汇总

| 类型 | 文件 | 动作 | 说明 |
|---|---|---|---|
| 新增 | `tool_compare.py` | 本交付 | P6 派生纯函数层（§2）|
| 新增 | `growth.py` | 本交付 | P7 派生 + 快照层（§3）|
| 新增 | `tests/unit/test_tool_compare.py` | 本交付（骨架）| §7.1 |
| 新增 | `tests/unit/test_growth.py` | 本交付（骨架）| §7.1 |
| 新增 | `assets/compare_preview.html` | 本交付 | §5.1 原型骨架 |
| 新增 | `assets/growth_preview.html` | 本交付 | §5.2 原型骨架 |
| 新增（实现期）| `tests/api/test_dashboard_compare_api.py` / `test_dashboard_trend_api.py` | 实现期 | §7.2 |
| 修改（实现期）| `dashboard.py` | 实现期 | 两个路由块（§4.2 粘贴）；骨架期不动 |
| 修改（实现期）| `config.default.json` | 实现期 | §6 两段配置 |
| 修改（实现期）| `assets/dashboard.html` 或 `PAGE_TEMPLATE` 视图 | 实现期 | compare/growth 视图 + nav |
| 修改（实现期）| CHANGELOG / README / ROADMAP | 实现期 | DoD §13.2 |
| 修改（实现期）| `pyproject.toml` | 实现期 | `[tool.coverage.run].source` 追加 `tool_compare`/`growth`（当前列表未含，不追加则新模块覆盖率不计入门禁）|

**公开接口**：`compare_tools(days, data_root, config)`、`growth_snapshot(data_root, config, force=False)`（+ 内部纯函数见 §2.4/§3.5）；HTTP 端点 `/api/ai-compare`、`/api/trend`（别名 `/api/tool-compare`、`/api/growth` 可选）。

---

## 9. 风险与回滚

### 9.1 风险清单

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | `minutes` 口径双漏斗不一致造成误解 | 中 | 明确标注两源口径 + notice；`sessions`/`minutes` 并列展示不合并 |
| R2 | `quality_avg` 周均值被 0 分天拉低 | 中 | `scored_days` 过滤 + 周条目暴露 `scored_days`（§3.2 发现）|
| R3 | 跨 N 天重算性能（collect 扫目录）| 中 | `max_days=90` 上限；进程内 LRU（§4.3）；性能用例限时 |
| R4 | 快照并发写 / 半写文件 | 低 | 唯一写者（dashboard 单进程）+ tmp + `os.replace` 原子替换；坏快照自愈 |
| R5 | git/insights 关闭时指标缺失 | 低 | `modify_ratio=None` 不参与 trend；`focus_score=0` 天标注 `insights 关闭`；不阻塞 |
| R6 | ISO 周跨年（W52/W01 边界）| 低 | `isocalendar()` 统一；周 key 含年份（`2026-W01`）无歧义 |
| R7 | 老用户无新配置字段 | 低 | 模块 `config()` 默认兜底（§6），不依赖 config_schema_version |
| R8 | 快照含敏感明细 | 中 | 只存周均值（隐私友好）；security 测试断言无明细字段 |

### 9.2 回滚方案

两个模块均为**独立新增**，不触碰任何既有模块；端点均为**新增只读路由**。
**回滚 = 删除 `tool_compare.py` + `growth.py` + 两个骨架测试 + 两个原型页 +（实现期）dashboard 路由块与 config 段**；
全程不写库、不改 `usage.jsonl`、不动鉴权/同源逻辑 → 历史数据零影响。删除快照文件后自动重建（自愈）。

---

## 10. file:line 一键定位表（编码时速查）

| 位置 | file:line |
|---|---|
| AI 会话聚合（tools stats 字段）| `ai_sessions.py:866 collect()` · `:852 _empty_tool_stats` · `:1013 _add_dim` |
| 会话质量分（quality_score 已入 conversation）| `ai_sessions.py:1043 _conversation_summary` · `:714 _quality_summary` · `:747 _attach_quality` |
| 前台 AI 时长（by_ai）| `report.py:171 aggregate()` · `:118 _aggregate_records`（by_ai 累计 `:159`）|
| 专注度 / 时间节省 | `insights.py:590 behavior_insights` · `:687 time_saved_insights` · `:183 _insights_config` |
| Git 产出 / 修改率 | `git_insights.py:153 analyze_repo` · `:37 git_config` |
| 日期目录枚举 + 路由守卫 | `dashboard.py:3440 _available_days` · `:2615 _valid_date` · `:2543 _auth_ok` · `:2551 _origin_allowed` · `:2594 _send_json` · `:113 _load_config_for_root` |
| 端点插入点 | `dashboard.py:2837`（`/api/timeline` 块 `:2807` 之后）|
| 进程内缓存范例 | `report.py:38 _agg_cache` |
| 视图注册表（实现期前端）| `dashboard.py:1167`（views 数组）、`:1133 switchView` |
| 本次新增文件 | `tool_compare.py` · `growth.py` · `tests/unit/test_tool_compare.py` · `tests/unit/test_growth.py` · `assets/compare_preview.html` · `assets/growth_preview.html` · 本文档 |