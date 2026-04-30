# Context Engine 设计

## 是什么

Context Engine 是 agent 循环和上下文管理策略之间的可插拔接口。它解决：**如何在有限的 context window 里放入最有价值的信息**。

## OpenClaw 的实现

OpenClaw 定义 `ContextEngine` 为带生命周期方法的插件接口：

```
bootstrap() → 为 session 初始化 engine 状态
assemble()  → 在 token 预算内组装模型上下文（可注入/重写消息）
ingest()    → 把新消息捕获到 engine 内部存储
compact()   → 减少 token 使用（摘要、裁剪）
afterTurn() → 回合后生命周期（持久化状态、触发后台压缩）
maintain()  → Transcript 维护（为安全/效率重写 entry）
```

默认 `LegacyContextEngine` 是 no-op pass-through — 真正的逻辑在 agent runner 的 sanitize/validate/limit 管道里。这个接口存在是为了第三方插件（RAG、自定义记忆系统）。

## PyClaw 的方式

### Phase 1: Protocol + DefaultContextEngine

```python
class ContextEngine(Protocol):
    async def assemble(self, messages, token_budget, prompt) -> AssembleResult: ...
    async def ingest(self, session_id, message) -> None: ...
    async def compact(self, session_id, messages, token_budget, force) -> CompactResult: ...
    async def after_turn(self, session_id, messages) -> None: ...

@dataclass
class AssembleResult:
    messages: list[dict]
    system_prompt_addition: str | None = None
```

`DefaultContextEngine`:
- `assemble()` → pass-through（返回原样消息）
- `ingest()` → no-op
- `compact()` → 委托给内置压缩（find_cut_point + LLM summarize）
- `after_turn()` → no-op

### Phase 2: 第三方集成

替换实现，无需改 agent runner：

```python
class Mem0ContextEngine:
    async def assemble(self, messages, token_budget, prompt):
        # 在 mem0 中查找相关记忆
        memories = await self.mem0.search(prompt, limit=5)
        # 注入为系统 prompt 追加内容
        return AssembleResult(
            messages=messages,
            system_prompt_addition=format_memories(memories)
        )

    async def ingest(self, session_id, message):
        # 把对话捕获到 mem0
        await self.mem0.add(message.content, user_id=session_id)
```

### 为什么要预先定义接口

Agent runner 从第一天就通过 ContextEngine 调用：
```python
assembled = await context_engine.assemble(messages, token_budget, prompt)
# ... LLM 调用 ...
await context_engine.ingest(session_id, response_message)
# ... 工具循环结束后 ...
await context_engine.after_turn(session_id, all_messages)
```

Phase 2 替换 engine 实现 — runner 代码不动。不预先定义的话，Phase 2 需要在 4 个调用点修改 runner，有回归风险。

### 与 Hook 的关系

| 机制 | 范围 | 用例 |
|------|-----|------|
| AgentHook (before_prompt_build) | 轻量 — 追加到系统 prompt | 简单记忆召回、技能注入 |
| ContextEngine | 重量级 — 重写消息、拥有压缩、管理生命周期 | 完整 RAG 管道、mem0、langchain memory |

两者共存。Hook 用于简单插件。ContextEngine 用于需要深度控制上下文组装的系统。
