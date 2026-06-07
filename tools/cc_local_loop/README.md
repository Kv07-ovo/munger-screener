# CC Local Loop（本地审查回环）— Phase 1

一个**本地、离线优先**的小工具：读取一份 `context.md`，产出 `review.md`
与 `next_cc_prompt.md`，用于在「Claude Code 改一轮 → 本地 AI 审一轮 →
人工决定是否继续」之间形成可控回环。

当前为 **Phase 1**：只搭最小可运行骨架，默认走 mock，不做真实高质量审查。

## 1. Phase 1 当前能力

- 提供 CLI：`python tools/cc_local_loop/local_review.py <context.md>`。
- 探测本地后端（Ollama / LM Studio）；探测不到则**自动降级 mock**。
- 在 `context.md` 同目录生成两份文件：
  - `review.md`：固定格式的审查结论（含 backend / verdict / Evidence / Risks 等）。
  - `next_cc_prompt.md`：下一轮交给 Claude Code 的完整提示词。
- 全程对输出做敏感信息脱敏，**绝不打印任何 API Key / Token**。
- Phase 1 **永远不会输出 PASS**（尚未实现真实审查逻辑）。

## 2. 怎么运行 local_review.py

- 前置：仓库根目录、已存在 `.venv`（用于后续测试命令）。
- 准备一份上下文文件，例如：`tools/cc_local_loop/runs/manual_test/context.md`。
- 运行（任选其一）：
  - `python tools/cc_local_loop/local_review.py tools/cc_local_loop/runs/manual_test/context.md`
  - `.venv\Scripts\python.exe tools/cc_local_loop/local_review.py tools/cc_local_loop/runs/manual_test/context.md`
- 运行后查看同目录下生成的 `review.md` 与 `next_cc_prompt.md`。
- `context.md` 中可写一行 `round_index: <数字>` 以标注当前轮次；缺省则显示 `unknown`。

## 3. mock 模式说明

- 当探测不到本地模型时，自动进入 mock。
- mock 模式下：
  - `review.md` 顶部明确标注 `backend=mock`，并带 ⚠️ 警告横幅。
  - Verdict **只能是 UNSURE**，不会是 PASS。
  - `next_cc_prompt.md` 明确声明：这是 mock 模板生成，不代表真实本地 AI 审查。
- mock 仅用于打通端到端流程，**不可据此判断代码是否安全或正确**。

## 4. Ollama / LM Studio 后续接入说明

- 默认地址（见 `config.json`）：
  - Ollama：`http://localhost:11434`
  - LM Studio：`http://localhost:1234/v1/chat/completions`
- Phase 1 仅做**基础探测 + 占位调用结构**，即使探测到也不会执行真实推理，
  verdict 仍为 UNSURE。
- 真实模型调用、证据提取与高质量审查将在 **Phase 2** 接入；
  审查者系统提示词基底见 `prompts/reviewer_system.md`。

## 5. 安全边界说明

- 纯标准库实现，**不新增任何第三方依赖**（不改 `requirements*.txt`）。
- 子进程一律 `shell=False`，无 shell 注入面。
- 所有进入 stdout / 文件的文本先经 `redact` 脱敏；**不打印密钥 / Token**。
- 工具只在 `tools/cc_local_loop/` 内读写；不触碰业务代码、`api/`、
  `web_frontend/`、`tests/`、`data/`、根 `.gitignore` 等。
- `runs/` 内的运行产物默认被 `runs/.gitignore` 忽略，不会入库。

## 6. 不会自动 commit / push / deploy

- 本工具**不会**执行 `git commit`、`git push` 或任何部署动作。
- 是否提交、是否推进到下一 Phase，均由人工显式决定。

## 7. Phase D — loop_controller.py（safe_auto 半自动回环）

### 7.1 是什么

- `loop_controller.py` 是把现有组件串起来的**确定性循环控制器**：
  `cc-intake → cc-plan → cc-implement → cc-collect → cc-local-review → verdict 闸门`，
  最多 5 轮后停下等人工验收。
- 它是**半自动（safe_auto）**：只做任务编排、状态管理、上下文收集、调用本地审查、
  应用闸门；**不改业务代码、不调用 Claude 改代码、不 commit / push / deploy**。
- 复用 `common.py` 的脱敏与安全子进程；纯标准库，不新增依赖。

### 7.2 safe_auto 半自动模式怎么用

典型一轮：

