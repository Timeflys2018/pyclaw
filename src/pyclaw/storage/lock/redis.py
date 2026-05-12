from __future__ import annotations

import secrets

import redis.asyncio as aioredis

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
else
    return 0
end
"""


class LockAcquireError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f"could not acquire lock: {key!r}")
        self.key = key


class LockLostError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f"lock lost: {key!r}")
        self.key = key


class RedisLockManager:
    def __init__(self, client: aioredis.Redis, *, key_prefix: str = "pyclaw:") -> None:
        self._client = client
        self._prefix = key_prefix

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def acquire(self, key: str, ttl_ms: int = 30_000) -> str:
        full = self._full_key(key)
        token = secrets.token_hex(16)
        ok = await self._client.set(full, token, nx=True, px=ttl_ms)
        if not ok:
            raise LockAcquireError(full)
        return token

    async def release(self, key: str, token: str) -> bool:
        full = self._full_key(key)
        result = await self._client.eval(_RELEASE_SCRIPT, 1, full, token)
        return bool(result)

    async def renew(self, key: str, token: str, ttl_ms: int = 30_000) -> bool:
        full = self._full_key(key)
        result = await self._client.eval(_RENEW_SCRIPT, 1, full, token, str(ttl_ms))
        return bool(result)
