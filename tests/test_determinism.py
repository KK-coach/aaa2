"""Bájtra azonos kimenet: ugyanazon az adatbázison kétszer futtatva a site-lépés, a gráf és a
megállapítások kimenetei nem változnak; a tárolt JSON kulcsai és a listaoszlopok rendezettek.
Szintetikus site-on, hálózat és LLM nélkül (a valódi készleteken: tests.acceptance.determinism)."""
import json

from aaa2.db.stable_json import dumps
from aaa2.entities.report import write_entity_table
from aaa2.entities.rules import run_rules
from aaa2.functions.findings import build_findings, export_findings, export_views
from aaa2.functions.graph import build_graph, export_csv
from aaa2.resolver.site import run_site
from tests.test_entities_site import NOON
from tests.test_findings import built


def outputs(con, out):
    """A pipeline LLM nélküli lépései és minden kimeneti fájl bájtjai."""
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    export_csv(con, out, "site")
    export_findings(con, out, "site")
    export_views(con, out, "site")
    write_entity_table(con, out / "site-entities.csv")
    return {path.name: path.read_bytes() for path in sorted(out.iterdir())}


def test_two_runs_on_the_same_database_write_identical_outputs(monkeypatch, tmp_path):
    con, _ = built(monkeypatch)
    first = outputs(con, tmp_path / "1")
    second = outputs(con, tmp_path / "2")
    assert len(first) == 9 and all(first.values())             # nem üres zöld
    assert first == second


def test_stored_json_has_sorted_keys_and_list_columns_are_sorted(monkeypatch, tmp_path):
    con, _ = built(monkeypatch)
    outputs(con, tmp_path / "1")
    checked = 0
    for table, column in (("edges", "evidence"), ("findings", "evidence"),
                          ("page_nodes", "decision"), ("page_main_entity", "evidence"),
                          ("entity_relations", "evidence"), ("merge_log", "evidence")):
        for (text,) in con.execute(
                f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL").fetchall():
            assert text == dumps(json.loads(text), ensure_ascii=False), (table, text)
            checked += 1
    assert checked > 50
    lists = con.execute("SELECT aliases FROM entities WHERE len(aliases) > 1").fetchall()
    assert lists and all(aliases == sorted(aliases) for (aliases,) in lists)
    assert dumps({"b": 1, "a": [2, 1]}) == '{"a": [2, 1], "b": 1}'      # a lista sorrendje marad
