# UsageMonitor · 电脑使用情况监控

> Pure vibe coding artifact
> Local-first · Python standard library + ctypes · Zero third-party runtime dependencies

## About

UsageMonitor is a local Windows usage monitoring tool. It runs as a background daemon, samples the foreground window at a fixed interval, records application usage time, and generates daily/weekly/monthly reports plus a local web dashboard.

Scope:

- Data stays local by default
- No screenshots, screen recording, keyboard logging, or chat content reading
- GUI install/uninstall, update check, and in-app update

## Features

- Foreground app timing: 5s polling by default, writes only on state change
- No timing while idle/locked: sessions are cut off after 3 minutes without input by default
- Software inventory scan: registry uninstall entries, Start Menu shortcuts, running processes
- Social contact recognition: WeChat, QQ, DingTalk window titles, with alias support
- Browser activity classification: video / coding / studying / other
- Browser URL history parsing: Chromium (Chrome/Edge, etc.) and Firefox
- AI coding monitoring: process-tree detection for opencode, pi agent, claude, etc.
- Daily/weekly/monthly reports: Markdown and CSV
- Local web dashboard: listens on 127.0.0.1 only
- Custom app grouping: overlay config, effective immediately
- Smart insights: offline rule engine, optional AI aggregate suggestions
- Update check & in-app update: GitHub Releases, SHA256 verification
- Optional SQLite backend: `usage.db`, an extra mirror/index beside JSONL
- Optional AI session deep stats: reads local AI tool session files and counts turns/generated output

## Modules

| File | Purpose |
|---|---|
| `monitor.py` | Daemon, tray, cross-day aggregation |
| `win32core.py` | Win32 API wrapper |
| `classifier.py` | Classification, contacts, AI tool recognition, config loading |
| `report.py` | Daily/weekly/monthly report generation, query, reclassification, verification |
| `dashboard.py` | Local web dashboard |
| `browser_history.py` | Browser history parsing |
| `insights.py` | Smart insight rules and AI client |
| `ai_sessions.py` | AI session deep stats |
| `sqlite_store.py` | Optional SQLite backend |
| `updater.py` | Update check and in-app update |
| `tray.py` | Tray icon |
| `paths.py` | Path resolution |

## Quick Start

```powershell
git clone https://github.com/Niangaol/UsageMonitor.git
cd UsageMonitor

# Test run for 30 seconds
python monitor.py --test 30

# Run in foreground
python monitor.py --foreground
```

By default, data is written to date directories under the project directory. Change `data_root` in `config.json` to use another location.

## Install & Uninstall

```powershell
# GUI installer
powershell -ExecutionPolicy Bypass -File installer.ps1

# Silent install
powershell -ExecutionPolicy Bypass -File installer.ps1 -Silent -InstallDir "D:\UsageMonitor" -NoLaunch

# Minimal script install
powershell -ExecutionPolicy Bypass -File install.ps1

# Uninstall
powershell -ExecutionPolicy Bypass -File uninstaller.ps1
```

## Common Commands

| Command | Purpose |
|---|---|
| `python monitor.py --tray` | Tray daemon |
| `python monitor.py --test N` | Run for N seconds then exit |
| `python monitor.py --admin` | Run as administrator (auto UAC elevation) |
| `python report.py --today` | Today's report |
| `python report.py --day YYYY-MM-DD --write` | Regenerate a day report |
| `python report.py --day YYYY-MM-DD --reclassify` | Reclassify history with current rules |
| `python report.py --verify --repair` | Verify and repair data |
| `python dashboard.py --open` | Open dashboard |
| `python insights.py --day YYYY-MM-DD --ai` | Smart insights |
| `python ai_sessions.py --day YYYY-MM-DD` | AI session deep stats |
| `python sqlite_store.py --backfill` | Backfill SQLite |
| `python sqlite_store.py --verify` | Verify JSONL/SQLite consistency |
| `python updater.py --check` | Check for updates |

## Build EXE

```powershell
python -m PyInstaller UsageMonitor.spec --noconfirm
# Output: dist\UsageMonitor.exe
# CI also generates dist\UsageMonitor.exe.sha256 on release
```

The packaged EXE is a single unsigned file and may be flagged by some antivirus products. Running from source avoids this issue.

## Configuration

### config.json

| Setting | Description |
|---|---|
| `data_root` | Data root; empty means the program directory |
| `poll_interval_s` | Polling interval, default 5s |
| `idle_threshold_s` | Idle threshold, default 180s |
| `retention_days` | Data retention, default 90 |
| `categories` | Category rules |
| `apps` / `uwp_app_names` | App display names and UWP package display-name mapping |
| `browser_history_enabled` / `browser_history` | Browser history toggle and paths |
| `firefox_dwell_max_s` | Firefox dwell-time estimation cap (default 600s) |
| `insights` | Smart insights; AI off by default |
| `update` | Update check; `check_on_startup` defaults true |
| `sqlite` | SQLite backend; defaults true |
| `ai_sessions` | AI session stats; off by default |
| `title_blacklist` | Title privacy blacklist |

See `config.default.json` for the full defaults.

### Other Data Files

- `aliases.json`: contact aliases (not committed)
- `app_groups.json`: app group overlay
- `ai_custom.json`: custom AI insight module
- `usage.db`: optional SQLite backend

## Data

- Raw session data: `YYYY-MM-DD/usage.jsonl`, one JSON object per line
- Fields include start/end, duration_ms, exe/app, title, category, contact, ai_tool, active, etc.
- Writes use `flush + fsync`
- Data retention defaults to 90 days; expired folders are cleaned automatically

## Privacy

- No network upload by default
- Dashboard listens on `127.0.0.1` only; all `/api/*` endpoints validate Origin/Referer
- AI insights are off by default; when enabled, only aggregate stats are sent, without window titles, URLs, or contact names
- Update checks only request public GitHub Releases metadata
- AI session stats are off by default; when enabled, only local session files are read

## Testing & CI

```powershell
python test_all.py   # 268 assertions
ruff check .         # 0 violations
```

CI: tests → coverage → PyInstaller build → EXE smoke test → Release on tag.

## Known Limitations

- Window titles of elevated applications may not be readable
- UWP/Store app titles may be empty
- Background tabs are not timed (foreground attention metric)
- The packaged EXE is unsigned and may be flagged by antivirus
- AI session parsing is best-effort; format differences may cause partial stats

## Docs

- [CHANGELOG.md](CHANGELOG.md) · [CHANGELOG.en.md](CHANGELOG.en.md)
- [简体中文 README](README.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [Requirements](项目需求与开发文档.md)
- GitHub Pages: https://niangaol.github.io/UsageMonitor/