# 会话系统设计

> 本文记录 PyClaw 会话系统的完整设计，包括与 OpenClaw 的对比分析、关键决策及实施路径。

## 一、问题根源

最初的 `session_id`（如 `feishu:cli_xxx:ou_abc`）身兼三职：

```
feishu:cli_xxx:ou_abc
  ↑ 路由地址（应该永久稳定）
  ↑ Redis 存储 key 前缀（应该可以轮换）
  ↑ workspace 路径派生源（colon → underscore）
```

这三个职责互相冲突。用户发 `/new` 时，没有任何方法在不破坏路由的情况下创建新对话。

## 二、OpenClaw 的两层设计

调查 OpenClaw TypeScript 实现后，发现它用两个独立概念解决了这个问题：

```
sessionKey  "agent:main:telegram:direct:ou_xxx"
                ← 路由地址，永久稳定，格式: agent:{agentId}:{rest}
                ← 存在于: sessions:{agentId} Hash (索引)
    │
    └── sessionId  "550e8400-e29b-41d4-a716-446655440000"
                    ← UUID，存储容器，/new 时轮换
                    ← 存在于: session:{sessionId}:* Redis keys
```

`/new` 命令只换 sessionId，sessionKey 不变。旧对话归档保留。

## 三、PyClaw 的适配方案

PyClaw 采用相同分层，但格式适配 Python 风格：

### 3.1 SessionKey 格式

```python
def build_session_key(app_id: str, event, scope: str) -> str:
    # DM
    if chat_type == "p2p":
        return f"feishu:{app_id}:{open_id}"
    # 群组 per-user 隔离
    if scope == "user":
        return f"feishu:{app_id}:{chat_id}:{open_id}"
    # 线程
    if scope == "thread" and thread_id:
        return f"feishu:{app_id}:{chat_id}:thread:{thread_id}"
    # 群组共享（默认）
    return f"feishu:{app_id}:{chat_id}"
```

这与原来的 `build_session_id()` 逻辑完全相同，只是语义角色变了。

### 3.2 SessionId 格式

```
{sessionKey}:s:{8-char-hex}

例: feishu:cli_xxx:ou_abc:s:a1b2c3d4
```

- 8-char hex = 32 位空间，约 42 亿组合，足够防碰撞
- 保留 sessionKey 前缀，便于日志调试和 SCAN 查询
- `:s:` 分隔符用于区分旧格式（无 `:s:` 的 session 是迁移前的老数据）

### 3.3 Redis Key 全景

```
┌─────────────────────────────────────────────────────────────────────┐
│  Per-Session（按 sessionId 索引，滑动 TTL）                           │
│  pyclaw:session:{<sessionId>}:header    STRING  SessionHeader JSON  │
│  pyclaw:session:{<sessionId>}:entries   HASH    消息内容             │
│  pyclaw:session:{<sessionId>}:order     LIST    消息顺序             │
│  pyclaw:session:{<sessionId>}:leaf      STRING  当前叶节点           │
│  session-lock:{<sessionId>}             STRING  写锁（30s）          │
├─────────────────────────────────────────────────────────────────────┤
│  Per-SessionKey（按 sessionKey 索引，无 TTL，永久保留）               │
│  pyclaw:skey:{<sessionKey>}:current     STRING  当前活跃 sessionId  │
│  pyclaw:skey:{<sessionKey>}:history     ZSET    所有历史 sessionId  │
│                                                 score = 创建时间ms  │
└─────────────────────────────────────────────────────────────────────┘
```

**为什么 skey 键无 TTL**：会话历史索引的价值在于完整性。session 数据可以过期，但索引（指向过期数据）依然保留。`/history` 显示"已归档"状态。

## 四、SessionRouter

`SessionRouter` 是路由层的核心，封装 sessionKey → sessionId → SessionTree 的解析逻辑：

```python
@dataclass
class SessionRouter:
    store: SessionStore

    async def resolve_or_create(session_key, workspace_id, agent_id="default"):
        # 1. 查新格式 skey:current
        session_id = await store.get_current_session_id(session_key)
        if session_id:
            tree = await store.load(session_id)
            if tree: return (session_id, tree)

        # 2. 懒迁移：检查旧格式（session_id == session_key）
        old_tree = await store.load(session_key)
        if old_tree:
            await store.set_current_session_id(session_key, session_key)
            return (session_key, old_tree)

        # 3. 全新创建
        tree = await store.create_new_session(session_key, workspace_id, agent_id)
        return (tree.header.id, tree)

    async def rotate(session_key, workspace_id, agent_id="default"):
        old_id = await store.get_current_session_id(session_key)
        tree = await store.create_new_session(
            session_key, workspace_id, agent_id,
            parent_session_id=old_id
        )
        return (tree.header.id, tree)
```

