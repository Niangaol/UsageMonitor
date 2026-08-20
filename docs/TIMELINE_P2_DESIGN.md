# Vibe 时间轴回放（v2.5 · P2 预研）—— 骨架设计文档

> 对应 `docs/VIBECODING_IMPLEMENTATION_GUIDE.md` §4.2「功能 B：Vibe Coding 时间轴回放」。
> 本阶段只交付**设计文档 + 空模块骨架**，不实现完整功能、不改核心逻辑。
> 实现时按本设计落地即可直接编码。

---

## 0. 命名约定（重要）

- 文档 §4.2 建议命名为 `vibe_timeline.py`；本任务按用户要求命名为 **`timeline.py`**。
- 二者是同一模块：`timeline.py` 负责事件派生的纯函数层，`vibe_timeline` 名称仅作配置段名保留（`config.vibe_timeline`）。
- 回滚时删除 `timeline.py` + 去掉 `/api/timeline` 路由即可，历史数据零影响。

---

## 1. 目标与边界

把某一天的「什么时间在哪个 AI 工具/项目上干活、花多少 token/钱、产出多少行、何时提交」还原成一条按时间递增的可回放时间轴。

**设计铁律**（对齐 §7 数据模型矩阵）：
- `usage.jsonl` 永不因派生逻辑修改；
- `timeline.py` 是**纯派生函数**，全部内存计算，**不落盘**；
- 对单日数据重算是廉价操作（日期范围小），默认每请求重算，进程内缓存为可选优化。

### 输入三源（全部已存在）

| 源 | 入口 | file:line | 需要的字段 |
|---|---|---|---|
| 前台会话 | `report.aggregate(date, root)` | `report.py:171` | `agg["sessions"]`：`start`/`end`/`category`/`ai_tool`/`duration_ms` |
| AI 会话深度 | `ai_sessions.collect(date, config, web_visits)` | `ai_sessions.py:716` | `total["conversations"]`：`first`/`last`/`tool`/`model`/`project`/`tokens_total`/`cost_total`/`generated_lines`（见 `_conversation_summary` `ai_sessions.py:892`） |
| Git 变更 | 底层 `git log`（复用 `git_insights` 只读命令层） | `git_insights.py:204 _parse_numstat` / `:112 _run_git` | 每个 commit：`date`/`hash`/`added`/`deleted` |

> ⚠️ **关键设计发现**：`git_insights.analyze_repo`（`git_insights.py:153`）只返回**当天聚合**（`commit_count`/`lines_added`…），**不暴露单条 commit 的时间戳**。要在时间轴上插入 commit，`timeline.py` 必须**自己调用 `git log`**（复用 `git_insights._run_git` + `_parse_numstat`）来拿 per-commit 事件，而不是消费 `git_insights.git_insights()` 的聚合结果。这是本设计区别于文档 §4.2 假设的关键补充。

---

## 2. `timeline.py` —— 数据结构

模块层级：`timeline.py` 是顶层纯函数模块，import `report` / `ai_sessions` / `git_insights` / `classifier` / `paths`。

### 2.1 时间戳归一化（统一可排序 key）

三源时间戳格式不一致，先转成统一内部表示 `_ts`：

| 源字段 | 原始格式 | 归一化 |
|---|---|---|
| `report.sessions[].start/end` | `"2026-08-20T09:12:00"`（naive 本地） | `datetime`（naive）|
| `ai_sessions.conversations[].first/last` | `"2026-08-20T09:47:00"`（naive 本地，见 `ai_sessions.py:300 _extract_timestamp`） | `datetime`（naive）|
| `git commit.date` | `"2026-08-20 09:48:30 +0800"`（`--date=iso` 带时区） | 去时区转本地 naive `datetime` |

统一后所有事件用 `_ts`（`datetime`）做时间轴排序，永不依赖字符串字典序。

### 2.2 事件记录（`event`）

```python
# timeline 内部事件（dataclass）：三种 kind，字段按 kind 可选
@dataclass
class Evt:
    kind: str            # "ai_block" | "ai_conversation" | "git_commit"
    ts: datetime         # 归一化时间轴 key
    payload: dict        # 各 kind 的展示字段（扁平 dict，见下）
```

对外 JSON 输出的事件统一带 `t_start`（`"HH:MM:SS"`）+ `kind`，兼容文档 §4.2 的示例结构。

### 2.3 对外响应结构（`build_timeline` 返回值）

