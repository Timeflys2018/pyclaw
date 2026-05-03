# PyClaw Test Report — 2026-05-03 (Change 2: implement-memory-store)

## Overview

| Category | Count | Result |
|---|---|---|
| Unit tests (existing) | 551 | ✅ All passed |
| Unit tests (new: memory store) | 48 | ✅ All passed |
| Integration tests (Redis, existing) | 8 | (skipped — not re-run) |
| Integration tests (new: Redis L1 index) | 7 | (skipped — requires PYCLAW_TEST_REDIS_HOST) |
| E2E tests (existing, Real LLM) | 6 | (skipped — not re-run, unchanged) |
| **Total unit tests** | **599** | **✅ 0 failures** |

**Ruff check:** ✅ All checks passed
**Python:** 3.12.9
**Test runner:** pytest 9.0.3

---

## Change Scope

Change 2 (implement-memory-store) 是**纯新增存储层**，不修改现有 agent runner、context engine、channel 或工具代码。因此：
- E2E 测试（Real LLM）不受影响——未触及任何 agent 调用路径
- 飞书/Web 全链路测试不适用——MemoryStore 尚未接入对话流（Change 3b 才接入）
- 本次验证聚焦于：存储层接口正确性 + 各后端实现 + 组合逻辑 + 现有测试零回归

---

## 新增测试用例详情

### 1. Protocol & 数据模型（6 tests）

| # | 测试文件 | 用例 | 验证内容 |
|---|---------|------|---------|
| 1 | test_memory_store_protocol.py | test_stub_satisfies_protocol | 实现全部 9 个 async 方法的类 `isinstance(_, MemoryStore)` 返回 True |
| 2 | | test_re_export_is_same_class | `storage.protocols.MemoryStore` 与 `storage.memory.base.MemoryStore` 是同一个类 |
| 3 | | test_memory_entry_serialization | MemoryEntry L2 序列化：layer/status/use_count 默认值正确 |
| 4 | | test_memory_entry_l3_fields | MemoryEntry L3 专有字段：last_used_at/use_count/status 正确赋值 |
| 5 | | test_archive_entry_serialization | ArchiveEntry 含 distance 字段序列化 |
| 6 | | test_archive_entry_distance_none | ArchiveEntry.distance 默认 None |

### 2. L1 Redis 索引层（8 tests）

| # | 测试文件 | 用例 | 验证内容 |
|---|---------|------|---------|
| 7 | test_redis_l1_index.py | test_index_get_returns_sorted_entries | HGETALL 解析 JSON → MemoryEntry 列表，按 updated_at 降序排列 |
| 8 | | test_index_get_empty_key_returns_empty_list | 空 key → 返回 [] |
| 9 | | test_index_update_writes_and_refreshes_ttl | HSET 写入 JSON + EXPIRE 刷新 TTL(30d) |
| 10 | | test_index_update_upsert_existing_entry | 同 entry_id 重复写入 → 更新而非新增 |
| 11 | | test_lru_eviction_by_entry_count | 超过 max_entries(30) → 按 updated_at 升序逐出最旧条目 |
| 12 | | test_lru_eviction_by_char_limit | 超过 max_chars(3000) → 按 updated_at 升序逐出直到 ≤ 限制 |
| 13 | | test_index_remove_existing_entry | HDEL 删除指定 entry |
| 14 | | test_index_remove_nonexistent_entry_no_error | 删除不存在的 entry → 静默返回，无异常 |

**Mock 方式**：使用 `unittest.mock.AsyncMock` 模拟 Redis Hash 操作（hgetall/hset/hdel/expire），通过 `side_effect` 维护内存 dict 模拟 Hash 状态。

### 3. L2/L3 SQLite 事实层 + SOP 层（10 tests）

| # | 测试文件 | 用例 | 验证内容 |
|---|---------|------|---------|
| 15 | test_sqlite_memory.py | test_store_l2_fact_and_search | L2 写入 facts 表 → FTS5 搜索命中 |
| 16 | | test_store_l3_procedure_and_search | L3 写入 procedures 表 → FTS5 搜索命中 |
| 17 | | test_l3_search_excludes_archived | L3 搜索自动过滤 status='archived' 的条目 |
| 18 | | test_cross_layer_search | 不指定 layers → 同时搜索 L2 + L3，合并结果 |
| 19 | | test_cjk_short_query_uses_like_fallback | CJK 2 字符查询 "服务" → LIKE 降级搜索仍能命中 |
| 20 | | test_latin_query_uses_fts5 | Latin 长查询 "quick brown" → FTS5 trigram 搜索命中 |
| 21 | | test_delete_removes_entry | 删除后搜索返回空 |
| 22 | | test_session_key_isolation | 写入 key_a → 搜索 key_b 返回空（per-session_key 物理隔离） |
| 23 | | test_connection_reuse | 同一 session_key 多次操作复用同一 aiosqlite 连接 |
| 24 | | test_close_releases_connections | close() 后连接 dict 清空 |

**实际 db**：使用 pytest `tmp_path` fixture，每个测试生成独立的临时 SQLite 文件。

