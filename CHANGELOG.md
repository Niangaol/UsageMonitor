# 更新日志

本项目所有值得记录的变更都归档在此文件中。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
发布流程：`git tag vX.Y.Z` 后由 CI 自动构建并发布 Release。

> 🌐 English version: [CHANGELOG.en.md](CHANGELOG.en.md)

## [Unreleased]

### 新增（ROADMAP Phase 4 · 行为洞察）

- **专注度评分**（离线规则）：基于最长专注段、编码/开发占比、每小时切换频率综合打分 0–100 并按高中低分级
- **死循环检测**：识别时间窗内密集短会话高频反复切换（如多应用快速往返）并告警
- **集成**：洞察页新增「行为洞察」面板（专注度卡 + 死循环告警）；日报「今日建议」加入专注度与死循环提示；`/api/insights` 返回 `behavior`
- **Vibe 编程人格分析**（趣味 · 离线）：基于当日活动分布按加权打分挑出人格脸谱（AI 驱动工程师 / 深度专注者 / 多线程快切王 / 节点循环受害者 / 夜行动物 / 终身学习者 / 社交达人 / 游戏玩家 / 全能六边形选手 / 自由探索者）
- **集成**：行为洞察面板顶部的人格卡 + 日报「今日建议」的人格提示；`/api/insights` 返回 `persona`
- 阈值可配置：`insights.behavior`（`short_session_s` / `switch_gap_s` / `death_loop_*` / `focus_*` 等）+ `insights.persona`（`enabled` / `min_total_min` / `night_start_hour` / `coding_categories`）
- **Git 代码变更分析**（Phase 2 · 只读本地提交）：配置 `insights.git.projects` 后，用 `git log --numstat` 统计指定日期的提交 / 新增 / 删除行 / 改动文件；以「修改率」（删除行 / 变更行）作返工近似指标
- **集成**：洞察页新增「代码产出（Git）」面板 + 日报「今日建议」的产出提示；`/api/insights` 返回 `git`；`python git_insights.py --day` CLI
- 只读、带超时、无 git / 未配置 / 非仓库路径均优雅降级；阈值可配 `insights.git`（`enabled` / `projects` / `timeout_s` / `top_files`）

### 测试

- 新增 `test_insights_behavior`：专注日高评分 / 高频往返命中死循环 / 空数据与关闭安全
- 新增 `test_insights_persona`：AI 编程日 / 死循环日 / 夜间学习日 / 空数据与开关安全
- 新增 `test_git_insights`：临时 git 仓库两次提交 / 汇总 found / 未配置与关闭 / 非仓库跳过

## [2.3.0] - 2026-08-18

### 新增（ROADMAP Phase 1 · AI 编程深度追踪 v1.5）

- **对话轮次追踪**（`ai_sessions.rounds`）：本地会话文件内按 user→assistant 配对计 Q/A 轮次；并通过 `browser_history` 访问明细深度解析 Web AI 会话（ChatGPT/Claude/Gemini 等聊天页面的会话分组，同一会话页的返回/刷新次数 ≈ 轮次，尽力而为）
- **Token 用量估算**（`ai_sessions.token_estimation`，默认开）：CJK 按 1 Token/字、其余按 4 字符/Token 折算输入/输出 Token，逐工具/逐会话统计
- **按模型拆分**（`by_model`）：从消息 `model` 字段或内容中的模型名（Claude/GPT/DeepSeek/Qwen 等）提取，聚合到工具/合计/会话详情
- **按项目拆分**（`by_project`）：从 cwd/project/repo 等字段提取，按「会话级」归口，避免工具目录名污染，聚合到工具/合计/会话详情
- **AI 会话深度默认开启**：`ai_sessions.enabled` 默认置 `true`（不再需要单独开启；可在配置显式关闭）
- **仪表盘「AI 会话详情」面板**：固定于**概览**页底部（新增 `/api/ai-sessions` 接口，始终展示），汇总卡（消息/轮次/Token 进/出）+ 模型/项目分布 + 本地会话详情表 + Web AI 会话表
- **前端结构调整**：移除会话深度的单独面板/单独页；「AI 洞察」独立为自身功能，未开启（`insights.ai.enabled=false`）时侧边栏**不显示**「AI 洞察」项，规则洞察保留在该页内
- **日报「AI 会话深度」章节**：汇总 + 模型/项目分布 + 本地/Web 会话详表（默认开启，有数据时即出现）
- 新增 `ai_sessions --web` CLI：附带解析浏览器侧 Web AI 会话
- 配置：`ai_sessions.enabled` 默认 `true`；新增 `ai_sessions.token_estimation`（默认 `true`）、`ai_sessions.web_ai.enabled`（默认 `true`）

