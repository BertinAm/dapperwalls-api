import time

from django.conf import settings
from django.core.cache import cache

WINDOWS = (
    ("hour", 60 * 60, "RATE_LIMIT_PER_HOUR"),
    ("day", 24 * 60 * 60, "RATE_LIMIT_PER_DAY"),
)


def hit(ip):
    """Record one submission attempt for this IP. Returns False if over a limit.

    Fixed, clock-aligned windows keyed on the resolved client IP. Every write
    passes an explicit timeout (DatabaseCache.incr would reset it to the
    default). Attempts over the limit are not counted.
    """
    ident = ip or "unknown"
    now = int(time.time())
    buckets = []
    for name, seconds, limit_setting in WINDOWS:
        key = f"enquiry-rl:{name}:{now // seconds}:{ident}"
        count = cache.get(key, 0)
        if count >= getattr(settings, limit_setting):
            return False
        buckets.append((key, count, seconds))
    for key, count, seconds in buckets:
        cache.set(key, count + 1, seconds)
    return True
