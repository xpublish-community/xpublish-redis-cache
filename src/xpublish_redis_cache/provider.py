"""Xpublish plugin providing Redis-backed LRU/TTL caches."""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any, cast

import cachetools
import CacheToolsUtils
import redis
from pydantic import Field, PrivateAttr
from xpublish import LockedMapping, Plugin, hookimpl

from .cache import PickleRedisCache

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping

DEFAULT_URL = "redis://localhost:6379/0"


def _default_url() -> str:
    return os.environ.get("XPUBLISH_REDIS_URL", DEFAULT_URL)


def _default_prefix() -> str:
    return os.environ.get("XPUBLISH_REDIS_PREFIX", "xpublish")


def _default_local_maxsize() -> int:
    return int(os.environ.get("XPUBLISH_REDIS_LOCAL_MAXSIZE", "0"))


def _default_exclude_namespaces() -> set[str]:
    raw = os.environ.get("XPUBLISH_REDIS_EXCLUDE_NAMESPACES", "")
    return {name.strip() for name in raw.split(",") if name.strip()}


class RedisCacheProvider(Plugin):  # type: ignore[misc]
    """Back ``deps.get_lru_cache`` / ``deps.get_ttl_cache`` with Redis.

    Values are pickled, and ``maxsize`` / ``getsizeof`` are ignored: configure
    Redis with ``maxmemory`` and ``maxmemory-policy allkeys-lru`` to bound memory.
    """

    name: str = "redis_cache"

    url: str = Field(default_factory=_default_url, description="Redis URL")
    prefix: str = Field(
        default_factory=_default_prefix,
        description="Key prefix for everything this plugin writes",
    )
    local_maxsize: int = Field(
        default_factory=_default_local_maxsize,
        description="If > 0, keep a process-local front cache of this many items",
    )
    exclude_namespaces: set[str] = Field(
        default_factory=_default_exclude_namespaces,
        description="Namespaces left to xpublish's local caches",
    )
    client: Any = Field(
        default=None,
        exclude=True,
        description="Pre-built Redis client, mainly for tests",
    )

    _caches: dict[tuple[str, str], MutableMapping[Any, Any]] = PrivateAttr(
        default_factory=dict
    )
    _lock: Any = PrivateAttr(default_factory=threading.RLock)

    def _client(self) -> Any:
        if self.client is None:
            self.client = redis.Redis.from_url(self.url)
        return self.client

    def _build(
        self,
        kind: str,
        namespace: str,
        ttl: float | None,
        front_factory: Callable[[], MutableMapping[Any, Any]],
    ) -> MutableMapping[Any, Any] | None:
        if namespace in self.exclude_namespaces:
            return None
        with self._lock:
            cached = self._caches.get((kind, namespace))
            if cached is not None:
                return cached
            back = PickleRedisCache(
                self._client(), prefix=f"{self.prefix}:{kind}:{namespace}:", ttl=ttl
            )
            cache: MutableMapping[Any, Any] = back
            if self.local_maxsize > 0:
                cache = CacheToolsUtils.TwoLevelCache(
                    LockedMapping(front_factory()), back, resilient=True
                )
            self._caches[(kind, namespace)] = cache
            return cache

    @hookimpl  # type: ignore[untyped-decorator]
    def get_lru_cache(
        self,
        namespace: str,
        maxsize: int,  # noqa: ARG002
        getsizeof: Callable[[Any], float] | None,  # noqa: ARG002
    ) -> MutableMapping[Any, Any] | None:
        """Return a Redis LRU cache (eviction left to Redis ``allkeys-lru``)."""
        return self._build(
            "lru",
            namespace,
            None,
            lambda: cast(
                "MutableMapping[Any, Any]",
                cachetools.LRUCache(maxsize=self.local_maxsize),
            ),
        )

    @hookimpl  # type: ignore[untyped-decorator]
    def get_ttl_cache(
        self,
        namespace: str,
        maxsize: int,  # noqa: ARG002
        ttl: float,
        getsizeof: Callable[[Any], float] | None,  # noqa: ARG002
    ) -> MutableMapping[Any, Any] | None:
        """Return a Redis cache whose keys expire after ``ttl`` seconds."""
        return self._build(
            "ttl",
            namespace,
            ttl,
            lambda: cast(
                "MutableMapping[Any, Any]",
                cachetools.TTLCache(maxsize=self.local_maxsize, ttl=ttl),
            ),
        )
