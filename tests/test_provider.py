from __future__ import annotations

import time
from typing import Any

import fakeredis
import numpy as np
import pytest
import xarray as xr
from fastapi.testclient import TestClient
from xpublish import LockedMapping, Rest
from xpublish.plugins.included.dataset_info import DatasetInfoPlugin

from xpublish_redis_cache import PickleRedisCache, RedisCacheProvider


@pytest.fixture
def client() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(server=fakeredis.FakeServer())


def make_rest(client: fakeredis.FakeRedis, **kwargs: Any) -> Rest:
    provider = RedisCacheProvider(client=client, **kwargs)
    return Rest({}, plugins={"redis_cache": provider})


def keys(client: fakeredis.FakeRedis) -> list[str]:
    return sorted(k if isinstance(k, str) else k.decode() for k in client.keys("*"))


def test_pickle_cache_round_trip_and_default(client: fakeredis.FakeRedis) -> None:
    cache = PickleRedisCache(client, prefix="p:")
    arr = np.arange(5)
    cache["a"] = arr
    np.testing.assert_array_equal(cache["a"], arr)
    assert "a" in cache
    assert cache.get("missing") is None
    assert cache.get("missing", 7) == 7
    with pytest.raises(KeyError):
        cache["missing"]
    del cache["a"]
    with pytest.raises(KeyError):
        del cache["a"]


def test_pickle_cache_iter_len_clear_are_prefix_scoped(
    client: fakeredis.FakeRedis,
) -> None:
    client.set("other", b"x")
    one = PickleRedisCache(client, prefix="one:")
    two = PickleRedisCache(client, prefix="two:")
    one["a"] = 1
    one["b"] = 2
    two["c"] = 3
    assert len(one) == 2
    assert sorted(one) == ["a", "b"]
    one.clear()
    assert len(one) == 0
    assert len(two) == 1
    assert client.get("other") == b"x"
    one.clear()  # clearing an empty cache is fine


def test_different_namespaces_use_different_prefixes(
    client: fakeredis.FakeRedis,
) -> None:
    deps = make_rest(client).dependencies()
    deps.get_lru_cache("a")["k"] = 1
    deps.get_lru_cache("b")["k"] = 2
    assert keys(client) == ["xpublish:lru:a:k", "xpublish:lru:b:k"]
    assert deps.get_lru_cache("a")["k"] == 1


def test_same_namespace_shares_data(client: fakeredis.FakeRedis) -> None:
    deps = make_rest(client).dependencies()
    deps.get_lru_cache("shared")["k"] = {"v": 1}
    assert deps.get_lru_cache("shared")["k"] == {"v": 1}
    # another Rest (another process, say) sees the data too
    other = make_rest(client).dependencies()
    assert other.get_lru_cache("shared")["k"] == {"v": 1}


def test_lru_has_no_expiry(client: fakeredis.FakeRedis) -> None:
    deps = make_rest(client).dependencies()
    deps.get_lru_cache("n")["k"] = 1
    assert client.pttl("xpublish:lru:n:k") == -1


def test_ttl_keys_get_a_pttl(client: fakeredis.FakeRedis) -> None:
    deps = make_rest(client).dependencies()
    cache = deps.get_ttl_cache("n", ttl=1.5)
    cache["k"] = 1
    assert 0 < client.pttl("xpublish:ttl:n:k") <= 1500


def test_ttl_keys_expire(client: fakeredis.FakeRedis) -> None:
    deps = make_rest(client).dependencies()
    cache = deps.get_ttl_cache("n", ttl=0.05)
    cache["k"] = 1
    time.sleep(0.15)
    assert "k" not in cache


def test_custom_prefix(client: fakeredis.FakeRedis) -> None:
    deps = make_rest(client, prefix="mine").dependencies()
    deps.get_lru_cache("n")["k"] = 1
    assert keys(client) == ["mine:lru:n:k"]


def test_excluded_namespace_falls_back_to_core(client: fakeredis.FakeRedis) -> None:
    deps = make_rest(client, exclude_namespaces={"local"}).dependencies()
    cache = deps.get_lru_cache("local")
    assert isinstance(cache, LockedMapping)
    cache["k"] = lambda: 1  # unpicklable values are fine locally
    assert keys(client) == []


