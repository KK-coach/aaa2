"""Az API homlokzat (aaa2/api): a lépések és a szerződést adó lekérdezések szintetikus site-on,
és a docstringekből generált referencia naprakészsége. Hálózat és LLM nélkül."""
import csv
import json
from pathlib import Path

import pytest

from aaa2 import api, contracts
from aaa2.api.reference import reference
from tests.test_entities_rules import html, ld, site

BASE = "https://pelda.hu"
REFERENCE = Path(__file__).resolve().parents[1] / "docs" / "api" / "reference.md"


def business():
    service = ld({"@type": "Service", "name": "Mérés", "url": f"{BASE}/meres/"})
    pages = {"/": html("Pelda", "<h1>Pelda</h1><a href='/meres/'>Mérés</a>",
                       head=ld({"@type": "Organization", "name": "Pelda", "url": f"{BASE}/"})),
             "/meres/": html("Mérés | Pelda", "<h1>Mérés</h1><p>Webanalitika.</p>", head=service),
             "/blog/": html("Blog | Pelda", "<h1>Blog</h1><a href='/meres/'>Mérés</a>")}
    return api.Site(site(pages), Path("pelda.duckdb"), "pelda")


def test_the_steps_run_in_order_and_the_queries_return_contracts():
    opened = business()
    assert opened.domain == "pelda.hu"
    with pytest.raises(api.GraphMissing):
        api.find(opened)
    messages = []
    extracted = api.extract(opened, llm=False, notify=messages.append)
    assert len(extracted.run_ids) == 1 and not extracted.estimate_only and messages == []
    resolved = api.resolve(opened, knowledge=False)
    assert resolved.site_run is not None and resolved.linked is None
    graph = api.build_graph(opened, wikidata=False)
    assert graph.run.pages == 3 and graph.paths == {}
    found = api.find(opened)
    assert found.paths == {} and sum(found.run.counts.values()) == len(api.findings(opened))
    con = opened.con
    for query, model, table in (
            (api.pages, contracts.Page, "pages"), (api.links, contracts.Link, "links"),
            (api.entities, contracts.Entity, "entities"),
            (api.kb_links, contracts.KbLink, "entities"),
            (api.page_nodes, contracts.PageNode, "page_nodes"),
            (api.edges, contracts.Edge, "edges"),
            (api.main_entities, contracts.MainEntity, "page_main_entity"),
            (api.weights, contracts.EntityWeight, "entity_weights"),
            (api.findings, contracts.Finding, "findings")):
        items = query(opened)
        assert all(isinstance(item, model) for item in items), table
        assert len(items) == con.execute(f"SELECT count(*) FROM {table}").fetchone()[0], table
    assert len(api.pages(opened)) == 3 and len(api.entities(opened)) >= 2     # nem üres zöld
    assert {e.type for e in api.edges(opened, "mentions")} == {"mentions"}
    assert {item.syntax for item in api.structured_data(opened)} == {"json-ld"}
    assert isinstance(api.site_profile(opened), contracts.Site)
    state = api.status(opened)
    assert (state.pages, state.errors, state.by_class) == (3, 0, [("2xx", 3)])
    assert api.entity_run(opened, extracted.run_ids[0])[1] == "rules"
    assert isinstance(api.entity_run_skipped(opened, extracted.run_ids[0]), dict)
    assert sum(count for _, count, _ in api.entity_type_counts(opened)) >= 2


def test_estimate_without_llm_only_reports():
    opened = business()
    messages = []
    extracted = api.extract(opened, llm=False, estimate=True, notify=messages.append)
    assert extracted.estimate_only and extracted.run_ids == []
    assert messages == ["becslés: az LLM-lépések kikapcsolva, nincs hívás"]


def test_errors_are_api_errors_with_exit_codes(tmp_path):
    with pytest.raises(api.SiteNotFound) as missing:
        api.open_site("nincs.hu", tmp_path / "nincs.duckdb")
    assert missing.value.code == 1 and "nincs adatbázis" in missing.value.message
    with pytest.raises(api.ApiError, match="ismeretlen tábla"):
        api.table_rows(business(), "nincs_ilyen")
    columns, rows = api.table_rows(business(), "pages")
    assert "url" in columns and "rendered_html" not in columns and len(rows) == 3
    assert api.domain_of("https://www.Pelda.hu/x") == "pelda.hu"
    assert api.domain_of("Pelda.HU") == "pelda.hu"


def test_the_generated_reference_is_current_and_covers_the_public_names():
    text = reference()
    assert REFERENCE.read_text(encoding="utf-8") == text, \
        "elavult: futtasd `python -m aaa2.api.reference docs/api`"
    for name in api.__all__:
        assert f"### `{name}`" in text, name
    for step in ("crawl", "extract", "resolve", "build_graph", "find"):
        assert callable(getattr(api, step))


def test_views_are_a_versioned_contract_consistent_with_the_csv_views(tmp_path):
    opened = business()
    api.extract(opened, llm=False)
    api.resolve(opened, knowledge=False)
    api.build_graph(opened, wikidata=False)
    paths = api.find(opened, out=tmp_path).paths
    views = api.views(opened)
    assert isinstance(views, contracts.SiteViews)
    assert (views.site, views.domain, views.schema_version) == (
        "pelda", "pelda.hu", contracts.SCHEMA_VERSION)
    # ugyanaz az adat, mint a CSV-nézetekben és a megállapítások CSV-jében
    def rows(key):
        with paths[key].open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    page_rows, entity_rows, finding_rows = rows("pages"), rows("entities"), rows("findings")
    assert len(views.pages) == len(page_rows) == 3
    assert len(views.entities) == len(entity_rows) >= 1                    # nem üres zöld
    assert len(views.findings) == len(finding_rows) == len(api.findings(opened))
    for page, row in zip(views.pages, page_rows, strict=True):
        assert (page.url, page.role, page.main_entity or "", page.h1 or "") == (
            row["url"], row["szerep"], row["fő entitás"], row["H1"])
        assert " || ".join(page.findings) == row["megállapítások"]
        assert {True: "igen", False: "nem", None: ""}[page.in_h1] == row["a H1-ben"]
    for entity, row in zip(views.entities, entity_rows, strict=True):
        assert (entity.name, str(entity.rank), " | ".join(entity.main_pages)) == (
            row["entitás"], row["rang"], row["fő oldalak"])
        assert "; ".join(f"{r.type} {r.direction} {r.entity}" for r in entity.relations) \
            == row["élek"]
    for finding, row in zip(views.findings, finding_rows, strict=True):
        assert (str(finding.finding_id), finding.label, finding.severity,
                " | ".join(finding.pages), finding.summary, finding.entity or "") == (
            row["azonosító"], row["típus"], row["súlyosság"], row["oldalak"],
            row["összefoglaló"], row["entitás"])
        assert finding.recommendation is None
    # a JSON stabil, visszaolvasható, és a fájl ugyanaz
    text = api.views_json(opened)
    assert text == api.views_json(opened)
    assert contracts.SiteViews.model_validate(json.loads(text)) == views
    assert json.loads(text)["schema_version"] == contracts.SCHEMA_VERSION
    written = api.export_views_json(opened, tmp_path)
    assert written.name == "pelda-views.json" and written.read_text(encoding="utf-8") == text
