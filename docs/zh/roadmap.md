# PyClaw 开发路线图

> 最后更新：2026-05-01
> 数据源：`openspec/changes/pyclaw-architecture/tasks.md`

## 当前状态

| 指标 | 数值 |
|------|------|
| 总 Roadmap Tasks | 90 |
| 已完成 | 69 (77%) |
| 剩余 | 21 (23%) |
| 测试覆盖 | 587 passed, 19 skipped |
| 已交付 Changes | 10 个（全部 complete） |

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

## UI/UX 优化（下一轮）

Web Channel v1 验证时发现的界面问题。参考：DeepSeek 聊天界面。

| 项目 | 描述 | 工时 |
|------|------|------|
| 对话区域居中 | `max-w-3xl mx-auto`，对话和输入框同宽居中 | 15min |
| 简化消息气泡 | 用户消息去掉蓝色填充，改为右对齐 + 细边框 | 30min |
| Session 标题用内容 | 用第一条用户消息作标题（非 session ID hash） | 30min |
| Session 时间分组 | 侧边栏按"今天"/"近7天"/"更早"分组 | 1h |
| 集群状态栏 | 底部显示 Worker 状态圆点 + "Session on Worker X"（admin only） | 2h |
| Markdown 渲染 | 标题大小、列表、行内代码、代码块+复制按钮 | 2h |
| 输入框精细化 | 圆角、阴影、与对话区域等宽居中 | 15min |
| 浅色模式 | 白色背景、细边框、匹配 DeepSeek 简洁风 | 1h |

**设计参考**：DeepSeek — 对话区域 max-width ~800px 居中、白色干净、session 按时间分组、无重色气泡。

## 公众号发布节奏

| 周次 | 标题 | 前置条件 |
|------|------|---------|
| 第 1 周 | 从 TypeScript 单体到存算分离 | Web Channel 完成（附 demo） |
| 第 2 周 | 为什么 pyclaw 选了 Python 而不是 Go | 无 |
| 第 4 周 | Agent 的上下文引擎该怎么设计 | Memory Store 完成 |
| 第 5 周 | 让 openclaw 的 skill 零改动跑在 pyclaw 上 | 无（已实现）|
