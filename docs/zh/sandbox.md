# 沙箱（Sandbox）

Sprint 3 引入了基于 Anthropic
[`@anthropic-ai/sandbox-runtime`](https://www.npmjs.com/package/@anthropic-ai/sandbox-runtime)
（`srt` 1.0.0+）的可选进程隔离层，作用于 `BashTool` 与 MCP 子进程。**默认策略
是 `none`**——已有部署升级到 Sprint 3 后行为与 Sprint 2.0.1 字节级一致。

## TL;DR — 三种后端

| `sandbox.policy` | 行为 | 适用场景 |
|---|---|---|
| `none`（默认） | 无隔离；继承父进程 env；与 Sprint 2.0.1 字节级一致 | 本地开发、可信单用户 |
| `srt` | 用 `srt` 包装 `BashTool` 与（按需）MCP 服务（macOS Seatbelt + Linux bwrap+seccomp） | 多用户、对外服务、合规场景 |

## 快速开始

1. 安装运行时（需要 Node.js，全平台支持）：

   ```bash
   npm install -g @anthropic-ai/sandbox-runtime
   srt --version   # → 1.0.0
   ```

2. 修改 `pyclaw.json`：

   ```json
   {
     "sandbox": {
       "policy": "srt",
       "productionRequireSandbox": false,
       "defaultFilesystem": {
         "allowWrite": ["~/.pyclaw/workspaces"],
         "denyRead": ["~/.ssh", "~/.aws", "~/.config"],
         "denyWrite": []
       },
       "defaultNetwork": {
         "allowedDomains": ["api.anthropic.com"],
         "deniedDomains": ["169.254.169.254"]
       },
       "defaultEnvAllowlist": []
     }
   }
   ```

3. 重启 PyClaw 并验证：

   ```bash
   curl -s localhost:8000/health | jq .sandbox
   # { "ready": true, "backend": "srt", "srt_version": "1.0.0", "warning": null }
   ```

4. 用 admin 身份发送 `/admin sandbox check`，可看到沙箱状态与每个 MCP server 的
   配置摘要。

## `srt` 1.0.0 必填 schema

PyClaw 每次调用都会生成 `srt-settings.json`，**所有必填字段都已填充**。注意：

- `network.allowedDomains` 必须列出**具体域名**。`["*"]` 会被 `srt` 拒绝。
- `network.deniedDomains` 必须存在（PyClaw 默认 `["169.254.169.254"]`，避免
  cloud-metadata IMDS 信息泄露）。
- `filesystem.{allowWrite, denyRead, denyWrite}` 三项都必须存在（空数组 `[]`
  也行，但不能省略）。

## 按用户覆盖

`UserProfile.sandbox_overrides`（通过 `pyclaw.json` 或 `/admin user set` 设
置）会**追加**到部署默认上，而不是替换：

```json
{
  "channels": {
    "web": {
      "users": [
        {
          "id": "alice",
          "password": "...",
          "role": "admin",
          "sandbox_overrides": {
            "filesystem": { "allowWrite": ["/tmp/alice-projects"] },
            "network": { "allowedDomains": ["registry.npmjs.org"] }
          }
        }
      ]
    }
  }
}
```

## 硬编码 Env Deny Floor（4-slot review F10 修复）

`build_clean_env(allowlist=...)` 会**强制**剥离一组凭证环境变量，**不受**
allowlist 影响。这避免 admin（或被盗的 admin token）通过
`env_allowlist=["*"]` 意外泄露密钥。

**始终被剥离**的变量：

- `ANTHROPIC_API_KEY`、`ANTHROPIC_*`、`PYCLAW_*`、`LITELLM_*`（PyClaw / 模型
  凭证）
- `OPENAI_API_KEY`
- `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、`AWS_SESSION_TOKEN`（AWS；
  `AWS_REGION` 若显式列出可通过）
- `SSH_AUTH_SOCK`、`SSH_AGENT_PID`
- `GITHUB_TOKEN`、`GH_TOKEN`
- `KUBECONFIG`、`KUBE_TOKEN`

`env_allowlist=["AWS_*"]` 在**配置加载时直接报错**——必须改成具体名字（如
`AWS_REGION`）。

## MCP Server 沙箱

每个 MCP server 的 `sandbox.enabled` 默认值由 `command` 决定：

| 命令 basename | 默认 | 原因 |
|---|---|---|
| `npx`、`uvx` | `false` | 自动豁免——沙箱会阻断 npm registry 探测（4-slot review F1） |
| 其他（本地 binary 路径） | `true` | 本地二进制路径默认开启隔离（Sprint 3 D6） |

运维可以显式覆盖：

```json
{
  "mcp": {
    "enabled": true,
    "servers": {
      "fs": {
        "command": "/usr/local/bin/mcp-server-fs",
        "args": ["/tmp"],
        "sandbox": { "enabled": true }
      },
      "github": {
        "command": "npx",
        "args": ["-y", "@github/mcp-server"],
        "sandbox": { "enabled": false }
      }
    }
  }
}
```

当 `sandbox.enabled=true` 但 `srt` 不可用时，对应 server 在 `/health.mcp` 与
`/admin sandbox check` 标记为 `failed`，但**不会阻塞 PyClaw 主进程启动**。
其他 server 仍然可用。

## Sprint 2 → Sprint 3 升级

如果你从 Sprint 2.0.1 升级且 `pyclaw.json` 里**没有** `sandbox` 段：

1. **不会破坏现有功能**：默认 `sandbox.policy="none"`，与 Sprint 2.0.1 字节
   级一致。
2. **MCP 默认值变化**：
   - `command="npx" / "uvx"` 配置：仍然无沙箱（auto-exempt）。启动时 PyClaw
     输出 INFO 日志说明原因。
   - `command="<本地 binary 路径>"` 配置：启动时 PyClaw 输出 WARNING，因为
     `sandbox.enabled` 默认变为 `true`。要么安装 `srt`，要么显式设置
     `sandbox: { enabled: false }` 关闭。
3. **真正升级到沙箱**：安装 `srt`、设置 `sandbox.policy="srt"`、重启。
   `/admin sandbox check` 会指出还需要哪些调整。

## 紧急 Override

如果 `srt` 强制开启（`productionRequireSandbox=true`）但运维需要在没装 `srt`
的情况下临时启动服务（比如 npm install 中途失败）：

```bash
PYCLAW_SANDBOX_OVERRIDE=disable .venv/bin/uvicorn pyclaw.app:app
```

PyClaw 会输出 CRITICAL 警告，且 `/health.sandbox.warning` 会带上 override 提
示便于审计。**生产启动脚本里不要常驻这个变量。**

## 故障排查

| 现象 | 可能原因 | 解决 |
|---|---|---|
| `/health.sandbox.backend = "none"` 但配了 `policy="srt"` | `srt` 不在 PATH 上 | `npm install -g @anthropic-ai/sandbox-runtime` |
| MCP server 状态 `failed: srt-config: …` | 生成的 `srt-settings.json` 被 `srt` 拒绝 | 看日志；常见是 `network.allowedDomains` 含 `"*"` 或必填字段缺失 |
| `npx` MCP server 拉包失败 | npx + 沙箱 + 没加 registry 域名 | 要么 `sandbox.enabled=false`，要么把 `registry.npmjs.org` 加到 `network.allowedDomains` |
| `bash` 读 `~/.aws/credentials` 报 permission denied | 设计如此——沙箱 `denyRead` 阻止凭证路径 | 合法需求请把具体路径加到 `sandbox_overrides.filesystem.allowRead` |

## 审计追踪

每条沙箱下的工具决策都会写一条 audit 日志，包含 `sandbox_backend` 字段。例：

```bash
journalctl -u pyclaw | grep tool_approval_decision | jq 'select(.sandbox_backend == "srt")'
```
