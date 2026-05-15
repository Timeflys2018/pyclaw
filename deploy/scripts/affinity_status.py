"""Print current pyclaw:affinity:* keys + worker ZSET state."""
from __future__ import annotations

import asyncio

from pyclaw.infra.redis import close_client, get_client
from pyclaw.infra.settings import load_settings


async def main() -> None:
    settings = load_settings()
    client = await get_client(settings.redis)
    try:
        cursor = 0
        keys = []
        while True:
            cursor, batch = await client.scan(cursor=cursor, count=100, match="pyclaw:affinity:*")
            for k in batch:
                k_str = k.decode() if isinstance(k, bytes) else k
                v = await client.get(k)
                v_str = v.decode() if isinstance(v, bytes) else (v or "")
                ttl = await client.ttl(k)
                keys.append((k_str, v_str, ttl))
            if cursor == 0:
                break

        print(f"pyclaw:affinity:* ({len(keys)} keys):")
        for k, v, ttl in keys:
            print(f"  {k}")
            print(f"    -> {v[-25:]}  TTL={ttl}s")

        workers = await client.zrange("pyclaw:workers", 0, -1, withscores=True)
        print(f"\npyclaw:workers ZSET ({len(workers)} members):")
        for member, _score in workers:
            m_str = member.decode() if isinstance(member, bytes) else member
            print(f"  {m_str}")
    finally:
        await close_client(client)


if __name__ == "__main__":
    asyncio.run(main())
