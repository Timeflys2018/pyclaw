# Feishu Channel Brainstorm 记录

**日期：** 2026-05-01  
**对应 change：** `implement-feishu-channel`  
**最终 tasks 数：** 52 个任务，5 个原子 commit

---

## 背景

PyClaw 已有完整的 agent core（LLM loop、tools、session store、timeouts、abort、streaming）和 Redis session backend，但没有任何用户入口。飞书机器人（App ID: `cli_a938d17de2b85cc1`）已配置好，这个 change 让 PyClaw 能通过飞书接收和回复消息。

参考实现：`openclaw/extensions/openclaw-lark`（TypeScript，~24K LoC）。  
Python SDK：`lark-oapi`（飞书官方），已验证 `lark_oapi.Client` 有 `.cardkit` 和 `.im` 属性。

---

## 8 轮问答的决策过程

### Q1：workspace 从哪里来？

**问题：** `run_agent_stream` 需要 `tool_workspace_path`，飞书消息来了 bot 在哪个目录工作？

**候选：**
- A. 全局默认工作区（所有会话共享）
- B. per-session 隔离（每个会话独立目录）
- C. 用户在消息里指定

**决策：B（per-session 隔离）为默认，config 可覆盖全局路径**

**理由：** B 保证不同会话互不干扰；全局覆盖适合"让 bot 操作某个固定项目目录"的场景；C 有安全风险。

---

### Q2：群组里 bot 响应哪些消息？

**问题：** 群里所有消息都回，还是只回 @ 它的？

**候选：**
- A. `requireMention: true`（只回 @ bot）
- B. 全部回复（需 `im:message.group_msg` 敏感权限）
- C. DM 全回，群组只回 @（上游 openclaw 默认）

**决策：C**

**理由：** 最自然的机器人行为。B 容易在大群刷屏，权限也敏感。

---

### Q3：非文字消息怎么处理？

**问题：** 图片、文件、语音、sticker 怎么办？

**演进过程：**
- 初始想法：text + post 处理，其他静默忽略
- 用户追问：**如果模型支持多模态，是不是放开限制好些？**
- 验证：Claude Sonnet 4 原生支持多模态；`ImageBlock` 在数据模型里有但链路没打通

**决策：text + post + image（多模态），LLMClient + RunRequest 同步升级，其他类型静默忽略**

**理由：** Claude Sonnet 4 能看图，不打通就浪费了。`lark-oapi` 支持图片下载，LiteLLM 支持 `image_url` base64 格式。

---

### Q4：群组 workspace 策略

**问题：** `sessionScope: "user"` 时群里所有人共享 workspace 还是每人独立？

**候选：**
- A. 群组共享 workspace
- B. 群内 per-user 隔离
- C. config 里可以选

**决策：C，默认共享，支持配置**

**命名讨论：**
- 原始名称 `threadSession: true` 语义不直白（上游的名字，原义是"话题独立 session"）
- 候选：`threadSession`、`workspaceMode`、`groupWorkspace`、`isolateUsers`、`sessionScope`
- **最终选择：`sessionScope`** 配合字符串枚举，比布尔值更可读更可扩展

| 值 | session ID | 含义 |
|---|---|---|
| `"chat"`（默认） | `feishu:{app_id}:{chat_id}` | 群组共享 |
| `"user"` | `feishu:{app_id}:{chat_id}:{open_id}` | 每人独立 |
| `"thread"` | `feishu:{app_id}:{chat_id}:thread:{thread_id}` | 每话题独立 |

---

### Q5：`sessionScope: "user"` 时群里上下文怎么组织？

**问题：** 每人 session 独立后，bot 对群组整体情况无感知，用户 A 问"群里刚才说了什么"bot 不知道。

**设计讨论：**
- 上游做法（完整隔离，群组感知通过飞书工具主动查）
- 双层 session（private + 共享 group context）
- session 隔离但 workspace 独立（两个独立维度）

**决策：完整隔离，接受"无群组感知"代价，未来通过飞书工具补齐**

**文档约定：** `sessionScope: "user"` 的代价明确写进文档，让用户知情地选择。

---

### Q6：非 @ bot 的群聊消息怎么处理？

**问题：** `observeAll: true` 时，群里所有消息都要存进 session transcript，这需要 `im:message.group_msg` 特殊权限。

**用户追问：** 这些消息从 Redis/file 读取 transcripts，为什么要权限？

**澄清：**
- 权限问题不是存储问题，是**飞书 WS 推送策略**
- 没有 `im:message.group_msg`，飞书根本不会把非 @ bot 的消息通过 WS 推给你
- `im:message.group_msg:get_as_user`（已有）≠ `im:message.group_msg`（机器人接收群组所有消息）

**后续讨论引出更根本的问题：** observeAll 把别人的聊天记录塞进 session transcript 会破坏 transcript 的 DAG 语义（transcript 是 bot 和用户的对话记录，不是群聊镜像）。更自然的做法是注入 system prompt。

---

### Q7（合并到 observeAll 演进）：如何让 bot 有群组上下文感知？

**问题：** 不需要特殊权限，能不能用 session transcripts 存所有消息？

**回答：** 可以，但实现方式应该是**被 @ 时主动拉取最近 N 条群消息注入 system prompt**，而不是实时存进 transcript。

**最终方案：** `groupContext` 字段替代 `observeAll`：

