# PyClaw 开发路线图

> 最后更新：2026-05-02
> 数据源：`openspec/changes/pyclaw-architecture/tasks.md`

## 当前状态

| 指标 | 数值 |
|------|------|
| 总 Roadmap Tasks | 100 |
| 已完成 | 86 (86%) |
| 剩余 | 14 (14%) |
| 测试覆盖 | 599 passed, 19 skipped |
| 已交付 Changes | 12 个（全部 complete） |

## 模块完成度

```
§1  项目脚手架          ██████████████████████████████ 6/6   ✅
§2  存储层协议          ████████████████████████░░░░░░ 4/5   🟡
§3  会话存储            ████████████████████████░░░░░░ 6/7   🟡
§4  Agent 核心          ██████████████████████████████ 10/10 ✅
§5  Skill Hub           ██████████████████████████████ 9/9   ✅
§6  渠道系统            ██████████████████████████████ 4/4   ✅
§7  飞书渠道            ██████████████████████████████ 10/10 ✅
§8  Web 渠道            ██████████████████████████████ 11/11 ✅
§9  记忆存储            ███░░░░░░░░░░░░░░░░░░░░░░░░░░░ 1/9   🔲
§10 Dreaming 引擎       ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ 0/6   🔲
§11 应用编排            ██████████████████████████░░░░ 5/7   🟡
§12 文档                ████████████████████░░░░░░░░░░ 4/6   🟡
```

## 分阶段执行计划

### Phase 1：可演示 ✅ 已完成

**目标**：任何人通过浏览器即可体验 PyClaw，无需飞书账号。

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 1 交付物                                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  用户 ──HTTP/WS──▶ PyClaw API ──▶ Agent ──▶ 流式回复       │
│                       │                                     │
│                       ▼                                     │
│              Bearer Token 鉴权                              │
│              WebSocket 流式推送                              │
│              docker compose up 一键启动                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

| 任务 | 描述 | 工时 |
|------|------|------|
| §8.1 | HTTP API：POST /api/chat/send, GET /api/sessions | 1 天 |
| §8.2 | WebSocket：流式推送 TextChunk | 1 天 |
| §8.3 | Bearer token 鉴权中间件 | 2 小时 |
| §8.4 | 接入 app.py | 2 小时 |
| §8.5 | 测试 | 半天 |
| §11.3 | 优雅关闭（SIGTERM drain） | 2 小时 |
| §11.6 | Dockerfile | 2 小时 |
| §11.7 | E2E 集成测试 | 半天 |

**完成后解锁**：
- 部署公共 demo（一台云服务器即可）
- 公众号文章发布（读者可直接体验）
- GitHub README 可放 live demo 链接

---

### Phase 2：有记忆（2 周）

**目标**：Agent 跨会话记住用户——从"对话工具"变为"个人助手"。

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 2 数据流                                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  对话结束                                                    │
│     │                                                       │
│     ▼                                                       │
│  ingest() ──chunk──▶ embed ──▶ sqlite-vec / pgvector        │
│                                                             │
│  新对话开始                                                  │
│     │                                                       │
│     ▼                                                       │
│  assemble() ──query──▶ hybrid search ──▶ 注入 system prompt │
│                        (vector + BM25)                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

| 任务 | 描述 | 工时 |
|------|------|------|
| §9.1 | Memory 模型 + Protocol 定义 | 半天 |
| §9.2 | SQLite + sqlite-vec 实现 | 1 天 |
| §9.3 | 文本分块（400 token / 80 overlap） | 半天 |
| §9.4 | Embedding 生成（litellm 统一接口） | 1 天 |
| §9.5 | 混合检索（向量 + 文本加权） | 1 天 |
| §9.7 | 测试 | 1 天 |
| §9.9 | Mem0ContextEngine 集成到 agent | 2 天 |
| §9.6 | PostgreSQL + pgvector（生产）| 1 天（可选）|

**关键设计决策**：
- Embedding 选型：litellm 统一接口（与 LLM 同 provider）
- 开发模式：sqlite-vec（零依赖），生产：pgvector
- 分块策略：400 token 对齐典型上下文窗口预算

---

### Phase 3：自我整理（2 周）

**目标**：Agent 空闲时自动整理记忆，从短期对话中提炼长期知识。