**懒迁移**无需停机、无需批量脚本。每个用户在首次发消息时自动迁移。

## 五、命令系统

### 5.1 架构原则

命令在**渠道层**拦截，不进入 agent runner：

```
飞书消息到达
    ↓
handle_feishu_message()
    ↓
is_command(text)?  ─── 是 ──→ commands.py 处理 → 直接回复 → 结束
    ↓ 否
SessionRouter.resolve_or_create()
    ↓
idle 检查（如需要轮换 → rotate()）
    ↓
dispatch_message() → agent runner → LLM
```

**理由**：命令毫秒级完成，不需要 LLM。命令回复用纯文字，无需 `cardkit:card:write` 权限。agent runner 职责单一。

### 5.2 完整命令列表

| 命令 | 行为 | 备注 |
|---|---|---|
| `/new` | 创建新 sessionId，旧对话归档 | 支持 `/new <初始消息>` |
| `/reset` | 同 `/new`，回复措辞不同 | |
| `/status` | 显示 sessionKey、sessionId、消息数、模型、创建时间 | |
| `/whoami` | 显示 open_id、chat_type、chat_id | |
| `/history` | 列举该 sessionKey 下最近 10 个历史会话 | |
| `/help` | 显示所有命令说明 | |
| `/idle <Xm\|Xh\|off>` | 设置空闲自动重置时长 | per-session 覆盖全局设置 |

未识别的 `/` 前缀消息透传给 agent。

### 5.3 /new 完整流程

```
用户: "/new"
    ↓
1. is_command("/new") → True
2. old_id = get_current_session_id(session_key)
3. new_tree = create_new_session(
       session_key, workspace_id,
       parent_session_id=old_id    ← 接上历史链
   )
4. reply_text("✨ 新会话已开始，之前的对话已归档。")
   （不走 agent，直接发送）
5. 结束

用户: "/new 用Python写斐波那契"
    ↓
1-4. 同上
5. dispatch_message("用Python写斐波那契", new_session_id)
   → agent 处理
```

## 六、空闲自动重置

### 6.1 机制

`SessionHeader.last_interaction_at` 记录最后一次用户消息时间。每次消息被 agent 处理后更新，系统事件不更新。

```
消息到达
    ↓
now - last_interaction_at > idle_minutes × 60?
    是 → rotate() 创建新 session（静默，不通知用户）
    否 → 正常处理
    ↓
agent 处理完毕 → update_last_interaction(session_id)
```

### 6.2 配置优先级

```
per-session /idle 30m  (最高优先级，存于 SessionHeader.idle_minutes_override)
    ↓ 覆盖
FeishuSettings.idle_minutes  (全局默认，0 = 关闭)
```

默认关闭（`idle_minutes = 0`），与 OpenClaw 一致。

## 七、OpenClaw 对比

| 维度 | OpenClaw | PyClaw |
|---|---|---|
| 路由键格式 | `agent:main:telegram:direct:ou_xxx` | `feishu:cli_xxx:ou_abc` |
| 存储容器格式 | UUID | `{sessionKey}:s:{8hex}` |
| Session 索引 | `sessions:{agentId}` Hash + ZSet | `skey:{sessionKey}:current` + `skey:{sessionKey}:history` |
| /new 行为 | 换 sessionId，归档旧转录，触发 memory hook | 换 sessionId，归档旧 Redis keys |
| TTL | 无（永久保留） | 滑动 30 天（索引键无 TTL） |
| 命令系统 | 40+ 命令，含 /subagents /acp /tts | 7 个 essential 命令 |
| parent_session | 用于线程 fork、subagent、dashboard | 用于 /new 历史链接 |
| 空闲重置 | daily（4AM）+ idle 双模式，per-channel 配置 | idle 模式，global + per-session 配置 |

## 八、未来演进

- **线程 session parent fork**：线程首次创建时从父群组 session 复制前 N 条消息作为上下文起点（OpenClaw 行为，100K token 上限保护）
- **Web channel 命令**：HTTP API 上的同等命令端点
- **session export**：`/export` 导出当前对话为 HTML 或 JSONL
- **Memory 集成**：`/new` 时触发 memory hook，从旧 session 提取摘要存入长期记忆（dreaming engine 前提）

---

*相关 change*: `openspec/changes/implement-session-key-rotation/`  
*相关决策*: D19（SessionKey/SessionId 分离）、D20（命令拦截）、D21（空闲重置）
