# MCP 服务器（Model Context Protocol）

PyClaw 支持 Anthropic [Model Context Protocol](https://modelcontextprotocol.io)
生态。在 `pyclaw.json` 加几行配置即可让 agent 用上
`@modelcontextprotocol/server-filesystem`、`@modelcontextprotocol/server-github`
以及任何 MCP 兼容服务器的工具——和内置工具同等地位，由 Sprint 1 的权限分级
系统统一管控。

## 快速上手（filesystem 服务器，30 秒）

```json
{
  "mcp": {
    "enabled": true,
    "servers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/projects"]
      }
    }
  }
}
```

重启 PyClaw。约 5 秒后 agent 即可看到 ~11 个 filesystem 工具
（`filesystem:read_file`、`filesystem:write_file`、`filesystem:list_directory` 等）。
在任意 channel 跑 `/mcp list` 确认。

## 配置参考

```jsonc
{
  "mcp": {
    "enabled": false,
    "servers": {
      "<server-name>": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": "{env:GITHUB_TOKEN}"},
        "transport": "stdio",
        "enabled": true,
        "trust_annotations": true,
        "forced_tool_class": null,
        "forced_tier": null,
        "connect_timeout_seconds": 30.0,
        "call_timeout_seconds": 60.0
      }
    }
  }
}
```

| 字段 | 类型 | 默认值 | 用途 |
|---|---|---|---|
| `command` | string | 必填 | 启动的可执行文件（`npx` / `uvx` / 完整路径）。 |
| `args` | string[] | `[]` | 传给 `command` 的参数。 |
| `env` | object | `{}` | 子进程的环境变量。值可使用 `{env:VAR}` 占位符（从 PyClaw 进程环境解析）。 |
| `transport` | `"stdio"` | `"stdio"` | 传输方式。Sprint 2 仅支持 `stdio`。SSE / streamable-http 计入 Sprint 2.1。 |
| `enabled` | bool | `true` | 单 server 开关，无需删配置即可禁用。 |
| `trust_annotations` | bool | `true` | 为 `true` 时，从 MCP 服务器声明的 `ToolAnnotations.readOnlyHint` 推导 `tool_class`。为 `false` 时，无视 server 声明，所有工具一律视为 `tool_class="write"`。 |
| `forced_tool_class` | `"read"` \| `"write"` \| null | null | Operator 覆盖推导出的 `tool_class`。可用于强制信任 / 强制提升一个 server 的所有工具。 |
| `forced_tier` | `"read-only"` \| `"approval"` \| `"yolo"` \| null | null | 单 server 权限层级。**仅可降级（更严格）**，详见下文。 |
| `connect_timeout_seconds` | float | 30.0 | 启动时等待 `connect_to_server` 的最大秒数。 |
| `call_timeout_seconds` | float | 60.0 | 单次工具调用的最大等待秒数。 |

> **server 配置 key**（`servers` 下的字典 key）禁止包含 `:` 或 `__`。这两者
> 分别为规范的 `{server}:{tool}` 命名空间和 LLM-API `__` 重写所保留。
> Pydantic 校验会在配置载入时直接报错。

## 凭证使用 `{env:VAR}` 占位符

把密钥嵌进 `pyclaw.json` 不安全（会被 commit、会泄露）。`env` 字段支持
`{env:VAR_NAME}` 整字符串占位符，启动时从 PyClaw 进程环境解析：

```jsonc
{
  "mcp": {
    "enabled": true,
    "servers": {
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": "{env:GITHUB_TOKEN}"}
      }
    }
  }
}
```

启动时设置变量：

```bash
GITHUB_TOKEN=ghp_xxx ./scripts/start.sh
```

占位符正则是 `^\{env:([A-Z_][A-Z0-9_]*)\}$`（大写 / 下划线 / 数字，首字符
非数字）。**仅匹配整字符串**——`"prefix-{env:VAR}"` 这种部分匹配会原样
透传（设计取舍：拒绝"魔法"式部分替换）。小写变量名也原样透传。

env 变量缺失时，对应 server 标记为 `failed` 且消息清晰：
`MCP server 'github' failed: env var referenced in 'GITHUB_TOKEN' is not set`。
其他 server 继续连接，PyClaw 进程不挂。

## 与权限分级的集成

每个 MCP 工具在注册时即推导出 `tool_class`：

```
forced_tool_class（operator 设置）            # 优先
  → trust_annotations 且 readOnlyHint        # → "read"
  → "write"                                  # 安全默认
```

权限分级评估**逐 call** 进行：

| 用户当前 tier | server 的 `forced_tier` | 实际 tier |
|---|---|---|
| 任意 | 未设 | 用户当前 tier |
| `yolo` | `"approval"` | `"approval"`（降级生效：`RANK[approval]=1 > RANK[yolo]=0`） |
| `yolo` | `"read-only"` | `"read-only"` |
| `approval` | `"read-only"` | `"read-only"` |
| `approval` | `"yolo"` | **保持 `"approval"`**——`forced_tier` 不能升级权限 |
| `read-only` | 任意 | 保持 `"read-only"`——用户最严格的选择优先 |

> **`forced_tier` 仅可降级。** 在不可信 server 上配 `forced_tier="yolo"`
> **不能** bypass 用户选择的 `approval`。这防止 server 配置 bug 静默削弱
> 用户的权限闸门。审计日志只在强制 tier **严格更严格**（实际生效）时
> 才记录 `tier_source="forced-by-server-config"`。

进入 `approval` 分支的工具调用，runner 给 hook 传**规范名**
（`github:search_issues`），而不是 LLM-form 名（`github__search_issues`）。
配置 `tools_requiring_approval: ["github:search_issues"]` 的 operator 可以
看到预期的匹配。`forced_tier="approval"` 调用直接绕过
`tools_requiring_approval` 白名单——该 server 的所有工具都被 gate。

## `/mcp` 斜杠命令

| 子命令 | 用途 |
|---|---|
| `/mcp list` | 每个 server 一行：name / status（connected / failed / disabled / pending）/ tool count / last_connect_at。Header 显示聚合计数和 `is_ready()`。 |
| `/mcp restart <name>` | 原子地切换一个 server 的 adapter。每 server 的 `asyncio.Lock` 串行化与 supervisor / 死亡检测的并发。 |
| `/mcp logs <name>` | server 标准错误的最近 ~3000 字符，**带秘密 redact**（任何在该 server 已解析 env 字典里的值都会被替换为 `<REDACTED>`）。 |

`/mcp list` 流式中也可用。Web 和飞书 channel 都支持。

## 非阻塞启动

PyClaw 把 MCP server 连接放进**后台 supervisor task**——FastAPI lifespan
**不会**为单 server 连接阻塞。`/health` 端点立即 200 OK，body 含 advisory
`mcp` 字段：

```json
{
  "status": "ok",
  "mcp": {
    "ready": false,
    "n_connected": 1,
    "n_failed": 0,
    "n_pending": 2,
    "n_disabled": 0,
    "total_tools": 11
  }
}
```

k8s readiness probe 不应把 MCP 连通性当 gate——MCP server 全挂 PyClaw 仍然
存活。完整论证见 `design.md` D7（6 个部署场景 + 6 个 trade-off）。

一个直接后果：PyClaw 启动后**第一条聊天**可能看到**部分工具集**（如 8 个
builtin + 11 个快 server 的工具，慢 server 还没连完）。后续 iteration 自动
看到新连上的工具。要确定性等待，可以 poll `/health` 直到 `mcp.n_pending == 0`，
或跑 `/mcp list`。

## 失败处理

* **server 调用中崩溃** —— adapter 内部抛 `MCPServerDeadError`；
  dispatcher 内部捕获（**不会**逃出 `_dispatch_single`），通过
  `task_manager.spawn` 非阻塞调度 `_handle_server_death`。Server 转为
  `failed` 状态，所有 adapter 注销。当前调用返回错误 `ToolResult`
  （`MCP server 'github' is unavailable. Removed from this conversation. Use
  /mcp restart github to retry.`），**并行 sibling 调用不受影响**。

* **`/mcp restart` 失败** —— 规范的"更安全默认"语义：重连失败时，
  **旧** adapter 也会被移除、server 标 `failed`。**生产关键 server 的
  restart 请挑低峰期**——重启失败会比重启前还差。

* **自动重启** —— Sprint 2 不实现。Operator 修完根因（凭证 / 网络 / 等）
  后手动 `/mcp restart`。带 backoff 的自动重启列入 Sprint 2.1 候选。

## 安全要点

* **`ToolAnnotations` 是提示，不是保证。** 一个声明 `readOnlyHint=true`
  的 server 仍然可能改状态。对于不完全审计过的 server，要么设
  `trust_annotations: false`，要么 `forced_tool_class: "write"` 强制走
  写权限闸门。

* **`{env:VAR}` 替换仅作用于 `env` 字段。** 它**不会**替换到
  `command` / `args`——argv 暴露的密钥在 `ps` 输出里也能看到，所以这是
  设计取舍。需要模板化命令，请用 wrapper 脚本。

* **`/mcp logs` redact 是 best-effort。** 它会把已解析 env 字典里的所有
  值替换成 `<REDACTED>`。**不会**捕捉派生哈希、base64 片段、或 server
  打印时改造过的密钥。`/mcp logs` 输出贴公开 issue 之前请人工 review。

* **审计日志字段。** 每条 MCP 工具的审批决策都会记录 `tier_source`
  （`"per-turn"` / `"channel-default"` / `"forced-by-server-config"`）。
  Forced 决策还包含 `forced_server`。怀疑某 server 行为异常时，消费审计
  日志做事后 forensics。

## 故障排查

| 症状 | 可能原因 | 修复 |
|---|---|---|
| `/mcp list` server `failed`，原因 `connect timeout (30s)` | 首次启动正在下载 npm 包 | 等一下，再 `/mcp restart <name>`。或预热 npm 缓存：`npx @modelcontextprotocol/server-foo --help`。 |
| `/mcp list` `failed`，原因 `env var referenced in 'X' is not set` | env var 缺失 | 在 PyClaw 进程环境设置（systemd `EnvironmentFile=`、docker `--env-file`、k8s Secret） |
| `/mcp list` `failed`，原因含 `tool name collision` | 两个 server 暴露了同名 remote tool | 禁用其中一个（`enabled: false`），或用 `forced_tool_class` 重命名（计入 Sprint 2.1） |
| Agent 找不到 LLM 调用的 MCP tool | 工具名跟现有 builtin 撞名，或 server 的 tool list 含 `:`（这些会被跳过） | 跑 `/mcp logs <name>` 找 "rejected: tool name contains ':'" |
| `/mcp restart` 报成功但 `/mcp list` 仍是 failed | Race 窗口：新连接其实又失败了。跑 `/mcp logs <name>`。 | 排查根因；规范的"更安全默认"语义会在 restart 失败时移除 adapter |
| Web UI approval modal 显示丑陋的 `__`-form 名字（`filesystem__read_file`）而非 `:`-form | Bug：runner 应该把 canonical 名传给 hook | v4 之后不应该出现——发 issue |
| `/health` `mcp.n_pending=N` 长时间不下降 | 慢 npm registry / 网络抖动 | 等，或 `/mcp restart`。PyClaw 始终保持可用。 |

参见：[权限与工具审批](permissions.md)、[配置参考](configuration.md)、
[部署指南](deployment.md)。
