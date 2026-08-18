# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).
Release flow: `git tag vX.Y.Z` → CI builds and publishes the Release automatically.

> 简体中文版: [CHANGELOG.md](CHANGELOG.md)

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

### Added (ROADMAP Phase 1 · AI coding deep tracking v1.5)

- **Turn tracking** (`ai_sessions.rounds`): counts Q/A pairs (user→assistant) inside local session files; also deep-parses browser visits into Web AI conversations (ChatGPT/Claude/Gemini chat pages grouped by conversation; returns/refreshes ≈ turns, best-effort)
- **Token estimation** (`ai_sessions.token_estimation`, default on): CJK ≈ 1 token/char, other ≈ 1 token/4 chars, split into input/output tokens per tool and per conversation
- **Breakdown by model** (`by_model`): extracts model names from message `model` fields or content patterns (Claude/GPT/DeepSeek/Qwen, etc.), aggregated to tool/total/conversation detail
- **Breakdown by project** (`by_project`): extracts from cwd/project/repo fields, attributed conversation-level to avoid tool-dir noise, aggregated to tool/total/conversation detail
- **AI session depth on by default**: `ai_sessions.enabled` now defaults to `true` (no opt-in needed; can be disabled in config)
- **Dashboard "AI session details" panel**: fixed at the bottom of **Overview** (new `/api/ai-sessions` endpoint, always shown): summary cards (messages/turns/tokens in·out) + model/project distribution + local conversation table + Web AI sessions table
- **Frontend restructure**: removed the separate session-depth panel/page; "AI Insights" is now its own feature and its **sidebar item is hidden when `insights.ai.enabled=false`** (rules stay in that page)
- **Daily report "AI session depth" section**: summary + model/project distribution + local/Web conversation tables (on by default; shown whenever data exists)
- **New `ai_sessions --web` CLI** to include browser-side Web AI conversations
- **Config**: `ai_sessions.enabled` defaults to `true`; new `ai_sessions.token_estimation` (default `true`) and `ai_sessions.web_ai.enabled` (default `true`)

### Added (ROADMAP Phase 3 · Cost & ROI)

- **Per-model cost estimation**: built-in pricing table updated to the latest generations (GPT-5.x/4.1/o3/o4-mini, Claude Fable 5/Opus 5/Sonnet 5/Haiku 4.5, DeepSeek V4, Gemini 3.x/2.5, Qwen3/GLM-5/Kimi/Doubao/Grok-4, etc.); cost = model price × tokens (input/output split)
- **Per-project cost allocation**: cost rolls up to projects through `by_project` (conversation-level), showing how much each project spent
- **Cost data everywhere**: `tools` / `total` / `by_model` / `by_project` / conversation details all carry `cost_in` / `cost_out` / `cost_total`
- **Overview panel**: new "cost estimate" card; cost columns added to model/project distribution and conversation table
- **Daily report "AI session depth" section**: cost summary and per-model/per-project cost display
- **CLI shows cost** in `ai_sessions --json` and text output
- **New config**: `ai_sessions.costs.enabled` (default `true`), `ai_sessions.costs.model_pricing` (empty by default)
- **Two ways to override a price**: ① config `ai_sessions.costs.model_pricing` (`{"gpt-5": [1.25, 10]}` or `{"...": {"input":..,"output":..}}`); ② drop an `ai_pricing.json` in the data directory (same format, highest priority, easy to maintain without touching config). Since prices drift, users are encouraged to maintain their own.

### Testing

- New `test_ai_sessions_costs`: per-model pricing / per-project allocation / custom price override / `costs.enabled=false` off path

### Testing

- Extended `test_ai_sessions` with rounds/tokens/by_model/by_project assertions
- Added `test_ai_sessions_phase1`: multi-turn conversation, model/project breakdown, conversation details, Web AI sessions (including disabled-switch paths)
## [2.2.0] - 2026-08-17

