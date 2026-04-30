# 超时与 Abort

PyClaw 强制执行多层超时以约束 Agent 运行时间，并通过 `asyncio.Event` abort 信号支持协作式取消。

## 三层超时

```
run timeout ─── 最外层，保护整个 turn（默认 300s）
  ├── idle timeout ─── LLM N 秒内未发送 token（默认 60s）
  ├── tool timeout ─── 单个 tool 执行上限（默认 120s）
  └── compaction timeout ─── 独立，默认 15 分钟
```

## 配置

```json
{
  "agent": {
    "timeouts": {
      "run_seconds": 300.0,
      "idle_seconds": 60.0,
      "tool_seconds": 120.0,
      "compaction_seconds": 900.0
    }
  }
}
```

任一值设为 `0` 表示**禁用**该层。

| 键 | 默认 | 禁用 | 说明 |
|---|---|---|---|
| `run_seconds` | 300 | `0` | 保护整个 Agent turn 的最外层超时。 |
| `idle_seconds` | 60 | `0` | LLM chunk 之间允许的最长间隔。 |
| `tool_seconds` | 120 | `0` | 默认单 tool 执行上限（tool 可覆盖）。 |
| `compaction_seconds` | 900 | `0` | 单次 Compaction 的安全超时。 |

### 为什么需要 idle timeout

仅靠 run timeout 无法捕获响应中途挂起的 LLM 流。TCP 连接保持打开，但 byte 停止流动。Idle timeout 测量 chunk *之间*的时间——若流停顿超过 `idle_seconds`，连接会被断开。

### 单 tool 覆盖

Tool 可通过 `timeout_seconds` 类属性声明自己的超时：

```python
class MyLongRunningTool:
    name = "reindex_database"
    timeout_seconds = 3600.0

    async def execute(self, args, context): ...
```

## Abort 信号

`run_agent_stream` 接受可选的 `abort: asyncio.Event`：

```python
abort = asyncio.Event()
async for event in run_agent_stream(request, deps, tool_workspace_path=p, abort=abort):
    if user_cancelled:
        abort.set()
```

当 `abort.set()` 被调用时，系统将取消传递至：

1. **LLM stream** — 当前的 `acompletion` 调用被取消，触发 `LLMError(code="aborted")`。
2. **Tool 执行** — `ToolContext.abort` 已被内置 tool 在 spawn 前和大 I/O 前检查；`BashTool` 发送 SIGTERM，等待 2 秒缓冲，然后 SIGKILL。
3. **Compaction** — 摘要调用被取消，checkpoint 被恢复。

Run 最终以 `ErrorEvent(error_code="aborted")` 结束。

## 错误码

因超时或 abort 终止的运行会产出 `ErrorEvent`：

| `error_code` | 含义 |
|---|---|
| `timeout` | Run 超过 `run_seconds`（或 run=0 时 idle 超时）。 |
| `aborted` | 外部调用了 `abort.set()`。 |
| `tool_loop` | 未知 tool 循环检测器在指导消息后仍被触发。 |
| `max_iterations` | 循环达到 `max_iterations` 上限仍未终止。 |
| `compaction_failed` | Compaction 出错，session 已回滚。 |
| `summary_failed` / `provider_error_4xx` / `provider_error_5xx` | Compaction 专属错误码（见 compaction-guide.md）。 |

## 最佳实践

- **生产环境**：将 `run_seconds` 收紧至 SLO（如交互式聊天用 60s）。`idle_seconds` 保持 60s。
- **批量任务**：将 `run_seconds` 提高至 1800+。将 tool 超时提高以适应长命令。
- **交互式取消**：始终传入 `abort` 事件。与客户端的 "停止" 按钮关联。
- **测试 abort 路径**：确保下游清理（子进程 kill、文件句柄）可靠发生。
