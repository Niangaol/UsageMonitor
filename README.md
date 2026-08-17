# UsageMonitor · 电脑使用情况监控

> 纯 vibe coding 产物
> 本地优先 · Python 标准库 + ctypes · 零第三方运行时依赖

## 项目说明

UsageMonitor 是一个 Windows 本地使用情况监控工具。它以后台守护进程方式运行，按固定间隔采集前台窗口信息，记录软件使用时长，并基于这些数据生成日报、周报、月报和本地网页仪表盘。

项目定位：

- 数据默认只保存在本机
- 不截屏、不录屏、不读取键盘输入、不读取聊天内容
- 支持通过 GUI 安装、卸载、检查更新和应用内更新

## 功能

- 前台应用计时：默认每 5 秒轮询一次，仅在状态变化时写入
- 空闲/锁屏不计时：默认 3 分钟无键鼠输入截断会话
- 软件清单扫描：注册表卸载项、开始菜单快捷方式、运行中进程
- 社交联系人识别：微信、QQ、钉钉窗口标题解析，支持别名表
- 浏览器活动分类：标题关键词划分视频/代码/学习/其他
- 浏览器 URL 历史解析：Chromium（Chrome/Edge 等）与 Firefox
- AI 编程监控：进程树识别终端中的 opencode、pi agent、claude 等
- 日报、周报、月报：Markdown 与 CSV
- 本地网页仪表盘：仅监听 127.0.0.1
- 应用分组自定义：覆盖层配置，实时生效
- 智能洞察：离线规则引擎，可选 AI 聚合统计建议
- 新版本检测与应用内更新：GitHub Releases，SHA256 校验
- 可选 SQLite 后端：`usage.db`，JSONL 之外的镜像/索引
- 可选 AI 会话深度统计：读取本地 AI 工具会话文件，统计轮数与生成量

## 模块

| 文件 | 作用 |
|---|---|
| `monitor.py` | 守护进程、托盘、跨天聚合 |
| `win32core.py` | Win32 API 封装 |
| `classifier.py` | 分类、联系人、AI 工具识别、配置加载 |
| `report.py` | 日报/周报/月报生成、查询、重分类、校验 |
| `dashboard.py` | 本地网页仪表盘 |
| `browser_history.py` | 浏览器历史解析 |
| `insights.py` | 智能洞察规则与 AI 客户端 |
| `ai_sessions.py` | AI 会话深度统计 |
| `sqlite_store.py` | 可选 SQLite 后端 |
| `updater.py` | 新版本检测与应用内更新 |
| `tray.py` | 托盘图标 |
| `paths.py` | 路径解析 |

## 快速开始

```powershell
git clone https://github.com/Niangaol/UsageMonitor.git
cd UsageMonitor

# 测试运行 30 秒
python monitor.py --test 30

# 前台运行
python monitor.py --foreground
```

运行后数据默认写入项目目录下的日期文件夹。可通过 `config.json` 的 `data_root` 修改。

## 安装与卸载

```powershell
# 图形安装向导
powershell -ExecutionPolicy Bypass -File installer.ps1

# 静默安装
powershell -ExecutionPolicy Bypass -File installer.ps1 -Silent -InstallDir "D:\UsageMonitor" -NoLaunch

# 脚本安装
powershell -ExecutionPolicy Bypass -File install.ps1

# 卸载
powershell -ExecutionPolicy Bypass -File uninstaller.ps1
```

## 常用命令

| 命令 | 作用 |
|---|---|
| `python monitor.py --tray` | 托盘守护进程 |
| `python monitor.py --test N` | 运行 N 秒后退出 |
| `python report.py --today` | 今日日报 |
| `python report.py --day YYYY-MM-DD --write` | 重新生成指定日报 |
| `python report.py --day YYYY-MM-DD --reclassify` | 按当前规则重分类历史 |
| `python report.py --verify --repair` | 校验并修复数据 |
| `python dashboard.py --open` | 打开仪表盘 |
| `python insights.py --day YYYY-MM-DD --ai` | 智能洞察 |
| `python ai_sessions.py --day YYYY-MM-DD` | AI 会话深度统计 |
| `python sqlite_store.py --backfill` | 回填 SQLite |
| `python sqlite_store.py --verify` | 校验 JSONL 与 SQLite 一致性 |
| `python updater.py --check` | 检查更新 |

## 打包 exe

```powershell
python -m PyInstaller UsageMonitor.spec --noconfirm
# 产物：dist\UsageMonitor.exe
# CI 发布时同时生成 dist\UsageMonitor.exe.sha256
```

打包版为单文件 exe，未做代码签名，可能被部分杀毒软件误报。可使用源码运行规避。

## 配置

### config.json

| 配置项 | 说明 |
|---|---|
| `data_root` | 数据根目录；空字符串表示程序所在目录 |
| `poll_interval_s` | 轮询间隔，默认 5 秒 |
| `idle_threshold_s` | 空闲判定阈值，默认 180 秒 |
| `retention_days` | 数据保留天数，默认 90 |
| `categories` | 大类分类规则 |
| `browser_history_enabled` / `browser_history` | 浏览器历史开关与路径 |
| `insights` | 智能洞察配置；AI 默认关闭 |
| `update` | 更新检查配置；`check_on_startup` 默认 true |
| `sqlite` | SQLite 后端开关；默认 true |
| `ai_sessions` | AI 会话统计；默认关闭 |
| `title_blacklist` | 标题隐私黑名单 |

完整默认值见 `config.default.json`。

### 其他数据文件

- `aliases.json`：联系人别名（不入库）
- `app_groups.json`：应用分组覆盖层
- `ai_custom.json`：AI 洞察自定义模块
- `usage.db`：可选 SQLite 后端

## 数据说明

- 原始会话数据：`YYYY-MM-DD/usage.jsonl`，每行一个 JSON 对象
- 字段包括 start/end、duration_ms、exe/app、title、category、contact、ai_tool、active 等
- 写入带 `flush + fsync`
- 数据默认保留 90 天，超期自动清理

## 隐私

- 默认无任何联网上传
- 仪表盘只监听 `127.0.0.1`，所有 `/api/*` 校验 Origin/Referer
- AI 洞察默认关闭；开启后发送聚合统计，不含窗口标题、URL、联系人名
- 更新检查仅请求 GitHub Releases 公开元数据
- AI 会话统计默认关闭；开启后只读取本地会话文件，不上传数据

## 测试与 CI

```powershell
python test_all.py   # 258 项断言
ruff check .         # 0 违规
```

CI 流程：测试 → coverage → PyInstaller 构建 → exe 冒烟 → 打 tag 时发布 Release。

## 已知局限

- 管理员权限运行的窗口标题可能读不到
- UWP/商店应用标题可能为空
- 后台标签页不计时（前台注意力口径）
- 打包 exe 未代码签名，可能有杀软误报
- AI 会话解析为 best-effort，不同工具格式差异可能导致部分统计缺失

## 文档

- [CHANGELOG.md](CHANGELOG.md) · [CHANGELOG.en.md](CHANGELOG.en.md)
- [English README](README.en.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [项目需求与开发文档.md](项目需求与开发文档.md)
- GitHub Pages：https://niangaol.github.io/UsageMonitor/