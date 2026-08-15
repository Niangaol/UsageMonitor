---
name: 拉取请求（Pull Request）
about: 提交代码改动，请先阅读 CONTRIBUTING.md
title: ""
labels: ""
assignees: ""

---

<!--
请先阅读《CONTRIBUTING.md》：代码风格（零第三方依赖/中文注释/类型注解）、测试规范（python test_all.py 全量 152+ 项）、提交规范（feat:/fix:/docs:/ci:/test:/chore: 前缀）、隐私约定（真实数据/联系人/别名不入库）。
-->

## 变更摘要

<!-- 一句话说明本次改动做了什么，为什么 -->

## 关联 Issue

<!-- 如有关联的 Issue，请在此填写编号，例如：Closes #123 -->

- 关联 Issue：`#`

## 变更内容

- [ ] 新增功能 / 修复
- [ ] 涉及数据写入（monitor / classifier / report 等），已检查 `usage.jsonl` 向后兼容
- [ ] 隐私相关：未引入真实数据 / 联系人 / 别名；本地服务端点已做安全校验（如有）

## 测试情况

<!-- 请如实填写，优先提供机器输出。

- [ ] 本地全量测试通过：`python test_all.py` → `ALL TESTS PASSED`（152+ 项）
- [ ] 新增/更新的测试用例已写入 `test_all.py`（沿用 `check()`/`ok()` 风格）
- [ ] 打包验证（如改动影响）：`python -m PyInstaller UsageMonitor.spec --noconfirm` + `.\dist\UsageMonitor.exe --version`

```text
（粘贴 python test_all.py 末尾输出，如：ALL TESTS PASSED · N 项断言）
```
-->

## 截图 / 验证说明（可选）

<!--
如图表/界面的改动，请附演示数据（python make_demo_data.py）截图或运行输出；
涉及真实数据的区域务必脱敏为 `[已隐藏]` 或「xxx」。
-->

## 检查清单

- [ ] 已阅读 CONTRIBUTING.md
- [ ] 代码遵循现有风格（零第三方依赖 / 中文注释 / 类型注解）
- [ ] 已全量跑通测试，未破坏既有用例
- [ ] `git status` 干净：未提交任何日期目录 / `aliases.json` / 运行数据
- [ ] 提交信息符合前缀规范
