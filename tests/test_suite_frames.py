"""A tesztsor keretei: a `slow` jelölés, a `live` kihagyása és a visszajátszás gyorsítótára."""
import subprocess
import sys

import duckdb

from tests import conftest
from tests.recorded import REFERENCE_SETS, Recording


def collected(*args: str) -> str:
    """A gyűjtés összesítő sora a megadott kapcsolókkal (a tesztek nem futnak)."""
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--collect-only", *args],
        capture_output=True, text=True, encoding="utf-8", check=False)
    return done.stdout.strip().splitlines()[-1]


def counts(line: str) -> tuple[int, int]:
    """(kiválasztott, összes) a gyűjtés összesítő sorából."""
    selected, _, total = line.split(" ")[0].partition("/")
    return int(selected), int(total or selected)


def test_live_tests_run_only_when_named_and_slow_tests_can_be_left_out():
    everything, total = counts(collected())
    fast, _ = counts(collected("-m", "not slow"))
    live, _ = counts(collected("-m", "live"))
    assert live > 0 and everything + live == total      # a `live` alapból kimarad
    assert 0 < fast < everything                        # a gyors sor a `live`-ot sem futtatja
    # a `reference_crawl` használói maguktól `slow` jelölést kapnak
    listed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--collect-only",
         "-m", "slow"], capture_output=True, text=True, encoding="utf-8", check=False).stdout
    assert "test_entities_rules.py::test_reference_entities[materia-crawl]" in listed
    assert "test_crawl.py::test_replay_materia_crawl" in listed         # a `SLOW` listából


def test_the_slow_list_names_existing_tests():
    for file, names in conftest.SLOW.items():
        source = (conftest.Path(__file__).parent / file).read_text(encoding="utf-8")
        for name in names:
            assert f"def {name}(" in source, (file, name)


def test_replay_cache_key_follows_the_recording_the_engine_and_the_options(tmp_path, monkeypatch):
    assert conftest.replay_key("nincs-ilyen-keszlet") is None
    name = next((n for n in REFERENCE_SETS if Recording(n).exists), None)
    if name is None:
        return
    key = conftest.replay_key(name)
    assert key == conftest.replay_key(name) and len(key) == 16
    seed, options = REFERENCE_SETS[name]
    monkeypatch.setitem(REFERENCE_SETS, name, (seed + "x", options))
    assert conftest.replay_key(name) != key             # más beállítás: más kulcs
    monkeypatch.setitem(REFERENCE_SETS, name, (seed, options))
    engine = tmp_path / "aaa2" / "engine"
    engine.mkdir(parents=True)
    (engine / "crawl.py").write_text("# más motor\n", encoding="utf-8")
    monkeypatch.setattr(conftest, "_SOURCE", tmp_path / "aaa2")
    assert conftest.replay_key(name) != key             # más motor: más kulcs


def test_cached_replay_loads_the_stored_crawl_into_memory(tmp_path, monkeypatch):
    name = "probakeszlet"
    monkeypatch.setattr(conftest, "REPLAY_CACHE", tmp_path)
    monkeypatch.setattr(conftest, "replay_key", lambda _: "0123456789abcdef")
    stored = duckdb.connect(str(tmp_path / f"{name}-0123456789abcdef.duckdb"))
    stored.execute("CREATE TABLE pages (url VARCHAR)")
    stored.execute("INSERT INTO pages VALUES ('https://pelda.hu/')")
    stored.close()
    con = conftest.cached_replay(name)
    assert con.execute("SELECT url FROM pages").fetchall() == [("https://pelda.hu/",)]
    con.execute("INSERT INTO pages VALUES ('https://pelda.hu/2')")      # a másolat írható
    again = conftest.cached_replay(name)
    assert again.execute("SELECT count(*) FROM pages").fetchone() == (1,)   # a tár nem változott
