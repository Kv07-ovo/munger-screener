# cc_local_loop Dashboard（本地只读监控面板）

一个**纯标准库、只读**的本地监控网站，用于实时观察 cc_local_loop 的运行状态：
`loop_controller` / `local_review` / `hooks` / `runs/` 产物 + 只读 git 状态。

## 1. 它是什么 / 不是什么

- **是**：一个只读的状态观察面板（GET-only HTTP + 静态页面 + 2 秒轮询）。
- **不是**：它**不**执行任务、**不**调用 Claude、**不**调用 `local_review`、
  **不**修改 `loop_controller` 状态、**不** commit/push/deploy、**不**写任何文件。
- 它只读取 `tools/cc_local_loop/runs/` 下的产物，以及必要的 `git status` / `git diff --stat`。

## 2. 启动

```
.venv\Scripts\python.exe tools\cc_local_loop\dashboard\server.py
```

可选参数：

- `--host`：默认 `127.0.0.1`（**仅允许** `127.0.0.1` 或 `localhost`）。
- `--port`：默认 `8765`。

然后浏览器打开：

```
http://127.0.0.1:8765
```

`Ctrl+C` 停止。

## 3. 安全边界（重要）

- **只监听 `127.0.0.1`**（loopback）。不绑 `0.0.0.0`。
- **不开放公网；不要反向代理；不要端口转发**。本面板没有任何鉴权，依赖 loopback 隔离。
- `Host` 头只接受 `127.0.0.1:<port>` / `localhost:<port>`，否则 `403`（缓解 DNS rebinding）。
- **GET only**：任何非 GET 请求返回 `405`。没有写 / 执行命令 / 删除 / commit / push / deploy 接口。
- **只读受控数据**：仅 `runs/` 下固定产物文件 + `dashboard/static/` + 只读 git；路径 `resolve` 后必须落在允许目录内，含 `..` / 分隔符 / 非法值一律拒绝。
- **绝不读取**：`.env` / `*.env` / `id_rsa` / `id_ed25519` / `known_hosts` / SSH 私钥 / Cookie 等敏感文件，也不提供任意仓库文件全文读取。
- **全程脱敏**：所有 JSON 输出经 `common.redact`；不打印任何 API Key / Token / SSH Key / Cookie / .env 内容。
- **长度上限**：单文件读取头部上限、日志只取尾部、diff 与列表均截断，防止超大日志卡死页面。

## 4. API（全部只读 GET）

- `GET /api/health` — `ok` / `server_time` / `bind` / `version`
- `GET /api/status` — `active` / `task_id` / `status` / `stage` / `round_index` / `max_rounds` / `last_verdict` / `backend` / `branch` / `updated_at` / `server_time`（**2 秒轮询**用）
- `GET /api/runs` — runs 任务列表（task_id / kind / status / round / is_active）
- `GET /api/current` — 当前 active task 的 STATE 摘要 + 当前轮 artifact 存在性
- `GET /api/rounds?task=<task_id>` — 每轮 plan / implement / context / review / next 是否存在 + 该轮 verdict
- `GET /api/review?task=<task_id>&round=<n>` — 解析 review.md 的 backend / model / verdict / evidence / risks / next_prompt_summary
- `GET /api/diff` — `git status` + `git diff --stat` + 短 HEAD
- `GET /api/logs?task=<task_id>&round=<n>&which=<hook|test|claude_stdout|claude_stderr|driver>` — 受控日志尾部（脱敏、截断；不存在则安全返回）
- `GET /` / `GET /static/index.html` / `GET /static/app.js` / `GET /static/style.css` — 静态页面

参数 `task` / `round` / `which` 均做白名单校验，非法直接 `400`。

## 5. 页面分区

- **Header**：项目名 / 当前状态徽标 / 刷新时间 / 连接状态
- **Task Overview**：active_task / task_id / branch / base_commit / round / status
- **Pipeline**：`start → round → implement → collect → review → decide → finish`
- **Local AI Review**：backend / model / verdict / evidence / risks / next prompt 摘要
- **Git**：status / diff --stat
- **Logs**：hook 快照 / 测试输出 / （未来）claude stdout/stderr / driver
- **Artifacts**：当前轮 context.md / review.md / next_cc_prompt.md / final_report.md 是否存在
- **Safety**：read-only / writes disabled / exec disabled / commit·push·deploy disabled / secrets redacted / 127.0.0.1 only

## 6. 和 Full Auto Driver 的关系

- 职责切分：**Driver 只写，Dashboard 只读**。
- 未来 Full Auto Driver 把每轮日志写到 `runs/<task>/round_NN/`
  （约定文件名：`claude_stdout.log` / `claude_stderr.log` / `driver.log`，写入前由 Driver 自行脱敏）。
- Dashboard 仅读取这些日志并**再次脱敏**展示；缺失文件优雅降级（显示「无该日志」），
  因此在 Driver 落地前面板即可使用。
- Dashboard 永远不回写 `runs/`、不改 `STATE.json`、不触发任何推进。