### 新增（ROADMAP Phase 3 · 成本与 ROI）

- **按模型费用估算**：内置主流模型定价表（USD/百万 Token）已更新到最新一代（GPT-5.x/4.1/o3/o4-mini、Claude Fable 5/Opus 5/Sonnet 5/Haiku 4.5、DeepSeek V4、Gemini 3.x/2.5、Qwen3/GLM-5/Kimi/Doubao/Grok-4 等）；按「模型 × Token」折算输入/输出费用
- **按项目成本分摊**：成本随 `by_project` 会话级归口到项目，查看每个项目花了多少钱
- **成本数据贯通**：`tools` / `total` / `by_model` / `by_project` / 会话详情均带 `cost_in` / `cost_out` / `cost_total`
- **仪表盘概览面板**：新增「成本估算」卡，模型/项目分布与会话详情表加成本列
- **日报「AI 会话深度」章节**：新增成本汇总与按模型/项目成本表现
- **CLI 展示费用**：`ai_sessions --json` 及文本输出含费用
- 配置新增：`ai_sessions.costs.enabled`（默认 `true`）、`ai_sessions.costs.model_pricing`（默认空）
- **自定义单价两途径**：① config `ai_sessions.costs.model_pricing`（`{"gpt-5": [1.25, 10]}` 或 `{"...": {"input":..,"output":..}}`）；② 数据目录下放 `ai_pricing.json`（同格式，优先级最高，便于不改 config 维护）——定价随厂商波动，建议用户自维护

### 测试

- 新增 `test_ai_sessions_costs`：按模型计价 / 按项目分摊 / 自定义单价 / `costs.enabled=false` 关闭路径

### 测试

- `test_ai_sessions` 扩展：轮次 / Token / by_model / by_project 断言
- 新增 `test_ai_sessions_phase1`：多轮会话、模型·项目拆分、会话详情、Web AI 会话（含开关关闭路径）
## [2.2.0] - 2026-08-17

### 新增
- **UWP/商店应用识别**：通过进程路径识别 WindowsApps 包并映射显示名（`config.uwp_app_names`，支持计算器/Store/照片/终端等）
- **管理员权限模式**：`python monitor.py --admin` 非管理员时自动请求 UAC 提权重启
- **Firefox 停留时长估算**：按相邻访问时间差估测停留时长（`config.firefox_dwell_max_s`，默认 600 秒）
- **更新供应链安全**：更新资产下载地址加入白名单校验（GitHub 官方域名 / `update.api_base` 域名），拒绝任意第三方地址
- **更多应用适配**：
  - 常用软件显示名/分类补充（Obsidian/Notion/Slack/Teams/企业微信/飞书/WhatsApp/LINE/Skype/Steam/Epic/Spotify/VLC/PowerToys/uTools 等）
  - 社交软件识别补充（企业微信/飞书/Slack/Teams/WhatsApp/LINE/Skype）
  - 浏览器适配补充（Vivaldi/Yandex/Chromium/Opera GX/Arc/Cent/2345/搜狗/傲游/Slimjet）
  - AI 工具识别补充（Codex/Goose/Amazon Q/DSH/pi/Claude Code/Gemini CLI/Continue/Bamboo/Augment/Warp）
  - 终端 TUI 工具补充（tmux/screen/btop/k9s/lazydocker/kubectl/ssh/curl/fzf/rg/ncdu/tig 等）

## [2.1.1] - 2026-08-17

### 新增
- **SQLite 一致性校验**：`sqlite_store.py --verify` 对比 JSONL 与 usage.db 记录数，发现差异可 `--rebuild` 修复
- **周报 SQLite 快速路径**：`report.aggregate_days()` 支持多日范围一次查询，周报/仪表盘周视图不再逐日扫 JSONL
- **更新模块测试**：新增 updater 版本比较、检测、下载校验、脚本生成、信号文件测试
- **仪表盘更新 API 测试**：覆盖 `/api/update/status|check|download|apply` 错误态
- **Release 资产补充**：CI 构建后生成并上传 `UsageMonitor.exe.sha256`
- **覆盖率范围扩展**：CI 覆盖率纳入 `insights/updater/sqlite_store/ai_sessions`

