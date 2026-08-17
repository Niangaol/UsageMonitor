# UsageMonitor · 电脑使用情况监控

> Pure vibe coding artifact · Local-first · Zero third-party dependencies

A local Windows background monitor: records software, social contacts, browser activity, and AI coding usage; generates daily/weekly/monthly reports, and provides a local web dashboard, smart insights, and in-app updates.

## Features

- Foreground app timing (5s polling, no timing while idle)
- WeChat / QQ / DingTalk contact recognition
- Browser activity classification + URL history (Chromium + Firefox)
- Vibe coding / AI coding monitoring (process-tree detection)
- Daily / weekly / monthly reports + local web dashboard
- Custom app grouping, smart insights (offline rules + optional AI)
- Update check & in-app update
- Optional: SQLite backend `usage.db`, AI session deep stats (opencode/ChatGPT/Claude/Cursor/Windsurf/Trae/DeepSeek/Pi Agent/DSH)

## Quick Start

```powershell
git clone https://github.com/Niangaol/UsageMonitor.git
cd UsageMonitor

python monitor.py --test 30    # test run for 30 seconds
python monitor.py --foreground # run in foreground
```

GUI install/uninstall: `installer.ps1` / `uninstaller.ps1`.

## Common Commands

| Command | Purpose |
|---|---|
| `python monitor.py` | Daemon (`--tray` tray icon, `--test N` test mode) |
| `python report.py --today` | Today's report |
| `python report.py --day 2026-08-10 --reclassify` | Reclassify history with new rules |
| `python dashboard.py --open` | Open local dashboard |
| `python insights.py --day 2026-08-10 --ai` | Smart insights |
| `python updater.py --check` | Check for updates |
| `python sqlite_store.py --backfill` | Backfill SQLite |
| `python ai_sessions.py --day 2026-08-10` | AI session deep stats |

## Build EXE

```powershell
python -m PyInstaller UsageMonitor.spec --noconfirm
# Output: dist\UsageMonitor.exe
```

## Configuration

- `config.json`: classification rules, blacklist, `data_root`, `insights`, `update`, `sqlite`, `ai_sessions`
- Empty `data_root` = program directory; data retention defaults to 90 days
- `ai_sessions.enabled` defaults to `false`; `sqlite.enabled` defaults to `true`
- Full fields: `config.default.json`

## Privacy

- Fully local, no upload by default; dashboard listens on `127.0.0.1` only
- AI insights are off by default; when enabled, only aggregate stats are sent (no titles/URLs/contacts)
- Update checks only request public GitHub Releases metadata
- AI session stats are off by default and only read local files

## Testing

```powershell
python test_all.py   # 242 assertions
ruff check .
```

## Links

- [Changelog](CHANGELOG.md) · [简体中文](README.md)
- [Contributing](CONTRIBUTING.md)
- [Requirements](项目需求与开发文档.md)
- Pages: https://niangaol.github.io/UsageMonitor/