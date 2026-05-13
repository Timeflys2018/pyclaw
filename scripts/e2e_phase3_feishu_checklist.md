# Phase 3 E2E: 飞书 Bot 16 Slash 命令手动测试 Checklist

> **适用范围**: 验证 `refactor-curator-architecture` + `expose-cli-to-chat` 两个 change 在真实飞书环境端到端。
>
> **前置**: Phase 1 (scripts/e2e_phase1_redis.py) ✅ 和 Phase 2 (scripts/e2e_phase2_curator.py) ✅ 已全绿。
>
> **估时**: 60 分钟（实际操作约 30 分钟，观察 log + 记录约 30 分钟）
>
> **打印建议**: 本 markdown 渲染后直接打印 → 逐项手工打钩。

---

## 0. 启动前准备（3 分钟）

```bash
cd /Users/timeriver/CascadeProjects/Project/pyclaw

# 确认配置（已有，不要动）
grep -A3 '"feishu"' configs/pyclaw.json | head -6
#   ✅ 应看到 "enabled": true, "appId": "cli_a938d17de2b85cc1"

# 启动服务
./scripts/start.sh
```

**期望日志**（等 10-15 秒，看到这三条即成功）：

```
INFO     uvicorn running on 0.0.0.0:8000
INFO     Feishu bot open_id: ou_xxxxxx
INFO     curator cycle using RedisLockManager (key: pyclaw:curator:cycle)
```

**如果启动报错**：
- `ModuleNotFoundError` → `.venv/bin/pip install -e ".[dev]"` 重装
- `Connection refused` (Redis) → 确认 `configs/pyclaw.json` 里的 Redis host/port 可达
- `Feishu bot open_id` 失败 → 检查 `appId` / `appSecret`

**观察 log 的 shell 保持打开**（后续所有测试都要看这个 shell 的实时 log）。

---

## 1. 用 PyClaw Feishu bot 打开聊天窗口

飞书里找到你的 bot（appId `cli_a938d17de2b85cc1` 对应的 bot），打开 P2P 聊天窗口。

---

## 2. Session Category（3 条命令，2 分钟）

### 2.1 `/new` — 创建新 session

**输入**: `/new`

**期望回复**: `✨ 新会话已开始`

**Log 验证**:
```
INFO session_router.rotate session_key=feishu:cli_xxx:ou_yyy old=ses_abc new=ses_def
```

**Fail 怎么办**: 检查 `app.state.settings` 是否正确注入（Phase G 的 `CommandContext.settings` 必填字段可能导致 crash）→ 看 log 里有没有 `TypeError: missing keyword argument 'settings'`。

- [ ] **PASS 2.1**

### 2.2 `/new 帮我写一首关于咖啡的五言绝句`

**这一条验证 `dispatch_user_message` callable**（Phase G 未验证过的关键路径）

**期望行为**:
1. 先收到 `✨ 新会话已开始`
2. **立即**开始流式输出一首五言诗（bot 调 LLM）

**Log 验证**:
```
INFO session rotated, dispatching initial user message
INFO agent run started ses_id=...
```

**Fail 怎么办**:
- 只收到 `✨ 新会话已开始` 没有诗 → `dispatch_user_message` callable 没被正确注入到 CommandContext
- 诗出现了但 session_id 还是旧的 → rotate 失败，说明 SessionRouter 配置问题

- [ ] **PASS 2.2** — 收到一首完整五言诗（4 句 × 5 字）

### 2.3 `/reset`

**期望回复**: `🔄 会话已重置` （文字不同，功能同 `/new`）

- [ ] **PASS 2.3**

---

## 3. Inspection Category（7 条命令，8 分钟）

### 3.1 `/status` — **核心验证点**

**期望**:
```
📊 会话状态
会话ID: ses_xxxxxx
模型:   anthropic/ppio/pa/claude-sonnet-4-6
消息数: 0（用户 0 / 助手 0 / 工具 0）
Token:  0 / 1000000 (0%)
空闲:   0 分钟
```

- [ ] **PASS 3.1** — 显示的 session_id 跟 2.3 里刚 `/reset` 后的一致

### 3.2 `/whoami`

**期望**:
```
UserId: ou_xxxxxxxxxx (你自己的 open_id)
Channel: feishu
Admin: false (或 true，取决于 configs/pyclaw.json 的 admin 配置)
```

- [ ] **PASS 3.2**

### 3.3 `/history`

**期望**: 列出最近 5 条历史 session（按时间倒序）

- [ ] **PASS 3.3**

### 3.4 `/help` — **核心验证点**

这一条验证 Phase G 的 `registry.list_by_category()` 能列出所有 16 条命令。

**期望**: 看到 **7 个 category**（session / inspection / context / config / evolution / model / skills），**总共 16 条命令**。