### 修复
- 修复若干测试断言对 JSON 空白格式的依赖

## [2.1.0] - 2026-08-17

### 新增
- **AI 会话深度统计支持更多工具**：
  - 新增 Cursor / Windsurf / Trae / DeepSeek / Pi Agent（π）/ DSH 的默认本地会话目录探测
  - 解析器增强：支持嵌套 `conversations` / `sessions` / `threads` / `entries` 等常见格式，兼容性更好
  - DSH 等路径仍可通过 `ai_sessions.paths` 自定义；未配置时自动探测常见目录

## [2.0.0] - 2026-08-17

### 新增
- **AI 会话深度统计**（§6.4.3，默认关闭）：
  - 新增 `ai_sessions.py`：读取 opencode / ChatGPT / Claude 等本地会话文件（JSON / JSONL），
    统计某天 AI 交互轮数、用户/助手消息数、生成行数/字符数
  - 仪表盘「洞察」视图新增「AI 会话深度」面板；`python ai_sessions.py --day ...` 或
    `python insights.py --ai-sessions` 可 CLI 查看
  - `config.default.json` 新增 `ai_sessions` 段（`enabled` 默认 false，`paths` 可自定义，
    缺省自动探测常见目录）
- **SQLite 后端 usage.db**（§6.5，可选高效查询）：
  - 新增 `sqlite_store.py`：在 data_root 下维护 `usage.db`，作为 JSONL 原始日志之外的额外镜像/索引
  - monitor 写入 JSONL 后 best-effort 同步写 SQLite；
    `python sqlite_store.py --backfill / --rebuild / --query / --status` 可回填与查询
  - `config.default.json` 新增 `sqlite.enabled`（默认 true，失败静默降级，不影响 JSONL）
- **GitHub Pages 文档站**（P2 #7）：
  - 新增 `docs/index.md` 与 `.github/workflows/pages.yml`，推送 master 自动发布文档站
- **Review 修正**：
  - 移除 `updater.py` 未使用的 `datetime` 导入（ruff 0 违规）
  - 修正 README 中 Firefox 支持说明（自 v1.1.0 起已支持 Firefox places.sqlite）

### 变更
- `UsageMonitor.spec` hiddenimports 增加 `sqlite_store`、`ai_sessions`
- 版本号升至 2.0.0

## [1.6.0] - 2026-08-17

### 新增
- **新版本检测**：
  - 启动后自动检查 GitHub Releases 最新版本，有新版本时托盘气泡提示（可配置
    `update.check_on_startup` 关闭；`update.api_base` 可覆盖检测源，测试/镜像用）
  - 托盘菜单新增「检查更新」，直接打开仪表盘设置页并自动检查
  - 仪表盘「设置 → 软件更新」可手动检查，展示最新版本、发布时间、更新说明与体积
- **应用内更新**：
  - 一键下载最新版 exe（后台线程 + 进度条；校验 Content-Length 大小与 GitHub 提供的
    SHA256 digest，校验失败自动中止）
  - 应用更新：写更新信号让守护进程优雅退出 → PowerShell 脚本等待全部进程退出
    （60 秒超时强杀兜底）→ 替换 exe → 自动重启 → 自清理
  - 开发模式（源码运行）仅支持检测，应用内安装会明确提示不可用
  - 新增 `/api/update/check`、`/api/update/status`、`/api/update/download`、
    `/api/update/apply`（apply 支持 `dryrun` 预览，测试用）

## [1.5.0] - 2026-08-17

### 新增
- **AI 洞察内容大幅扩充**（发送给 AI 的聚合数据新增多个维度，均只含聚合数字、不含隐私）：
  - 星期/周末、首次与末次活跃时间、平均会话时长、上午/下午/晚上/深夜时段分布
  - 工作/学习占比（AI 编程 + 开发工具 + 办公学习 + 设计创作）
  - 子分类 Top 5、终端工具 Top 3、近 7 天日均活跃与会话数对比
