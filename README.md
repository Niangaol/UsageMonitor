# UsageMonitor · 电脑使用情况监控

> 纯 vibe coding 产物 · 本地优先 · 零第三方依赖

Windows 本地后台监控：记录软件、社交联系人、浏览器活动、AI 编程使用时长；生成日报/周报/月报，提供本地网页仪表盘、智能洞察与应用内更新。

## 功能

- 前台应用计时（5s 轮询，空闲不计时）
- 微信/QQ/钉钉联系人识别
- 浏览器活动分类 + URL 历史（Chromium + Firefox）
- vibe coding / AI 编程监控（终端进程树识别）
- 日报 / 周报 / 月报、本地网页仪表盘
- 应用分组自定义、智能洞察（离线规则 + 可选 AI）
- 新版本检测与应用内更新
- 可选：SQLite 后端 `usage.db`、AI 会话深度统计（opencode/ChatGPT/Claude/Cursor/Windsurf/Trae/DeepSeek/Pi Agent/DSH）

## 快速开始

```powershell
git clone https://github.com/Niangaol/UsageMonitor.git
cd UsageMonitor

python monitor.py --test 30    # 测试运行 30 秒
python monitor.py --foreground # 前台运行
```

图形安装/卸载：`installer.ps1` / `uninstaller.ps1`。

## 常用命令

| 命令 | 作用 |
|---|---|
| `python monitor.py` | 守护进程（`--tray` 托盘，`--test N` 测试） |
| `python report.py --today` | 今日日报 |
| `python report.py --day 2026-08-10 --reclassify` | 按新规则重分类历史 |
| `python dashboard.py --open` | 打开本地仪表盘 |
| `python insights.py --day 2026-08-10 --ai` | 智能洞察 |
| `python updater.py --check` | 检查更新 |
| `python sqlite_store.py --backfill` | 回填 SQLite |
| `python ai_sessions.py --day 2026-08-10` | AI 会话深度统计 |

## 打包 exe

```powershell
python -m PyInstaller UsageMonitor.spec --noconfirm
# 产物：dist\UsageMonitor.exe
```

## 配置

- `config.json`：分类规则、黑名单、`data_root`、`insights`、`update`、`sqlite`、`ai_sessions`
- `data_root` 为空 = 程序所在目录；数据默认保留 90 天自动清理
- `ai_sessions.enabled` 默认 `false`；`sqlite.enabled` 默认 `true`
- 完整字段见 `config.default.json`

## 隐私

- 纯本地，默认无任何上传；仪表盘仅监听 `127.0.0.1`
- AI 洞察默认关闭，开启后才发送聚合统计（不含标题/URL/联系人）
- 更新检查只请求 GitHub Releases 公开元数据
- AI 会话统计默认关闭，只读取本地文件

## 测试

```powershell
python test_all.py   # 242 项断言
ruff check .
```

## 链接

- [更新日志](CHANGELOG.md) · [English](README.en.md)
- [贡献指南](CONTRIBUTING.md)
- [需求文档](项目需求与开发文档.md)
- Pages：https://niangaol.github.io/UsageMonitor/