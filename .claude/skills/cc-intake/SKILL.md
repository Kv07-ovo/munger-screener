---
name: cc-intake
description: 半自动本地审查回环（cc-loop）的第一步——任务接收与拆解。当用户要开始一个新任务、需要把一句话需求拆成结构化任务卡，并做开工前安全门禁（工作区是否干净、是否已有未结任务）时使用。仅在显式开始一个 cc-loop 任务时触发，不在日常对话中自动触发。
allowed-tools: Read Glob Grep Bash(git status:*) Bash(git log:*) Bash(git rev-parse:*) Write
---

# cc-intake — 任务接收与拆解

## 触发方式
- 用户显式 `/cc-intake <任务描述>`，或由 `cc-loop` 在回环起点调用。
- 不在普通对话里自动触发（避免无意义拆解）。

## 职责
- 把用户一句话任务，拆成结构化「任务卡」：目标、范围、验收标准、受影响模块、明确的禁止改动清单。
- 执行**开工前安全门禁**：确认工作区干净、当前无未结的活跃任务。
- 初始化本任务的运行目录与状态（仅在真实运行阶段；Phase B 仅为骨架，不实际写入）。

## 输入
- 用户任务描述。
- `git status --porcelain`（判断工作区是否干净）。
- 仓库结构（Glob/Grep/Read 只读勘察）。
- `tools/cc_local_loop/config.json`（读取策略，如 max_rounds、受保护路径）。

## 输出
- `tools/cc_local_loop/runs/<ts>-<slug>/task.md`（结构化任务卡）。
- `tools/cc_local_loop/runs/<ts>-<slug>/STATE.json`（任务状态）。
- `tools/cc_local_loop/runs/.active_task`（活跃任务锁）。
- 说明：以上为真实运行阶段的产物；Phase B 仅交付本 SKILL.md 骨架。

## 允许行为
- 读 git 状态、读仓库结构。
- 仅向 `tools/cc_local_loop/runs/` 下写任务说明类文件。

## 禁止行为
- 改任何业务文件。
- 跑测试。
- commit / push / deploy / 删除文件。
- 工作区脏或已有活跃任务时继续开工。

## 失败时怎么停
- `git status --porcelain` 非空（工作区脏）→ 立即停，汇报脏文件，不写锁、不开工。
- 已存在 `runs/.active_task`（有未结任务）→ 立即停，提示先结束上一个任务。
- 任务描述无法形成可验收的范围 → 停并要求补充。

## 是否允许改文件
- 否。仅向 `runs/` 写任务/状态文件，不碰业务代码。

## 是否允许运行测试
- 否。

## 是否允许调用 tools/cc_local_loop/
- 仅**读** `config.json` 获取策略；不运行其中任何脚本。

## 与其他 skills 的边界
- 只做「拆解 + 门禁」，不做计划（交给 `cc-plan`）、不做实现（交给 `cc-implement`）。
- 通常由 `cc-loop` 在回环起点调用；产出的 `task.md` 是 `cc-plan` 的输入。
