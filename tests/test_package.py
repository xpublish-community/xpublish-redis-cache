from __future__ import annotations

import importlib.metadata

import xpublish_redis_cache as m


def test_version() -> None:
    assert importlib.metadata.version("xpublish_redis_cache") == m.__version__