### Added
- **UWP/Store app recognition**: detects WindowsApps packages from process path and maps display names (`config.uwp_app_names`; Calculator/Store/Photos/Terminal, etc.)
- **Admin privilege mode**: `python monitor.py --admin` requests a UAC elevation restart when not running as admin
- **Firefox dwell-time estimation**: estimates dwell time from the interval to the next visit (`config.firefox_dwell_max_s`, default 600s)
- **Update supply-chain security**: update asset download URLs are allowlist-validated (GitHub official domains / `update.api_base` domain); arbitrary third-party URLs are rejected
- **More app adaptations**:
  - Common app display names/classification (Obsidian/Notion/Slack/Teams/WeCom/Feishu/WhatsApp/LINE/Skype/Steam/Epic/Spotify/VLC/PowerToys/uTools, etc.)
  - Social app recognition additions (WeCom/Feishu/Slack/Teams/WhatsApp/LINE/Skype)
  - Browser adaptations (Vivaldi/Yandex/Chromium/Opera GX/Arc/Cent/2345/Sogou/Maxthon/Slimjet)
  - AI tool recognition additions (Codex/Goose/Amazon Q/DSH/pi/Claude Code/Gemini CLI/Continue/Bamboo/Augment/Warp)
  - Terminal TUI tool additions (tmux/screen/btop/k9s/lazydocker/kubectl/ssh/curl/fzf/rg/ncdu/tig, etc.)

## [2.1.1] - 2026-08-17

### Added
- **SQLite consistency check**: `sqlite_store.py --verify` compares JSONL and usage.db record counts; use `--rebuild` to fix differences
- **SQLite fast path for weekly aggregation**: `report.aggregate_days()` queries a date range in one pass; weekly report/dashboard week view no longer scans JSONL day by day
- **Updater tests**: added version compare, check, download verification, script generation, and signal file tests
- **Dashboard update API tests**: covers `/api/update/status|check|download|apply` error states
- **Release asset**: CI now generates and uploads `UsageMonitor.exe.sha256`
- **Coverage scope**: CI coverage now includes `insights/updater/sqlite_store/ai_sessions`

### Fixed
- Fixed several test assertions that depended on JSON whitespace formatting

## [2.1.0] - 2026-08-17

### Added
- **AI session deep stats now supports more tools**:
  - Added default local session directory detection for Cursor / Windsurf / Trae / DeepSeek / Pi Agent (π) / DSH
  - Parser enhanced to handle nested `conversations` / `sessions` / `threads` / `entries` and other common formats
  - Paths such as DSH can still be customized via `ai_sessions.paths`; common directories are auto-detected when not configured

## [2.0.0] - 2026-08-17

### Added
- **AI session deep stats** (§6.4.3, off by default):
  - New `ai_sessions.py`: reads local session files (JSON / JSONL) from opencode / ChatGPT / Claude, etc.,
    and counts AI interaction turns, user/assistant messages, generated lines/chars for a day
  - New "AI Session Depth" panel on the dashboard Insights view; CLI via `python ai_sessions.py --day ...` or
    `python insights.py --ai-sessions`
  - `config.default.json` adds the `ai_sessions` section (`enabled` defaults to false; `paths` is customizable,
    otherwise common directories are auto-detected)
- **SQLite backend usage.db** (§6.5, optional high-performance queries):
  - New `sqlite_store.py`: maintains `usage.db` under `data_root` as an extra mirror/index beside the JSONL raw logs
  - The monitor best-effort writes to SQLite after appending JSONL;
    `python sqlite_store.py --backfill / --rebuild / --query / --status` can backfill and query
  - `config.default.json` adds `sqlite.enabled` (default true; failures degrade silently and never affect JSONL)
