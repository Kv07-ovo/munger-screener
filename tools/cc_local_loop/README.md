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
