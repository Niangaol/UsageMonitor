# 贡献指南 · CONTRIBUTING

欢迎为 **电脑使用情况监控（UsageMonitor）** 贡献代码、文档或 Issue。本指南面向所有协作者，
约定项目的开发环境、代码风格、测试、提交、分支与 PR、发布以及隐私要求。

请确保已阅读并理解 [README](README.md) 与《[项目需求与开发文档](./项目需求与开发文档.md)》，
尤其是其中的**架构**、**数据说明**与**隐私约定**章节。

> 一句话原则：**纯本地、零第三方依赖、隐私第一**。任何改动都要尊重这三点。

---

## 目录

- [开发环境要求](#开发环境要求)
- [代码风格约定](#代码风格约定)
- [现有文件结构说明](#现有文件结构说明)
- [测试规范](#测试规范)
- [提交规范（commit message）](#提交规范commit-message)
- [分支与 PR 流程](#分支与-pr-流程)
- [发布流程](#发布流程)
- [隐私约定](#隐私约定)

---

## 开发环境要求

本项目为 **Windows 专属**工具，开发环境需满足：

| 项 | 要求 |
|---|---|
| 操作系统 | **Windows 10 / Windows 11（x64，64 位）** |
| Python | **Python 3.10 及以上**（推荐 3.11；CI 使用 3.11） |
| 架构 | **64 位**（`win32core.py` 直接 `ctypes` 调用 Win32 API，32 位解释器不可用） |
| Git | 任意可用版本 |
| 可选 | `pip install pyinstaller`（本地重建 exe 时用） |

- 项目**零第三方运行时依赖**，正常工作只需 Python 标准库 + `ctypes`，**不要**引入 psutil / pywin32 等包。
- 需要打包测试时，用 `python -m PyInstaller UsageMonitor.spec --noconfirm` 重建 exe（见 README「打包为 exe」）。
- 只读检查、本地定时任务、Windows 基础操作一般无需管理员权限；但读取**提权窗口标题**需要以管理员身份运行监控进程。

---

## 代码风格约定

请严格遵循以下约定，保持与现有代码一致：

- **零第三方依赖**：项目刻意 **只使用 Python 标准库 + `ctypes` 直调 Win32**。
  提交的代码不得新增对 `pip install xxx` 的依赖（测试、文档除外，见下方说明）。
  如需读取系统信息，优先用 `CreateToolhelp32Snapshot` 等 Win32 API，而非额外轮询或第三方包。
- **中文注释**：源码注释、docstring、README、日志文案一律使用**简体中文**；标识符与关键字仍用英文。
- **类型注解**：函数与类尽量带类型注解（`from __future__ import annotations` 已普遍使用），方便静态检查与阅读。
- **编码与换行**：源码文件保持 **UTF-8**，建议配合 CRLF 行尾；`# -*- coding: utf-8 -*-` 用于需显式标识的脚本。
- **日志与输出**：面向用户的打印/日志统一走项目现有的日志机制（`errors.log`、标准输出格式化）；不要新增与现有一致的风格冲突的输出方式。
- **健壮性**：边界情况要兜底（如窗口标题为空、进程树不全、数据文件损坏等），失败时静默降级或记入 `errors.log`，不要向用户抛裸异常。
- **不写死路径**：路径解析统一走 `paths.py`（全项目零硬编码路径，请看 README「数据位置」），不要新增硬编码盘符。
- 保持代码**小步增量**：改动尽量局部、可读、可回滚，避免大范围重构夹带进功能提交。

### 现有文件结构说明

改动前先熟悉以下核心文件（详细职责见 README「目录结构」）：

| 文件 | 职责 | 涉及改动注意 |
|---|---|---|
| `monitor.py` | 守护进程：轮询前台窗口、写 `usage.jsonl`、跨天聚合 | ⚠️ 改动影响数据写入，务必全量回归测试 |
| `win32core.py` | Win32 API 封装（`ctypes`，零第三方） | 新 API 调用都放这里 |
| `classifier.py` | 分类引擎：类别/联系人/AI 工具/黑名单/子分类 | 分类规则改这里或 `config.json` |
| `inventory.py` | 软件清单扫描 | 读取注册表/快捷方式 |
| `report.py` | 日报/周报/月报生成与 CLI 查询 | — |
| `browser_history.py` | 浏览器 URL 级历史解析（Chromium/Firefox） | ⚠️ 涉及时复制的只读打开，勿改浏览器数据 |
| `dashboard.py` | 本地网页仪表盘（仅 127.0.0.1） | ⚠️ `/api/*` 必须做 Origin/Token 校验 |
| `tray.py` | 托盘图标（可选） | — |
| `test_all.py` | 完整集成测试（无头确定性） | 新增功能必须补测试 |
| `paths.py` | 统一路径解析 | 新增路径一律加到这里 |
| `install.ps1` / `uninstall.ps1` | 安装/卸载（计划任务） | — |
| `UsageMonitor.spec` | PyInstaller 打包配置 | 新增资源/图标需同步 |

> `config.default.json` 是分类/关键词/黑名单规则的**单一事实源**；新增规则优先改它，用户覆盖放 `config.json`。

---

## 测试规范

- **必须全量跑通最简命令**：
  ```powershell
  python test_all.py
  ```
  全量断言 **152+ 项**（随功能持续增长），全部通过时打印 `ALL TESTS PASSED`，约 1 分钟。
  **任何提交前都必须保证全量通过**，不能只跑自己新增的用例。
- **新增功能必须补测试**：在 `test_all.py` 追加对应用例，遵循现有的 `check()` / `ok()` 断言风格与
  `run_scenario(...)` 场景编排方式；用**猴子补丁**模拟前台窗口/空闲/进程树，保持无头、确定性、可重复。
  命名沿现有 `test_xxx` 风格，并在文件顶部/相关段落记录对应需求的 `§` 编号（见《需求文档》§14 测试方案）。
- 测试里**不要依赖真实前台/网络/浏览器数据**；用 `fresh_tmp(...)` 隔离临时数据目录，用例结束后清理。
- 涉及数据写入的改动（monitor / classifier / report 等），务必跑 `python report.py --verify`（及 `--repair`）验证数据完整性路径不被破坏。
- 打包类改动可用 `python -m PyInstaller UsageMonitor.spec --noconfirm` 顺带验证 exe 冒烟（`--version`）。

> 本仓库**不在 CI 外强制**第三方测试框架；保持与 `test_all.py` 一致的自定义断言风格即可，避免为测试引入新依赖。

---

## 提交规范（commit message）

提交信息使用**中文描述**，并带如下**前缀**（参考现有 `git log` 风格），前缀后接冒号与一句话/短段中文摘要：

| 前缀 | 用途 | 示例 |
|---|---|---|
| `feat:` | 新功能 | `feat: 应用分组自定义（覆盖层分类 + 仪表盘分组视图 + API）` |
| `fix:` | 缺陷修复 | `fix: report.py 缺失 import re（verify 路径 NameError）` |
| `docs:` | 文档（README / 文档 / 注释 / TODO） | `docs: README 目录/架构图/FAQ + --version 统一版本号` |
| `ci:` | CI / 工作流 / 构建脚本 | `ci: Release 步骤幂等（allowUpdates）` |
| `test:` | 测试相关 | `test: 补托盘调度 9 项断言` |
| `chore:` | 杂项（依赖/配置/工具链） | `chore: gitignore 覆盖沙箱测试临时目录（.tmp_*/）` |

规范要点：

- 首行建议 ≤ 72 字；需要时用空行分隔，加一段说明正文（中文）。
- 一个 commit 只做一件事；不要把无关改动（如格式化 + 功能 + 文档）混进一个 commit。
- 涉及隐私/数据语义的改动，在正文里说明对现有 `usage.jsonl` 数据是否向后兼容。
- 不要提交运行数据或本地私有配置（见[隐私约定](#隐私约定)）；提交前 `git status` 确认干净。

---

## 分支与 PR 流程

采用 **GitHub Flow 的简化版**，主分支为 `master`：

1. 从最新的 `master` 拉出**功能分支**，命名风格 `feature/<简述>` 或 `<类型>/<简述>`
   （如 `feature/app-groups`、`fix/report-import`）。
2. 在功能分支上小步提交（遵循上面的提交规范），**中途与提交前都全量跑 `python test_all.py`**。
3. 推送到远程后，打开 **Pull Request**，使用仓库提供的 [PULL_REQUEST_TEMPLATE](./.github/PULL_REQUEST_TEMPLATE.md) 填写：
   变更摘要、关联 Issue、测试情况、截图/验证说明。
4. 至少 **1 位协作者 Review** 后合并；有修改意见就继续补 commit，直到通过。
5. **PR 需通过 CI**（`.github/workflows/build.yml` 的 `test` job 会在 tag push 与手动触发时跑全量测试）。
   CI 未通过前不要自行 merge 到 master。
6. 合并到 `master` 后删掉已合并的功能分支。

> CI 目前由 **打 tag** / `workflow_dispatch` 触发（见下方发布流程）。为使 PR 也能得到 CI 校验，
> 可在 PR 合入前通过手动触发或本地 `python test_all.py` 保证通过。

---

## 发布流程

> 详见 README「打包为 exe」与 `.github/workflows/build.yml`。

1. **版本号递增**：在 `version.py` 递增 `__version__`（遵循 `vX.Y.Z` 语义化），并**同步更新《CHANGELOG.md》**（按 keep-a-changelog 格式记录 `Added / Fixed / Changed`）。
2. 确保本期所有改动已合并到 `master`，且 `python test_all.py` 全量通过。
3. **打 tag 并推送**（触发 CI 自动「测试 → 构建 exe → 冒烟 → 创建 Release」）：
   ```powershell
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
4. 查看 GitHub Actions 中的 `test` 与 `build` job：`build` 依赖 `test` 通过，之后构建 `dist\UsageMonitor.exe`，
   冒烟 `.\dist\UsageMonitor.exe --version`，并（仅 tag push）自动创建 GitHub Release 附上 exe 产物。
5. Release 完成后，在 Release 页面核对产物与版本号一致；如需要可补充发布说明。

> CI 会用 `--version` 冒烟，请确保 tag 版本与 `version.py` 一致（P2 后续计划加入二者一致性断言）。

---

## 隐私约定

本项目高度敏感于**个人隐私**，贡献者务必遵守：

- **运行数据绝不入库**：每天的日期文件夹（`usage.jsonl` / `report.md` / 软件清单 / 浏览器访问明细）
  包含**真实窗口标题与 URL**，属于个人隐私。`.gitignore` 已排除 `20*-*/`、`usage.db` 等；
  **提交代码时确保 `git status` 不出现任何日期目录或数据文件**。
- **本地私有配置不入库**：`aliases.json`（真实联系人别名）被 `.gitignore` 忽略；
  仓库只提供 `aliases.example.json` 模板。不要在 Issue/PR/提交中粘贴真实的联系人名、真实窗口标题或真实 URL。
- 代码本身不得包含任何**个人/演示数据**；分类规则、关键词、黑名单放 `config.default.json`（可入库的通用规则）。
- 截图/演示一律使用 `python make_demo_data.py` 生成的**虚构数据**，且不截到真实使用记录。
- 默认**无任何联网上传**；仪表盘仅监听 `127.0.0.1`；不实现截屏/录屏/OCR/键盘钩子/剪贴板读取。
  新增的本地服务端点必须做安全校验（`/api/*` 校验 `Origin`/`Referer` 与可选的 Dashboard Token）。
- 汇报 bug 或隐私相关问题时，先在本地以 `[已隐藏]` 替换敏感字段（黑名单命中即为 `[已隐藏]`），再提交日志片段。

---

再次感谢你的贡献！有任何疑问可在 Issue 中提出，或先阅读《项目需求与开发文档》与 [TODO.md](TODO.md)。
