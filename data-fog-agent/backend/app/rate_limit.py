"""Per-minute request limits, counted in Redis so every API worker shares them."""

import time

import redis
from fastapi import HTTPException

from app import config

client = redis.Redis.from_url(config.REDIS_URL)


def check_rate_limit(name: str, key: str, per_minute: int):
    """Raises 429 once `key` (a user id or an IP) has called `name` more than `per_minute` times.

    ponytail: fixed one-minute windows, so a burst across a window edge can reach twice the
    limit. Use a sliding window if that ever matters.
    """
    window_key = f"rate:{name}:{key}:{int(time.time() // 60)}"
    with client.pipeline() as pipe:
        count, _ = pipe.incr(window_key).expire(window_key, 60, nx=True).execute()
    if count > per_minute:
        raise HTTPException(429, "Too many requests. Wait a minute and try again.")
