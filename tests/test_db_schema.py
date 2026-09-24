"""A séma lefut memóriában, és a migráció idempotens."""
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
    try:
        con.execute(
            "INSERT INTO page_entities (page_id, entity_id, position, evidence, source) "
            "VALUES (1, 1, 'body', NULL, 'llm')"
        )
        assert False, "bizonyíték nélkül nem lehet sor"
    except Exception:
        pass
