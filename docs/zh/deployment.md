# 部署指南

PyClaw 可以从单文件本地脚本一直跑到 K8s 多实例集群。本指南给四种典型形态的可执行步骤:

1. **本地开发** (`./scripts/start.sh`)
2. **单实例生产** (Docker 单容器 + 外置 Redis)
3. **多实例生产** (`deploy/docker-compose.multi.yml` — 3 worker + Redis + nginx)
4. **本机多 worker dev** (`make worker1/2/3 + nginx-start` — 不用 Docker)

每种形态后面都有 **健康检查 + 故障排查** 子节。

---

## 1. 本地开发

最快路径, 5 分钟跑起来:

```bash
git clone https://github.com/Timeflys2018/pyclaw.git
cd pyclaw

# 复制配置模板, 填入至少一个模型 provider 的 API key
cp configs/pyclaw.example.json configs/pyclaw.json
$EDITOR configs/pyclaw.json

./scripts/start.sh
```

`start.sh` 会:
1. 创建 `.venv/` (如不存在)
2. `pip install -e ".[dev]"` 安装依赖
3. `cd web && npm install && npm run build` (如 `web/dist/` 不存在)
4. 探测 Redis (有就用, 没有用进程内存)
5. 启动 `uvicorn pyclaw.app:create_app --factory --host 0.0.0.0 --port 8000 --reload`

打开 http://localhost:8000, 默认登录 `admin` / `changeme` (在 configs/pyclaw.json 里改)。

### 健康检查

- `GET http://localhost:8000/health` 返回 `{"status": "ok"}`
- `--reload` 模式下改 Python 代码自动重启
- 前端开发改 `web/src/`: 单开 `cd web && npm run dev` (Vite 跑在 5173, 通过 `vite.config.ts` 反代到 8000)

### 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `Address already in use` | 8000 被占 | `lsof -ti :8000 | xargs kill` |
| `pydantic_core._pydantic_core.ValidationError` | `pyclaw.json` 字段格式错 | 对照 [`configs/pyclaw.example.json`](../../configs/pyclaw.example.json) |
| 启动后访问页面空白 | 前端没 build | `cd web && npm install && npm run build` |
| 模型调用 401/403 | `apiKey` 没填 | 改 `configs/pyclaw.json` providers.<name>.apiKey |

---

## 2. 单实例生产 (Docker)

适合个人/小团队, 一台主机跑 PyClaw + Redis:

```bash
# 1. 准备配置
cp configs/pyclaw.example.json configs/pyclaw.production.json
$EDITOR configs/pyclaw.production.json
# 必改: web.jwtSecret, web.adminToken, web.users 用强密码,
# providers.<name>.apiKey, redis.host = "redis"

# 2. 启动
docker compose up -d

# 3. 验证
curl -fsS http://localhost:8000/health
docker compose logs -f pyclaw
```

[`docker-compose.yml`](../../docker-compose.yml) 已经带:
- PyClaw 容器 (multi-stage build: node:20-alpine 构建前端 → python:3.12-slim 运行)
- Redis 7 + appendonly 持久化
- depends_on healthcheck (Redis 起来后才启 PyClaw)
- volumes: `pyclaw-workspaces` (用户工作区), `redis-data` (Redis AOF)
- 健康检查: 30s 间隔 `curl /health`

### 反向代理 (TLS)

生产应该在外面加一层 nginx/Caddy 处理 TLS 和域名。最简 Caddy 配置:

```caddyfile
chat.example.com {
    reverse_proxy localhost:8000 {
        # WebSocket 长连接
        transport http {
            read_timeout 300s
        }
    }
}
```

### 升级到新版本

```bash
git pull
docker compose build --no-cache pyclaw
docker compose up -d pyclaw
# 0 downtime: 新容器健康后 nginx 自动切, Redis 数据继续用
```

### 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| 容器启动 healthcheck 失败 | uvicorn 还在 lifespan startup | `docker compose logs` 看具体阶段; 一般 30 秒内会过 |
| Redis 连不上 | 容器名写错 | configs/pyclaw.json 里 `redis.host` 必须是 `"redis"` (compose service 名) |
| 重启后会话丢 | session_backend 是 memory | 改成 `"redis"`; volume 已经持久化 redis-data |

---

## 3. 多实例生产 (active-active)

3 个 PyClaw worker + Redis + nginx 单入口。新增的 [`deploy/docker-compose.multi.yml`](../../deploy/docker-compose.multi.yml) 一条命令拉起整套:

### 步骤

```bash
# 1. 准备 secrets
cp deploy/.env.example deploy/.env
$EDITOR deploy/.env
# 必改: PYCLAW_WEB_JWT_SECRET, PYCLAW_WEB_ADMIN_TOKEN
# 用 python -c "import secrets; print(secrets.token_urlsafe(64))" 生成

# 2. 准备共享配置 (3 个 worker 共用)
cp configs/pyclaw.example.json configs/pyclaw.json
$EDITOR configs/pyclaw.json
# 关键改动:
# - storage.session_backend: "redis"
# - storage.lock_backend: "redis"
# - redis.host: "redis"
# - affinity.enabled: true
# - channels.web.enabled: true
# - providers.<name>.apiKey 填好

# 3. 启动整套 (worker1 + worker2 + worker3 + redis + nginx)
docker compose -f deploy/docker-compose.multi.yml --env-file deploy/.env up -d

# 4. 等所有 healthcheck 过 (一般 60 秒内)
docker compose -f deploy/docker-compose.multi.yml --env-file deploy/.env ps

# 5. 验证单入口
curl -fsS http://localhost/health    # nginx 转发到任一 healthy worker

# 6. 验证 affinity gateway
docker compose -f deploy/docker-compose.multi.yml exec worker1 \
    python deploy/scripts/affinity_status.py
```