def test_caches_are_memoized(client: fakeredis.FakeRedis) -> None:
    deps = make_rest(client).dependencies()
    assert deps.get_lru_cache("n") is deps.get_lru_cache("n")
    assert deps.get_ttl_cache("n") is not deps.get_lru_cache("n")


def test_two_level_front_serves_hits_without_redis(client: fakeredis.FakeRedis) -> None:
    deps = make_rest(client, local_maxsize=10).dependencies()
    cache = deps.get_lru_cache("n")
    cache["k"] = [1, 2]
    client.flushall()
    assert cache["k"] == [1, 2]  # served by the front cache
    ttl = deps.get_ttl_cache("t", ttl=60)
    ttl["k"] = 1
    client.flushall()
    assert ttl["k"] == 1


def test_two_level_survives_redis_outage() -> None:
    class Broken(fakeredis.FakeRedis):
        def set(self, *_args: Any, **_kwargs: Any) -> Any:
            msg = "down"
            raise ConnectionError(msg)

    deps = make_rest(Broken(), local_maxsize=10).dependencies()
    cache = deps.get_lru_cache("n")
    cache["k"] = 1
    assert cache["k"] == 1


def test_legacy_cache_round_trips_numpy_through_redis(
    client: fakeredis.FakeRedis,
) -> None:
    rest = make_rest(client)
    arr = np.arange(12.0).reshape(3, 4)
    with pytest.warns(DeprecationWarning, match="get_lru_cache"):
        legacy = rest.cache
    legacy.put("arr", arr, 1)
    np.testing.assert_array_equal(legacy.get("arr"), arr)
    assert keys(client) == ["xpublish:lru:xpublish:arr"]


def test_plugin_is_hashable_and_does_not_connect() -> None:
    provider = RedisCacheProvider(url="redis://nowhere.invalid:1/0")
    assert isinstance(hash(provider), int)
    assert provider.client is None
    assert "client" not in provider.model_dump()


def test_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XPUBLISH_REDIS_URL", "redis://example:1234/2")
    assert RedisCacheProvider().url == "redis://example:1234/2"
    monkeypatch.delenv("XPUBLISH_REDIS_URL")
    assert RedisCacheProvider().url == "redis://localhost:6379/0"


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XPUBLISH_REDIS_PREFIX", "env")
    monkeypatch.setenv("XPUBLISH_REDIS_LOCAL_MAXSIZE", "64")
    monkeypatch.setenv("XPUBLISH_REDIS_EXCLUDE_NAMESPACES", "a, b,,c")
    provider = RedisCacheProvider()
    assert provider.prefix == "env"
    assert provider.local_maxsize == 64
    assert provider.exclude_namespaces == {"a", "b", "c"}
    # explicit arguments still win
    assert RedisCacheProvider(prefix="arg").prefix == "arg"


def test_settings_defaults_without_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in (
        "XPUBLISH_REDIS_PREFIX",
        "XPUBLISH_REDIS_LOCAL_MAXSIZE",
        "XPUBLISH_REDIS_EXCLUDE_NAMESPACES",
    ):
        monkeypatch.delenv(var, raising=False)
    provider = RedisCacheProvider()
    assert provider.prefix == "xpublish"
    assert provider.local_maxsize == 0
    assert provider.exclude_namespaces == set()


def test_client_is_created_lazily_from_url(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[str] = []
    fake = fakeredis.FakeRedis()

    def from_url(url: str) -> fakeredis.FakeRedis:
        made.append(url)
        return fake

    monkeypatch.setattr("redis.Redis.from_url", from_url)
    provider = RedisCacheProvider(url="redis://x/0")
    assert not made
    provider.get_lru_cache("n", 1, None)
    assert made == ["redis://x/0"]


def test_end_to_end_info(client: fakeredis.FakeRedis) -> None:
    ds = xr.Dataset({"air": (("x",), np.arange(4.0))}, coords={"x": np.arange(4)})
    rest = Rest(
        {"air": ds},
        plugins={
            "redis_cache": RedisCacheProvider(client=client),
            "dataset_info": DatasetInfoPlugin(),
        },
    )
    http = TestClient(rest.app)
    first = http.get("/datasets/air/info")
    second = http.get("/datasets/air/info")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["dimensions"] == {"x": 4}
    found = keys(client)
    assert found
    assert all(k.startswith("xpublish:lru:dataset_info:") for k in found)
