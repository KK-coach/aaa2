"""A séma lefut memóriában, a migráció idempotens, és egy kilőtt folyamat adatbázisa megnyílik."""
import subprocess
import sys

import duckdb
import pytest

from aaa2.db.connect import connect, migrate
from aaa2.llm.schemas import ENTITY_TYPES

EXPECTED = {
    "site", "crawl_runs", "crawl_queue", "pages", "links", "headings",
    "schema_blocks", "entities", "page_entities", "llm_calls", "rules", "rule_events",
    "entity_runs", "blocks", "mention_sources", "page_entities_v1",
}


def test_schema_creates_all_tables():
    con = connect(":memory:")
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name NOT LIKE '\\_%' ESCAPE '\\'"
    ).fetchall()}
    assert EXPECTED <= tables


def test_migrate_is_idempotent():
    con = connect(":memory:")
    assert migrate(con) == []


def test_a_mention_has_a_block_and_a_span_unless_it_is_schema():
    con = connect(":memory:")
    insert = ("INSERT INTO page_entities (page_id, entity_id, block_id, char_start, char_end, "
              "surface_form, position) VALUES (1, 1, ?, ?, ?, ?, ?)")
    con.execute(insert, [5, 2, 9, "Példa", "body"])
    con.execute(insert, [None, None, None, "Példa", "schema"])
    for block, start, end, surface, position in (
            (None, None, None, "Példa", "body"),        # blokk nélkül csak schema
            (5, 2, 9, "Példa", "schema"),               # a schemának nincs blokkja
            (5, 9, 2, "Példa", "body"),                 # üres vagy fordított pozíció
            (5, 2, 9, None, "body")):                   # szöveg szerinti alak nélkül nincs sor
        with pytest.raises(duckdb.ConstraintException):
            con.execute(insert, [block, start, end, surface, position])
    with pytest.raises(duckdb.ConstraintException):          # egy említés egyszer
        con.execute(insert, [5, 2, 9, "Példa", "body"])


def test_blocks_kind_and_region_are_closed_lists():
    con = connect(":memory:")
    insert = ("INSERT INTO blocks (page_id, ordinal, kind, region, heading_path, text) "
              "VALUES (1, ?, ?, ?, [], 'x')")
    con.execute(insert, [0, "title", "content"])
    con.execute(insert, [1, "card", "chrome"])
    for ordinal, kind, region in ((2, "div", "content"), (3, "other", "menu"),
                                  (1, "other", "content")):
        with pytest.raises(duckdb.ConstraintException):
            con.execute(insert, [ordinal, kind, region])


def test_entity_type_is_one_of_the_ten():
    con = connect(":memory:")
    for entity_type in ENTITY_TYPES:
        con.execute("INSERT INTO entities (name, lang, type) VALUES (?, 'hu', ?)",
                    [f"X-{entity_type}", entity_type])
    assert len(ENTITY_TYPES) == 10
    assert con.execute("SELECT count(DISTINCT type) FROM entities").fetchone() == (10,)
    with pytest.raises(duckdb.ConstraintException, match="CHECK constraint"):
        con.execute("INSERT INTO entities (name, type) VALUES ('Y', 'software')")
    with pytest.raises(duckdb.ConstraintException, match="entities.type"):
        con.execute("INSERT INTO entities (name) VALUES ('Z')")


def test_migration_005_keeps_existing_rows(tmp_path):
    """Egy 004-es állapotú adatbázis entitás- és oldal-entitás sorai átkerülnek; az új oszlopok
    NULL-ok, az azonosító-szekvencia onnan folytatódik, a régi hívássor 1 kísérletet kap."""
    import aaa2.db.connect as connect_module
    path = tmp_path / "x.hu.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE _migrations (name VARCHAR PRIMARY KEY, applied_at TIMESTAMP)")
    for sql_file in sorted(connect_module.MIGRATIONS_DIR.glob("00[1-4]_*.sql")):
        con.execute(sql_file.read_text(encoding="utf-8"))
        con.execute("INSERT INTO _migrations VALUES (?, current_timestamp)", [sql_file.name])
    con.execute("INSERT INTO entities (name, type, aliases) VALUES ('Materia', 'brand', ['MTM'])")
    con.execute("INSERT INTO page_entities (page_id, entity_id, position, evidence, source) "
                "VALUES (1, 1, 'title', 'Materia', 'rule')")
    con.execute("INSERT INTO llm_calls (domain, model, purpose, called_at) "
                "VALUES ('entity', 'gemini-3.8-flash', 'extract', now())")
    con.close()
    con = connect(path)
    assert con.execute("SELECT entity_id, name, lang, type, aliases FROM entities").fetchall() == [
        (1, "Materia", None, "brand", ["MTM"])]
    assert con.execute("SELECT evidence, source, llm_call_id FROM page_entities_v1"
                       ).fetchall() == [("Materia", "rule", None)]
    assert con.execute("SELECT count(*) FROM page_entities").fetchone() == (0,)
    con.execute("INSERT INTO entities (name, type) VALUES ('Budapest', 'place')")
    assert con.execute("SELECT max(entity_id) FROM entities").fetchone() == (2,)
    assert con.execute("SELECT call_id, attempts FROM llm_calls").fetchall() == [(1, 1)]


KILLED_WRITER = """
import os, sys
from aaa2.db.connect import connect
con = connect(sys.argv[1])
con.execute("INSERT INTO site (domain, seed_url) VALUES ('x.hu', 'https://x.hu/')")
for i in range(20):
    con.execute("INSERT INTO pages (url, status) VALUES (?, 200)", [f"https://x.hu/{i}/"])
os._exit(0)
"""


def test_database_of_killed_process_reopens(tmp_path):
    """Friss adatbázis, migráció, oldalsorok, majd a folyamat lezárás nélkül kilép (mint egy
    megszakított crawl): az adatbázis megnyílik, és a sorok megvannak."""
    path = tmp_path / "x.hu.duckdb"
    subprocess.run([sys.executable, "-c", KILLED_WRITER, str(path)], check=True)
    assert path.with_name("x.hu.duckdb.wal").exists()
    con = connect(path)
    assert con.execute("SELECT count(*) FROM pages").fetchone() == (20,)