**📌 特别注意** `/help` 应该显示:
- `/new` `/reset` 在 `session` category
- `/status` `/whoami` `/history` `/help` `/tasks` `/memory` 在 `inspection` category
- `/compact` `/export` 在 `context` category（新 category）
- `/model` 在 `model` category
- `/idle` 在 `config` category
- `/extract` `/curator` 在 `evolution` category
- `/skills` 在 `skills` category

**Fail 怎么办**: 如果少命令，检查 `register_builtin_commands()` 是否在 app startup 时被调用（log 搜 `registry`）。

- [ ] **PASS 3.4** — 16 条全部显示

### 3.5 `/tasks list`

**期望**（如果 curator 在跑）:
```
📋 后台任务 (1-2 running)
t000001  curator         system (xx s ago)   → ... (仅 admin 可见)
```

**正常情况可能看到 0 running** — 这也 OK，表示系统静止。

- [ ] **PASS 3.5** — 返回合法输出（不 crash）

### 3.6 `/memory stats`

**期望**:
```
📦 记忆统计（session: feishu:cli_xxx:ou_yyy）
L1 Redis:      0-N entries / X KB
L2 Facts:      X 条
L3 Procedures: X 条 active / Y archived / Z graduated
L4 Archives:   X session summaries
```

- [ ] **PASS 3.6**

### 3.7 `/memory list --procedures`

**期望**: 列出当前 session 的 procedures（可能为空，取决于你历史用过这个 bot 没）。

- [ ] **PASS 3.7** — 不 crash

---

## 4. Context Category（2 条命令，3 分钟）— **Phase C+D 核心验证**

### 4.1 准备：先跟 bot 多聊几轮

在 `/compact` 之前需要有**足够的上下文**，否则压缩没效果。发 3-5 条消息：

```
介绍一下 OAuth 2.0 的四种授权类型
详细讲 authorization code flow
refresh token 的作用是什么？
security best practices？
```

### 4.2 `/compact`

**期望**:
```
✓ 上下文已压缩
   原始 token: 8500
   压缩后:    3200
   节省:      5300 (62%)
```

（数字仅示例）

**Log 验证**:
```
INFO compaction started session_id=ses_xxx
INFO compaction complete before_tokens=8500 after_tokens=3200
```

**Fail 怎么办**:
- 看到 "⏳ 冷却中，还需 Xs" → 刚刚跑过 compaction，等 60s 再试
- 超时 → compaction LLM call 15-minute timeout 不太可能命中，查 log 里的 error

- [ ] **PASS 4.2** — 收到压缩成功 + token 数下降

### 4.3 验证 `/status` 的 token 数确实降了

再跑 `/status`，消息数可能没变（压缩不删消息，只做 system prompt 摘要），但**Token 数应显著下降**。

- [ ] **PASS 4.3**

### 4.4 `/export markdown`

**期望**:
```
✓ 已导出到
/Users/you/.pyclaw/workspaces/feishu_cli_xxx_ou_yyy/exports/export_20260512_164530.md
```

**本地验证**:
```bash
ls ~/.pyclaw/workspaces/feishu_*/exports/ | tail -1
cat ~/.pyclaw/workspaces/feishu_*/exports/$(ls -t ~/.pyclaw/workspaces/feishu_*/exports/ | head -1)
```

**应看到**: markdown 格式的完整对话记录（user + assistant entries）。

- [ ] **PASS 4.4** — 文件生成 + markdown 内容完整

### 4.5 `/export json inline`

**期望**: bot **直接在飞书消息里回复** JSON 内容（不落盘）。

- [ ] **PASS 4.5**

---

## 5. Model Category（1 条命令，2 分钟）

### 5.1 `/model`

**期望**:
```
当前模型: anthropic/ppio/pa/claude-sonnet-4-6 (image, pdf)

可用模型:
  📦 anthropic
    • anthropic/ppio/pa/claude-sonnet-4-6 (image, pdf)
    • anthropic/ppio/pa/claude-opus-4-6 (image, pdf)
    • anthropic/ppio/pa/claude-opus-4-7 (image, pdf)
  📦 openai
    • azure_openai/gpt-5.4 (image)
    • azure_openai/gpt-5.3-codex            ← 无 modality tag
    • azure_openai/gpt-4o (image)
    • azure_openai/gpt-4o-mini (image)
```

**📌 特别注意**:
- ✅ `codex` 不应有 `(image)` tag（它是 text-only）
- ✅ 其他都应有 `(image)` 或 `(image, pdf)` tag

- [ ] **PASS 5.1** — modality tag 正确显示

### 5.2 `/model azure_openai/gpt-5.3-codex`

**期望**:
```
✓ 模型已切换为 `azure_openai/gpt-5.3-codex`（下次对话生效）
ℹ️ 该模型不支持图片处理
```