- **AI 洞察客制化模块**（与应用分组同模式，持久化于数据目录 `ai_custom.json`）：
  - 自定义 Provider 预设：任意新增/删除 OpenAI 兼容端点，显示在「设置 → Provider 预设」下拉中并优先于内置预设
  - 提示词定制：逐段勾选发送给 AI 的数据内容、调整洞察数量范围（1-10 条）、填写自定义指令（最多 500 字，附加到提示词末尾）
  - 新增 `GET/POST /api/ai/module`、`GET /api/ai/module/export`、`POST /api/ai/module/import`，洞察页可直接导出/导入整份模块配置（迁移/备份）

## [1.4.0] - 2026-08-16

### 新增
- **图形安装向导**（`installer.ps1`，零依赖）：类似成熟软件的安装体验——选择安装目录、
  注册登录自启与每日日报计划任务、创建开始菜单/桌面快捷方式、登记到「添加或删除程序」；
  支持 `-Silent` 静默安装（自动化/CI 可用）。
- **图形卸载器**（`uninstaller.ps1`）：从「添加或删除程序」或命令行触发，停止运行中的
  实例并清理计划任务/快捷方式/注册表条目/程序文件，可选择是否连记录数据一起删除。
- AI 洞察支持 **Ollama 本地模型**：
  - 新增「Ollama 本地」provider 预设（默认 `http://127.0.0.1:11434/v1`，API Key 可留空）
  - 设置页选中 Ollama 后自动填入端点/模型，并可一键「刷新 Ollama 模型列表」
    （读取本地已安装模型，输入框可下拉选择；未安装/未启动 Ollama 时给出明确提示）
  - 新增 `GET /api/insights/ollama/models`（经仪表盘代理本地 Ollama `/api/tags`，免跨域问题）

## [1.3.1] - 2026-08-16

### 修复
- 修复仪表盘「分组」页导入配置按钮：点击「导入配置」现在会弹出文件选择框，原生文件选择框不再裸露在页面上；
  导入中显示进度提示，导入失败后自动清空选择，可再次选择同一文件重试。
- 「设置 → 数据恢复」的原生文件选择框同样改为隐藏，新增「选择备份文件」按钮并显示已选文件名。

## [1.3.0] - 2026-08-16

### 新增
- 应用分组更细粒度客制化：
  - `app_groups.json` 新增 `app_names`（每个 exe 的自定义显示名）与 `group_meta`（分组元数据）
  - 仪表盘「分组」视图新增「显示名」编辑列，改名后新会话/仪表盘即时生效
  - 新增 `/api/groups/rename`、`/api/groups/export`、`/api/groups/import`
  - 分组视图新增「导出配置 / 导入配置」按钮，可整份备份/迁移分组配置
- `classifier.resolve_app_name()` 支持用户自定义显示名优先于 `config.json` 的 `apps` 映射。

## [1.2.1] - 2026-08-16

### 修复
- 修复打包版 exe 点击托盘「打开仪表盘」仍回退浏览器的问题：`_find_electron_shell()`
  改用 `paths.script_dir()` 并探测父目录（exe 在 `dist/` 时项目根在父目录），
  同时移除会令 Electron 以 Node 模式运行的 `ELECTRON_RUN_AS_NODE` 环境变量。

## [1.2.0] - 2026-08-16

智能洞察大版本：离线规则建议 + 可选 AI 洞察（内置 Provider 预设 / 自定义端点 / 设置页开关）。

### 新增
- 智能洞察模块（v1.2.0 候选）：新增 `insights.py`（纯标准库），离线规则引擎基于
  `report.aggregate()` 生成学习 / 游戏 / 健康 / 效率 / 平衡 / 趋势六类结构化建议；
  可选 AI 建议（OpenAI 兼容 `chat/completions`，`urllib` 零依赖，默认关闭、聚合统计
  隐私过滤、成功写缓存 `<data_root>/YYYY-MM-DD/insights.json` + 线程单飞锁）。
- 仪表盘「洞察」视图（侧边栏新入口）：`GET /api/insights`（规则即时 + AI 读缓存）与
  `GET /api/insights/ai?date=…&refresh=1`（强制重生成）；规则卡片 severity 配色、
  AI 面板状态/错误态。
