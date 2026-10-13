"""A Redis-backed mutable mapping that stores pickled values."""

from __future__ import annotations

import pickle
from typing import TYPE_CHECKING, Any

import CacheToolsUtils

if TYPE_CHECKING:
    from collections.abc import Iterator

_GLOB_SPECIAL = str.maketrans({c: "\\" + c for c in "*?[]\\"})


class PickleRedisCache(CacheToolsUtils.PrefixedRedisCache):
    """``CacheToolsUtils.PrefixedRedisCache`` that can hold arbitrary Python objects.

    Differences from the parent class:

    * values are pickled rather than JSON encoded, so numpy and xarray objects work;
    * ``get`` honours its ``default``;
    * iteration, ``len`` and ``clear`` only touch keys under this cache's prefix;
    * ``ttl`` may be a float (milliseconds precision) or ``None`` for no expiry.

    Only unpickle data from a Redis server you trust.

    Args:
        cache: A ``redis.Redis`` client (or compatible).
        prefix: Prefix prepended to every key.
        ttl: Seconds an entry lives, or ``None`` for no expiry.
    """

    def __init__(self, cache: Any, prefix: str = "", ttl: float | None = None) -> None:
        super().__init__(cache, prefix, ttl)

    def _redis_key(self, key: Any) -> str:
        return str(self._prefix + str(key))

    def _serialize(self, s: Any) -> bytes:
        return pickle.dumps(s)

    def _deserialize(self, s: bytes) -> Any:
        return pickle.loads(s)

    def __getitem__(self, key: Any) -> Any:
        val = self._cache.get(self._redis_key(key))
        if val is None:
            raise KeyError(key)
        return self._deserialize(val)

    def __setitem__(self, key: Any, value: Any) -> None:
        px = None if self._ttl is None else max(1, int(self._ttl * 1000))
        self._cache.set(self._redis_key(key), self._serialize(value), px=px)

    def __delitem__(self, key: Any) -> None:
        if not self._cache.delete(self._redis_key(key)):
            raise KeyError(key)

    def __contains__(self, key: Any) -> bool:
        return bool(self._cache.exists(self._redis_key(key)))

    def get(self, key: Any, default: Any = None) -> Any:
        """Return the value for ``key``, or ``default`` if it is absent."""
        try:
            return self[key]
        except KeyError:
            return default

    def _scan(self) -> Iterator[bytes | str]:
        pattern = self._prefix.translate(_GLOB_SPECIAL) + "*"
        return iter(self._cache.scan_iter(match=pattern))

    def __iter__(self) -> Iterator[str]:
        for raw in self._scan():
            key = raw.decode() if isinstance(raw, bytes) else raw
            yield key[len(self._prefix) :]

    def __len__(self) -> int:
        return sum(1 for _ in self._scan())

    def clear(self) -> None:
        """Delete every entry under this cache's prefix (not the whole database)."""
        keys = list(self._scan())
        if keys:
            self._cache.delete(*keys)
