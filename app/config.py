import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql://lab:lab@localhost:55432/lab"
    redis_url: str = "redis://localhost:56379/0"
    cache_enabled: bool = True
    cache_ttl: int = 60
    pool_size: int = 16
    pool_timeout: float = 1.0
    request_timeout: float = 5.0
    drain_file: str = "/tmp/lab-draining"

    @classmethod
    def from_env(cls):
        settings = cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            redis_url=os.getenv("REDIS_URL", cls.redis_url),
            cache_enabled=os.getenv("CACHE_ENABLED", "true").lower() == "true",
            cache_ttl=int(os.getenv("CACHE_TTL", "60")),
            pool_size=int(os.getenv("DB_POOL_SIZE", "16")),
            pool_timeout=float(os.getenv("DB_POOL_TIMEOUT", "1")),
            request_timeout=float(os.getenv("REQUEST_TIMEOUT", "5")),
            drain_file=os.getenv("DRAIN_FILE", cls.drain_file),
        )
        if (
            min(
                settings.cache_ttl,
                settings.pool_size,
                settings.pool_timeout,
                settings.request_timeout,
            )
            <= 0
        ):
            raise ValueError("TTL, pool size and timeouts must be positive")
        return settings
