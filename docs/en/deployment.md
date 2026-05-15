# Deployment Guide

PyClaw runs anywhere from a single-file local script up to a multi-instance
K8s cluster. This guide covers four typical shapes with executable steps:

1. **Local development** (`./scripts/start.sh`)
2. **Single-instance production** (Docker container + external Redis)
3. **Multi-instance production** (`deploy/docker-compose.multi.yml` — 3 workers + Redis + nginx)
4. **Local multi-worker dev** (`make worker1/2/3 + nginx-start`, no Docker)

Each shape includes a **health check + troubleshooting** subsection.

---

## 1. Local development

Fastest path, ~5 minutes:

```bash
git clone https://github.com/Timeflys2018/pyclaw.git
cd pyclaw

# Copy the config template, fill in at least one provider API key
cp configs/pyclaw.example.json configs/pyclaw.json
$EDITOR configs/pyclaw.json

./scripts/start.sh
```

`start.sh` will:
1. Create `.venv/` if missing
2. `pip install -e ".[dev]"` for dependencies
3. `cd web && npm install && npm run build` if `web/dist/` is absent
4. Probe Redis (use it if available, otherwise in-memory mode)
5. Launch `uvicorn pyclaw.app:create_app --factory --host 0.0.0.0 --port 8000 --reload`

Open http://localhost:8000, log in with the credentials you set in
`configs/pyclaw.json` (default: `admin` / `changeme`).

### Health checks

- `GET http://localhost:8000/health` returns `{"status": "ok"}`
- `--reload` mode restarts on Python source changes
- Frontend dev: run `cd web && npm run dev` separately (Vite on 5173, proxies
  to 8000 via `vite.config.ts`)

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Address already in use` | port 8000 occupied | `lsof -ti :8000 | xargs kill` |
| `pydantic_core._pydantic_core.ValidationError` | malformed `pyclaw.json` | diff against [`configs/pyclaw.example.json`](../../configs/pyclaw.example.json) |
| Blank page after login | frontend not built | `cd web && npm install && npm run build` |
| 401/403 from model calls | missing `apiKey` | edit `configs/pyclaw.json` providers.<name>.apiKey |

---

## 2. Single-instance production (Docker)

Suited to personal / small-team use, one host running PyClaw + Redis:

```bash
# 1. Prepare config
cp configs/pyclaw.example.json configs/pyclaw.production.json
$EDITOR configs/pyclaw.production.json
# Required edits: web.jwtSecret, web.adminToken, web.users with strong creds,
# providers.<name>.apiKey, redis.host = "redis"

# 2. Launch
docker compose up -d

# 3. Verify
curl -fsS http://localhost:8000/health
docker compose logs -f pyclaw
```

The shipped [`docker-compose.yml`](../../docker-compose.yml) provides:
- PyClaw container (multi-stage build: node:20-alpine → python:3.12-slim)
- Redis 7 with appendonly persistence
- `depends_on` healthcheck (PyClaw waits for Redis)
- Volumes: `pyclaw-workspaces` (user workspace dirs), `redis-data` (Redis AOF)
- Healthcheck: 30s interval `curl /health`

### Reverse proxy (TLS)

Production should sit behind an outer nginx/Caddy for TLS and domain handling.
Minimal Caddy config:

```caddyfile
chat.example.com {
    reverse_proxy localhost:8000 {
        # WebSocket
        transport http {
            read_timeout 300s
        }
    }
}
```

### Upgrading

```bash
git pull
docker compose build --no-cache pyclaw
docker compose up -d pyclaw
# Zero downtime: nginx switches once the new container is healthy; Redis
# data is preserved.
```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Container starts but healthcheck fails | uvicorn still in lifespan startup | `docker compose logs` to inspect; usually clears within 30s |
| Cannot reach Redis | wrong container name | `redis.host` in `pyclaw.json` must be `"redis"` (the compose service name) |
| Sessions disappear after restart | session_backend is memory | switch to `"redis"`; the `redis-data` volume already persists data |

---

## 3. Multi-instance production (active-active)

Three PyClaw workers + Redis + nginx single entry. The new
[`deploy/docker-compose.multi.yml`](../../deploy/docker-compose.multi.yml) runs
the whole stack with one command:

### Steps

```bash
# 1. Prepare secrets
cp deploy/.env.example deploy/.env
$EDITOR deploy/.env
# Required edits: PYCLAW_WEB_JWT_SECRET, PYCLAW_WEB_ADMIN_TOKEN
# Generate via: python -c "import secrets; print(secrets.token_urlsafe(64))"

# 2. Prepare shared config (used by all 3 workers)
cp configs/pyclaw.example.json configs/pyclaw.json
$EDITOR configs/pyclaw.json
# Critical edits:
# - storage.session_backend: "redis"
# - storage.lock_backend: "redis"
# - redis.host: "redis"
# - affinity.enabled: true
# - channels.web.enabled: true
# - providers.<name>.apiKey set

# 3. Launch the full stack (worker1 + worker2 + worker3 + redis + nginx)
docker compose -f deploy/docker-compose.multi.yml --env-file deploy/.env up -d

