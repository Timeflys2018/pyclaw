# 多用户部署

Sprint 3 把 PyClaw 的隔离模型从「个人/小团队」（D26）升级为**可配置的多用
户 + 角色 + 沙箱隔离**。本文是把 PyClaw 当作共享服务运行的运维手册。

## 核心概念

| 概念 | 控制什么 | 存储位置 |
|---|---|---|
| **Role**（`admin` / `member`） | 是否能跑 `/admin user set/list/show` 和 `/admin sandbox check` | Redis 实时（`pyclaw:userprofile:{channel}:{user_id}`）+ JSON 兜底 |
| **`tier_default`** | 4 层 tier resolution 链中的「per-user」层（per-message > sessionKey > **user** > deployment） | 同上 |
| **`tools_requiring_approval`** | 设值时 **REPLACE**（替换，不是 union）渠道默认；`[]` 表示「啥都不审批」 | 同上 |
| **`env_allowlist`** | BashTool 子进程额外允许的 env 变量（受硬编码 deny floor 约束） | 同上 |
| **`sandbox_overrides`** | 在部署默认基础上**追加** filesystem / network 配置 | 同上 |

## 渠道隔离（Sprint 3 D2）

Web 的 `alice` 和飞书的 `ou_alice` 是**两个独立的 profile**，存在不同的 Redis
key 与 JSON 数组下：

```
pyclaw:userprofile:web:alice
pyclaw:userprofile:feishu:ou_a1b2c3
```

跨渠道身份映射（「这个飞书用户其实就是 Web 的 alice」）属于 Sprint 3.x 议
程，本期不实现。

## 通过 JSON 配置用户（运维方式）

```json
{
  "channels": {
    "web": {
      "users": [
        {
          "id": "alice",
          "password": "...",
          "role": "admin",
          "tier_default": "yolo"
        },
        {
          "id": "bob",
          "password": "...",
          "role": "member",
          "tier_default": "read-only",
          "tools_requiring_approval": ["bash"]
        }
      ]
    }
  }
}
```

JSON 改动需重启生效。

## 运行时配置用户（admin slash 命令）

admin 在任意聊天中：

```
/admin user list
/admin user show bob
/admin user set bob tier=read-only role=member
```

Redis 写入（TTL 30 天滑动窗口）下一条消息即生效，**不用重启**。Redis 与 JSON
同 `user_id` 时 **Redis 胜出**（覆盖 JSON）。

### Last-admin 保护（4-slot review F4）

如果 alice 是**唯一**的 admin，`/admin user set alice role=member` 会被拒
绝：`❌ Cannot demote the last admin. Promote another user to admin first.`
要先提升另一个用户为 admin 才能降级自己。

## Tier Resolution（Sprint 3 4 层）

每次工具调用，runner 按以下顺序解析（首个非 None 生效）：

1. **Per-message 覆盖** — Web 输入框 tier picker / 飞书 `/tier <tier> --once`
2. **Per-sessionKey 覆盖** — `/tier <tier>`（绑 sessionKey 持久化）
3. **Per-user `tier_default`** ← Sprint 3 新增
4. **渠道部署默认** — `settings.channels.{web,feishu}.default_permission_tier`

Per-server `forced_tier`（Sprint 2）叠加在以上之上：只能**收紧**，不能放宽。

## 审计字段增强（Sprint 3）

每条工具决策审计行新增：

```json
{
  "event": "tool_approval_decision",
  "user_id": "alice",
  "role": "member",
  "sandbox_backend": "srt",
  "tier_source": "user-default",
  ...
}
```

运维只用一句 `jq` 就能查「alice 上周在沙箱里做了什么」。

## 推荐生产配置

1. 设置 `sandbox.policy="srt"` + 安装 `srt`（详见 [sandbox.md](./sandbox.md)）
2. 在 `users[]` 至少配一个 `role=admin`
3. Web/飞书渠道 `default_permission_tier="approval"`
4. 不常做交互式编辑的 Web 用户设 `tier_default="read-only"`
5. **必须启用 Redis**（`storage.session_backend=redis`）——否则
   `/admin user set` 不能持久化运行时改动

## 相关文档

- [权限与审批分级](./permissions.md) — tier 完整语义
- [沙箱](./sandbox.md) — 隔离后端配置
- [架构决策 D26](./architecture-decisions.md)