- **GitHub Pages docs site** (P2 #7):
  - New `docs/index.md` and `.github/workflows/pages.yml`; pushes to master automatically publish the docs site
- **Review fixes**:
  - Removed the unused `datetime` import in `updater.py` (ruff: 0 violations)
  - Fixed the README Firefox support statement (Firefox places.sqlite has been supported since v1.1.0)

### Changed
- `UsageMonitor.spec` hiddenimports now include `sqlite_store` and `ai_sessions`
- Version bumped to 2.0.0

## [1.6.0] - 2026-08-17

### Added
- **New-version detection**:
  - Automatically checks the latest GitHub Release after startup and shows a tray balloon when a new version is available
    (configurable via `update.check_on_startup`; `update.api_base` can override the check source for testing/mirrors)
  - New "Check for Updates" tray menu item that opens the dashboard Settings page and checks automatically
  - The dashboard "Settings → Software Update" page supports manual checks and shows the latest version, release date, release notes, and size
- **In-app updates**:
  - One-click download of the latest EXE (background thread + progress bar; verifies Content-Length size and the SHA256 digest provided by GitHub; aborts automatically on verification failure)
  - Applying an update: writes an update signal so the daemon exits gracefully → a PowerShell script waits for all processes to exit
    (60-second timeout with a force-kill fallback) → replaces the EXE → restarts automatically → cleans up
  - Dev mode (running from source) only supports checking; in-app installation clearly reports that it is unavailable
  - New `/api/update/check`, `/api/update/status`, `/api/update/download`, and `/api/update/apply`
    (`apply` supports `dryrun` for preview/testing)

## [1.5.0] - 2026-08-17

### Added
- **Expanded AI insight content** (new aggregate dimensions sent to the AI, all numbers only — no private data):
  - Weekday/weekend, first and last active time, average session length, morning/afternoon/evening/late-night distribution
  - Work/study share (AI coding + dev tools + work & study + design/creation)
  - Top 5 subcategories, top 3 terminal tools, and a 7-day comparison of daily active time and session count
- **Customizable AI insights module** (same pattern as app groups, persisted in the data root as `ai_custom.json`):
  - Custom provider presets: add/remove any OpenAI-compatible endpoint; they appear in the Settings → Provider presets dropdown and take priority over built-in presets
  - Prompt customization: toggle which data sections are sent to the AI, adjust the insight count range (1–10), and add a custom instruction (up to 500 chars, appended to the end of the prompt)
  - New `GET/POST /api/ai/module`, `GET /api/ai/module/export`, and `POST /api/ai/module/import`; the Insights page can export/import the whole module config (migration/backup)

## [1.4.0] - 2026-08-16

### Added
- **Graphical install wizard** (`installer.ps1`, zero dependencies): a familiar installer experience — choose the install directory,
  register login auto-start and the daily report scheduled task, create Start Menu/desktop shortcuts, and register in "Add or Remove Programs";
  supports `-Silent` for automation/CI.
- **Graphical uninstaller** (`uninstaller.ps1`): launch from "Add or Remove Programs" or the command line; stops running instances and
  cleans up scheduled tasks/shortcuts/registry entries/program files, with an option to delete recorded data too.
- AI insights support **Ollama local models**:
  - New "Ollama local" provider preset (default `http://127.0.0.1:11434/v1`; API key can be left blank)
  - Selecting Ollama on the Settings page auto-fills the endpoint/model, with a one-click "Refresh Ollama model list"
    (reads locally installed models into a dropdown; shows a clear message when Ollama is not installed/started)
  - New `GET /api/insights/ollama/models` (proxied by the dashboard to local Ollama `/api/tags`, avoiding CORS issues)

## [1.3.1] - 2026-08-16

### Fixed
- Fixed the dashboard "Groups" import button: clicking "Import Config" now opens a file picker instead of leaving a native file input exposed on the page;
  shows progress during import, and clears the selection after a failed import so the same file can be selected again.
- The "Settings → Data Restore" native file input is likewise hidden; added a "Choose backup file" button that shows the selected file name.

## [1.3.0] - 2026-08-16

### Added
- More granular app-group customization:
  - `app_groups.json` adds `app_names` (custom display name per exe) and `group_meta` (group metadata)
  - The dashboard "Groups" view adds a "Display name" editable column; renames take effect immediately for new sessions and the dashboard
  - New `/api/groups/rename`, `/api/groups/export`, and `/api/groups/import`
  - The Groups view adds "Export Config / Import Config" buttons to back up/migrate the whole grouping config
- `classifier.resolve_app_name()` now lets user-defined display names take priority over the `apps` mapping in `config.json`.

## [1.2.1] - 2026-08-16

### Fixed
- Fixed the packaged EXE still falling back to the browser when clicking the tray "Open Dashboard": `_find_electron_shell()`
  now uses `paths.script_dir()` and probes the parent directory (when the EXE is in `dist/`, the project root is the parent),
  and removes the `ELECTRON_RUN_AS_NODE` environment variable that made Electron run in Node mode.

## [1.2.0] - 2026-08-16

A major smart-insights release: offline rule suggestions + optional AI insights (built-in provider presets / custom endpoints / settings-page toggle).

### Added
- Smart insights module (v1.2.0 candidate): new `insights.py` (pure standard library), an offline rule engine that generates
  structured study/game/health/efficiency/balance/trend advice from `report.aggregate()`;
  optional AI suggestions (OpenAI-compatible `chat/completions`, zero dependencies via `urllib`, off by default, privacy-filtered
  aggregate statistics, successful results cached at `<data_root>/YYYY-MM-DD/insights.json`, thread-safe single-flight lock).
- Dashboard "Insights" view (new sidebar entry): `GET /api/insights` (rules on demand + AI from cache) and
  `GET /api/insights/ai?date=…&refresh=1` (force regeneration); rule-card severity colors, AI panel status/error states.
- Dashboard "Settings" page adds an AI optional-feature panel: **enable/disable switch**, built-in provider presets
  (OpenCode Go / OpenAI / DeepSeek / Moonshot / OpenRouter / Zhipu GLM / Qwen / Custom),
  Base URL / API Key / Model / timeout / raw-title sample switch, saved to `config.json`;
  new `GET /api/insights/settings` and `POST /api/insights/settings` (API key is never echoed; leaving it blank preserves the existing one).
- Daily report `report.md` appends a "📌 Today's suggestions" section (offline rule insights only, when `insights.enabled &&
  insights.in_report` is enabled; never makes network requests).
- `config.default.json` adds the `insights` section (rule thresholds + AI endpoint + built-in provider presets);
  the local and repository default is `ai.enabled=false` (optional feature off by default; users can enable it in one click on the Settings page).
- CLI: `python insights.py --day YYYY-MM-DD [--ai] [--json] [--data-root …]`.
- Tests: 8 new smart-insight test groups (rules / AI prompt privacy / AI call / provider presets / cache /
  dashboard API / AI settings API / report section).

### Changed
- `UsageMonitor.spec` `hiddenimports` now includes `insights`.
- Added `ruff.toml` with a baseline lint rule set (E4/E7/E9/F) and cleaned up existing E/F violations.
- Documentation synced: README (Chinese/English) adds a "Smart Insights" section and privacy statement; TODO tracks execution status.

## [1.1.0] - 2026-08-15

Feature-enhancement major release: custom app grouping (P0) + nine feature enhancements (P1) + engineering/quality items (P2).

### Added
- Custom app grouping (P0): `classifier` supports `load/save_app_groups` (TTL 5s cache + atomic writes),
  `all_categories`, and `classify_category` with user overrides taking priority; the dashboard adds GET `/api/groups`,
  POST `/api/groups/set|add|delete` (with Origin validation) and the "Groups" view (6th sidebar item).
- Feature enhancements P1 (nine items): weekly/monthly report views (`/api/week`, `/api/month`), data export
  (`/api/export`, CSV/JSON with injection protection), backup download and restore upload (`/api/backup[/restore]`,
  allowlist + path-traversal protection), light theme toggle, optional access token (`dashboard_token`, hmac
  constant-time comparison, off by default), `classifier` config hot reload (mtime + TTL 3s + shallow copy to avoid pollution),
  per-loop config re-read in the `monitor` loop (`data_root` stays at the startup value), tray balloon notifications (`show_balloon`,
  NIF_INFO + click event opens the report view), and Firefox history support (auto profile discovery, PRTime conversion,
  unified output structure).
- Electron desktop shell (standalone app window instead of the default browser): electron-app/ (Electron 33 shell, ~1280x820 window),
  auto-detects/starts the Python dashboard service, cleans up a self-started service when the window closes, and reuses a tray-kept service;
  `--smoke` mode (start → screenshot → exit, for CI); `monitor.open_dashboard` prefers the Electron shell (packaged EXE > dev mode),
  falls back to the default browser, and `USAGEMON_USE_BROWSER=1` forces the browser fallback; paths environment variables
  `USAGEMON_PROJECT_DIR`/`DATA_ROOT`/`PORT`/`PYTHON`.
- English README (`README.en.md`) with bilingual cross-links.
- Engineering/quality (P2): CHANGELOG.md (keep-a-changelog format), CONTRIBUTING.md contribution guide,
  Issue/PR templates (bug_report / feature_request / pull request), README CI/Release badges,
  antivirus false-positive guidance; CI adds version↔git tag sync validation and coverage reports (uploaded as artifacts).

### Fixed
- Fixed the `do_POST` unknown-path 405 regression; `/api/groups` classification now uses the service `data_root`.
- Fixed a historical `report.py` missing `import re` bug (NameError on the verify path).

### Changed
- gitignore now covers sandbox test/runtime temp directories (`.tmp_*/`).
- Added handover documents (app-group feature live demo + full feature/engineering todo list).
- Tests: all 152 assertions pass (9 new tray-scheduler tests, 14 `test_app_groups` assertions).

## [1.0.0] - 2026-08-13

First official release: a local Windows usage monitoring tool (Phase 1-3 + refined monitoring dimensions).
Pure standard library with zero third-party dependencies; static CPU < 0.1%, memory < 25 MB.

### Added
- Monitoring core (win32core.py, 5s polling, writes only on state change, cross-day isolation, idle truncation, zero writes while static).
- Foreground-window session timing; software inventory scan (registry / Start Menu / processes) + automatic classification + daily auto-refresh;
  social contact recognition (WeChat / QQ / DingTalk) + alias table; browser site classification + URL-level history parsing
  (lock-safe, dwell time, cross-day apportionment); vibe coding monitoring (opencode / pi agent(π) / ChatGPT, etc.,
  process tree + title dual recognition); terminal TUI tool recognition / secondary subcategories / window state / session URL association.
- Daily Markdown summary (overview + hourly distribution + categories + contacts + AI + browser details + inventory summary);
  weekly/monthly reports / JSON export / reclassification / local web dashboard / tray / auto-start.
- Dashboard frontend rebuilt: fixed left sidebar (Overview / Trends / Report / Sessions / Logs 5 views), warm gray dark
  design system (#101318 + amber single accent #e0a53c), restrained rounded corners / thin borders / monospaced numbers, no AI-generated
  look (no purple gradients / glassmorphism / emoji), animations (view switching / number roll / bars / heatmap entry /
  hover feedback / skeleton screens / prefers-reduced-motion), trend heatmap (24h × days), report page Markdown smooth progress rendering,
  session page filtering/search, compact duration formatting, and unified label styles.
- Unified logging: applog.py rolling logs (1 MB × 5), integrated into monitor / report / dashboard;
  `/api/log` endpoint + log view (runtime logs + error logs, auto-refresh every 15s).
- Icon assets (assets/icon.png / icon.ico / tray.ico) + project screenshots + branded README + custom tray
  icon (tray.py loads it first, falls back to the system icon); make_demo_data.py fake demo-data generator.
- Single source of truth for config: config.default.json (DEFAULT_CONFIG loaded from file),
  classifier.py `--sync-config` to validate differences, and completes `editor_exes`.
- Portability: new paths.py (frozen-aware), removing all 13 hardcoded `D:` paths;
  UsageMonitor.spec (EXE uses icon.ico + embedded icon resources).
- CI auto-build: .github/workflows/build.yml (Windows EXE build + auto Release on tag,
  Release permission / idempotent allowUpdates / action-gh-release v2 parameter fixes).
- Unified version number: version.py = 1.0.0; monitor / report / dashboard all support `--version`.

### Fixed
- Security: dashboard `/api/*` validates Origin/Referer must point to `127.0.0.1:<port>`; malicious sites get 403;
  pages add `X-Frame-Options: DENY` + CSP.
- Reliability: usage.jsonl writes use flush + fsync; report.py `--verify`/`--repair`
  (removes broken lines with auto-backup and rebuilds missing daily reports).
- Portability: fixed the hidden bug where the packaged EXE wrote data into the `_MEIPASS` temp directory.
- Frontend: fixed DATA_ROOT double replacement / JSON.parse pre-decoding / double-quote nesting three template-injection bugs;
  HTML responses add `Cache-Control: no-store`; fixed heatmap opacity transition not visible under virtual time.
- Tests: test_all adds 11 dashboard API tests (endpoints / 403 / security headers / error codes / path traversal);
  post-build `UsageMonitor.exe --version` smoke test; all 125 assertions pass the gate.

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
