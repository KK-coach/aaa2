"""Közös fixture: a rögzített referencia-crawlok visszajátszása futásonként egyszer."""
import asyncio

import pytest

from tests.recorded import REFERENCE_SETS, replay_crawl


@pytest.fixture(scope="session")
def reference_crawl():
    """Név → a visszajátszott crawl kapcsolata, vagy None, ha nincs felvétel. Készletenként az
    első kéréskor játssza vissza, utána ugyanazt a kapcsolatot adja."""
    cache = {}

    def get(name):
        if name not in cache:
            seed, options = REFERENCE_SETS[name]
            replayed = asyncio.run(replay_crawl(name, seed, options))
            cache[name] = replayed[1] if replayed else None
        return cache[name]

    return get
