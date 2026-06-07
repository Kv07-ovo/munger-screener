---
name: cc-local-review
description: 半自动本地审查回环（cc-loop）的本地 AI 审查步骤。调用 tools/cc_local_loop/local_review.py 对当轮 context.md 做本地审查，得到 review.md 与 next_cc_prompt.md，并把 verdict（PASS/NEEDS_FIX/UNSURE）回报给闸门。仅在一个 cc-loop 任务进行中、已有当轮 context.md 时使用；不改业务代码，mock 模式下绝不自动推进。
allowed-tools: Read Bash(.venv/Scripts/python.exe tools/cc_local_loop/local_review.py:*) Bash(python tools/cc_local_loop/local_review.py:*)
---

# cc-local-review — 本地 AI 审查

## 触发方式
- 由 `cc-loop` 在收集完成后调用，或用户显式 `/cc-local-review`。
- 前提：本任务已有当轮 `context.md`。

## 职责
- 对当轮 `context.md` 运行 `tools/cc_local_loop/local_review.py`。
- 读取生成的 `review.md` 与 `next_cc_prompt.md`，把 verdict 与 backend（含是否 mock）回报给 `cc-loop` 闸门。

## 输入
- `tools/cc_local_loop/runs/<ts>-<slug>/round_<n>/context.md`。

## 输出
- 同目录 `review.md`、`next_cc_prompt.md`（由脚本生成）。
- 向 `cc-loop` 回报：`backend`、`verdict`、`round_index`。

## 允许行为
- 运行 `local_review.py`（只读其输入、写其既定输出）。
- 读 `review.md` / `next_cc_prompt.md`。

## 禁止行为
- 改任何业务文件。
- 在 mock 模式（或脚本降级）下把 verdict 当作 PASS、继续自动推进。
- commit / push / deploy / 删除文件。

## 失败时怎么停
- 脚本非零退出 → 停，verdict 视为 UNSURE，交回 `cc-loop` 等人工。
- `backend=mock` 或落降级 → verdict 一律按 UNSURE 处理，**停**等人工判断，不进入下一轮。

## 是否允许改文件
- 否（不改业务代码；review.md/next_cc_prompt.md 由脚本写入 `runs/`）。

## 是否允许运行测试
- 否（它运行的是 review，不是业务测试）。

## 是否允许调用 tools/cc_local_loop/
- 是——运行**现有**的 `local_review.py`；不修改 `tools/cc_local_loop/` 任何现有文件。

## 与其他 skills 的边界
- 只做审查与回报，不改码、不收集、不决定走停（走停由 `cc-loop` 闸门依据 verdict 决定）。
- 上游是 `cc-collect`，下游是 `cc-loop`（消费 verdict）。
