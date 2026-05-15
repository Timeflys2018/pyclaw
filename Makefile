# PyClaw dev workflow shortcuts
# Run from project root: `make <target>`

.PHONY: help nginx-start nginx-stop nginx-reload nginx-test nginx-status nginx-log \
        worker1 worker2 worker3 worker-status worker-kill \
        affinity-status redis-clean

CONF := $(PWD)/deploy/nginx-dev.conf
PREFIX := $(PWD)/deploy/

help:
	@echo "PyClaw dev shortcuts (run from project root):"
	@echo ""
	@echo "  Nginx (reverse proxy on :9000 → workers on :8000/:8001/:8002):"
	@echo "    make nginx-start    Start nginx (single entry: http://localhost:9000)"
	@echo "    make nginx-stop     Stop nginx (graceful)"
	@echo "    make nginx-reload   Reload config without dropping connections"
	@echo "    make nginx-test     Validate nginx config syntax"
	@echo "    make nginx-status   Show nginx process + port"
	@echo "    make nginx-log      Tail nginx access log"
	@echo ""
	@echo "  Workers (each runs in its own terminal):"
	@echo "    make worker1        PORT=8000 .venv/bin/python -m pyclaw.app | tee /tmp/pyclaw-w1.log"
	@echo "    make worker2        PORT=8001 ..."
	@echo "    make worker3        PORT=8002 ..."
	@echo "    make worker-status  Show all worker processes + listen ports"
	@echo ""
	@echo "  Observability:"
	@echo "    make affinity-status   Show current pyclaw:affinity:* keys"
	@echo "    make redis-clean       Remove all pyclaw:affinity:* + dead workers from ZSET"

# ---------- Nginx ----------

nginx-start:
	@echo "→ Starting nginx (entry: http://localhost:9000)"
	@nginx -c $(CONF) -p $(PREFIX)
	@sleep 1
	@$(MAKE) -s nginx-status

nginx-stop:
	@echo "→ Stopping nginx (graceful)"
	@nginx -s quit -c $(CONF) -p $(PREFIX) 2>/dev/null || echo "  (already stopped)"

nginx-reload:
	@echo "→ Reloading nginx config"
	@nginx -s reload -c $(CONF) -p $(PREFIX)

nginx-test:
	@nginx -t -c $(CONF) -p $(PREFIX)

nginx-status:
	@echo "→ Nginx processes:"
	@ps -ef | grep nginx | grep -v grep | awk '{printf "  PID=%-7s %s\n", $$2, $$8}' || echo "  (none)"
	@echo "→ Port 9000:"
	@lsof -iTCP:9000 -sTCP:LISTEN 2>/dev/null | tail -n +2 | awk '{printf "  %s PID=%s %s\n", $$1, $$2, $$8}' || echo "  (not listening)"

nginx-log:
	@tail -f $(PREFIX)logs/nginx-access.log

# ---------- Workers ----------
# 注意: 每个 worker 命令需要在独立 terminal 跑 (前台进程)

worker1:
	PORT=8000 .venv/bin/python -m pyclaw.app 2>&1 | tee /tmp/pyclaw-w1.log

worker2:
	PORT=8001 .venv/bin/python -m pyclaw.app 2>&1 | tee /tmp/pyclaw-w2.log

worker3:
	PORT=8002 .venv/bin/python -m pyclaw.app 2>&1 | tee /tmp/pyclaw-w3.log

worker-status:
	@echo "→ PyClaw worker processes:"
	@ps -ef | grep -E "PORT=80|pyclaw.app" | grep -v grep | awk '{printf "  PID=%-7s %s\n", $$2, substr($$0, index($$0, $$8))}' | head -10 || echo "  (none)"
	@echo ""
	@echo "→ Listening ports:"
	@lsof -iTCP:8000,8001,8002 -sTCP:LISTEN 2>/dev/null | tail -n +2 | awk '{printf "  PID=%s %s\n", $$2, $$9}' || echo "  (none listening)"

# ---------- Observability ----------

affinity-status:
	@.venv/bin/python deploy/scripts/affinity_status.py

redis-clean:
	@echo "→ Cleaning pyclaw:affinity:* and stale worker entries from ZSET"
	@.venv/bin/python deploy/scripts/redis_clean.py