### 工作机理

- nginx 用 `ip_hash` 把同一 client IP 分到同一 worker (减少 PubSub 转发开销; **不是**正确性保证)
- PyClaw 的 Session Affinity Gateway 是真正的正确性保证: 每个会话锁定到一个 worker (Redis `pyclaw:affinity:<session_key>`); 即使 nginx 错路, 收到请求的 worker 也会通过 Redis PubSub 把消息转发给真正的 owner worker
- worker 死了 → TTL 过期 → 下次请求 `force_claim` 会把会话迁移到活的 worker (PUBLISH-subscriber-count 检测)

### 扩缩容

**加 worker**:

1. `deploy/docker-compose.multi.yml` 加一个 `worker4: { <<: *worker }` (用 anchor)
2. `deploy/nginx.conf` upstream 块加 `server worker4:8000;`
3. `docker compose -f deploy/docker-compose.multi.yml up -d worker4 nginx`

**减 worker**:

1. 先在 nginx 上把 worker 改成 `down`: `server worker3:8000 down;` 然后 reload nginx — 阻断新请求
2. 等几分钟让现有 affinity 自然过期
3. `docker compose stop worker3`

### 健康检查

- 每个 worker `GET :8000/health` (compose 内部 healthcheck 已配)
- nginx `GET :80/health` 走到任一 healthy worker
- Affinity 状态: `make affinity-status` 或 `python deploy/scripts/affinity_status.py` 列出所有活 worker + 当前会话归属

### 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| nginx 502 但 worker healthy | nginx 还没 reload upstream | `docker compose restart nginx` |
| 会话切换到别的 worker 后历史丢 | session_backend 不是 redis | 必须 `"redis"` |
| 多个 worker 不互通 | affinity.enabled 漏配 | configs/pyclaw.json 加 `"affinity": {"enabled": true}` |
| Redis OOM | 没设 maxmemory | docker-compose.multi.yml 已设 `--maxmemory 512mb --maxmemory-policy allkeys-lru` |
| 升级时短暂 5xx | rolling 重启 | 用 `docker compose up -d --no-deps worker1`, 一台一台来 |

---

## 4. 本机多 worker dev (无 Docker)

调试 affinity gateway 跨 worker 转发场景, 不想搞 Docker:

```bash
# 三个终端各跑一个 worker (端口 8000/8001/8002)
make worker1   # tee /tmp/pyclaw-w1.log
make worker2   # tee /tmp/pyclaw-w2.log
make worker3   # tee /tmp/pyclaw-w3.log

# 第四个终端起 nginx 反代 (本地配置, 监听 9000)
make nginx-start

# 单入口
curl -fsS http://localhost:9000/health

# 看当前 affinity 分布
make affinity-status

# 全清 Redis (开发时常用)
make redis-clean
```

`Makefile` 详情见 `make help`。`deploy/nginx-dev.conf` 是开发用的 nginx 配置 (本地 9000 → 三个 worker)。

### 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `make worker2` 报 `Address already in use` | 8001 被占 | `lsof -ti :8001 | xargs kill` |
| affinity-status 看不到 worker | Redis 没起 / configs 里 redis 配错 | `redis-cli ping` 应返 PONG |
| 两个 worker 都说 "I own session X" | 多个 PyClaw 部署用了同一个 Redis 没分前缀 | 改 `redis.keyPrefix` 隔离 |

---

## 通用部署清单

部署前过一遍:

- [ ] `configs/pyclaw.json` 已基于 [`pyclaw.example.json`](../../configs/pyclaw.example.json) 定制
- [ ] `web.jwtSecret` 已改 (默认 `"change-me-in-production"` 是哨兵值, 不改会被代码警告)
- [ ] `web.adminToken` 已改, 长度 ≥ 32 字节
- [ ] `web.users` 不再是 `[{id: "admin", password: "changeme"}]`
- [ ] 至少一个 provider 的 `apiKey` 填了真实值
- [ ] 多实例: `affinity.enabled: true` + `storage.session_backend: "redis"` + `storage.lock_backend: "redis"`
- [ ] 多实例: 每个 worker 挂同一个 `pyclaw.json` (volume mount read-only)
- [ ] 启用记忆: `memory.base_dir` 必须挂共享卷 (多实例情况下), 或者只跑单实例
- [ ] 准备好备份: Redis AOF + `~/.pyclaw/memory/*.db` SQLite 文件 + workspace 目录
- [ ] 反向代理 (Caddy/nginx/ALB) 配 TLS 和真实域名
- [ ] 监控: `/health` 端点 + `make affinity-status` 定期采集

---

## 相关文档

- [配置参考](./configuration.md) — 所有 Settings 字段含义
- [架构决策](./architecture-decisions.md) — 部署相关决策 (D5 compute-storage 分离, D24 multi-instance, D25 session affinity)
- [Session Affinity Gateway smoke 测试报告](../../reports/affinity-gateway-smoke-2026-05-15.md) — 多实例场景的实测验证