- `python tools/cc_local_loop/loop_controller.py start "<任务描述>"`
  创建任务、初始化 `round_01/`、写 `task.md` 与 `STATE.json`、写 `runs/.active_task`。
- `python tools/cc_local_loop/loop_controller.py round`
  准备本轮的 `plan_prompt.md`（仅 round 1）与 `implement_prompt.md`（每轮）。
- **由人工 / Claude 按 `implement_prompt.md` 实施最小改动并自测**（这一步控制器不代劳）。
- `python tools/cc_local_loop/loop_controller.py collect`
  收集脱敏 `context.md`（git status/diff/log + 测试输出）。加 `--no-tests` 可跳过测试采集。
- `python tools/cc_local_loop/loop_controller.py review`
  调 `local_review.py` 生成 `review.md` / `next_cc_prompt.md`，解析 verdict。
- `python tools/cc_local_loop/loop_controller.py decide`
  应用 verdict 闸门（见 7.4）。
- 任意时刻：`status` 看状态与下一步；`finish` 在安全状态收尾。
- 自检：`python tools/cc_local_loop/loop_controller.py dry-run "<描述>"`
  端到端模拟，不改业务代码、不调用 Claude、不跑真实测试、不写 `.active_task`。

### 7.3 每个命令

- `start "<任务>"`：门禁（工作区干净、无活跃任务）→ 建任务目录/round_01/task.md/STATE.json/.active_task。
- `round`：幂等地确认当前轮目录与提示词；上一轮若有 `next_cc_prompt.md` 会作为本轮输入。
- `collect [--no-tests]`：生成当前轮脱敏 `context.md`，仅写入 `runs/<task>/round_<n>/`。
- `review`：调用 `local_review.py`，解析 PASS/NEEDS_FIX/UNSURE；mock 或 UNSURE 必停。
- `decide`：按闸门走停（见 7.4）。
- `status`：显示活跃任务、当前轮、状态、最近 verdict、下一步建议。
- `finish`：仅在安全状态（等待人工验收/判断、到达最大轮次、已结束）允许；写 `final_report.md`、移除 `.active_task`。
- `dry-run "<任务>"`：Phase E 前的端到端冒烟验证。

### 7.4 verdict 闸门规则（强制 max_rounds=5）

- `PASS` → 写 `final_report.md`，状态 `WAITING_HUMAN_ACCEPTANCE`，停止；**不自动 commit**。
- `UNSURE`（含 mock 降级）→ 状态 `WAITING_HUMAN_DECISION`，停止等人工。
- `NEEDS_FIX` 且 round < max_rounds → round +1，准备下一轮 `implement_prompt.md`，**不调用 Claude**。
- `NEEDS_FIX` 且 round ≥ max_rounds → 状态 `MAX_ROUNDS_REACHED`，停止。
- `max_rounds` 从 config 读取并**硬上限 5**，无法被配置调高。

### 7.5 为什么不自动调用 Claude

- 实现一律由**人工 / 当前会话按 `cc-implement` 规则**完成，控制器只生成提示词与收集证据。
- 这样保证「改码」始终在人的监督下发生，避免无人值守地修改代码。

### 7.6 为什么不自动 commit / push / deploy

- 控制器源码中**不存在** `git commit` / `git push` / 部署命令的任何调用路径；
  唯一的子进程是只读 git（status/diff/log/rev-parse/branch）、配置的测试命令、以及 `local_review.py`。
- `STATE.json` 中 `auto_commit` / `auto_push` / `auto_deploy` / `allow_delete` 恒为 `false`。
- 是否提交 / 推送 / 部署，均由人工显式执行。

### 7.7 如何恢复中断的任务

- 任务状态持久化在 `runs/<task>/STATE.json`，活跃任务记录在 `runs/.active_task`。
- 重开终端后直接 `status` 即可看到当前轮与下一步；按提示从 `round` / `collect` / `review` / `decide` 继续。
- 控制器各步骤幂等（如 `round` 不覆盖已存在的提示词），可安全重试。

### 7.8 如何 finish 任务

- 只有在安全状态（`WAITING_HUMAN_ACCEPTANCE` / `WAITING_HUMAN_DECISION` /
  `MAX_ROUNDS_REACHED` / `FINISHED`）才允许 `finish`。
- `finish` 写 `final_report.md`、把状态置为 `FINISHED`、移除 `.active_task` 锁（这是控制器唯一会删除的文件，
  与业务/产物文件的 `allow_delete` 无关），**不** commit / push / deploy。
