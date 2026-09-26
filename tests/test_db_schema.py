"""A séma lefut memóriában, a migráció idempotens, és egy kilőtt folyamat adatbázisa megnyílik."""
import subprocess
import sys

import duckdb
import pytest

from aaa2.db.connect import connect, migrate

EXPECTED = {
    "site", "crawl_runs", "crawl_queue", "pages", "links", "headings",
    "schema_blocks", "entities", "page_entities", "llm_calls", "rules", "rule_events",
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


def test_page_entities_requires_evidence():
    con = connect(":memory:")
    con.execute("INSERT INTO pages (url) VALUES ('https://x.hu/')")
    con.execute("INSERT INTO entities (name) VALUES ('X')")
    with pytest.raises(duckdb.ConstraintException, match="page_entities.evidence"):
        con.execute(
            "INSERT INTO page_entities (page_id, entity_id, position, evidence, source) "
            "VALUES (1, 1, 'body', NULL, 'llm')"
        )


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