```jsonc
{
  "date": "2026-08-20",
  "events": [
    { "t_start": "09:12:00", "t_end": "09:47:00", "kind": "ai_block",
      "tool": "opencode", "project": "VibeTrace", "model": "deepseek-v4-pro",
      "tokens_total": 12000, "cost_total": 0.31, "generated_lines": 180,
      "conversations": 6, "quality": 82 },
    { "t_start": "09:48:30", "kind": "git_commit", "hash": "ab12c3d",
      "project": "VibeTrace", "added": 150, "deleted": 20, "modify_ratio": 0.12 },
    { "t_start": "10:02:00", "kind": "ai_conversation",
      "tool": "chatgpt", "project": "未识别", "model": "gpt-4o",
      "tokens_total": 3000, "cost_total": 0.02, "generated_lines": 40 }
  ],
  "summary": { "ai_minutes": 95, "commit_count": 3, "churn": 520,
               "total_cost": 0.9, "ai_blocks": 4, "conversations": 9 }
}
```

`kind` 语义：
- `ai_block`：粗粒度「AI 工作块」（若干相邻 AI 会话段合并得到），是时间轴主叙事单元；
- `ai_conversation`：块内无法进一步归并的单条会话（独立展示，保留 detail）；
- `git_commit`：一次 commit 提交点。

> 骨架阶段简化：先用 `ai_block` + `git_commit` 两种 kind（对齐文档 §4.2），
> `ai_conversation` 作为一个可选的 detail 展示层级，具体可视 UI 需要再加。
> **最小可用版本**：`ai_block` + `git_commit` 即可满足验收标准（3 段会话合并 + 2 commit + 有序）。

---

## 3. `timeline.py` —— 函数签名与算法

全部为**纯函数**（输入 → 输出，无副作用），便于单测。

### 3.1 公开 API

```python
# timeline.py
def build_timeline(date: str, data_root: str, config: dict,
                   project: str | None = None) -> dict:
    """构建某天的时间轴事件 + 摘要（纯派生，不落盘）。"""
```

### 3.2 内部函数与 file:line 定位

| 函数 | 建议定位 | 职责 |
|---|---|---|
| `timeline_config(config) -> dict` | `timeline.py:60` | 读 `config.vibe_timeline`，带默认 `{enabled: True, merge_gap_s: 120}`；风格对齐 `git_insights.git_config`（`git_insights.py:37`） |
| `_norm_dt(ts) -> datetime | None` | `timeline.py:85` | 三源时间戳 → naive `datetime` |
| `_collect_ai_sessions(date, root, config) -> list[dict]` | `timeline.py:105` | 调 `report.aggregate`（`report.py:171`），筛 `category==AI编程` 或 `ai_tool` 非空 的段 |
| `_collect_git_commits(config, day) -> list[dict]` | `timeline.py:135` | 复用 `git_insights.git_config`（`git_insights.py:37`）+ `_normalize_projects` + `_run_git`（`git_insights.py:112`）+ `_parse_numstat`（`git_insights.py:204`），产出 per-commit `{date, hash, project, added, deleted}` |
| `_merge_blocks(sessions, gap_s) -> list[dict]` | `timeline.py:170` | 按 `ai_tool`+时间相邻合并（间隔 < `gap_s` 归一块），输出粗粒度 AI 块 |
| `_attach_conversations(blocks, convs, gap_s) -> list[dict]` | `timeline.py:210` | 用 `ai_sessions.collect` 的 conversations(`ai_sessions.py:716`) 的 `first/last` 把 token/cost/generated_lines 叠加回对应块 |
| `_to_events(blocks, commits, project) -> list[dict]` | `timeline.py:250` | 合并 `ai_block` + `git_commit`，统一 `t_start`，`project` 过滤，按 `_ts` 升序排序 |
| `_summarize(events) -> dict` | `timeline.py:290` | 汇总 `ai_minutes`/`commit_count`/`churn`/`total_cost` 等 |

### 3.3 Merge 算法（`build_timeline` 主流程）

```
build_timeline(date, root, config, project=None):
  cfg = timeline_config(config)
  if not cfg["enabled"]:
      return empty(date)                    # events=[], summary 归零（200 空态）
  sessions = _collect_ai_sessions(...)      # 仅 AI 相关段
  blocks   = _merge_blocks(sessions, cfg["merge_gap_s"])
              # 同 ai_tool 且相邻间隔 < gap_s 合并；否则各自成块
  convs    = ai_sessions.collect(date, config).total.conversations
  blocks   = _attach_conversations(blocks, convs, cfg["merge_gap_s"])
              # 每个 conversation 按 first 落在某块 → 累加 token/cost/generated_lines
  commits  = _collect_git_commits(config, date)
  events   = _to_events(blocks, commits, project)   # 排序 + 过滤
  return { "date", "events", "summary": _summarize(events) }
```

