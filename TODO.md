# 交接文档 / 待办清单

> 交接时间：2026-08-14 · 项目：电脑使用情况监控（UsageMonitor）
> 远程仓库：https://github.com/Niangaol/UsageMonitor（master 分支）
> 最后提交：P0 应用分组功能完成并推送（见下文执行记录）；此前提交 `06f1aa7`（Electron 桌面壳）

---

## P0 ✅ 已完成：应用分组自定义（2026-08-15 完成并提交）

### 完成内容

| 文件 | 内容 | 验证状态 |
|---|---|---|
| `classifier.py` | `load_app_groups()/save_app_groups()`（TTL 5s 缓存+原子写）、`all_categories()`、`classify_category()` 加「用户覆盖(exe 精确) > 配置关键词 > 其他」 | ✅ 143 项测试全过 |
| `dashboard.py` | `GET /api/groups`（含 `_collect_known_apps`）、`POST /api/groups/set\|add\|delete`（原子写 `app_groups.json` + Origin 校验）；`PAGE_TEMPLATE` 加「分组」视图（sidebar 第 6 项） | ✅ 143 项测试全过；修复 do_POST 未知路径 405 回归；`/api/groups` 分类使用服务 data_root |
| `test_all.py` | `test_app_groups`（覆盖层+API 增删改移出+POST 恶意 Origin 403） | ✅ 全部通过（143 项） |
| `README.md` | 功能特性、仪表盘视图列表、分组数据文件与 API 文档、app_groups.json 配置说明 | ✅ |
| `docs/screenshots/view_groups.png` | 分组视图截图（demo 数据 + Edge headless + `?view=groups`） | ✅ DOM 验证通过 |

### 执行记录

1. `python test_all.py` 全量回归 → 发现 `test_dashboard_api`「POST 405」回归（do_POST 从 405 改为 404），修复
2. `test_app_groups` 14 项断言全部通过
3. 截图验证：demo 数据 + Edge headless + `?view=groups`（`docs/screenshots/view_groups.png`）
4. README 补充分组功能说明（数据文件/API/用法）
5. 重建 `dist\UsageMonitor.exe`（PyInstaller）+ 重启计划任务 `UsageMonitor`（日志确认新实例启动）
6. `git commit` + `git push`（master）

---

## P1 ✅ 已完成：功能增强（2026-08-15 完成并提交，9/11 项）

| # | 事项 | 状态 |
|---|---|---|
| 1 | **仪表盘周报/月报视图** | ✅ `/api/week`、`/api/month`（复用 report 聚合）+ sidebar「周报/月报」视图 |
| 2 | **仪表盘数据导出** | ✅ `/api/export?type=csv\|json&scope=day\|week\|month`，日报/周报/月报视图导出按钮（CSV 防注入清洗） |
| 3 | **AI 会话深度统计**（需用户确认） | ⏸️ 未做（等待用户确认） |
| 4 | **数据备份/恢复** | ✅ `/api/backup`（zip 下载）+ `/api/backup/restore`（白名单+路径穿越防护，合并覆盖） |
| 5 | **配置热重载** | ✅ classifier.load_config 缓存（mtime+TTL 3s+浅拷贝）；monitor 循环内每轮重读（data_root 保持启动值） |
| 6 | **托盘通知** | ✅ 日报生成后气泡提示（19:30 后首次发现弹一次，防重启误弹，点击打开日报视图） |
| 7 | **仪表盘主题切换** | ✅ 自动/浅色/深色（CSS 变量 + localStorage + 跟随系统 + canvas 重绘） |
| 8 | **仪表盘访问口令** | ✅ `dashboard_token`（默认关闭；开启后所有 /api 需 X-Dashboard-Token，hmac 常量时间比较） |
| 9 | **Firefox 历史支持** | ✅ places.sqlite（moz_places/moz_historyvisits，PRTime 换算），最近修改 profile 自动发现 |
| 10 | **SQLite 后端 usage.db** | ⏸️ 未做（大工程长期项） |
| 11 | **多语言 README** | ✅ README.en.md（482 行完整英文版 + 双语互链） |

执行记录：5 个并行 subagent 按文件隔离分工（dashboard.py / tray.py+monitor.py / browser_history.py / classifier.py / README），
主代理集成：修复 monitor 热重载与测试 config 隔离、token 读取 data_root 语义、无效去重逻辑清理；
全量测试 **152 项通过**（新增托盘调度 9 项）；API 冒烟（周/月/导出/备份/恢复/口令 401 校验）全部通过；
修复 HEAD 遗留 bug：report.py 缺失 `import re`（verify 路径 NameError）。

## P1 剩余 ⏸️

| # | 事项 | 说明 |
|---|---|---|
| 3 | **AI 会话深度统计**（doc §6.4.3） | **优先级已放低**（用户 2026-08-15 指示）：读 opencode/ChatGPT 本地会话文件，统计轮数/生成行数；涉及第三方数据格式，默认不做 |
| 10 | **SQLite 后端 usage.db**（评审建议 3 长期项） | **待定**（大工程，需专项设计）：行级主键+按日索引，多年数据聚合不再全量扫 JSONL；JSONL 仍为原始日志 |

## P2 ✅ 已完成：工程 / 质量（2026-08-15 完成并提交，6/7 项）