```
┌─────────────────────────────────────────────────────────────┐
│  Dreaming 三阶段                                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Light Dreaming (每小时)                                     │
│  └─▶ 去重 + 候选暂存                                        │
│                                                             │
│  Deep Dreaming (每天)                                        │
│  └─▶ LLM 提炼：对话片段 → 长期知识                          │
│      "用户喜欢简洁的回答风格"                                │
│      "用户的项目用 Python + FastAPI"                          │
│                                                             │
│  REM Dreaming (每周)                                         │
│  └─▶ 跨记忆模式发现                                         │
│      "用户周一常问项目规划，周五常做总结"                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

| 任务 | 描述 | 工时 |
|------|------|------|
| §10.1 | APScheduler + Redis job store | 1 天 |
| §10.2 | Leader election（多实例只一个做） | 半天 |
| §10.3 | Light dreaming：去重 + 候选 | 1 天 |
| §10.4 | Deep dreaming：LLM 提炼 | 2 天 |
| §10.5 | REM dreaming：模式发现 | 2 天 |
| §10.6 | 测试 | 1 天 |

---

### Phase 4：企业就绪（1 周）

**目标**：水平扩展到多实例部署。

| 任务 | 描述 | 工时 |
|------|------|------|
| §11.2 | Worker identity + 健康注册 | 1 天 |
| §11.5 | Session affinity gateway | 2 天 |
| §12.5 | 配置参考文档 | 半天 |
| §12.6 | 部署指南（多实例） | 半天 |
| §2.4 | File lock fallback | 半天 |
| §3.3 | File session store（零依赖模式） | 1 天 |

---

## 延迟决策

以下项目经过评估，确认当前阶段不实施：

| 项目 | 延迟理由 | 触发条件 |
|------|---------|---------|
| Session Affinity Gateway (35 tasks) | 单实例 + Redis 足够；当前零 ROI | 实际多实例部署时 |
| File session store | InMemory(开发) + Redis(生产) 覆盖全场景 | 用户要求零依赖模式 |
| `requires.config` 资格检查 | PyClaw 无 per-skill config schema | 设计 per-skill 设置时 |
| Skill `skillKey` 覆盖 | 低影响，无真实技能仅依赖此字段 | 需要完全 OpenClaw 对等时 |
| 技能 nested root 启发式 | 发现层的边缘 case | 用户上报问题时 |

## 本次 Session 关键技术决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 技能发现时机 | per-request（在 runner 层）| workspace_path 只有 per-request 才知道 |
| Prompt 渲染归属 | `PromptInputs.skills_prompt: str`，prompt.py 拥有 | Budget/compact 逻辑无法适配 SkillSummary 接口 |
| 资格检查顺序 | OS → always → bins → anyBins → env | 精确匹配 OpenClaw；OS 不可被 always 绕过 |
| ClawHub 字段映射 | displayName/summary/latestVersion.version | 适配实际 API 响应（与文档不同） |
| CLI 框架 | argparse（标准库） | 4 个命令不需要额外依赖 |

## 风险登记

| 风险 | 影响 | 缓解 |
|------|------|------|
| ClawHub API Schema 变更 | 技能安装/搜索失败 | 有 mock 单元测试；字段映射隔离在一个函数 |
| sqlite-vec Python 3.12 兼容性 | Memory Store 无法启动 | 备选：hnswlib 或 numpy cosine |
| Embedding API 开发成本 | 预算超支 | litellm + mock embedding 跑测试 |
| 单实例 Redis SPOF | 全部会话丢失 | Redis 持久化(RDB/AOF)；Phase 4 file 备选 |
| Web Channel 安全 | 未授权访问 | Bearer token + rate limit 从第一天起 |

## UI/UX 优化 ✅ 已完成

Web Channel v1 验证时发现的界面问题。参考 DeepSeek 界面风格，已在 `enhance-web-ui` change 中全部完成。

| 项目 | 描述 | 状态 |
|------|------|------|
| 对话区域居中 | `max-w-3xl mx-auto`，对话和输入框同宽居中 | ✅ |
| 简化消息气泡 | 用户消息右对齐 + 细边框，assistant 左对齐无背景 | ✅ |
| Session 标题用内容 | 用第一条用户消息作标题（后端 `title` 字段） | ✅ |
| Session 时间分组 | 侧边栏按"Today"/"Last 7 days"/"Earlier"分组 | ✅ |
| 集群状态栏 | 底部 32px 状态栏 Worker 圆点（admin only） | ✅ |
| Markdown 渲染 | react-markdown + remark-gfm，代码块复制按钮 | ✅ |
| 浅色模式 | 纯白背景、#f3f4f6 代码块、#e5e7eb 边框 | ✅ |

---

## 项目结构优化

基于 Oracle 架构审核 + 业界对比（LangChain、OpenAI Agents、CrewAI、Pydantic AI）。

### 已完成 ✅

| 项 | 描述 | 状态 |
|---|------|------|
| P0 | session_router 回调注入，解除 feishu 耦合 | ✅ |
| P2 | 提取 SkillProvider Protocol，core/ 不再导入 skills/ | ✅ |
| P5 | hooks.py AgentHook Protocol 补全 before/after_compaction | ✅ |
| P6 | dispatch.py 移入 feishu/（仅 feishu 使用） | ✅ |

### 待执行

| 项 | 描述 | 工时 | 触发条件 |
|---|------|------|---------|
| P1 | 删除死代码（空 stubs、channels/models.py、dead facades） | 30 min | 开始 Memory Store 前 |
| P3 | InMemorySessionStore 延迟导入（保留零配置 DX） | 30 min | 可选，低优先级 |

### P1 待删除清单

| 目标 | 说明 |
|------|------|
| `orchestration/` | 空包（只有 docstring） |
| `plugins/` | 空包树（dreaming/ + memory/ 全是空 stub） |
| `storage/memory/` | 空 stub（postgres.py + sqlite.py 各 1 行） |
| `storage/config/` | 空包 |
| `storage/lock/file.py` | 空 stub |
| `storage/session/file.py` | 空 stub |
| `infra/config.py` | 空 |
| `infra/logging.py` | 空 |
| `channels/models.py` | 死代码（FeishuSender/Message，无人引用） |
| `registry.py` | ChannelRegistry 从未实例化 |
| `base.py` 中 `OutboundReply` | 从未使用 |
| `storage/__init__.py` re-exports | 仅 1 个测试使用 facade |
| `storage/protocols.py` 中 `MemoryStore`/`ConfigStore` | 无实现者 |

---

## 下一步行动建议

### 立即可做（本周）

| 优先级 | 行动 | 预计工时 | 价值 |
|--------|------|---------|------|
| 🥇 | **P1 删除死代码** | 30 min | Memory Store 前置清理，避免在空 stubs 上写实现 |
| 🥇 | **§13.4 接通 cluster.status WS 推送** | 2h | 前端 ClusterPanel 终于能工作 |
| 🥈 | **§11.3 优雅关闭（SIGTERM drain）** | 2h | 生产部署必需 |

### 核心开发（下周起）

| 阶段 | 内容 | 工时 |
|------|------|------|
| §9.1-9.5 | Memory Store：Protocol + SQLite + 分块 + Embedding + 混合检索 | 1.5 周 |
| §9.7 | 测试 | 1 天 |
| §9.9 | Mem0ContextEngine 集成到 agent | 2 天 |

### 收尾（第三周）

| 行动 | 工时 |
|------|------|
| §12.5 配置参考文档 | 半天 |
| §12.6 部署指南 | 半天 |
| 公众号文章 #2："Agent 的记忆系统该怎么设计" | 1 天 |

### 不做的事项

| 项目 | 理由 |
|------|------|
| §10 Dreaming Engine | 依赖 §9 完成 |
| §11.5 Session Affinity | 单实例 + Redis 锁够用 |
| §2.4 File lock / §3.3 File session | InMemory + Redis 覆盖全场景 |
| P4 提取 runner.py 步骤函数 | Oracle 评估为伪改善（线性管道 top-to-bottom 是最可读结构） |

## 用户隔离（当前状态 + 升级路径）

> 详见 architecture-decisions.md D26

**当前定位**：个人/小团队助手，非多租户 SaaS。Session 数据、Redis 键、飞书 workspace 已完全隔离。

**已知限制**（当前可接受）：
- Web 用户共享 tool_workspace_path（单用户 admin 下无影响）
- BashTool 无沙箱（依赖 Tool Approval Hook 审批高风险操作）

**升级触发条件**：
- 多 web 用户接入 → 实施 per-user workspace（1h）
- 公开 demo / 不信任用户 → 实施 BashTool 沙箱（1-3 天）
- 企业多团队 → Store 层 ACL + per-user AGENTS.md（3h）

**§9 Memory Store 约束**：memory key 必须含 session_key 前缀，确保跨用户记忆完全隔离。

---

## 公众号发布节奏

| 周次 | 标题 | 前置条件 |
|------|------|---------|
| 第 1 周 | 从 TypeScript 单体到存算分离 | Web Channel 完成（附 demo） |
| 第 2 周 | 为什么 pyclaw 选了 Python 而不是 Go | 无 |
| 第 4 周 | Agent 的上下文引擎该怎么设计 | Memory Store 完成 |
| 第 5 周 | 让 openclaw 的 skill 零改动跑在 pyclaw 上 | 无（已实现）|
