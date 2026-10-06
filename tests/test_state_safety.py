"""Állapotvédelem: az újraépítés nem törölheti a kész állapotot a siker előtt, a védelmi üzenet
igazat mond, és a migráció a naplójával együtt történik meg vagy sehogy."""
import duckdb
import pytest
import zstandard

import aaa2.db.connect as connect_module
from aaa2 import api
from aaa2.api import steps
from aaa2.db.connect import connect, migrate
from aaa2.functions.findings import build_findings
from aaa2.functions.graph import build_graph
from tests.test_entities_pipeline import (
    BODY,
    PAGE_REPLY,
    Scripted,
    mention,
    pipeline_run,
    reply,
)
from tests.test_entities_rules import html, ld, site


def counts(con):
    return con.execute(
        "SELECT (SELECT count(*) FROM entities), (SELECT count(*) FROM page_entities), "
        "(SELECT count(*) FROM page_nodes), (SELECT count(*) FROM findings)").fetchone()


def built_site(tmp_path):
    """Kész állapot: entitások a szabálykörből, gráf és megállapítások."""
    organization = ld({"@type": "Organization", "name": "Példa Kft.", "url": "https://pelda.hu/"})
    con = site({"/": html("Példa Kft.", "<main><p>Üdv a Példa Kft. oldalán.</p></main>",
                          head=organization),
                "/a/": html("A oldal", "<main><h2>Alcím</h2><p>Szöveg.</p></main>"),
                "/b/": html("B oldal", "<main><h1>B</h1><p>Szöveg.</p></main>")})
    target = api.Site(con, tmp_path / "pelda.hu.duckdb", "pelda")
    api.rebuild_entities(target, knowledge=False)
    build_graph(con)
    build_findings(con)
    return con, target


def test_a_failed_rebuild_keeps_the_finished_state(tmp_path, monkeypatch):
    con, target = built_site(tmp_path)
    before = counts(con)
    assert before[0] > 0 and before[2] == 3 and before[3] > 0, before

    def broken(*args, **kwargs):
        raise RuntimeError("hiba az újraépítés közben")

    monkeypatch.setattr(steps, "run_rules", broken)
    with pytest.raises(RuntimeError):
        api.rebuild_entities(target, knowledge=False)
    assert counts(con) == before


def test_a_failed_site_round_keeps_the_finished_state(tmp_path, monkeypatch):
    con, target = built_site(tmp_path)
    before = counts(con)

    def broken(*args, **kwargs):
        raise RuntimeError("hiba a site-körben")

    monkeypatch.setattr(steps, "run_site", broken)
    with pytest.raises(RuntimeError):
        api.rebuild_entities(target, knowledge=False)
    assert counts(con) == before


def test_the_guard_message_is_true_after_the_block_rebuild(tmp_path):
    # a tárolt kinyerés egyetlen kész oldalának a tartalma megváltozik: a blokképítés az oldal
    # blokkjait és említéseit eldobná, a védelem pedig megállítja a futást
    con = site({"/": html("Példa Kávézó | Kávé", BODY),
                "/b/": html("B", "<p>A Budapest Coffee Fest idén is lesz</p>")})
    answer = reply(mention("b1", "Budapest Coffee Fest", "Budapest Coffee Fest", "event"))
    pipeline_run(con, Scripted([PAGE_REPLY, answer], [{"decisions": "nem lista"}] * 2), tmp_path)
    target = api.Site(con, tmp_path / "pelda.hu.duckdb", "pelda")
    changed = html("B", "<p>Egészen más szöveg áll ezen az oldalon</p>")
    con.execute("UPDATE pages SET rendered_html = ?, main_content = 'Egészen más szöveg' "
                "WHERE url LIKE '%/b/'", [zstandard.ZstdCompressor().compress(changed.encode())])
    before = counts(con), con.execute("SELECT count(*), max(text) FROM blocks").fetchone()
    assert before[0][1] > 0
    with pytest.raises(api.ApiError) as error:
        api.rebuild_entities(target, knowledge=False)
    assert error.value.code == 3 and "érintetlenek" in error.value.message
    # az üzenet szerint az entitás-táblák érintetlenek: az említések és a blokkok is azok
    assert (counts(con), con.execute("SELECT count(*), max(text) FROM blocks").fetchone()) == before


def test_a_migration_and_its_log_entry_happen_together(tmp_path, monkeypatch):
    # megszakadás a sémamódosítás és a naplózás között: a migráció második utasítása elbukik
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "001_init.sql").write_text("CREATE TABLE t (a INTEGER);", encoding="utf-8")
    monkeypatch.setattr(connect_module, "MIGRATIONS_DIR", folder)
    path = tmp_path / "x.duckdb"
    connect(path).close()
    rename = "ALTER TABLE t RENAME COLUMN a TO b;"
    (folder / "002_rename.sql").write_text(rename + "\nSELECT * FROM nincs_ilyen_tabla;",
                                           encoding="utf-8")
    with pytest.raises(duckdb.Error):
        connect(path)
    # a félbemaradt migráció nem hagy nyomot: az oszlop a régi nevén áll, a napló üres róla
    con = duckdb.connect(str(path))
    assert [row[0] for row in con.execute("DESCRIBE t").fetchall()] == ["a"]
    assert con.execute("SELECT name FROM _migrations ORDER BY name").fetchall() == [
        ("001_init.sql",)]
    con.close()
    # a javított migráció a következő megnyitáskor egyszer lefut; az újranyitás idempotens
    (folder / "002_rename.sql").write_text(rename, encoding="utf-8")
    con = connect(path)
    assert [row[0] for row in con.execute("DESCRIBE t").fetchall()] == ["b"]
    assert migrate(con) == []
    con.close()
    connect(path).close()
