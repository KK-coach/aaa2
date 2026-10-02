"""Közös fixture és a tesztsor keretei.

Jelölések: `live` (élő site vagy fizetős API; csak `pytest -m live`), `slow` (felvételt
visszajátszó, több adatbázisos vagy 10 mp fölötti teszt). A gyors sor: `pytest -m "not slow"
-n auto`; a teljes sor (`pytest -n auto`) csak merge előtt fut. A `reference_crawl` fixture-t
használó tesztek maguktól `slow` jelölést kapnak, és készletenként egy xdist-csoportba kerülnek
(`--dist loadgroup`), így egy készletet egy worker játszik vissza, és a rá épülő tesztek a
gyűjtés sorrendjében, ugyanazon a kapcsolaton futnak.

A visszajátszott crawl a lemezen gyorsítótárazódik (`tests/fixtures/.replay-cache/`): a kulcs a
felvétel indexe, a motor (`aaa2/engine`, `aaa2/db`) forrása és a crawl-beállítás; ha bármelyik
változik, a készlet újra visszajátszódik. `AAA2_REPLAY_CACHE=0` kikapcsolja.
"""
import asyncio
import hashlib
import os
from pathlib import Path

import duckdb
import pytest

from tests.recorded import FIXTURES_DIR, REFERENCE_SETS, Recording, replay_crawl

REPLAY_CACHE = FIXTURES_DIR / ".replay-cache"

# A mérés szerint (2026-10-02, egy szálon) 10 mp fölötti, felvételt visszajátszó vagy több
# adatbázisos tesztek: fájl → tesztnevek. A `reference_crawl` használóit nem kell felsorolni.
SLOW = {
    "test_crawl.py": {
        "test_replay_materia_crawl",                                            # 27 mp, felvétel
        "test_cli_crawl_status_export",                                         # 24 mp
        "test_changed_seed_is_rendered_and_volatile_token_is_not_a_change",     # 21 mp
        "test_hash_skip_leaves_unchanged_rows",                                 # 16 mp
        "test_page_transaction_is_all_or_nothing_and_resume_completes",         # 16 mp
        "test_resume_after_interrupt_with_db_reopen",                           # 13 mp
    },
    "test_determinism.py": {
        "test_two_runs_on_the_same_database_write_identical_outputs",           # 15 mp
        "test_stored_json_has_sorted_keys_and_list_columns_are_sorted",         # 11 mp
    },
    "test_render.py": {"test_hard_timeout_returns_and_replaces_context"},       # 14 mp
    "test_findings.py": {"test_context_and_covered_entities_are_not_uncovered"},  # 11 mp
    "test_contracts.py": {"test_contracts_build_from_the_current_data"},        # több adatbázis
    # négyszer gyűjti be a tesztsort külön folyamatban
    "test_suite_frames.py": {"test_live_tests_run_only_when_named_and_slow_tests_can_be_left_out"},
}
_SOURCE = Path(__file__).parent.parent / "aaa2"


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """A `live` tesztek csak akkor futnak, ha a `-m` kifejezés megnevezi őket; a
    `reference_crawl` használói és a `SLOW` lista tesztjei `slow` jelölést kapnak, az előbbiek
    készletenkénti xdist-csoportot is. A `SLOW` listában maradt, már nem létező név hiba."""
    wants_live = "live" in (config.option.markexpr or "")
    known = {(item.path.name, item.originalname) for item in items}
    stale = sorted((file, name) for file, names in SLOW.items() for name in names
                   if (file, name) not in known and any(i.path.name == file for i in items))
    whole = not config.option.keyword and not any("::" in arg for arg in config.args)
    if stale and whole:
        raise pytest.UsageError(f"a SLOW listában nem létező teszt: {stale}")
    kept, dropped = [], []
    for item in items:
        if "reference_crawl" in getattr(item, "fixturenames", ()):
            params = getattr(getattr(item, "callspec", None), "params", {})
            name = next((value for value in params.values()
                         if isinstance(value, str) and value in REFERENCE_SETS), "egyéb")
            item.add_marker(pytest.mark.slow)
            item.add_marker(pytest.mark.xdist_group(f"reference-{name}"))
        if item.originalname in SLOW.get(item.path.name, ()):
            item.add_marker(pytest.mark.slow)
        (dropped if item.get_closest_marker("live") and not wants_live else kept).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


def replay_key(name: str) -> str | None:
    """A gyorsítótár kulcsa: a felvétel indexe, a motor forrása és a crawl-beállítás; None, ha
    nincs felvétel."""
    index = Recording(name).index_path
    if not index.exists():
        return None
    digest = hashlib.sha256(index.read_bytes())
    for folder, pattern in (("engine", "*.py"), ("engine/config", "*.txt"), ("db", "*.py"),
                            ("db", "*.sql")):
        for path in sorted((_SOURCE / folder).glob(pattern)):
            digest.update(path.name.encode())
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    digest.update(repr(REFERENCE_SETS[name]).encode())
    return digest.hexdigest()[:16]


def cached_replay(name: str):
    """A készlet visszajátszott crawlja memóriabeli kapcsolaton: a gyorsítótárból, ha a kulcs
    egyezik, különben visszajátszva és eltárolva; None, ha nincs felvétel."""
    key = replay_key(name)
    if key is None:
        return None
    use_cache = os.environ.get("AAA2_REPLAY_CACHE", "1") != "0"
    path = REPLAY_CACHE / f"{name}-{key}.duckdb"
    if use_cache and path.exists():
        con = duckdb.connect(":memory:")
        con.execute(f"ATTACH '{path.as_posix()}' AS cached (READ_ONLY)")
        con.execute("COPY FROM DATABASE cached TO memory")
        con.execute("DETACH cached")
        return con
    seed, options = REFERENCE_SETS[name]
    con = asyncio.run(replay_crawl(name, seed, options))[1]
    if use_cache:
        REPLAY_CACHE.mkdir(parents=True, exist_ok=True)
        for old in REPLAY_CACHE.glob(f"{name}-*.duckdb*"):
            old.unlink(missing_ok=True)
        partial = path.with_suffix(f".{os.getpid()}.tmp")
        con.execute(f"ATTACH '{partial.as_posix()}' AS target")
        con.execute("COPY FROM DATABASE memory TO target")
        con.execute("DETACH target")
        os.replace(partial, path)
    return con


@pytest.fixture(scope="session")
def reference_crawl():
    """Név → a visszajátszott crawl kapcsolata, vagy None, ha nincs felvétel. Készletenként az
    első kéréskor játssza vissza (vagy tölti a gyorsítótárból), utána ugyanazt a kapcsolatot
    adja."""
    cache = {}

    def get(name):
        if name not in cache:
            cache[name] = cached_replay(name)
        return cache[name]

    return get
