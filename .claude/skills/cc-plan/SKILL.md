---
name: cc-plan
description: 半自动本地审查回环（cc-loop）的计划步骤。基于已拆解的 task.md 与源码，产出文件级、最小步骤、可回滚、可测试的实施计划。仅在一个 cc-loop 任务进行中、需要为当前轮次制定计划时使用；不在普通对话中自动触发，也不直接改代码。
allowed-tools: Read Glob Grep Bash(git diff:*) Bash(git log:*) Write
---

# cc-plan — 文件级实施计划

## 触发方式
- 由 `cc-loop` 在每轮实现前调用，或用户显式 `/cc-plan`。
- 前提：本任务已由 `cc-intake` 产出 `task.md`。

## 职责
- 基于 `task.md` 与相关源码，制定**当前这一轮**的最小步骤计划。
- 计划要落到文件级：改哪些文件、每步做什么、如何回滚、用什么测试验证。

## 输入
- `tools/cc_local_loop/runs/<ts>-<slug>/task.md`。
- 上一轮的 `next_cc_prompt.md`（若存在）。
- 相关源码（只读）。

## 输出
- `tools/cc_local_loop/runs/<ts>-<slug>/round_<n>/plan.md`（文件级最小步骤计划）。

## 允许行为
- 读代码、读 `git diff`/`git log`（了解现状）。
- 仅向 `runs/` 下写 `plan.md`。

## 禁止行为
- 改任何业务文件。
- 直接进入实现（不写代码）。
- commit / push / deploy / 删除文件。

## 失败时怎么停
- `task.md` 缺失或范围不清 → 停并要求先跑 `cc-intake` 或补充范围。
- 计划无法做到「最小、可回滚、可测试」→ 停并说明卡点，不强行出计划。

## 是否允许改文件
- 否。仅向 `runs/` 写 `plan.md`。

## 是否允许运行测试
- 否（只规划测试，不执行）。

## 是否允许调用 tools/cc_local_loop/
- 否（不运行其中脚本；最多读 `config.json` 了解约束）。

## 与其他 skills 的边界
- 只产出计划，不实现、不收集、不审查。
- 上游是 `cc-intake`（提供 task.md），下游是 `cc-implement`（消费 plan.md）。