**关键 SQL 验证点**：
- UPSERT（`INSERT...ON CONFLICT(id) DO UPDATE`）而非 INSERT OR REPLACE，保持 rowid 稳定
- FTS5 外部内容表通过触发器同步（AFTER INSERT/DELETE/UPDATE）
- FTS5 查询使用 `_escape_fts5_query()` 转义特殊字符（双引号包裹）
- < 3 字符查询自动降级为 LIKE（trigram tokenizer 要求 ≥ 3 字符）

### 4. Embedding 生成模块（3 tests）

| # | 测试文件 | 用例 | 验证内容 |
|---|---------|------|---------|
| 25 | test_embedding.py | test_dimensions | `EmbeddingClient(dimensions=4096).dimensions` 返回 4096 |
| 26 | | test_embed | `embed("text")` 调用 `litellm.aembedding()` 并返回 embedding 列表 |
| 27 | | test_embed_batch | `embed_batch(["a","b"])` 批量调用并返回列表的列表 |

**Mock 方式**：`unittest.mock.patch("pyclaw.storage.memory.embedding.aembedding")` 替换 litellm 调用。

### 5. L4 归档层 — sqlite-vec 向量搜索（6 tests）

| # | 测试文件 | 用例 | 验证内容 |
|---|---------|------|---------|
| 28 | test_archive.py | test_archive_session_writes_to_both_tables | archives 表写入 summary + archives_vec 写入 embedding 向量 |
| 29 | | test_search_archives_returns_nearest_results | 向量 KNN 搜索返回 ArchiveEntry 列表，含 distance |
| 30 | | test_embedding_failure_on_archive_still_writes_summary | embedding API 失败 → summary 仍写入，向量跳过（优雅降级） |
| 31 | | test_embedding_failure_on_search_returns_empty | embedding API 失败 → 返回空列表，无异常 |
| 32 | | test_search_empty_archives_returns_empty | 空归档 → 返回空列表 |
| 33 | | test_no_embedding_client_returns_empty | 无 EmbeddingClient 构造 → 仍能归档(纯文本)，搜索返回空 |

**前置条件**：`pytest.importorskip("sqlite_vec")` — sqlite-vec 未安装时自动跳过。

**关键验证点**：
- sqlite-vec 扩展延迟加载（首次 archive/search 时才加载，L2/L3 操作不触发）
- L2/L3/L4 共用同一个 db 文件（`{session_key}.db`）
- `archives_vec` 虚拟表使用 `vec0(embedding float[{dim}] distance_metric=cosine)`
- 向量序列化使用 `sqlite_vec.serialize_float32()`

### 6. CompositeMemoryStore 组合层（12 tests）

| # | 测试文件 | 用例 | 验证内容 |
|---|---------|------|---------|
| 34 | test_composite.py | test_index_get_delegates_to_l1 | index_get → 委托给 RedisL1Index |
| 35 | | test_index_update_delegates_to_l1 | index_update → 委托给 RedisL1Index |
| 36 | | test_index_remove_delegates_to_l1 | index_remove → 委托给 RedisL1Index |
| 37 | | test_search_delegates_to_sqlite | search → 委托给 SqliteMemoryBackend |
| 38 | | test_store_delegates_and_updates_l1 | store → 委托 sqlite.store() + 自动 l1.index_update()（L1 摘要截断 ≤ 100 chars） |
| 39 | | test_store_short_content_not_truncated | 短内容不截断 |
| 40 | | test_delete_delegates_and_removes_from_l1 | delete → 委托 sqlite.delete() + l1.index_remove() |
| 41 | | test_archive_session_delegates_to_sqlite | archive_session → 委托给 sqlite |
| 42 | | test_search_archives_delegates_to_sqlite | search_archives → 委托给 sqlite |
| 43 | | test_close_calls_all_backends | close → 调用 l1.close() + sqlite.close() |
| 44 | | test_close_catches_exceptions | 某个 backend close 抛异常 → 其他 backend 仍正常关闭 |
| 45 | | test_composite_satisfies_protocol | `isinstance(CompositeMemoryStore(...), MemoryStore)` 返回 True |

### 7. 工厂函数（3 tests）

| # | 测试文件 | 用例 | 验证内容 |
|---|---------|------|---------|
| 46 | test_factory.py | test_unknown_backend_raises_value_error | `memory_backend="postgres"` → `ValueError` |
| 47 | | test_none_redis_client_raises_value_error | `redis_client=None` → `ValueError("requires a Redis client")` |
| 48 | | test_sqlite_backend_creates_composite | 正确构造 RedisL1Index + SqliteMemoryBackend + CompositeMemoryStore |

---

## 集成测试（Redis L1 Index）

文件 `tests/integration/storage/memory/test_redis_l1_index.py`，需要设置 `PYCLAW_TEST_REDIS_HOST` 环境变量才会执行：

