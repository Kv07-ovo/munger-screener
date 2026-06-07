---
name: cc-implement
description: 半自动本地审查回环（cc-loop）中唯一允许修改业务代码的步骤。严格按 plan.md 实现当前这一轮的最小改动并自测。仅在一个 cc-loop 任务进行中、已有当轮 plan.md 时使用；不在普通对话中自动触发，绝不脱离计划改代码，绝不碰密钥与生产配置。
allowed-tools: Read Glob Grep Edit Write Bash(.venv/Scripts/python.exe -m pytest:*) Bash(python -m pytest:*) Bash(git status:*) Bash(git diff:*)
---

# cc-implement — 受计划约束的实施（唯一改码者）

## 触发方式
- 由 `cc-loop` 在拿到当轮 `plan.md` 后调用，或用户显式 `/cc-implement`。
- 前提：本任务已有当轮 `plan.md`。

## 职责
- 严格按 `plan.md` 实现**当前这一轮**的最小改动。
- 改完自测，确认本轮改动可被测试覆盖、未引入明显回归。

## 输入
- 当轮 `plan.md`。
- 上一轮 `next_cc_prompt.md`（若存在，含上轮发现的问题）。
- 相关源码。

## 输出
- 对业务代码的改动（工作区 diff）。
- 自测日志（供 `cc-collect` 收集）。

## 允许行为
- Edit / Write 业务代码（受保护路径除外）。
- 运行项目测试（pytest）。
- 读代码、读 `git status`/`git diff` 核对范围。

## 禁止行为
- 改 `.env` / 密钥 / Token / Cookie / SSH Key。
- 改生产配置。
- 改根 `.gitignore` / `package.json` / `requirements*.txt` / `pyproject.toml`（除非用户单独批准）。
- 删除文件。
- commit / push / deploy。
- 打印任何密钥值。
- 超出 `plan.md` 范围擅自扩张改动。

## 失败时怎么停
- 测试无法运行或持续失败 → 停，把失败原因写入当轮目录，交回 `cc-loop`/`cc-collect`，不强行继续。
- 发现需要改受保护路径才能完成 → 停并请求用户单独批准，不擅自改动。
- 实际所需改动超出计划范围 → 停并回到 `cc-plan` 重新规划。

## 是否允许改文件
- **是**——但仅限业务代码，且排除受保护路径（.env/密钥/生产配置/根 manifest 等）。

## 是否允许运行测试
- 是（pytest，用于自测本轮改动）。

## 是否允许调用 tools/cc_local_loop/
- 否（实现阶段不碰回环工具，审查交给 `cc-local-review`）。

## 与其他 skills 的边界
- 是六个 skill 中**唯一**改业务代码者；其余只读或只写 `runs/`。
- 上游是 `cc-plan`（提供 plan.md），下游是 `cc-collect`（收集本轮 diff 与测试结果）。