# 4. Wait for all healthchecks (typically <60s)
docker compose -f deploy/docker-compose.multi.yml --env-file deploy/.env ps

# 5. Verify single entry
curl -fsS http://localhost/health    # nginx routes to any healthy worker

# 6. Verify the affinity gateway
docker compose -f deploy/docker-compose.multi.yml exec worker1 \
    python deploy/scripts/affinity_status.py
```

### How it works

- nginx uses `ip_hash` to pin same-IP clients to the same worker (this reduces
  PubSub forwarding overhead; it is **not** a correctness guarantee)
- The PyClaw Session Affinity Gateway is the real correctness guarantee: each
  session locks to a specific worker (Redis `pyclaw:affinity:<session_key>`).
  If nginx misroutes, the receiving worker forwards the message to the owning
  worker via Redis PubSub.
- Worker dies → TTL expires → next request triggers `force_claim`, migrating
  the session to a live worker (PUBLISH-subscriber-count detection).

### Scaling up / down

**Add a worker**:

1. Add `worker4: { <<: *worker }` (anchor reuse) to `deploy/docker-compose.multi.yml`
2. Add `server worker4:8000;` to the upstream block in `deploy/nginx.conf`
3. `docker compose -f deploy/docker-compose.multi.yml up -d worker4 nginx`

**Remove a worker**:

1. Mark it `down` in nginx: `server worker3:8000 down;` then reload nginx —
   blocks new requests
2. Wait a few minutes for existing affinity to expire naturally
3. `docker compose stop worker3`

### Health checks

- Per-worker: `GET :8000/health` (already wired into compose healthchecks)
- nginx: `GET :80/health` proxies to any healthy worker
- Affinity status: `make affinity-status` or `python deploy/scripts/affinity_status.py`
  lists all live workers + current session ownership

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| nginx 502 but worker is healthy | nginx upstream not yet reloaded | `docker compose restart nginx` |
| Switching workers loses history | session_backend not redis | must be `"redis"` |
| Workers do not collaborate | affinity.enabled missing | `"affinity": {"enabled": true}` in `pyclaw.json` |
| Redis OOM | maxmemory not set | already `--maxmemory 512mb --maxmemory-policy allkeys-lru` in `docker-compose.multi.yml` |
| Brief 5xx during upgrade | rolling restart issue | use `docker compose up -d --no-deps worker1` and roll one at a time |

---

## 4. Local multi-worker dev (no Docker)

For debugging the affinity gateway's cross-worker forwarding without Docker:

```bash
# Run one worker per terminal (ports 8000 / 8001 / 8002)
make worker1   # tee /tmp/pyclaw-w1.log
make worker2   # tee /tmp/pyclaw-w2.log
make worker3   # tee /tmp/pyclaw-w3.log

# In a fourth terminal, start the local nginx (port 9000)
make nginx-start

# Single entry
curl -fsS http://localhost:9000/health

# Inspect affinity distribution
make affinity-status

# Clear Redis (handy during dev)
make redis-clean
```

See `make help` for all shortcuts. `deploy/nginx-dev.conf` is the dev nginx
config (local 9000 → three workers).

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `make worker2` reports `Address already in use` | 8001 occupied | `lsof -ti :8001 | xargs kill` |
| affinity-status shows no workers | Redis down or wrong config | `redis-cli ping` should return PONG |
| Two workers both claim "I own session X" | multiple PyClaw deployments share the same Redis without prefixes | change `redis.keyPrefix` to isolate |

---

## Pre-deployment checklist

Run through this list before going live:

- [ ] `configs/pyclaw.json` customized from [`pyclaw.example.json`](../../configs/pyclaw.example.json)
- [ ] `web.jwtSecret` changed (default `"change-me-in-production"` is a sentinel
  value the code warns about)
- [ ] `web.adminToken` set, length ≥ 32 bytes
- [ ] `web.users` is no longer `[{id: "admin", password: "changeme"}]`
- [ ] At least one provider has a real `apiKey`
- [ ] Multi-instance: `affinity.enabled: true` + `storage.session_backend: "redis"` + `storage.lock_backend: "redis"`
- [ ] Multi-instance: every worker mounts the same `pyclaw.json` (read-only)
- [ ] Memory enabled: `memory.base_dir` is on a shared volume (multi-instance) or single-instance
- [ ] Backups planned: Redis AOF + `~/.pyclaw/memory/*.db` SQLite files + workspace dirs
- [ ] Outer reverse proxy (Caddy/nginx/ALB) configured for TLS + real domain
- [ ] Monitoring: scrape `/health` + periodically run `make affinity-status`

---

## See also

- [Configuration reference](./configuration.md) — every Settings field
- [Architecture decisions](./architecture-decisions.md) — deployment-relevant
  decisions (D5 compute-storage separation, D24 multi-instance, D25 session
  affinity)
- [Affinity gateway smoke test report](../../reports/affinity-gateway-smoke-2026-05-15.md)
  — multi-instance scenario, real-world verification
