# Compaction 指南

Compaction 是 PyClaw 用于将对话保持在 LLM 上下文窗口内的机制。当 session 增长到一定规模时，Agent 会将较早的消息压缩为摘要，同时保留最近的对话内容。

## 触发条件

当估算的 token 用量超过阈值时，Compaction 会触发：

```
estimated_tokens > context_window * compaction.threshold
```

默认阈值为 0.8（80%）。估算会应用 1.2 倍安全边际。

## 配置

所有设置位于 `pyclaw.json` 的 `agent.compaction`：

```json
{
  "agent": {
    "compaction": {
      "model": null,
      "threshold": 0.8,
      "keep_recent_tokens": 20000,
      "timeout_seconds": 900.0,
      "truncate_after_compaction": false
    }
  }
}
```

| 键 | 默认值 | 说明 |
|---|---|---|
| `model` | `null` | 覆盖用于摘要的 LLM 模型。默认使用聊天模型。 |
| `threshold` | `0.8` | 触发 Compaction 的 `context_window` 比例。 |
| `keep_recent_tokens` | `20000` | 保留不压缩的最近消息的最小 token 数。 |
| `timeout_seconds` | `900.0` | 单次 Compaction 的安全超时（15 分钟）。 |
| `truncate_after_compaction` | `false` | 若为 true，当 Compaction 后仍超过预算时硬截断剩余消息。 |

### 使用更便宜的模型做 Compaction

摘要是一项边界明确的任务，通常不需要使用最昂贵的模型：

```json
{
  "agent": {
    "default_model": "anthropic/claude-opus-4",
    "compaction": { "model": "openai/gpt-4o-mini" }
  }
}
```

## 保留内容

摘要 prompt 明确要求 LLM 原样保留以下标识符：
- UUID、Hash、ID
- 主机名、IP 地址、端口、URL
- 文件名和路径
- 模型名、session ID、commit SHA、错误码

## 行为

### 重复用户消息去重
Compaction 前，60 秒窗口内连续重复的用户消息（经 NFC + 空白折叠 + 小写归一化后比较）会被去重。短于 24 字符的消息永不去重。这处理双发场景同时不丢失 "ok" 这类短确认。

### 真实对话防护
如果 session 只包含非对话条目（心跳、系统通知），Compaction 完全跳过。避免对空闲 session 发起无意义的摘要调用。

### 超大消息 fallback
任何超过上下文窗口 50% 的单条消息会被排除在摘要之外，并替换为 `[omitted oversized message from {role}]`。防止单个超大 tool 输出撑爆摘要调用本身。

### 多阶段摘要
大型 transcript 按 token 份额切分，逐块摘要，再合并为最终摘要。

### 剥离 Tool-Result 详情
`toolResult.details`（内部元数据字段）在构建摘要 payload 前会被移除，仅对 LLM 可见内容进行摘要。

### 快照与回滚
Compaction 执行前，session tree 会被快照。若 Compaction 失败（超时、摘要错误、abort），session 会从快照恢复——绝不会留下半压缩状态。

### Token 估算健全性检查
若压缩后的 token 估算值大于压缩前，`tokens_after` 会被报告为 `None` 而非伪造值。

## Reason Codes

每个 `CompactResult` 都携带一个 `reason_code` 用于可观测性：

| 代码 | 含义 |
|---|---|
| `compacted` | 成功 |
| `no_compactable_entries` | 无可压缩内容（例如仅心跳） |
| `below_threshold` | 未达触发阈值 |
| `already_compacted_recently` | 已跳过（近期已压缩） |
| `live_context_still_exceeds_target` | 已压缩但仍超过预算 |
| `guard_blocked` | 策略/防护阻止 |
| `summary_failed` | LLM 摘要出错 |
| `timeout` | 超过安全超时 |
| `aborted` | 外部 abort 触发 |
| `provider_error_4xx` / `provider_error_5xx` | Provider HTTP 错误 |
| `unknown` | 未分类 |

## Hooks

插件可在 `AgentHook` 上注册 `before_compaction(ctx)` / `after_compaction(ctx, result)`。Hook 异常会被捕获并记录——永不会中断 Compaction。Memory 插件利用此机制在 Compaction 边界同步长期记忆。

## 另见

- `timeouts-and-abort.md` — 超时与取消配置
- `architecture-decisions.md` D17/D18 — Session DAG 与存储 Protocol
- `upstream-compaction-audit.md` — 所采用的上游行为清单
