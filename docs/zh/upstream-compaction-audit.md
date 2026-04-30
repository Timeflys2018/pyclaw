# 上游 OpenClaw Compaction 子系统 — 审计参考

来源: `github.com/openclaw/openclaw` HEAD `388019f5b6`（已同步到 `~/CascadeProjects/selfLearning/openclaw`）。本文档作为 PyClaw `harden-agent-core` change 的参考基线。

## 范围

上游在 `src/agents/pi-embedded-runner/` 和 `src/agents/` 下有约 22 个专门处理 compaction 的文件。PyClaw 当前只有一个 `compaction.py`，实现是朴素的"切点 + LLM 总结"。本文档列出所有应当被 PyClaw 评估的上游概念。

## 生命周期概览

```
触发 (budget / overflow / manual)
  ↓
compactEmbeddedPiSession() ── 入队 session lane + global lane
  ↓
ContextEngine 拥有 compaction? ──是──> contextEngine.compact() + maintenance
  ↓ 否
compactEmbeddedPiSessionDirect()
  ├─ 获取 session write lock
  ├─ Sanitize: sanitize → validate → dedupe → limit → repair pairing
  ├─ 构建 hook metrics (original + current counts)
  ├─ 触发 before_compaction hooks
  ├─ Guard: 如果没有真实对话则跳过
  ├─ compactWithSafetyTimeout(session.compact(), 15min, {abortSignal, onCancel})
  ├─ 如果是 manual: hardenManualCompactionBoundary()
  ├─ 可能 rotateTranscriptAfterCompaction() (successor 文件)
  ├─ 保存 checkpoint (回滚快照)
  ├─ 触发 after_compaction hooks
  └─ runPostCompactionSideEffects (transcript update 事件 + memory sync)
```

## 文件清单 (上游)

| 文件 | 用途 | LoC |
|------|------|-----|
| `compact.ts` | 主 direct compaction runtime | 1273 |
| `compact.types.ts` | 参数 + metrics 类型 | 83 |
| `compact.queued.ts` | 队列入口 + lane queueing + ContextEngine 委托 | 334 |
| `compact.runtime.ts` | 懒加载 proxy | 15 |
| `compact.hooks.test.ts` | Hook 生命周期测试 | 1122 |
| `compaction-duplicate-user-messages.ts` | 60 秒窗口重复检测 | 109 |
| `compaction-hooks.ts` | before/after hook 编排 + 副作用 | 308 |
| `compaction-runtime-context.ts` | 运行时上下文 + 压缩目标解析 | 127 |
| `compaction-safety-timeout.ts` | 15 分钟超时 wrapper + abort 集成 | 93 |
| `compaction-successor-transcript.ts` | 压缩后 transcript 轮转 | 282 |
| `compact-reasons.ts` | 原因码分类 (10+ 类) | 76 |
| `empty-assistant-turn.ts` | 零 token 空响应检测 | 57 |
| `context-engine-maintenance.ts` | 压缩后 + 回合后 maintenance | 651 |
| `manual-compaction-boundary.ts` | /compact 手动边界硬化 | 117 |
| `src/agents/compaction.ts` | 共享总结算法 | 579 |
| `src/agents/compaction-real-conversation.ts` | 真实对话启发式 | 85 |

## 核心算法

### 多阶段总结 (`compaction.ts`)
- `BASE_CHUNK_RATIO = 0.4`, `MIN_CHUNK_RATIO = 0.15`
- `SAFETY_MARGIN = 1.2` 应用于所有 token 估算
- `SUMMARIZATION_OVERHEAD_TOKENS = 4096` 预留给 prompt overhead
- `summarizeInStages`: 按 token 份额分块 → 每块总结 → 合并总结
- `summarizeWithFallback`: 完整 → 部分（排除超大消息）→ 仅注释
- `splitMessagesByTokenShare`: 分块时保留 tool_use/tool_result 配对

### 标识符保留策略
三种模式:
- `"strict"` (默认) — 保留 UUID、hash、ID、hostname、IP、端口、URL、文件名
- `"custom"` — 用户自定义指令
- `"off"` — 不做特殊处理

### 重复用户消息去重
- 窗口: 默认 60 秒 (可配)
- 最小长度: 24 字符 (更短的 ack 永不去重)
- 归一化: 空白压缩 → NFC → 小写
- Key: 归一化文本 → `lastSeenAt` 时间戳

### 安全超时
- 默认: 900_000 ms (15 分钟)
- 配置: `agents.defaults.compaction.timeoutSeconds`
- 赛跑: 用户 compaction 函数 vs 超时信号 vs 外部 abort 信号
- 触发: 调用 `onCancel` → `session.abortCompaction()` — 幂等

### 原因码
- `no_compactable_entries`
- `below_threshold`
- `already_compacted_recently`
- `live_context_still_exceeds_target`
- `guard_blocked`
- `summary_failed`
- `timeout`
- `provider_error_4xx` (400/401/403/429)
- `provider_error_5xx` (500/502/503/504)
- `unknown`

### Hooks

内部事件: `session:compact:before` / `session:compact:after`

插件 hooks:
- `before_compaction` — 接收 `{messageCount, tokenCount}` + 运行时上下文
- `after_compaction` — 接收 `{messageCount, tokenCount, compactedCount, sessionFile}` + 上下文

Hook 异常被捕获记录，不会中止 compaction。

### 压缩后 Memory Sync
三种模式 (`agents.defaults.memorySearch.sync.postCompactionMode`):
- `"off"` — 不同步
- `"async"` — fire-and-forget
- `"await"` — 阻塞直到完成

需要 `postCompactionForce: true` 才真正运行。

## 哪些不会直接迁移到 PyClaw

| 上游特性 | 为什么不迁移 |
|---------|-------------|
| pi-coding-agent DAG session (带 parentId) | PyClaw 用 Redis/文件的扁平消息列表 |
| CommandLane 全局/会话队列 | 改为 Redis 分布式锁的作用域 |
| `session.compact()` / `session.abortCompaction()` | PyClaw 必须自己实现 LLM 总结 + abort |
| Agent harness 委托 | PyClaw 没有 agent harness |
| MCP/LSP 工具运行时 | compaction 不需要实时工具 |
| Successor transcript (文件式) | PyClaw: 等价物是 Redis key 轮转 / entry 剪枝 |

## 可移植概念 (PyClaw 应采纳)

按 `harden-agent-core` 实施优先级排序:

### P0 — 正确性与可靠性
1. 安全超时 (默认 15 分钟, 可配, AbortSignal 集成)
2. 重复用户消息去重 (60 秒窗口, NFC 归一化)
3. Token 估算完备性校验 (`tokens_after > tokens_before` → null)
4. 总结前剥离 `toolResult.details` (安全)
5. 真实对话守卫 (仅 heartbeat 时跳过)

### P1 — 质量
6. 多阶段总结 (分块 → 分别总结 → 合并)
7. 标识符保留指令
8. 超大消息降级 (>50% 上下文 → 排除 + 标注)
9. 自适应 chunk ratio
10. 截断后的 tool_use/result 配对修复

### P2 — 扩展性
11. before/after hooks 带异常隔离
12. 原因码分类
13. 压缩模型覆盖 (config)
14. 压缩后 memory sync (await/async/off)
15. Checkpoint 快照与回滚

### P3 — 分布式
16. 队列并发控制 (Redis 锁作用域 + 系统信号量)
17. Run 级超时宽限期
18. 手动压缩边界硬化
19. Transcript 轮转等价物 (Redis entry GC)
