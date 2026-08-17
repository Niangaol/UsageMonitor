# 交接文档 / 待办清单

> 交接时间：2026-08-14 · 项目：电脑使用情况监控（UsageMonitor）
> 远程仓库：https://github.com/Niangaol/UsageMonitor（master 分支）
> 当前版本：v2.2.0（version.py = 2.2.0）
> 当前提交：53b428f（2026-08-17）

---

## 版本里程碑

| 版本 | 状态 | 关键内容 |
|---|---|---|
| v1.0.0 | ✅ 已发布 | 监控核心、日报/仪表盘、CI 构建 |
| v1.1.0 | ✅ 已发布 | 应用分组（P0）+ 九项增强（P1）+ Electron 壳 |
| v1.2.0 / 1.2.1 / 1.3.0 | ✅ 已发布 | 智能洞察、分组显示名/导入导出、修复 |
| v1.3.1 / v1.4.0 | ⚠️ 无 tag/Release | 仅在 CHANGELOG 有记录，从未发布 |
| v1.5.0 | ✅ 已发布 | AI 洞察扩充 + 客制化模块 + 图形安装向导 |
| v1.6.0 | ⚠️ 无 tag/Release？ | 新版本检测与应用内更新（代码已合入 2.0 演进） |
| v2.0.0 | ✅ 已发布 | AI 会话深度统计、SQLite 后端、GitHub Pages、Review 修复 |
| v2.1.0 | ✅ 已发布 | AI 统计支持更多工具（Cursor/Windsurf/Trae/DeepSeek/Pi Agent/DSH） |
| v2.1.1 | ✅ 已发布 | SQLite 一致性校验、周聚合快速路径、updater/更新 API 测试、SHA256 资产、覆盖率扩展 |
| v2.2.0 | ✅ 已发布 | UWP 识别、管理员模式、Firefox 停留时长、更新供应链安全、更多应用适配 |

---

## 已完成（截至 v2.2.0）

### P1
- ✅ 仪表盘周报/月报视图
- ✅ 仪表盘数据导出（CSV/JSON）
- ✅ AI 会话深度统计（`ai_sessions.py`，默认关闭）
- ✅ 数据备份/恢复
- ✅ 配置热重载
- ✅ 托盘通知
- ✅ 主题切换
- ✅ 仪表盘访问口令
- ✅ Firefox 历史支持
- ✅ SQLite 后端 `usage.db`（`sqlite_store.py`，JSONL 仍为原始事实源）
- ✅ 多语言 README

### P2
- ✅ CHANGELOG.md + CHANGELOG.en.md
- ✅ CONTRIBUTING.md + Issue/PR 模板
- ✅ README CI/Release 徽章
- ✅ version.py ↔ tag 同步校验
- ✅ 测试覆盖率（含 insights/updater/sqlite_store/ai_sessions）
- ✅ GitHub Pages 文档站
- ⏸️ exe 代码签名（无证书，未做）

### 长期目标
- ✅ UWP/商店应用识别（`win32core.get_uwp_app_name` + `config.uwp_app_names`）
- ✅ 管理员权限模式（`monitor.py --admin` 自动 UAC 提权）
- ✅ Firefox 停留时长估算（`config.firefox_dwell_max_s`，默认 600s）
- ✅ 更新供应链安全（资产下载地址白名单）

### 更多应用适配
- ✅ 常用软件显示名/分类补充（Obsidian/Notion/Slack/Teams/企业微信/飞书/WhatsApp/LINE/Skype/Steam/Epic/Spotify/VLC/PowerToys/uTools 等）
- ✅ 社交软件识别补充（企业微信/飞书/Slack/Teams/WhatsApp/LINE/Skype）
- ✅ 浏览器适配补充（Vivaldi/Yandex/Chromium/Opera GX/Arc/Cent/2345/搜狗/傲游/Slimjet）
- ✅ AI 工具识别补充（Codex/Goose/Amazon Q/DSH/pi/Claude Code/Gemini CLI/Continue/Bamboo/Augment/Warp）
- ✅ 终端 TUI 工具补充（tmux/screen/btop/k9s/lazydocker/kubectl/ssh/curl/fzf/rg/ncdu/tig）

---

## 未做 / 待定

| # | 项 | 说明 |
|---|---|---|
| 1 | exe 代码签名 | 需要有效的代码签名证书，当前无证书 |
| 2 | AI 会话解析精度 | 第三方工具格式差异较大，目前 best-effort，可能统计缺失 |
| 3 | GitHub Pages 只做简单 landing | 如需完整文档站可继续扩展（当前够用） |
| 4 | 周报/月报多语言 / UI 多语言 | 可选，当前 UI 中文 |
| 5 | AI 编程深度追踪（长期规划） | 见 [docs/ROADMAP.md](docs/ROADMAP.md)：会话级追踪 / 质量分析 / 成本 ROI / 行为洞察 |

---

## 已知限制

- 管理员权限窗口标题：普通权限读取不到，可用 `monitor.py --admin` 以管理员运行
- UWP/商店应用：已能识别包显示名，但部分应用仍可能按 exe 记录
- 后台标签页不计时（前台注意力口径）
- 打包 exe 未代码签名，可能有杀软误报
- Firefox 停留时长是估算值（相邻访问间隔，上限可配）

---

## 交接备忘（环境/命令）

- **代理**：`127.0.0.1:7897`；git 已配代理；gh 已登录（Niangaol）
- **Python**：默认 `python`=3.14；带 PyInstaller 的 3.11 在
  `C:\Users\niangao\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe`
- **构建**：`python -m PyInstaller UsageMonitor.spec --noconfirm`（先停守护任务，exe 会被占用）
- **测试**：`python test_all.py`（268 项全过）；`ruff check .`（0 违规）
  - 若 Windows 临时目录权限导致测试失败，可先清理 `%TEMP%\usagemon_hist_*` / `dsh-*`
- **发布**：`git tag vX.Y.Z && git push origin vX.Y.Z` → CI 自动测试→构建→冒烟→Release
- **守护**：计划任务 `UsageMonitor`（exe）/`UsageMonitorReport`（每日 19:30 日报）
