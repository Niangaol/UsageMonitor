# 更新日志

本项目所有值得记录的变更都归档在此文件中。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
发布流程：`git tag vX.Y.Z` 后由 CI 自动构建并发布 Release。

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

[未发布]: https://github.com/Niangaol/UsageMonitor/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.1.0
[1.0.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.0.0
