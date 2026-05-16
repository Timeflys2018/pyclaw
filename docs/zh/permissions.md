# 权限与工具审批

PyClaw 让你控制 agent 的自治程度。三档权限 tier + `tools_requiring_approval`
白名单一起,在安全和效率之间取得平衡。

> 为何现在做:之前 tool approval 在协议层接通了,但**运行时从未注入 hook**,
> 所有 web 用户在不知情中跑 YOLO。README 之前称 ✅ shipped 是 overclaim。
> 这份文档描述 Sprint 1 ship 后的真实行为。

## 三档权限 tier

| Tier | 行为 | 典型场景 |
|------|------|---------|
| `read-only` | 写类工具 (`bash`, `write`, `edit`, `forget`) **不弹审批,直接拒绝**。读类工具 (`read`, `grep`, `glob`, `skill_view`, `update_working_memory`) 和 `memorize` 自由执行 | 调研、走代码、头脑风暴。Agent 能记笔记 (`memorize`) 但不能动你的 workspace |
| `approval` | `tools_requiring_approval` 里的工具触发 channel 审批流(Web modal / 飞书 CardKit 卡片),其他工具自动放行 | **默认值**。日常工作的安全基线 |
| `yolo` | 所有工具直接放行,审批门完全失效 | 可信自动化、批处理、一次性沙盒 |

用户**逐 turn**选 tier,后端不持久化。前端把选择记在 `localStorage`,刷新页面后保持一致。

## 工具分类

每个内置工具声明 `tool_class`,read-only tier 据此分流:

| Class | 工具 | Read-only 行为 |
|-------|------|---------------|
| `read` | `read`, `grep`, `glob`, `skill_view`, `update_working_memory` | 自由执行 |
| `memory-write-safe` | `memorize` | 自由执行(memory 子系统自带 guard) |
| `write` | `bash`, `write`, `edit`, `forget` | 自动拒绝 |

read-only 拒绝写类工具时,agent 收到:

```
Tool '<name>' is not available in read-only mode. (Mode can be changed in the input footer.)
```

措辞**故意中性** —— agent 不会主动建议用户切档放低安全;括号里的提示给人类用户保留了发现性。

## 配置

`pyclaw.json`(或环境变量 override)控制每 channel 的行为:

```jsonc
{
  "channels": {
    "web": {
      "enabled": true,
      "defaultPermissionTier": "approval",      // "read-only" | "approval" | "yolo"
      "toolApprovalTimeoutSeconds": 60,         // 超过 N 秒自动拒绝
      "toolsRequiringApproval": ["bash", "write", "edit"]
    },
    "feishu": {
      "enabled": true,
      "defaultPermissionTier": "approval",
      "toolApprovalTimeoutSeconds": 60,
      "toolsRequiringApproval": ["bash", "write", "edit"]
    }
  }
}
```

两个 channel 共享同一套默认值。如果你也想门控 `forget`(销毁性记忆操作),把它加进
`toolsRequiringApproval`。`approval` tier 下把 `toolsRequiringApproval` 设为 `[]`
等价于 yolo(对被门控的工具而言)。

## 各 channel 的审批呈现

### Web channel

- 前端 WebSocket 发 `chat.send` 时显式带 `tier` 字段;默认值通过
  `/api/web/settings` 从 `WebSettings.default_permission_tier` 取
- 触发门控工具时,服务端发 `tool.approve_request`,前端弹 modal:工具名 / 参数 /
  reason / **tier badge**(让用户清楚是哪一档触发的)
- 用户点 Approve / Reject,runner 解锁,要么执行工具要么跳过(附拒绝错误)
- **Per-turn 切档**:中途切 tier 在下一条消息生效;已弹的审批保留原 tier

### 飞书 channel

- 推一张 CardKit interactive 卡片,两个按钮(✅ Approve / ❌ Deny)+ 60 秒倒计时,
  每 5 秒 patch 一次
- 卡片 `schema: "2.0"` + `update_multi: true`,审批生命周期内 PATCH 都能生效
- **Originator-only 授权**:发起 agent 的用户的 open_id 嵌在每个按钮的 `value` 里。
  只有这个用户的点击算数;群里其他人点了会看到 toast(`Only the originator can
  approve/deny this action.`),卡片不变
- 决策或超时时,卡片 PATCH 到终态(`✅ Approved by ou_xxx at HH:MM:SS` /
  `❌ Denied` / `⌛ Timed Out`),按钮消失
- **生效前提**:飞书 app 必须在开发者后台订阅 `card.action.trigger` 回调
  (Events & Callbacks)

### 各 channel 能力对比

| 能力 | Web | 飞书 |
|------|-----|------|
| 三档控制 | ✅ | ✅ |
| Per-turn 切档 | ✅ 通过 `chat.send.tier` | 沿用 channel 默认 |
| 用户主动审批 | ✅ Modal | ✅ CardKit 卡片 |
| Originator-only 授权 | 不适用(单用户 WS) | ✅ |
| 倒计时显示 | 隐式(60s) | 可见(5s 间隔 patch) |
| Audit log | ✅ 结构化 JSON | ✅ 结构化 JSON |