**📌 `ℹ️` warning 必须出现**（切到 text-only model）。

- [ ] **PASS 5.2**

### 5.3 再跑一次 `/status`

**期望**: `模型: azure_openai/gpt-5.3-codex` ← 确实切换了。

- [ ] **PASS 5.3**

### 5.4 切回 vision 模型 `/model anthropic/ppio/pa/claude-sonnet-4-6`

**期望**:
```
✓ 模型已切换为 `anthropic/ppio/pa/claude-sonnet-4-6`（下次对话生效）
```

**📌 这次 ℹ️ warning 不应出现**（切到 vision model）。这是 progressive disclosure 的关键。

- [ ] **PASS 5.4**

---

## 6. Config Category（1 条命令，1 分钟）

### 6.1 `/idle 10m`

**期望**: `✓ 空闲超时已设为 10 分钟`

### 6.2 `/status`

**期望**: 空闲时长相关字段显示正确（`空闲: 0 分钟`）

- [ ] **PASS 6**

---

## 7. Evolution Category（2 条命令，5 分钟）— **refactor 最核心验证**

### 7.1 `/curator review-status`

**期望**:
```
📅 LLM review 状态
上次触发: 从未（或 X days ago）
下次允许: 立即（或 X 后）
```

**这验证 `CuratorStateStore.get_last_review_at()` 在真实 Redis 读取的路径**。

- [ ] **PASS 7.1**

### 7.2 `/curator list --auto`

**期望**: 列出当前 session 的 auto-extracted active SOPs（可能为空，取决于用 bot 的历史）。

- [ ] **PASS 7.2** — 不 crash

### 7.3 `/curator list --stale`

**期望**: 列出 30d 未用的 SOP（可能为空）。

- [ ] **PASS 7.3**

### 7.4 `/curator list --archived`

**期望**: 列出归档 SOP。

- [ ] **PASS 7.4**

### 7.5 `/curator review-trigger` — **🔥 最核心验证**

**这一条触发的是完整的 CuratorCycle.execute()——DistributedMutex + CuratorStateStore + run_llm_review 全部联合测试。**

**期望（两种情况之一）**:

**情况 A** (configs 里 `llmReviewEnabled: false`，默认):
```
⚠️ LLM review 未启用（configs.evolution.curator.llmReviewEnabled=false）
要启用，修改 configs/pyclaw.json 后重启。
```

**情况 B** (手动改 config 启用后):
```
🔄 触发 LLM review 中...
（等 30-60 秒）
✅ LLM review 完成
   审查 SOP: X 条
   晋升:    X 条
   归档:    X 条
   失败:    0 条
```

**Log 验证**（关键！）:
```
INFO curator cycle acquired lock owner=manual:feishu:... mode=review_only force=True
INFO (如果有 SOP) curator llm review actions=X db=feishu_xxx.db
INFO curator cycle complete owner=manual:...
```

**📌 这条 log 证明**:
- ✅ `DistributedMutex` 在真实飞书 path 下能 acquire/release
- ✅ `CuratorStateStore` 正确写入
- ✅ `run_llm_review` 纯函数调用
- ✅ filename policy 正确解析你的真实 session db (`feishu_cli_xxx_ou_yyy.db`)

**Fail 怎么办**:
- `⏳ 另一实例正在运行` → 上次 cycle 还在跑，等 1 分钟
- `LockAcquireError` → Redis 连接问题
- crash with traceback → **这是 refactor 真实 bug**，贴 traceback 给我

- [ ] **PASS 7.5** — **最核心验证**：真实 curator cycle 跑完

### 7.6 `/extract` — 手动触发 SOP 提取

**期望**:
```
🔄 正在提取 SOP...
（等 15 秒）
✅ 提取完成
   candidates: X
   written:    X
```

- [ ] **PASS 7.6** — 不 crash

---

## 8. Skills Category（1 条命令，2 分钟）

### 8.1 `/skills list`

**期望**: 列出当前 workspace 的 skills（可能为空如果没装过）。

- [ ] **PASS 8.1** — 不 crash

### 8.2 `/skills check`

**期望**:
```
📋 Skills eligibility report
(如果没 skills)
  No skills installed in this workspace.
```

- [ ] **PASS 8.2**

---

## 9. 异常路径 & 边界测试（5 分钟）

### 9.1 错误命令

**输入**: `/nonexistent`

**期望**: `❓ 未知命令 /nonexistent，用 /help 查看所有命令`

- [ ] **PASS 9.1**

### 9.2 权限测试（如果你不是 admin）

**输入**: `/tasks list --all`

**期望**: `❌ --all 需要管理员权限`

- [ ] **PASS 9.2**（或跳过如果你是 admin）

### 9.3 带危险字符的 session_key（如果可复现）

