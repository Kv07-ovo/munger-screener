---
name: cc-loop
description: 半自动本地审查回环的总入口。用户给出一个任务后，按 intake → plan → implement → collect → local-review → verdict 闸门 的标准流程编排，最多循环 5 轮后停下等人工验收。默认 safe_auto 半自动：实现仍由当前会话按 cc-implement 规则执行，绝不无人值守全自动、绝不自动 commit/push/deploy。仅在用户显式发起一个任务时使用。
allowed-tools: Read Glob Grep Skill Bash(git status:*) Bash(git log:*) Bash(.venv/Scripts/python.exe tools/cc_local_loop/loop_controller.py:*) Bash(python tools/cc_local_loop/loop_controller.py:*)
---

# cc-loop — 回环总入口与编排

## 触发方式
- 用户显式 `/cc-loop <任务>`。这是「输入一个任务即按标准流程推进」的唯一入口。

## 职责
- 串联编排：`cc-intake` → `cc-plan` → `cc-implement` → `cc-collect` → `cc-local-review` → verdict 闸门。
- 管理轮次（≤ max_rounds=5）与任务隔离（每任务独立 `runs/<ts>-<slug>/`、每轮 `round_<n>/`）。
- 依据 verdict 决定走停（见下「闸门规则」）。
- 真实运行时通过 `loop_controller.py`（Phase D 才创建）执行确定性步骤；Phase B 仅交付本编排说明骨架。

## 闸门规则（依据 cc-local-review 回报的 verdict）
- `UNSURE`（含 mock 降级）→ **停**，等人工判断。
- `PASS` → **停**，等人工验收（不自动 commit）。
- `NEEDS_FIX` 且 round < 5 → 写 `next_cc_prompt.md`，round + 1，回到 `cc-implement`。
- round == 5 → 无论 verdict 如何，**停**。

## 输入
- 用户任务描述。
- `tools/cc_local_loop/runs/<ts>-<slug>/STATE.json`（任务与轮次状态）。
- `tools/cc_local_loop/config.json`（max_rounds、模式、策略）。

## 输出
- 触发各阶段产物（task.md / plan.md / context.md / review.md / next_cc_prompt.md）。
- 最终停机状态（UNSURE / PASS / 到达 max_rounds）与给人工的汇报。

## 允许行为
- 调用其他五个 skill 进行编排（Skill 工具）。
- 读 `runs/` 状态、读 git 状态。
- 执行安全门禁（工作区干净、单一活跃任务、分支一致）。
- 真实运行时调用 `loop_controller.py`。

## 禁止行为
- 自己直接改业务代码（实现一律委托 `cc-implement`）。
- 自动 commit / push / deploy。
- 绕过 max_rounds=5。
- 在 `UNSURE` / `PASS` 时继续自动推进。
- 把两个不同任务混进同一轮记录。
- 工作区不干净时直接开始新任务。

## 失败时怎么停
- 门禁不通过（工作区脏 / 已有活跃任务 / 分支不符）→ 立即停并汇报，不开工。
- 任一子 skill 失败 → 停在当前轮，保留现场，汇报状态。
- verdict 为 UNSURE/PASS 或 round 到 5 → 按闸门停机，交人工。

## 是否允许改文件
- 否——编排者不改业务代码；改码委托 `cc-implement`，状态/产物写在 `runs/`。

## 是否允许运行测试
- 间接——通过 `cc-implement`/`cc-collect`/`loop_controller.py` 跑测试，自身不直接执行业务测试。

## 是否允许调用 tools/cc_local_loop/
- 是——真实运行时调用 `loop_controller.py`（Phase D 创建）；不修改 `tools/cc_local_loop/` 现有文件。

## 与其他 skills 的边界
- 是总指挥，只编排与闸门，不承担任何具体阶段的执行细节。
- 下游依次调度 `cc-intake` / `cc-plan` / `cc-implement` / `cc-collect` / `cc-local-review`，并依据其回报推进或停机。