**幂等与鲁棒性**（对齐 §10 边界表 `时间戳乱序/重复 → 仍有序且幂等`）：
- 排序一律用 `_norm_dt` 后的 `datetime`，输入乱序也能输出有序；
- 时间戳缺失/解析失败的事件丢弃（不崩、不占位）；
- `ai_sessions`/`git` 任一源解析失败时降级（block 只带基本信息），不影响时间轴整体。

---

## 4. `dashboard.py` —— `/api/timeline` 契约与缓存

### 4.1 端点注册位置

在 `dashboard.py` `do_GET` 内，紧邻 `/api/ai-sessions` 路由块之后插入（`dashboard.py:2712` 附近，即 `:2692` 起的 ai-sessions 块结束后的下一处）。复用既有守卫链路：

- 同源校验：`do_GET` 开头已统一调用 `_origin_allowed`（`dashboard.py:2551`）；
- 鉴权：`do_GET` 开头对 `/api/*` 已统一调用 `_auth_ok`（`dashboard.py:2543`）；
- 日期校验：`self._valid_date(query)`（`dashboard.py:2522`），非法返回 `400 {"error":"invalid date"}`；
- 响应：`self._send_json`（`dashboard.py:2501`），统一隐私/安全头。

> 端点命中断言顺序完全照抄相邻端点模式，插入即可被现有鉴权/同源逻辑覆盖，无需新增安全代码。

### 4.2 请求 / 响应契约

**请求**
```
GET /api/timeline?date=2026-08-20[&project=VibeTrace]
```
- `date` 必填，`_valid_date` 校验（`YYYY-MM-DD`），非法 → `400`
- `project` 可选，模糊匹配置（substring，大小写不敏感）；不传则返回全部

**响应（200）**
```jsonc
{ "date": "2026-08-20",
  "events":  [ ...见 §2.3... ],
  "summary": { "ai_minutes":95, "commit_count":3, "churn":520, "total_cost":0.9,
               "ai_blocks":4, "conversations":9 } }
```

**错误语义**
- `400 {"error":"invalid date"}` —— date 非法/缺失
- `400 {"error":"invalid project"}` —— （可选）project 含非法字符
- `500 {"error":"timeline unavailable: <detail>"}` —— 内部解析异常（仿 `/api/ai-sessions` 的 500 降级，`dashboard.py:2714`）
- **无数据 → 200 空态**（`events:[]`、`summary` 全零），不返回 500（对齐验收标准）

### 4.3 缓存策略

| 层级 | 策略 | 依据 |
|---|---|---|
| **默认** | 每请求重算，不缓存 | 单日派生廉价、日期范围小；对齐 §7 矩阵「时间轴事件=实时派生」 |
| **可选优化** | 进程内 LRU 缓存（key=`(date, project)`，文件 mtime/size 失效） | 对齐 `report._agg_cache`（`report.py:38`）模式，`dashboard.py` 顶层加一个小模块级 `_timeline_cache` |
| 不落盘 | 不写 JSONL / SQLite | 铁律：派生与事实隔离 |

进程内缓存实现建议：完全复用 `report.py:38` 的 LRU 思路（`_agg_cache` key=(date,data_root)，失效=usage.jsonl mtime/size）。骨架阶段**可先不做缓存**，接口预留 `_timeline_cache` 命名即可。

### 4.4 最小端点实现（骨架，待实现阶段粘贴）

```python
if path == "/api/timeline":
    date = self._valid_date(query)
    if not date:
        self._send_json({"error": "invalid date"}, 400)
        return
    project = query.get("project", [None])[0]
    config = _load_config_for_root(root, self.server.config_path)
    try:
        import timeline  # noqa: PLC0415
        data = timeline.build_timeline(date, root, config, project=project)
        self._send_json({"date": date, **data})
    except Exception as exc:  # noqa: BLE001
        self._send_json({"error": f"timeline unavailable: {exc}"}, 500)
    return
```

---

## 5. 前端回放视图 —— 最小可用原型

不侵入 `dashboard.py` 的 `PAGE_TEMPLATE` 内联 JS，独立出一个可打开的演示页
`assets/timeline_preview.html`（本仓库新增静态资源，零核心逻辑改动）。