| # | 事项 | 状态 |
|---|---|---|
| 1 | **CHANGELOG.md** | ✅ keep-a-changelog 格式，[未发布]（P0+P1+Electron）+ [1.0.0]（2026-08-13） |
| 2 | **CONTRIBUTING.md + Issue/PR 模板** | ✅ 贡献指南（环境/风格/测试/提交/PR/发布/隐私）+ bug_report/feature_request/PR 模板 |
| 3 | **README CI/Release 徽章** | ✅ workflow badge + release badge（中英文 README） |
| 4 | **version.py ↔ git tag 同步校验** | ✅ build.yml 新增 Assert step（tag 触发与 tag 比对；dispatch 与 version.py 比对） |
| 5 | **测试覆盖率** | ✅ coverage 接入 test job（--source 全模块，report + xml 上传 artifact，暂不强制阈值） |
| 6 | **exe 代码签名** | ✅ 无证书；README 中英文补「杀软误报处理」指引（白名单/源码运行/VirusTotal/开源审计） |
| 7 | **GitHub Pages 文档站**（可选） | ⏸️ 未做（可选低优先） |

执行记录：4 个并行 subagent（CHANGELOG / CONTRIBUTING+模板 / README 徽章+签名 / CI workflow）；
主代理集成：CHANGELOG 仓库 URL 修正、README 补「贡献指南」链接与测试数修正（152）、中英文同步。

## 智能洞察 ✅ 已完成（2026-08-15，v1.2.0 候选，不发布 tag）

| 文件 | 内容 | 验证状态 |
|---|---|---|
| `insights.py` | 规则引擎（study/game/health/efficiency/balance/trend）+ AI 客户端（urllib/InsightsError）+ 缓存/单飞锁 + CLI + 内置 provider 预设/自定义 provider | ✅ 新增 8 组测试全过 |
| `config.default.json` | 新增 `insights` 段（enabled/in_report/rules/ai），深合并自动生效 | ✅ |
| `config.json` | 本地与仓库默认 `ai.enabled=false`（可选功能默认关闭，用户可在设置页开启） | ✅ |
| `report.py` | 日报末尾追加「📌 今日建议」段（仅离线规则洞察） | ✅ |
| `dashboard.py` | `/api/insights`、`/api/insights/ai`、`/api/insights/settings` + 「洞察」视图 + 设置页 AI 开关/预设/自定义保存 | ✅ |
| `UsageMonitor.spec` | `hiddenimports` 增加 `'insights'` | ✅ |
| `test_all.py` | 新增 8 组（规则、AI 提示词隐私、AI 调用、provider 预设、缓存、仪表盘 API、AI 设置 API、日报段落），全量 **223 项通过** | ✅ |
| `ruff.toml` | 锁定基础规则集（E4/E7/E9/F），并清理存量 E/F 违规；`ruff check .` 零违规 | ✅ |
| `README.md` / `README.en.md` / `CHANGELOG.md` | 功能特性、智能洞察章节、Provider 预设/设置开关说明、配置说明、隐私声明同步 | ✅ |

执行记录：
1. `python test_all.py` 全量回归 → **223 项通过**（原 152 + 新增 71 项断言）
2. `python insights.py --day 2026-08-10 --data-root demo_data` 命中学习/游戏/趋势 3 条规则
3. `python insights.py --day 2026-08-10 --json` 真实数据命中 5 条规则
4. AI 真实调用受外部网络限制返回 HTTP 403，错误态正常；`_chat_completion` 已用 monkeypatch 测试覆盖 URL/请求体/HTTP 错误/超时
5. `python -m ruff check .` → All checks passed
6. 设置页 AI 开关/provider 预设/自定义保存 API 已通过测试；API Key 不回显、留空保留
7. exe 重建随下次发布由 CI 完成（UsageMonitor.spec 已加入 hiddenimports）

## P3 ⚪ 已知限制（记录，不修）

- 管理员权限窗口标题读不到（需管理员模式运行监控，未实现该选项）
- UWP/商店应用标题可能为空（按 exe 记录）
- 后台标签页不计时（前台注意力口径，文档已说明）
- pi agent 进程名未确认（现有 `π` 标题关键词兜底覆盖）
- 浏览器"停留时长"含挂机（标签页前台口径，与 monitor 活跃口径并列展示）

---

## 交接备忘（环境/命令）

- **代理**：`127.0.0.1:7897`。git 已配本地代理；gh 已登录（Niangaol）；npm 用 `& "C:\Program Files\nodejs\npm.cmd"`；gh 需 `$env:Path` 加 `C:\Program Files\GitHub CLI`
- **Python**：默认 `python`=3.11（有 PyInstaller）；`py -3`=3.14（无 PyInstaller）
- **构建**：`python -m PyInstaller UsageMonitor.spec --noconfirm`（先停守护任务，exe 会被占用）
- **发布**：`git tag vX.Y.Z && git push origin vX.Y.Z` → CI 自动测试→构建→冒烟→Release
- **测试**：`python test_all.py`（223 项全过）；`python -m ruff check .`（零违规）
- **视觉验收**：Edge headless 截图 + qwen3.7-plus（key 自动读 `~/.config/opencode/opencode.json`）
- **守护**：计划任务 `UsageMonitor`（exe）/`UsageMonitorReport`（每日 19:30 日报）
- **测试管线文件**（temp 目录，未入库）：`shot_views.py`（5 视图截图）、`qwen_ui_qa.py`（视觉验收）
