"""A szerződések (aaa2/contracts): felépíthetők-e a mai adatból, és naprakész-e a JSON Schema
exportjuk. Hálózat és LLM nélkül."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aaa2 import contracts
from aaa2.contracts import CONTRACTS, SCHEMA_VERSION
from aaa2.contracts.export import export, json_schemas, schema_text
from tests.contract_rows import SOURCES, build_all, table_rows
from tests.test_entities_site import NOON
from tests.test_findings import BASE, built

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "docs" / "contracts"
NAMED = ("LLMCall", "Page", "Link", "PageMeta", "Block", "Mention", "Candidate", "Entity", "Alias",
         "Relation", "MergeRecord", "KbLink", "PageNode", "Edge", "MainEntity", "EntityWeight",
         "Finding", "StructuredData")


def test_every_named_contract_exists_with_a_version_and_a_module():
    names = {model.__name__ for model in CONTRACTS}
    assert set(NAMED) <= names
    for model in CONTRACTS:
        assert model.model_fields["schema_version"].default == SCHEMA_VERSION
        assert model.module in ("llm", "crawl", "extract", "resolve", "graph", "findings")


def test_contracts_build_from_the_current_data(monkeypatch):
    con, _ = built(monkeypatch)
    # a szintetikus site-on üres táblák egy-egy sora, a séma megszorításai mellett
    (page_id, other_page), = con.execute(
        "SELECT min(page_id), max(page_id) FROM pages").fetchall()
    (entity_id, name), = con.execute(
        "SELECT entity_id, name FROM entities ORDER BY entity_id LIMIT 1").fetchall()
    (run_id,) = con.execute("SELECT max(run_id) FROM entity_runs").fetchone()
    con.execute("INSERT INTO links (from_page_id, to_url, to_page_id, anchor, position, nofollow, "
                "ordinal) VALUES (?, ?, ?, 'Mérés', 'content', false, 0)",
                [page_id, f"{BASE}/meres/", other_page])
    con.execute("INSERT INTO soft_checks (run_id, page_id, entity_id, canonical, type, structure, "
                "blocks, mentions, prominent, rank, knowledge, sol, kept) VALUES "
                "(?, ?, ?, ?, 'concept', 'heading', 2, 3, true, 1, NULL, NULL, true)",
                [run_id, page_id, entity_id, name])
    con.execute("INSERT INTO llm_calls (domain, page_id, model, tokens_in, tokens_out, cost_usd, "
                "purpose, called_at) VALUES ('pelda.hu', ?, 'gpt-6-luna', 10, 5, 0.001, "
                "'extract', ?)", [page_id, NOON])
    con.execute("INSERT INTO merge_log (run_id, kept_id, removed_id, kept_name, removed_name, "
                "rule, evidence, merged_at) VALUES (?, ?, NULL, ?, 'x', 'alias', ?, ?)",
                [run_id, entity_id, name, json.dumps({"alias": "x"}), NOON])
    result = build_all(con)
    # a pipeline minden táblájából minden sor szerződéssé válik
    for model, (table, _) in SOURCES.items():
        rows, items = result[model.__name__]
        assert rows == con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        assert len(items) == rows
    assert set(result) == {model.__name__ for model in CONTRACTS}
    for name, (rows, _) in result.items():
        assert rows > 0, name                               # nem üres zöld
    # a szerződés a sor értékeit hordozza: a mezők a forrásoszlopokkal egyeznek
    for model, (_, query) in SOURCES.items():
        for row, item in zip(table_rows(con, query), result[model.__name__][1], strict=True):
            data = item.model_dump()
            for column, value in row.items():
                field = model.renamed.get(column, column)
                if field not in model.model_fields:
                    continue
                if field in model.json_fields() and isinstance(value, str):
                    value = json.loads(value)
                if value is None and isinstance(data[field], list):
                    value = []
                assert data[field] == value, (model.__name__, column)
    # a sorból kimaradó oszlop nem vész el észrevétlenül: csak az ismert kivételek maradnak ki
    left_out = {}
    for model, (table, query) in SOURCES.items():
        if model in (contracts.Entity, contracts.KbLink):
            continue
        columns = set(table_rows(con, query + " LIMIT 1")[0]) if result[model.__name__][0] else set()
        missing = {c for c in columns if model.renamed.get(c, c) not in model.model_fields}
        if missing:
            left_out[table] = missing
    assert left_out == {"pages": {"canonical", "hreflang"}}            # ezek a PageMeta mezői
    entity_columns = set(table_rows(con, "SELECT * FROM entities LIMIT 1")[0])
    covered = set(contracts.Entity.model_fields) | set(contracts.KbLink.model_fields)
    assert entity_columns <= covered
    # a forrás és a strukturált adat a szülőjével együtt épül
    assert sum(len(m.sources) for m in result["Mention"][1]) == result["MentionSource"][0]
    assert sum(len(m.structured_data) for m in result["PageMeta"][1]) \
        == result["StructuredData"][0]
    assert {item.syntax for item in result["StructuredData"][1]} == {"json-ld"}


def test_a_contract_is_immutable_and_rejects_unknown_fields():
    finding = contracts.Finding(finding_id=1, type="unclear_topic", severity="low", summary="x",
                                evidence='{"url": "https://pelda.hu/"}')
    assert finding.evidence == {"url": "https://pelda.hu/"} and finding.recommendation is None
    with pytest.raises(ValidationError):
        finding.summary = "y"
    with pytest.raises(ValidationError):
        contracts.Finding(finding_id=1, type="t", severity="low", summary="x", extra=1)
    with pytest.raises(ValidationError):
        contracts.Edge(edge_id=1, from_kind="page", from_id=1, to_kind="entity", to_id=2,
                       type="unknown", source="x")
    # a többi jelölés helye megvan a strukturált adatban
    micro = contracts.StructuredData(page_id=1, syntax="microdata", type="Product",
                                     data={"name": "x"})
    assert micro.syntax == "microdata"


def test_the_exported_json_schemas_are_current(tmp_path):
    schemas = json_schemas()
    assert set(schemas) == {model.__name__ for model in CONTRACTS}
    for name, schema in schemas.items():
        assert schema["x-schema-version"] == SCHEMA_VERSION
        assert "schema_version" in schema["properties"]
        assert (SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8") \
            == schema_text(schema), f"elavult: futtasd `python -m aaa2.contracts.export {SCHEMA_DIR.name}`"
    assert sorted(p.name for p in SCHEMA_DIR.glob("*.schema.json")) \
        == sorted(f"{name}.schema.json" for name in schemas)
    assert len(export(tmp_path)) == len(CONTRACTS)
