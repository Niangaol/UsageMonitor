---
layout: default
title: UsageMonitor
---

# UsageMonitor · 电脑使用情况监控

Windows 本地使用情况监控工具：纯本地、零第三方依赖、静默低占用。

- 记录每天使用的软件、社交联系人、浏览器活动与 AI 编程时长
- 自动生成日报 / 周报 / 月报，提供本地网页仪表盘
- 智能洞察（离线规则 + 可选 AI）
- 新版本检测与应用内更新（v2.0+）
- 可选 SQLite 后端（`usage.db`）与 AI 会话深度统计

## 文档

- 完整中文说明：[README.md](../README.md)
- English README: [README.en.md](../README.en.md)
- 更新日志：[CHANGELOG.md](../CHANGELOG.md) · [CHANGELOG.en.md](../CHANGELOG.en.md)
- AI 编程深度追踪规划：[ROADMAP.md](ROADMAP.md)

## 快速开始

```powershell
git clone https://github.com/Niangaol/UsageMonitor.git
cd UsageMonitor
python monitor.py --test 30
python monitor.py --foreground
```

> 详细安装、配置、隐私与打包说明请见 README。