这个需要你有多个 chat（P2P + group），确认每个 chat 的 session 隔离正确：

```
~/.pyclaw/memory/ 下应该看到多个 .db 文件
每个对应一个 chat，filename 形如：
  feishu_cli_xxx_ou_yyy.db       (P2P)
  feishu_cli_xxx_oc_zzz.db       (group)
  feishu_cli_xxx_oc_zzz_thread_t.db  (group thread)
```

```bash
ls -la ~/.pyclaw/memory/*.db
```

**📌 验证 Phase F** (`HumanReadableNaming` backward compat): filename 格式跟**重构前**完全一致（只在 `:` → `_`，没有 hash / escape）。

- [ ] **PASS 9.3** — filename 格式符合预期

---

## 10. 终极验证: 多消息流式 + 正在流式时 /stop

### 10.1 发一个会流式一段时间的长 prompt

**输入**: `写一篇关于 OAuth 2.0 authorization code flow 的 1500 字技术文章`

**观察**: Bot 开始流式输出（会写 1-2 分钟）。

### 10.2 **在 bot 还在流式的时候**，马上发 `/stop`

**期望**:
1. Bot 的流式输出**立即停止**
2. 飞书里看到 `⏸ 已发送中止信号`

**Log 验证**:
```
INFO abort signal received
INFO agent run aborted mid-stream ses_id=...
```

**Fail 怎么办**: `/stop` 走 ProtocolOp 旁路（E8 article 里讲的），**不进 SessionQueue**。如果 `/stop` 排队了 → 死锁 → bot 继续写完才停（bug）。

- [ ] **PASS 10** — `/stop` 真的打断了正在流式的 bot

---

## 11. 收尾

### 11.1 停止服务

```bash
# 在启动 shell 按 Ctrl+C
```

**期望 log**:
```
INFO curator cycle cancel initiated
INFO heartbeat task cancelled
INFO TaskManager shutdown completed cancelled=X running=0
INFO uvicorn shutdown complete
```

**📌 关键**: `running=0` — 没有僵尸任务留下。这验证 Phase D `DistributedMutex.__aexit__` 正确 cleanup。

- [ ] **PASS 11** — 服务干净退出

---

## 📊 Phase 3 Summary

共 **30+ 个测试点**，覆盖：
- **7 个 category** × **16 个命令**
- 3 个 channel-specific 边界（nonexistent / perm / session_key format）
- 1 个流式打断（ProtocolOp `/stop` 旁路）
- 1 个干净 shutdown

---

## 填写 Report

测试完毕，把 **PASS/FAIL** 数字回复我，例如：

```
Phase 3 Result:
  Section 2 (Session):    3/3 PASS
  Section 3 (Inspection): 7/7 PASS
  Section 4 (Context):    4/5 PASS — 4.4 /export markdown 文件路径不对
  Section 5 (Model):      4/4 PASS
  Section 6 (Config):     1/1 PASS
  Section 7 (Evolution):  5/6 PASS — 7.5 /curator review-trigger 报 LockAcquireError
  Section 8 (Skills):     2/2 PASS
  Section 9 (Edge):       3/3 PASS
  Section 10 (/stop):     1/1 PASS
  Section 11 (Shutdown):  1/1 PASS
  
  Total: 31/32 PASS
  
  Failures:
    4.4: ...
    7.5: ...
```

失败的项我会立刻帮你 debug。

---

## 🆘 Common Failures + Debug 速查

| 症状 | 可能原因 | 怎么查 |
|---|---|---|
| 所有命令都报 `TypeError: ... 'settings'` | Phase G 的 `CommandContext.settings` 字段注入失败 | `grep "settings=" src/pyclaw/channels/feishu/command_adapter.py` 确认有 `settings=ctx.settings_full` |
| `/curator review-trigger` 报 `AttributeError: 'NoneType' object has no attribute 'spawn'` | TaskManager 没注入 | log 搜 `task_manager` 初始化 |
| `/status` 显示 `模型: None` | `SessionHeader.model_override` 逻辑错了 | 看 `cmd_status` handler |
| Bot 启动后不响应 | Feishu WebSocket 连接失败 | log 搜 `feishu_client` / `lark_oapi` |
| `/compact` 永远 `⏳ 冷却中` | 冷却 key 在 Redis 卡住 | `redis-cli DEL pyclaw:compact:cooldown:<session_id>` |

---

> **📚 背景文献**:
> - Phase 1+2 自动化脚本: `scripts/e2e_phase1_redis.py` / `scripts/e2e_phase2_curator.py`
> - Refactor 决策: `openspec/changes/archive/2026-05-12-refactor-curator-architecture/`
> - 16 命令架构: `DailyWork/wechat/drafts/E8-command-registry-architecture-v2.md`