**能力**（最小可用）：
1. 日期输入（默认今天，写死可测日期）；
2. `fetch("/api/timeline?date=...")` 调通并处理 400/200 空态；
3. 表格展示 `events`：时间 | kind | 工具/项目 | model | tokens | cost | 行数 / commit hash + 增删；
4. 顶部一行 summary。

**接入 dashboard 形态**：等实现阶段把该 JS 逻辑内联进 `PAGE_TEMPLATE` 对应的 tab/视图块即可，本原型仅验证「API 调通 + 表格展示」链路。

原型代码见 `assets/timeline_preview.html`（本仓库已放）。

---

## 6. 测试策略（骨架预演，实现阶段补齐单测）

`tests/unit/test_timeline.py`（对齐文档 `tests/unit/test_vibe_timeline.py`）：

| 用例 | 断言 |
|---|---|
| 无 AI 会话 | `events==[]`，summary 全零 |
| 相邻段合并（间隔<gap_s） | 合并成 1 个 `ai_block` |
| 间隔超阈值不合并 | 拆成多个事件 |
| 时间戳乱序输入 | 输出按 `t_start` 递增 |
| 空/坏 date | 返回空态 / 400 |
| git commit 插入 | 按 commit.date 落在时间轴正确位置 |
| project 过滤 | 只返回匹配 project 的事件 |

---

## 7. 风险与回滚方案

### 7.1 风险清单

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | **git commit 时间戳缺失**：`analyze_repo` 不暴露 per-commit 时间，需底层 `git log`（见 §1 发现） | 高 | timeline 复用 `git_insights._run_git`/`_parse_numstat`；git 缺失/失败 → 降级，不影响 AI 块 |
| R2 | commit `date` 带时区（`+0800`）与 naive ISO 混排 | 中 | `_norm_dt` 统一去时区转本地 naive，单测覆盖跨时区用例 |
| R3 | 相邻会话反复横跳（A/B 交替）导致块过碎 | 中 | `merge_gap_s` 可配（默认 120s）；块合并用「同 tool + 间隔阈值」双条件 |
| R4 | conversations 的 `first/last` 与前台 session 时间戳对齐误差 | 中 | 用「落在块时间窗内」判定叠加，容忍 ±gap_s，不强制严格包含 |
| R5 | 大 project 列表导致 git 命令多/慢 | 低 | 沿用 `git_insights` 超时（`timeout_s`，默认 10s）；失败降级不阻塞 |
| R6 | 数据膨胀到历史所有天 | 低 | 单日范围小；趋势用 E/D 复用 `_available_days` 分页，不做全量 |

### 7.2 回滚方案

- `timeline.py` 是**独立新模块**（纯函数），不触碰 `report.py`/`ai_sessions.py`/`git_insights.py` 核心逻辑；
- `/api/timeline` 是**新增只读端点**，不修改任何既有路由；
- **回滚 = 删除 `timeline.py` + 删除 `dashboard.py` 新增的 `/api/timeline` 路由块 + 删除 `assets/timeline_preview.html`**；
- 全程不写库、不改 `usage.jsonl`、不改鉴权/同源逻辑 → 历史数据零影响；
- 新配置 `config.vibe_timeline.{enabled,merge_gap_s}` 由 `timeline_config()` 带默认值兜底，老用户 `config.json` 不加字段也能正常跑（增量生效，对应 P1 的 config_schema_version 迁移，见 §4.1/文档 §9）。

---

## 8. file:line 一键定位表（编码时速查）

| 位置 | file:line |
|---|---|
| 前端会话聚合 | `report.py:171 aggregate()` |
| AI 会话深度 | `ai_sessions.py:716 collect()` · `:892 _conversation_summary` · `:300 _extract_timestamp` |
| Git 只读命令/解析 | `git_insights.py:37 git_config` · `:112 _run_git` · `:153 analyze_repo` · `:204 _parse_numstat` |
| 路由守卫 & JSON | `dashboard.py:2551 _origin_allowed` · `:2543 _auth_ok` · `:2522 _valid_date` · `:2501 _send_json` · `:113 _load_config_for_root` |
| 端点插入点 | `dashboard.py:2712`（`/api/ai-sessions` 块 `:2692` 之后） |
| 进程内缓存范例 | `report.py:38 _agg_cache` |
| 本次新增文件 | `timeline.py`（顶层）· `tests/unit/test_timeline.py` · `docs/TIMELINE_P2_DESIGN.md` · `assets/timeline_preview.html` |
