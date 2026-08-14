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

## P1 🟡 功能增强（建议顺序）

| # | 事项 | 说明 | 依赖 |
|---|---|---|---|
| 1 | **仪表盘周报/月报视图** | 后端 `--week/--month` 已支持，前端无入口。加 sidebar「周报/月报」或报表视图内切换 | 无 |
| 2 | **仪表盘数据导出** | 会话/日报一键下载 CSV/JSON（现仅 CLI `--json`/`--write`，UI 无按钮） | 无 |
| 3 | **AI 会话深度统计**（doc §6.4.3，需用户确认） | 读 opencode/ChatGPT 本地会话文件，统计轮数/生成行数；涉及第三方数据格式，默认不做 | 用户确认 |
| 4 | **数据备份/恢复** | 一键把 data_root 打包 zip / 导入恢复（隐私数据迁移场景） | 无 |
| 5 | **配置热重载** | config.json 修改后免重启生效（现需重启；可文件 mtime 检测 + 定时重载） | 无 |
| 6 | **托盘通知** | 19:30 日报生成后托盘气泡提示（现仅静默生成） | 无 |
| 7 | **仪表盘主题切换** | 深色（现）/浅色主题 + 持久化偏好 | 无 |
| 8 | **仪表盘访问口令（可选加固）** | Origin 校验已挡浏览器攻击面；同机其他程序仍可读。可加可选 token/口令（默认关闭） | 无 |
| 9 | **Firefox 历史支持** | 现仅 Chromium 系；places.sqlite 结构不同（moz_places/moz_historyvisits），需单测 | 无 |
| 10 | **SQLite 后端 usage.db**（评审建议 3 长期项） | 行级主键+按日索引，多年数据聚合不再全量扫 JSONL；JSONL 仍为原始日志 | P0 完成后 |
| 11 | **多语言 README** | English README（GitHub 国际化受众） | 文档 |

## P2 🟢 工程 / 质量

| # | 事项 | 说明 |
|---|---|---|
| 1 | **CHANGELOG.md** | 缺失。建议 keep-a-changelog 格式，从 v1.0.0 开始补 |
| 2 | **CONTRIBUTING.md + Issue/PR 模板** | `.github/ISSUE_TEMPLATE/`、`PULL_REQUEST_TEMPLATE.md`（缺失） |
| 3 | **README CI/Release 徽章** | README 无 CI 状态徽章；加 `github.com/.../actions/workflows/build.yml/badge.svg` 与 release 徽章 |
| 4 | **version.py ↔ git tag 同步校验** | CI 里断言 `UsageMonitor.exe --version` 与 tag 一致，防漏 bump |
| 5 | **测试覆盖率** | 加 coverage 报告（CI 上传） |
| 6 | **exe 代码签名** | 杀软误报对策（doc 建议项）；无证书时至少 README 加白名单指引 |
| 7 | **GitHub Pages 文档站**（可选） | README/截图 静态站 |

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
- **测试**：`python test_all.py`（129 项 + 未跑通的分组测试）
- **视觉验收**：Edge headless 截图 + qwen3.7-plus（key 自动读 `~/.config/opencode/opencode.json`）
- **守护**：计划任务 `UsageMonitor`（exe）/`UsageMonitorReport`（每日 19:30 日报）
- **测试管线文件**（temp 目录，未入库）：`shot_views.py`（5 视图截图）、`qwen_ui_qa.py`（视觉验收）