- 仪表盘「设置」页新增 AI 可选功能面板：**启用/关闭开关**、内置 Provider 预设
  （OpenCode Go / OpenAI / DeepSeek / Moonshot / OpenRouter / 智谱 GLM / 通义千问 / 自定义）、
  Base URL / API Key / Model / 超时 / 原始标题样本开关，保存后写入 `config.json`；
  新增 `GET /api/insights/settings` 与 `POST /api/insights/settings`（API Key 不回显、留空保留）。
- 日报 `report.md` 追加「📌 今日建议」段（仅离线规则洞察，`insights.enabled &&
  insights.in_report` 时启用，绝不发起网络请求）。
- `config.default.json` 新增 `insights` 配置段（规则阈值 + AI 端点 + 内置 provider 预设）；
  本地与仓库默认 `ai.enabled=false`（可选功能默认关闭，用户可在设置页一键开启）。
- CLI：`python insights.py --day YYYY-MM-DD [--ai] [--json] [--data-root …]`。
- 测试新增 8 组智能洞察测试（规则 / AI 提示词隐私 / AI 调用 / provider 预设 / 缓存 /
  仪表盘 API / AI 设置 API / 日报段落）。

### 变更
- `UsageMonitor.spec` `hiddenimports` 增加 `insights`。
- 新增 `ruff.toml` 锁定基础 lint 规则集（E4/E7/E9/F），并清理存量 E/F 违规。
- 文档同步：README（中英）新增「智能洞察」章节与隐私声明；TODO 记录执行状态。

## [1.1.0] - 2026-08-15

功能增强大版本：应用分组自定义（P0）+ 九项功能增强（P1）+ 工程/质量项（P2）。

### 新增
- 应用分组自定义（P0）：`classifier` 支持 `load/save_app_groups`（TTL 5s 缓存 + 原子写）、
  `all_categories`、`classify_category` 用户覆盖优先；仪表盘新增 GET `/api/groups` 与
  POST `/api/groups/set|add|delete`（含 Origin 校验）及「分组」视图（侧边栏第 6 项）。
- 功能增强 P1（九大项）：周报 / 月报视图（`/api/week`、`/api/month`）、数据导出
  （`/api/export`，CSV/JSON 防注入）、备份下载与恢复上传（`/api/backup[/restore]`，
  白名单 + 路径穿越防护）、浅色主题切换、可选访问口令（`dashboard_token`，hmac
  常量时间比较，默认关闭）、`classifier` 配置热重载（mtime + TTL 3s + 浅拷贝防污染）、
  `monitor` 循环内每轮重读配置（`data_root` 保持启动值）、托盘气泡通知（`show_balloon`，
  NIF_INFO + 点击事件打开日报视图）、Firefox 历史支持（自动发现 profile、PRTime 换算、
  统一输出结构）。
- Electron 桌面壳（独立应用窗口替代默认浏览器）：electron-app/（Electron 33 壳，约
  1280x820 窗口），自动探测 / 启动 Python 仪表盘服务，窗口关闭清理自启服务、托盘常驻
  服务则复用；`--smoke` 冒烟模式（启动 → 截图 → 退出，CI 自检用）；`monitor.open_dashboard`
  优先 Electron 壳（打包 exe > dev 模式），找不到回退默认浏览器，`USAGEMON_USE_BROWSER=1`
  强制回退；paths 环境变量 `USAGEMON_PROJECT_DIR`/`DATA_ROOT`/`PORT`/`PYTHON`。
- 英文版 README（`README.en.md`）及双语互链。
- 工程/质量（P2）：CHANGELOG.md（keep-a-changelog 格式）、CONTRIBUTING.md 贡献指南、
  Issue/PR 模板（bug_report / feature_request / pull request）、README CI/Release 徽章、
  杀软误报处理指引；CI 新增 version↔git tag 同步校验与 coverage 覆盖率报告（上传 artifact）。

### 修复
- 修复 `do_POST` 未知路径 405 回归；`/api/groups` 分类使用服务 `data_root`。
- 修复 report.py 缺失 `import re` 的历史遗留 bug（verify 路径 NameError）。

### 变更
- gitignore 覆盖沙箱测试 / 运行临时目录（`.tmp_*/`）。
- 新增交接文档（应用分组功能现场 + 完整功能 / 工程待办清单）。
- 测试：全量 152 项通过（新增托盘调度 9 项、`test_app_groups` 14 项断言）。

## [1.0.0] - 2026-08-13