## 审批日志

每次决策(自动或人为)在 `pyclaw.audit.tool_approval` logger 上发一条 INFO 级 JSON 行:

```json
{
  "event": "tool_approval_decision",
  "ts": "2026-05-16T10:30:45Z",
  "conv_id": "conv_abc",
  "session_id": "web:alice:c1",
  "channel": "web",
  "tool_name": "bash",
  "tool_call_id": "call_42",
  "tier": "approval",
  "decision": "approve",
  "decided_by": "user_alice",
  "decided_at": "2026-05-16T10:30:50Z",
  "elapsed_ms": 5333,
  "user_visible_name": "@alice"
}
```

`decided_by` 取值:

| 值 | 含义 |
|----|------|
| `auto:not-gated` | 工具不在 `tools_requiring_approval`,自动放行 |
| `auto:read-only` | 写类工具因 tier 是 read-only 被自动拒 |
| `auto:yolo` | yolo 隐式直通(很少发出;runner 在 yolo 下跳过 logging) |
| `auto:timeout` | 审批超时,无用户响应 |
| `auto:post-failed` | channel 推送审批 prompt 失败(比如 CardKit API 异常) |
| `<user-id>` | 审批者的 Web `user_id` 或飞书 `open_id` |

Sprint 1 的实现**只把 audit 行写到 logger sink**(各部署自己的 stdout / 文件 /
journald)。要长期保留请接你自己的日志聚合(Loki / ELK / 云日志)。持久化存储
(Redis sorted set / SQLite)作为 Sprint 1.1 follow-up TA2 跟踪。

## Tier 切换书签

除了每条决策行,会话每次切换 permission tier 时(中途切档)还会发一条单独的行:

```json
{
  "event": "permission_tier_changed",
  "ts": "2026-05-16T10:30:45Z",
  "session_id": "web:alice:c1",
  "channel": "web",
  "from_tier": "approval",
  "to_tier": "yolo",
  "user_id": "alice"
}
```

用这条可以一行 grep 回答 "这个 session 什么时候切到 yolo 了" — 不用扫每条工具决策行。

## 日志级别 + 运营建议

Audit 行在 `pyclaw.audit.tool_approval` logger 上以 **INFO 级别**发出。这跟 sudo、
AWS CloudTrail、Spring Boot Actuator、Vault 的传统一致:audit 是特权操作记录,
**生产必须送达 sink**,所以绝对不放 DEBUG。

如果你想在生产屏蔽其他库的 INFO 噪声(比如 lark-oapi)但保留 audit 行,**单独**
给 audit logger 配置,让它的 level 跟 root 解耦:

```python
import logging
audit_logger = logging.getLogger("pyclaw.audit.tool_approval")
audit_logger.setLevel(logging.INFO)
audit_logger.propagate = True  # 让 root handler 收到
```

ELK / Loki / Splunk pipeline 过滤建议:`event=tool_approval_decision OR event=permission_tier_changed`
精确捞出 audit 流。

## 与 D26 隔离模型的关系

工具审批是**per-session** 的。同一个 deployment 上的两个用户有独立的对话流水线、
独立的审批队列、独立的 audit 流。飞书的 originator-only 检查防止群内跨用户审批,
但**跨租户隔离**(多团队 SaaS)仍需要
[D26: 用户隔离模型](./architecture-decisions.md#d26-user-isolation-model--personal-assistant-not-multi-tenant-saas)
描述的升级路径。

## MCP 集成（Sprint 2）

MCP 导入的工具与内置工具**共用同一个**审批 gate——不分叉。Per-server 配置
可覆盖 `tool_class` 推导（`forced_tool_class`）并强制更严格的 tier
（`forced_tier`，**仅可降级**）。完整集成契约见 [MCP 服务器](./mcp.md)，
包含 per-call tier 评估算法和 `tier_source="forced-by-server-config"`
审计日志标记。

## 已知限制(KNOWN-ISSUES.md 跟踪)

- **TA1** per-tool glob(如 `bash:rm -rf *: deny`)— Sprint 1.1 follow-up
- **TA2** 持久化 audit log — Sprint 1.1 视需求决定
- **TA3** per-user 权限 profile — 跟多租户一起做
- **TA6** CardKit 倒计时 rate-limit(50 并发上限)— 持续观察

## 卡死的审批怎么救

如果飞书回调订阅没配好,卡片点击会无响应。两种办法:

1. 等 60 秒超时自动拒
2. 重启 PyClaw —— 审批 registry 是 in-memory 的,重启即清空,对应的 agent run
   会拿到 timeout deny

排查:飞书开发者后台是不是没有 `card.action.trigger` 事件流入。修复:在
Events & Callbacks → Subscribed Callbacks 订阅这个回调,WebSocket 模式还要
开启长连接。
