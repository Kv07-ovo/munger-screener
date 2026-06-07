# cc_local_loop Hooks（Claude Code 安全层）— Phase C

本目录是 cc_local_loop 半自动回环的 **Claude Code hooks 安全层**。
由项目级 `.claude/settings.json` 引用，不修改 `.claude/settings.local.json`。

## 1. guard_pretooluse.py 的职责

- 绑定到 **PreToolUse** 事件。
- 读取 hook 的 stdin JSON，识别 `tool_name` 与 `tool_input`，对**危险命令**和
  **受保护路径修改**输出 deny（`permissionDecision: deny`，exit 0）。
- 安全命令/普通文件编辑：不输出，静默放行，交回正常权限流程。
- **核心规则硬编码**在脚本内（`BUILTIN_*`），`config.json` 只能**增加**规则、
  无法删弱核心——被篡改的 config 也无法重新放行 `rm -rf` / `git push` / 改 `.env`。
- 所有 deny 理由经 `common.redact` 脱敏；**绝不回显任何密钥值**。
- 内部异常默认 **fail_closed**（拦截），可由 `config.json` 的 `hook_fail_mode` 切换。

## 2. collect_onstop.py 的职责

- 绑定到 **Stop** 事件，在 Claude 每轮结束响应时做**轻量快照准备**。
- Phase C 只是安全骨架：**不做**完整 collect、**不**续跑、**不**调用模型。
- 仅当存在 `tools/cc_local_loop/runs/.active_task` 时工作；否则 `exit 0` 直接退出。
- 只向 `runs/` 下追加一行脱敏的时间戳快照标记；不碰任何业务文件。
- 快照写入前一律经 `redact` 脱敏：正常走 `common.redact`；即便 `common.py` 无法导入，
  也使用本地 **fallback redact**（标准库正则，覆盖 `sk-`/`ghp_`/`hf_`/`xox`/`AKIA`、
  `Bearer`、`api_key=`/`token=`/`password=`/`secret=` 等），**绝不是 no-op**，
  确保任何降级路径下密钥都不会落入快照。

## 3. 拦截规则列表

命令类（Bash / PowerShell，匹配命令字符串）：
- `git push`、`git reset --hard`、`git clean -fd` / `-xdf`
- `rm -rf`（含 `-fr` / 分开的 `-r ... -f`）
- `Remove-Item -Recurse -Force`（任意顺序）、`del /s`、`rmdir /s` / `rd /s`
- deploy 类：行首 `deploy`、`vercel`、`netlify deploy`、`render deploy`，
  以及 config 追加的 `npm run deploy` / `fly|wrangler|serverless|sls|eb deploy` 等
- 密钥读取/打印：`cat/type/Get-Content .env`、`printenv`、`env |`、
  `Get-ChildItem env:`、`echo $ANTHROPIC_API_KEY` / `$env:*KEY` 等

路径类（Edit / Write / MultiEdit / NotebookEdit，匹配文件名）：
- 机密（**永不允许**）：`.env` / `*.env` / `.env.*`、`id_rsa` / `id_ed25519` /
  `known_hosts` / `authorized_keys`、`*.pem` / `*.key` / `*.ppk`、`*cookie*`
- 默认受保护清单（**需用户单独批准后才能改**）：根 `.gitignore`、`package.json`、
  `package-lock.json`、`requirements*.txt`、`pyproject.toml`

说明：清单类文件属于「默认受保护」，不是永久不能改。确需修改时，必须先获得用户
单独批准（参照 Phase A/B 的受保护文件批准流程），再临时放行。

## 4. 如何本地手动测试 hook

向脚本喂模拟的 hook JSON（命令行不要直接出现危险字符串，建议用文件喂 stdin）：

- 安全命令应放行（无输出、退出 0）：
  - `{"tool_name":"Bash","tool_input":{"command":"git status"}}`
- 危险命令应 deny（输出含 `permissionDecision":"deny"` 的 JSON）：
  - `{"tool_name":"Bash","tool_input":{"command":"git push"}}`
- 危险路径应 deny：
  - `{"tool_name":"Write","tool_input":{"file_path":".env"}}`
- Stop hook：
  - 无 `runs/.active_task` 时：`exit 0`，不写任何文件。
  - 有 `runs/.active_task` 时：仅向 `runs/` 写一行快照标记。

运行解释器建议使用项目 venv：`.venv\Scripts\python.exe`。

## 5. 为什么 Stop hook 不自动续跑

- 自动续跑（在 Stop hook 里 `decision: block`）极易形成 **runaway 自循环**，
  是整套系统最容易失控的点。
- 因此 Stop hook **只做快照**、永不阻塞、永不触发下一轮。
- 推进轮次是 `loop_controller.py`（Phase D）与人工闸门的职责，不属于 Stop hook。
- 额外用 `stop_hook_active` 字段做防递归：已在 stop-hook 轮次内则直接退出。

## 6. 安全边界

- hooks 是**纵深防御网**，不是硬沙箱：若解释器缺失，harness 无法运行 hook，
  此时会在 harness 层 fail-open。真正的主控仍是 `loop_controller.py`（从不发出
  危险命令）+ skills 的「禁止行为」约束。
- guard 与 controller 共享 `config.json` 同一份策略（protected_paths /
  command_denylist / secret_env_names），一份策略、两处执行。
- 脚本纯标准库 + 复用 `common.py`，不新增第三方依赖。

## 7. 如何临时禁用 hooks

- 禁用 Stop hook：把 `config.json` 的 `"stop_hook_enabled"` 设为 `false`。
- 临时禁用全部 hooks：移除/重命名 `.claude/settings.json`（不要改
  `settings.local.json`），或在该文件中暂时清空 `hooks`。
- 调整失败策略：`config.json` 的 `"hook_fail_mode"` 在 `fail_closed`（默认，
  更安全）与 `fail_open`（更不打断）之间切换。

## 8. 不自动 commit / push / deploy

- 这两个 hook **不会**执行 `git commit` / `git push` / 任何部署。
- guard 反而会**拦截** push 与 deploy 类命令；collect_onstop 只读事件、只写
  `runs/` 快照。是否提交/推进由人工显式决定。