| 值 | 含义 | 权限需求 |
|---|---|---|
| `"none"` | 不注入群组上下文 | - |
| `"recent"`（默认） | 被 @ 时拉取最近 N 条注入 system prompt | `im:message:readonly`（已有）|
| `"all"` | 实时推送所有消息 | `im:message.group_msg`（需申请）|

**`groupContextSize: 20`**（默认）

**关键区别：**
- `groupContext="recent"` 注入 **system prompt**（背景上下文，每次重新拉取）
- `observeAll="all"` 写入 **session transcript**（持久化，实时同步）

---

### Q8：Bot 人设从哪里来？

**问题：** System prompt 里 bot 怎么介绍自己？是硬编码、config 还是文件？

**用户提出：** 上游 openclaw 是通过 bootstrap files（AGENTS.md、SOUL.md、USER.md 等）实现的。

**验证：** 读了 openclaw 的 `workspace.ts` 和 `bootstrap-files.ts`，确认：
```
workspace/
├── AGENTS.md      ← 行动指南，每次注入 system prompt
├── SOUL.md        ← 性格/定位（首次初始化生成）
├── IDENTITY.md    ← 自我介绍
├── USER.md        ← 用户信息
└── BOOTSTRAP.md   ← 首次运行 Q&A（完成后删除）
```

**存算分离问题：**
- OpenClaw 哲学：文件是第一等公民，单机
- PyClaw 哲学：文件系统易失，多机需要 Redis

**三个路径分析：**
- A. 接受文件方案（放弃完全存算分离）→ 技术债
- B. 彻底进 Redis（用户体验差，没人想 `redis-cli SET`）
- C. `WorkspaceStore` 抽象（文件做编辑入口，Redis 做读取来源）→ **选这个**

**决策：路径 C，分两步**
1. 这个 change：`WorkspaceStore` Protocol + `FileWorkspaceStore`（单机可用，结构正确）
2. 下一个 change：`RedisWorkspaceStore` + 文件 watcher（多实例场景）

**本 change 具体实现：** per-session workspace 里有 `AGENTS.md` 就注入 system prompt，没有就用默认。只做这一件事，其他 bootstrap 文件（SOUL.md 等）延后。

---

### 并发模型（Q6 衍生）

**问题：** 同一个群里多用户同时 @ bot，怎么处理？

**决策：按 sessionScope 自动选择**
- `sessionScope="chat"` → per-chat 串行队列（共享 session 必须串行保证一致性）
- `sessionScope="user"` → per-user 并行（独立 session 各自串行）

**实现：** `asyncio.Queue` per-session_id，session_id 本身已经编码了 scope 差异，queue key 直接用 session_id。

---

## 从初稿到最终版的主要变化

| 维度 | 初稿 | 最终版 | 变化原因 |
|---|---|---|---|
| 群组上下文 | `observeAll: true/false` | `groupContext: "none"/"recent"/"all"` | 不需要特殊权限；注入 system prompt 比存 transcript 更干净 |
| workspace 管理 | 直接 open() 读文件 | `WorkspaceStore` Protocol | 存算分离架构的正确延伸，和 SessionStore 设计一致 |
| 消息类型 | text + post | text + post + image | Claude Sonnet 4 支持多模态，不打通浪费 |
| 会话配置 | `threadSession: bool` | `sessionScope: "chat"/"user"/"thread"` | 更可读，三值枚举比布尔更灵活 |
| 并发模型 | 未考虑 | 按 sessionScope 自动选择 | Chat 共享 session 必须串行 |
| Bot 人设 | settings 字段 | AGENTS.md 文件注入（WorkspaceStore 读取） | 对标上游 openclaw，文件编辑体验更好 |

---

## 后续 Change 规划

**这个 change 里故意不做，下一个 change 做：**
```
implement-redis-workspace-store    RedisWorkspaceStore + 文件 watcher
implement-file-session-backend     session_backend="file" (JSONL)
implement-web-channel              HTTP API + WebSocket 入口
groupContext="all"                 等 im:message.group_msg 权限申请后
```

**明确推迟，未来按需：**
```
card interactions（按钮/表单）     需要 card.action.trigger 事件处理
reaction 触发                     表情回应驱动 agent
多账号 Feishu                     现在只支持单账号
Webhook HTTP 模式                 有 WS 就够了
document/drive 工具               飞书文档/日历独立大 change
SOUL.md / USER.md 完整 bootstrap  先做 AGENTS.md，其他延后
```

---

## 权限清单

| 权限 | 用途 | 状态 |
|---|---|---|
| `im:message.group_at_msg:readonly` | 接收群组 @ bot 消息（WS） | ✅ 已有 |
| `im:message.p2p_msg:readonly` | 接收 DM 消息（WS） | ✅ 已有 |
| `im:message:readonly` | 主动拉取群聊历史（groupContext="recent"） | ✅ 已有 |
| `im:resource` | 下载图片附件 | ✅ 已有 |
| `im:message:send_as_bot` | 发送消息/回复 | ✅ 已有 |
| `cardkit:card:write` | 创建/更新 CardKit 流式卡片 | ✅ 已有 |
| `cardkit:card:read` | 读取卡片状态 | ✅ 已有 |
| `im:message.group_msg` | 接收群组所有消息（groupContext="all"） | ❌ 需申请 |
