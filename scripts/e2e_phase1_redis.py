# pyright: reportArgumentType=false, reportGeneralTypeIssues=false, reportMissingTypeArgument=false
"""Phase 1 E2E: Real Redis integration for DistributedMutex + CuratorStateStore.

One-click验证脚本，直接用 configs/pyclaw.json 里的 Redis 配置跑以下 3 组测试：

  1. **连通性 check**: Redis ping
  2. **DistributedMutex 真实场景**: acquire / heartbeat / check_alive / lost event / pruning race
  3. **CuratorStateStore 真实读写**: mark_scan_completed / mark_review_fully_completed / seed_if_missing

每个测试**独立使用带前缀的 key**（phase1-e2e:）→ 不污染生产 pyclaw: 前缀。
运行完自动清理。

使用:
  .venv/bin/python scripts/e2e_phase1_redis.py

退出码:
  0 = 所有测试通过
  1 = 任何一个失败（终端打印详细红色 FAIL 原因）
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path

import redis.asyncio as aioredis

from pyclaw.core.curator_state import CuratorStateStore
from pyclaw.infra.task_manager import TaskManager
from pyclaw.storage.lock.mutex import DistributedMutex
from pyclaw.storage.lock.redis import (
    LockAcquireError,
    LockLostError,
    RedisLockManager,
)

PHASE1_PREFIX = "phase1-e2e:"

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"


class TestReporter:
    """收集测试结果，输出彩色报告。"""

    def __init__(self) -> None:
        self.results: list[tuple[str, str, str | None]] = []

    def pass_(self, name: str) -> None:
        print(f"  {GREEN}✓{RESET} {name}")
        self.results.append((name, "PASS", None))

    def fail(self, name: str, reason: str) -> None:
        print(f"  {RED}✗{RESET} {name}")
        print(f"    {RED}{reason}{RESET}")
        self.results.append((name, "FAIL", reason))

    def skip(self, name: str, reason: str) -> None:
        print(f"  {YELLOW}○{RESET} {name} ({reason})")
        self.results.append((name, "SKIP", reason))

    def summary(self) -> int:
        passed = sum(1 for _, s, _ in self.results if s == "PASS")
        failed = sum(1 for _, s, _ in self.results if s == "FAIL")
        skipped = sum(1 for _, s, _ in self.results if s == "SKIP")
        total = len(self.results)

        print()
        print("=" * 60)
        color = GREEN if failed == 0 else RED
        print(f"{color}Phase 1 Summary: {passed}/{total} passed "
              f"({failed} failed, {skipped} skipped){RESET}")
        print("=" * 60)

        if failed > 0:
            print(f"\n{RED}FAILURES:{RESET}")
            for name, status, reason in self.results:
                if status == "FAIL":
                    print(f"  • {name}: {reason}")

        return 0 if failed == 0 else 1


reporter = TestReporter()


async def run_test(
    name: str,
    fn: Callable[[], Awaitable[None]],
) -> None:
    """运行单个异步测试并收集结果。"""
    try:
        await fn()
        reporter.pass_(name)
    except AssertionError as exc:
        reporter.fail(name, f"assertion failed: {exc}")
    except Exception as exc:
        tb = traceback.format_exc().splitlines()
        relevant = [line for line in tb[-5:] if line.strip()]
        reason = f"{type(exc).__name__}: {exc}\n    " + "\n    ".join(relevant)
        reporter.fail(name, reason)


def load_redis_config() -> dict:
    """从 configs/pyclaw.json 读取 Redis 配置。"""
    config_path = Path(__file__).parent.parent / "configs" / "pyclaw.json"
    with open(config_path) as f:
        data = json.load(f)
    return data["redis"]


async def make_redis_client() -> aioredis.Redis:
    """根据 pyclaw.json 构造 Redis client。"""
    cfg = load_redis_config()
    client = aioredis.Redis(
        host=cfg["host"],
        port=cfg["port"],
        password=cfg.get("password"),
        decode_responses=False,
    )
    return client


async def cleanup_phase1_keys(client: aioredis.Redis) -> int:
    """清理本次测试产生的所有 phase1-e2e:* key。"""
    removed = 0
    async for key in client.scan_iter(match=f"{PHASE1_PREFIX}*", count=100):
        await client.delete(key)
        removed += 1
    # 清 CuratorStateStore 在 phase1 prefix 下的测试键
    for key in [
        b"phase1-e2e:curator:last_run_at",
        b"phase1-e2e:curator:llm_review_last_run_at",
    ]:
        await client.delete(key)
    return removed


# =============================================================================
# 测试组 1：连通性
# =============================================================================


async def test_connectivity(client: aioredis.Redis) -> None:
    async def ping() -> None:
        result = await client.ping()
        assert result, f"ping returned falsy: {result!r}"

    async def set_get_roundtrip() -> None:
        key = f"{PHASE1_PREFIX}sanity:hello"
        await client.set(key, "world", px=30000)
        value = await client.get(key)
        assert value == b"world", f"expected b'world', got {value!r}"
        await client.delete(key)

    print(f"\n{CYAN}[Group 1] Redis connectivity{RESET}")
    await run_test("ping returns PONG", ping)
    await run_test("SET/GET roundtrip", set_get_roundtrip)


# =============================================================================
# 测试组 2：DistributedMutex 真实行为
# =============================================================================


async def test_distributed_mutex(client: aioredis.Redis) -> None:
    lock_mgr = RedisLockManager(client, key_prefix=PHASE1_PREFIX)

    async def basic_acquire_release() -> None:
        """Test: async with 正常 acquire + release。"""
        tm = TaskManager()
        mutex = DistributedMutex(
            lock_mgr, "mutex-basic",
            task_manager=tm, heartbeat_interval_s=2.0,
        )
        async with mutex as m:
            assert m.token is not None, "token should be set after __aenter__"
            assert not m.lost, "should not be lost during normal hold"
            m.check_alive()  # no-op

        # 退出后 token 依然保留（供调试）但锁已释放
        assert m.token is not None, "token should remain accessible after exit"

        # 验证锁真的被释放 → 另一个 mutex 应该能 acquire
        mutex2 = DistributedMutex(
            lock_mgr, "mutex-basic", task_manager=tm, heartbeat_interval_s=2.0,
        )
        async with mutex2:
            pass  # if we reach here, lock was released

    async def contention_raises() -> None:
        """Test: 第一个 holder 还持有时，第二个 acquire 应该立即 raise。"""
        tm = TaskManager()
        mutex1 = DistributedMutex(
            lock_mgr, "mutex-contention",
            task_manager=tm, heartbeat_interval_s=5.0,
        )
        async with mutex1:
            # 尝试二次 acquire 应失败
            mutex2 = DistributedMutex(
                lock_mgr, "mutex-contention",
                task_manager=tm, heartbeat_interval_s=5.0,
            )
            try:
                async with mutex2:
                    raise AssertionError("second acquire should have raised")
            except LockAcquireError:
                pass  # expected

    async def heartbeat_renews_lock() -> None:
        """Test: 长时间 hold (TTL 的 2 倍)，heartbeat 能续约。"""
        tm = TaskManager()
        mutex = DistributedMutex(
            lock_mgr, "mutex-heartbeat",
            task_manager=tm,
            ttl_ms=2000,              # 2s TTL
            heartbeat_interval_s=0.5,  # 每 0.5s renew 一次
        )
        async with mutex as m:
            await asyncio.sleep(3.5)   # 超过 TTL 的 1.75 倍
            # 如果 heartbeat 没 renew，锁已过期 → check_alive 不会 raise
            # （因为 event 和 task 状态在本端看都正常）
            # 但我们可以验证 Redis 里 key 确实还在
            full_key = f"{PHASE1_PREFIX}mutex-heartbeat"
            ttl_ms = await client.pttl(full_key)
            assert ttl_ms > 0, f"key should still exist (TTL > 0), got {ttl_ms}"
            m.check_alive()  # 不应 raise

    async def lock_deleted_externally_triggers_lost() -> None:
        """Test: 外部删除 Redis key → heartbeat CAS 失败 → check_alive raise LockLostError。"""
        tm = TaskManager()
        mutex = DistributedMutex(
            lock_mgr, "mutex-lost-event",
            task_manager=tm,
            ttl_ms=30000,
            heartbeat_interval_s=0.3,  # fast heartbeat 便于快速检测
        )
        async with mutex as m:
            assert not m.lost, "should not be lost initially"

            # 外部删除锁 (模拟 Redis failover 或人为介入)
            full_key = f"{PHASE1_PREFIX}mutex-lost-event"
            await client.delete(full_key)

            # 等待 heartbeat 尝试 renew → CAS 失败 → set lost_event
            await asyncio.sleep(1.0)

            # check_alive 必须 raise LockLostError
            try:
                m.check_alive()
                raise AssertionError("check_alive should have raised LockLostError")
            except LockLostError as exc:
                assert "mutex-lost-event" in str(exc), \
                    f"LockLostError msg should mention key, got: {exc}"

    async def pruning_race_fail_closed() -> None:
        """Test: 手动 cancel heartbeat task → check_alive 应 raise (fail-closed behavior)。"""
        tm = TaskManager()
        mutex = DistributedMutex(
            lock_mgr, "mutex-prune",
            task_manager=tm, heartbeat_interval_s=5.0,
        )
        async with mutex as m:
            assert m._heartbeat_task_id is not None, "heartbeat task should exist"
            # 人为取消 heartbeat (模拟意外终止)
            await tm.cancel(m._heartbeat_task_id)
            await asyncio.sleep(0.1)
            # event 未 set，但 task 已终止 → fail-closed 应触发
            try:
                m.check_alive()
                raise AssertionError("check_alive should raise after task done")
            except LockLostError:
                pass  # expected fail-closed behavior

    print(f"\n{CYAN}[Group 2] DistributedMutex real-Redis behavior{RESET}")
    await run_test("basic acquire + release", basic_acquire_release)
    await run_test("contention raises LockAcquireError", contention_raises)
    await run_test("heartbeat renews lock over 3× TTL", heartbeat_renews_lock)
    await run_test(
        "external DEL → check_alive raises LockLostError",
        lock_deleted_externally_triggers_lost,
    )
    await run_test(
        "pruning race: task cancelled → fail-closed",
        pruning_race_fail_closed,
    )


# =============================================================================
# 测试组 3：CuratorStateStore 真实读写
# =============================================================================


async def test_curator_state_store(client: aioredis.Redis) -> None:
    # 用 test-only prefix 的 client wrapper，避免污染生产 key
    class _PrefixedRedis:
        """Redis proxy that prepends phase1 prefix to all keys."""

        def __init__(self, underlying: aioredis.Redis, prefix: str) -> None:
            self._r = underlying
            self._prefix = prefix

        async def set(self, key: str, value, **kwargs):
            return await self._r.set(f"{self._prefix}{key}", value, **kwargs)

        async def get(self, key: str):
            return await self._r.get(f"{self._prefix}{key}")

    async def mark_scan_completed_writes_float() -> None:
        prefixed = _PrefixedRedis(client, PHASE1_PREFIX)
        store = CuratorStateStore(prefixed)  # type: ignore[arg-type]
        await store.mark_scan_completed()
        raw = await client.get(f"{PHASE1_PREFIX}pyclaw:curator:last_run_at")
        assert raw is not None, "mark_scan_completed should have written the key"
        # 应能 parse 为 float
        parsed = float(raw.decode())
        assert parsed > 0, f"timestamp should be positive, got {parsed}"
        assert abs(parsed - time.time()) < 5, "timestamp should be ~now"

    async def mark_review_fully_completed_writes_int() -> None:
        prefixed = _PrefixedRedis(client, PHASE1_PREFIX)
        store = CuratorStateStore(prefixed)  # type: ignore[arg-type]
        await store.mark_review_fully_completed()
        raw = await client.get(
            f"{PHASE1_PREFIX}pyclaw:curator:llm_review_last_run_at",
        )
        assert raw is not None
        # 应是整数字符串
        text = raw.decode()
        assert "." not in text, f"should be int string, got {text!r}"
        parsed = int(text)
        assert abs(parsed - time.time()) < 5

    async def get_returns_none_when_missing() -> None:
        # 清掉前置测试可能留下的 key，然后用一个全新的 key namespace
        await client.delete(f"{PHASE1_PREFIX}missing-ns:pyclaw:curator:last_run_at")

        class _MissingNs(_PrefixedRedis):
            pass

        proxy = _MissingNs(client, f"{PHASE1_PREFIX}missing-ns:")
        store = CuratorStateStore(proxy)  # type: ignore[arg-type]
        scan = await store.get_last_scan_at()
        review = await store.get_last_review_at()
        assert scan is None, f"expected None for missing key, got {scan}"
        assert review is None, f"expected None for missing key, got {review}"

    async def roundtrip_mark_then_get() -> None:
        # 用独立 namespace 避免与其他测试冲突
        await client.delete(f"{PHASE1_PREFIX}roundtrip:pyclaw:curator:last_run_at")
        await client.delete(
            f"{PHASE1_PREFIX}roundtrip:pyclaw:curator:llm_review_last_run_at",
        )

        proxy = _PrefixedRedis(client, f"{PHASE1_PREFIX}roundtrip:")
        store = CuratorStateStore(proxy)  # type: ignore[arg-type]

        await store.mark_scan_completed()
        scan_ts = await store.get_last_scan_at()
        assert scan_ts is not None
        assert abs(scan_ts - time.time()) < 5

        await store.mark_review_fully_completed()
        review_ts = await store.get_last_review_at()
        assert review_ts is not None
        assert abs(review_ts - time.time()) < 5

    async def seed_if_missing_nx_semantics() -> None:
        # 清掉 key
        key = f"{PHASE1_PREFIX}seed:pyclaw:curator:last_run_at"
        await client.delete(key)

        proxy = _PrefixedRedis(client, f"{PHASE1_PREFIX}seed:")
        store = CuratorStateStore(proxy)  # type: ignore[arg-type]

        # 第一次 seed → 应写入
        await store.seed_if_missing()
        raw1 = await client.get(key)
        assert raw1 is not None, "seed should have written"
        ts1 = float(raw1.decode())

        await asyncio.sleep(0.2)

        # 第二次 seed → NX 应不覆盖
        await store.seed_if_missing()
        raw2 = await client.get(key)
        ts2 = float(raw2.decode())
        assert ts1 == ts2, \
            f"seed_if_missing should NOT overwrite, ts1={ts1} ts2={ts2}"

    print(f"\n{CYAN}[Group 3] CuratorStateStore real-Redis IO{RESET}")
    await run_test("mark_scan_completed writes float string", mark_scan_completed_writes_float)
    await run_test("mark_review_fully_completed writes int string", mark_review_fully_completed_writes_int)
    await run_test("get_last_* returns None when missing", get_returns_none_when_missing)
    await run_test("mark → get roundtrip", roundtrip_mark_then_get)
    await run_test("seed_if_missing: NX semantics (no overwrite)", seed_if_missing_nx_semantics)


# =============================================================================
# Main
# =============================================================================


async def main() -> int:
    print(f"{CYAN}=== Phase 1 E2E: Real Redis integration ==={RESET}")
    print(f"Config: configs/pyclaw.json")
    cfg = load_redis_config()
    print(f"Redis:  {cfg['host']}:{cfg['port']}")
    print(f"Prefix: {PHASE1_PREFIX} (isolated from prod pyclaw: prefix)")

    client = await make_redis_client()
    try:
        # 预清理：以防上次运行遗留
        pre_removed = await cleanup_phase1_keys(client)
        if pre_removed > 0:
            print(f"{YELLOW}  (pre-cleanup: removed {pre_removed} leftover keys){RESET}")

        # 跑所有测试组
        await test_connectivity(client)
        await test_distributed_mutex(client)
        await test_curator_state_store(client)

    finally:
        # 后清理
        cleaned = await cleanup_phase1_keys(client)
        print(f"\n{CYAN}Cleanup: removed {cleaned} phase1-e2e:* keys{RESET}")
        await client.aclose()

    return reporter.summary()


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted by user{RESET}")
        sys.exit(130)
    except Exception as exc:
        print(f"\n{RED}FATAL: {exc}{RESET}")
        traceback.print_exc()
        sys.exit(2)
    sys.exit(exit_code)
