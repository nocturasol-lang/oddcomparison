#!/usr/bin/env python3.12
"""Diagnostic script for Redis connection, set/get, and TTL."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid


async def _run() -> int:
    try:
        from redis.asyncio import Redis
        from redis.exceptions import RedisError
    except Exception as exc:
        print(f"[ERROR] redis dependency missing or broken: {exc}")
        return 2

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    key = f"tests:redis:diag:{uuid.uuid4().hex}"
    value = "ok"
    ttl = 15
    client = None

    print(f"[INFO] Testing Redis URL: {redis_url}")
    try:
        client = Redis.from_url(redis_url, decode_responses=True)
        pong = await client.ping()
        print(f"[PASS] ping -> {pong}")

        set_ok = await client.set(key, value, ex=ttl)
        print(f"[PASS] set key={key} -> {set_ok}")

        got = await client.get(key)
        print(f"[PASS] get key={key} -> {got!r}")
        if got != value:
            print("[FAIL] Retrieved value does not match.")
            return 1

        remaining_ttl = await client.ttl(key)
        print(f"[PASS] ttl key={key} -> {remaining_ttl}s")
        if remaining_ttl is not None and remaining_ttl <= 0:
            print("[FAIL] TTL is not positive.")
            return 1

        print("[RESULT] Redis diagnostics passed.")
        return 0
    except RedisError as exc:
        print(f"[ERROR] Redis operation failed: {exc}")
        print("       Ensure Redis is running and REDIS_URL is correct.")
        return 1
    except OSError as exc:
        print(f"[ERROR] Network/OS error while connecting to Redis: {exc}")
        return 1
    finally:
        if client is not None:
            try:
                await client.delete(key)
            except Exception:
                pass
            try:
                await client.aclose()
            except Exception:
                pass


def main() -> None:
    code = asyncio.run(_run())
    sys.exit(code)


if __name__ == "__main__":
    main()