| # | 用例 | 验证内容 |
|---|------|---------|
| 1 | test_roundtrip_write_and_read | 真实 Redis 写入 → 读取往返 |
| 2 | test_empty_key_returns_empty | 不存在的 key 返回空列表 |
| 3 | test_eviction_by_count | 写入 7 条（max=5）→ 只保留最新 5 条 |
| 4 | test_eviction_by_chars | 总 chars 超限 → 淘汰最旧条目 |
| 5 | test_ttl_is_set | 写入后 Redis key TTL > 0 且 ≤ 配置值 |
| 6 | test_remove_entry | 删除后读取返回空 |
| 7 | test_remove_nonexistent | 删除不存在的 entry 无异常 |

执行命令：
```bash
PYCLAW_TEST_REDIS_HOST=ares.tj-info-ai-dms-mem0.cache.srv \
PYCLAW_TEST_REDIS_PORT=22300 \
PYCLAW_TEST_REDIS_PASSWORD=tM1hgzAoYqdP6A_mSgcgJL1Ahh_jOO9b \
.venv/bin/pytest tests/integration/storage/memory/ -v
```

---

## 未覆盖的测试（待 Change 3b 补充）

| 场景 | 原因 | 何时补充 |
|------|------|---------|
| 飞书全链路（用户发消息 → agent 记忆 → 跨 session 回忆） | MemoryStore 未接入 agent loop | Change 3b |
| Web 全链路（WebSocket 对话 → memorize 工具 → search 验证） | 同上 | Change 3b |
| 真实 embedding API 集成测试 | 需要 embedding 服务在线 | 可单独执行 |
| L1 索引注入 system prompt | ContextEngine 未改造 | Change 3b |
| archive_session 在 session 结束时自动触发 | ingest() 未改造 | Change 3b |
| memorize 工具的"无执行无记忆"校验 | 工具未实现 | Change 3b |

---

## Bug 修复记录（Oracle 审查 → 修复）

| 编号 | 严重度 | 问题 | 修复 |
|------|--------|------|------|
| C1 | CRITICAL | `store()` 对未知 layer 静默丢数据 | 添加 `else: raise ValueError`；`MemoryEntry.layer` 改为 `Literal["L1","L2","L3"]` |
| C2 | CRITICAL | FTS5 索引损坏（INSERT OR REPLACE 改变 rowid，default recursive_triggers=OFF 导致 DELETE 触发器不触发） | 改为 `INSERT...ON CONFLICT(id) DO UPDATE SET`（UPSERT），保持 rowid 不变 |
| C3 | CRITICAL | `redis_client=None` 时 factory 仍构造 RedisL1Index → 运行时崩溃 | Factory 添加 fail-fast 检查 |
| H3 | HIGH | 2 字符 Latin 查询走 FTS5 trigram 返回空（trigram 要求 ≥ 3 字符不分语言） | `use_like = len(query) < 3`（移除 `_has_cjk` 依赖） |
| H5 | HIGH | FTS5 MATCH 未转义特殊字符（`"`, `OR`, `NEAR` 等 FTS5 语法） | 添加 `_escape_fts5_query()` 用双引号包裹 + 内部转义 |
| H4 | HIGH | CompositeMemoryStore 缺 `isinstance(_, MemoryStore)` 测试 | 新增 `test_composite_satisfies_protocol` |
| — | REFACTOR | L4 独立 db 文件（`_archive.db`）不必要 | 合并到同一 db 文件，sqlite-vec 延迟加载 |

---

## 文件清单

### 新增文件

| 文件 | 功能 |
|------|------|
| `src/pyclaw/storage/memory/__init__.py` | 包入口，re-export |
| `src/pyclaw/storage/memory/base.py` | MemoryStore Protocol + MemoryEntry/ArchiveEntry 数据模型 |
| `src/pyclaw/storage/memory/redis_index.py` | L1 Redis Hash 索引层（LRU 淘汰 + TTL） |
| `src/pyclaw/storage/memory/sqlite.py` | L2/L3 SQLite + FTS5 + L4 归档 + sqlite-vec 向量搜索 |
| `src/pyclaw/storage/memory/embedding.py` | EmbeddingClient（litellm aembedding 封装） |
| `src/pyclaw/storage/memory/composite.py` | CompositeMemoryStore（L1 + SQLite 委托） |
| `src/pyclaw/storage/memory/factory.py` | create_memory_store() 工厂函数 |
| `scripts/feishu_send.py` | 飞书消息发送测试脚本（OAuth + P2P/群聊） |
| `tests/unit/storage/memory/*.py` | 7 个测试文件，48 个测试用例 |
| `tests/integration/storage/memory/*.py` | Redis L1 集成测试（7 用例） |

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/pyclaw/infra/settings.py` | 新增 MemorySettings + EmbeddingSettings |
| `src/pyclaw/storage/protocols.py` | re-export MemoryStore |
| `pyproject.toml` | sqlite extras 添加 sqlite-vec |
| `configs/pyclaw.json` | 新增 memory + embedding 配置段 |
| `configs/pyclaw.example.json` | 新增 memory + embedding 示例 |

### 删除文件

| 文件 | 原因 |
|------|------|
| `src/pyclaw/storage/memory/archive.py` | 功能合并到 sqlite.py（L4 与 L2/L3 共用一个 db） |