首个正式版本：Windows 本地使用情况监控工具（Phase 1-3 + 监控维度细化）。
纯标准库零依赖，静态 CPU <0.1%，内存 <25MB。

### 新增
- 监控核心（win32core.py，5s 轮询、状态变化才写盘、跨天隔离、空闲截断、零写入静态）。
- 前台窗口会话计时；软件清单扫描（注册表 / 开始菜单 / 进程）+ 自动分类 + 每日自动刷新；
  社交联系人识别（微信 / QQ / 钉钉）+ 别名表；浏览器站点分类 + URL 级历史解析
  （锁安全、停留时长、跨天分摊）；vibe coding 监控（opencode / pi agent(π) / ChatGPT 等，
  进程树 + 标题双重识别）；终端 TUI 工具识别 / 二级子分类 / 窗口状态 / 会话 URL 关联。
- 每日汇总 MD 日报（总览 + 小时分布 + 分类 + 联系人 + AI + 浏览器明细 + 清单概要）；
  周报 / 月报 / JSON 导出 / 重分类 / 本地网页仪表盘 / 托盘 / 开机自启。
- 仪表盘前端重构：左侧固定侧边栏（概览 / 趋势 / 日报 / 会话 / 日志 5 视图）、暖灰暗色
  设计系统（#101318 + 琥珀单强调色 #e0a53c）、克制圆角 / 细边框 / 等宽数字、无 AI
  生成味（去紫色渐变 / 玻璃拟态 / emoji）、动画（视图切换 / 数字滚动 / 柱状 / 热力图入场 /
  悬停反馈 / 骨架屏 / prefers-reduced-motion）、趋势页热力图（24 小时 × 天数）、日报页
  Markdown 平滑进度条渲染、会话页筛选 / 搜索、紧凑时长格式、统一标签样式。
- 统一日志系统：applog.py 滚动日志（1MB × 5），monitor / report / dashboard 均接入；
  `/api/log` 端点 + 日志视图（运行日志 + 错误日志，15s 自动刷新）。
- 图标资产（assets/icon.png / icon.ico / tray.ico）+ 项目截图 + README 品牌化 + 自定义托盘
  图标（tray.py 优先加载，回退系统图标）；make_demo_data.py 虚构演示数据生成器。
- 配置文件单一事实源：config.default.json（DEFAULT_CONFIG 改从文件加载）、
  classifier.py `--sync-config` 校验差异、补全 `editor_exes`。
- 可移植性：新增 paths.py（frozen 感知），消除全部 13 处硬编码 `D:` 路径；
  UsageMonitor.spec（exe 使用 icon.ico + 内置图标资源）。
- CI 自动构建：.github/workflows/build.yml（Windows 构建 exe + 打 tag 自动发布 Release，
  Release 生成权限 / 幂等 allowUpdates / action-gh-release v2 参数修复）。
- 统一版本号：version.py = 1.0.0，monitor / report / dashboard 均支持 `--version`。

### 修复
- 安全：dashboard `/api/*` 校验 Origin / Referer 必须指向 `127.0.0.1:<port>`，恶意站点 403；
  页面加 `X-Frame-Options: DENY` + CSP。
- 可靠性：usage.jsonl 写入 flush + fsync；report.py `--verify`/`--repair`
  （剔除坏行自动备份 + 重建缺失日报）。
- 可移植性：修复 exe 打包后数据写进 `_MEIPASS` 临时目录的隐蔽 bug。
- 前端：修复 DATA_ROOT 双替换 / JSON.parse 预解码 / 双引号嵌套三个模板注入 bug；
  HTML 响应加 `Cache-Control: no-store`；修复热力图 opacity 过渡在虚拟时间下不可见。
- 测试：test_all 新增 11 项 dashboard API 测试（端点 / 403 / 安全头 / 错误码 / 路径穿越），
  构建后 `UsageMonitor.exe --version` 冒烟，全量 125 项门禁通过。

[2.2.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v2.2.0
[2.1.1]: https://github.com/Niangaol/UsageMonitor/releases/tag/v2.1.1
[2.1.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v2.1.0
[2.0.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v2.0.0
[1.6.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.6.0
[1.5.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.5.0
[1.4.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.4.0
[1.3.1]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.3.1
[1.3.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.3.0
[1.2.1]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.2.1
[1.2.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.2.0
[1.1.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.1.0
[1.0.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.0.0
