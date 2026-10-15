# xpublish-redis-cache

[![Actions Status][actions-badge]][actions-link]
[![Documentation Status][rtd-badge]][rtd-link]

[![PyPI version][pypi-version]][pypi-link]
[![Conda-Forge][conda-badge]][conda-link]
[![PyPI platforms][pypi-platforms]][pypi-link]

[![GitHub Discussion][github-discussions-badge]][github-discussions-link]

[![Coverage][coverage-badge]][coverage-link]P

<!-- prettier-ignore-start -->
[actions-badge]:            https://github.com/xpublish-community/xpublish-redis-cache/actions/workflows/ci.yml/badge.svg
[actions-link]:             https://github.com/xpublish-community/xpublish-redis-cache/actions
[conda-badge]:              https://img.shields.io/conda/vn/conda-forge/xpublish-redis-cache
[conda-link]:               https://github.com/conda-forge/xpublish-redis-cache-feedstock
[github-discussions-badge]: https://img.shields.io/static/v1?label=Discussions&message=Ask&color=blue&logo=github
[github-discussions-link]:  https://github.com/xpublish-community/xpublish-redis-cache/discussions
[pypi-link]:                https://pypi.org/project/xpublish-redis-cache/
[pypi-platforms]:           https://img.shields.io/pypi/pyversions/xpublish-redis-cache
[pypi-version]:             https://img.shields.io/pypi/v/xpublish-redis-cache
[rtd-badge]:                https://readthedocs.org/projects/xpublish-redis-cache/badge/?version=latest
[rtd-link]:                 https://xpublish-redis-cache.readthedocs.io/en/latest/?badge=latest
[coverage-badge]:           https://codecov.io/github/xpublish-community/xpublish-redis-cache/branch/main/graph/badge.svg
[coverage-link]:            https://codecov.io/github/xpublish-community/xpublish-redis-cache

<!-- prettier-ignore-end -->

## Overview

A Redis based cache provider plugin for
[Xpublish](https://github.com/xpublish-community/xpublish).

## Install and configure

```bash
pip install xpublish-redis-cache
```

The plugin registers through the `xpublish.plugin` entry point, so installing it
and setting `XPUBLISH_REDIS_URL` environment variable will get things running.
It does not connect to Redis until the first cache is requested. Configure it by
passing your own instance via `xpublish.Rest(plugins=...)` (remember that
passing `plugins=` disables entry point loading, so list every plugin you want):

```python
from xpublish_redis_cache import RedisCacheProvider

provider = RedisCacheProvider(url="redis://cache:6379/0", local_maxsize=256)
```

| Field                | Default                                                             | Meaning                                                                 |
| -------------------- | ------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `url`                | `XPUBLISH_REDIS_URL`, else `redis://localhost:6379/0`               | Redis connection URL                                                    |
| `prefix`             | `XPUBLISH_REDIS_PREFIX`, else `xpublish`                            | Keys are `<prefix>:<lru\|ttl>:<namespace>:<key>`                        |
| `local_maxsize`      | `XPUBLISH_REDIS_LOCAL_MAXSIZE`, else `0`                            | If positive, add a per-process in-memory front cache of this many items |
| `exclude_namespaces` | `XPUBLISH_REDIS_EXCLUDE_NAMESPACES` (comma-separated), else `set()` | Namespaces left to Xpublish's local caches (for unpicklable values)     |

Explicit constructor arguments take precedence over environment variables.

## Semantics

- `maxsize` and `getsizeof` are **ignored**. Bound memory on the server with
  `maxmemory` and `maxmemory-policy allkeys-lru`. LRU caches have no expiry of
  their own.
- TTL caches set a Redis expiry (`PX`) of `ttl` seconds; float TTLs work.
- Values are **pickled**, so cached values must be picklable. Only point this at
  a Redis server you trust, since unpickling untrusted data is unsafe. Use
  `exclude_namespaces` to keep unpicklable values in process.
- Iterating, `len()` and `clear()` only touch the cache's own prefix, never the
  rest of the database.
- With `local_maxsize > 0` each cache is a `CacheToolsUtils.TwoLevelCache`: a
  thread-safe in-process LRU/TTL cache in front of Redis. Hits skip the network
  round trip; Redis failures are tolerated (`resilient=True`) and the front
  cache keeps serving. Note the front cache can serve entries another process
  has since changed or evicted.
- The deprecated `deps.cache` (`get`/`put`) is also routed to Redis under the
  `xpublish` namespace.
