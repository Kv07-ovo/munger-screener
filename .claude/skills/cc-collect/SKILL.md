---
name: cc-collect
description: 半自动本地审查回环（cc-loop）的上下文收集步骤。把本轮的 git diff、测试结果、计划对照、执行报告汇成脱敏后的 context.md，供本地 AI 审查使用。仅在一个 cc-loop 任务进行中、实现步骤完成后使用；只读业务代码、只写 runs/，绝不把未脱敏的密钥写入。
allowed-tools: Read Glob Grep Bash(git diff:*) Bash(git status:*) Bash(.venv/Scripts/python.exe:*) Write
---

# cc-collect — 上下文收集与脱敏

## 触发方式
- 由 `cc-loop` 在实现完成后调用，或由 Stop 收集 hook（Phase C）触发，或用户显式 `/cc-collect`。

## 职责
- 把「本轮做了什么」汇成 `context.md`：改动文件清单、`git diff` 摘要、测试输出、与 `plan.md` 的对照、执行报告。
- 写入前对所有内容**脱敏**（复用 `tools/cc_local_loop/common.py` 的 redact），确保不落任何密钥值。

## 输入
- `git diff` / `git status`（本轮改动）。
- 测试日志（来自 `cc-implement` 的自测）。
- 当轮 `plan.md`。

## 输出
- `tools/cc_local_loop/runs/<ts>-<slug>/round_<n>/context.md`，且文件内含 `round_index: <n>`（供 `local_review.py` 解析轮次）。

## 允许行为
- 读 `git diff`/`git status`、读测试日志、读源码。
- 仅向 `runs/` 下写 `context.md`。
- 复用 `common.py` 的脱敏 helper（只读调用，不修改该文件）。

## 禁止行为
- 改任何业务文件。
- 写入未脱敏的密钥 / Token / Cookie / SSH Key。
- commit / push / deploy / 删除文件。

## 失败时怎么停
- 拿不到 diff 或测试日志 → 在 `context.md` 标注「收集不完整」并停，交回 `cc-loop`，不伪造内容。
- 检测到疑似密钥无法可靠脱敏 → 停并告警，不写出该段内容。

## 是否允许改文件
- 否。仅向 `runs/` 写 `context.md`。

## 是否允许运行测试
- 可触发一次测试以采集输出；也可直接复用 `cc-implement` 已产生的测试日志（优先复用，避免重复跑）。

## 是否允许调用 tools/cc_local_loop/
- 是——**只读**复用 `common.py` 的脱敏 helper；不修改 `tools/cc_local_loop/` 现有文件，不新建 collect_context.py（收集逻辑放在本 skill 指令 / loop_controller 内）。

## 与其他 skills 的边界
- 只收集与脱敏，不审查（交给 `cc-local-review`）、不改代码。
- 上游是 `cc-implement`，下游是 `cc-local-review`（消费 context.md）。
