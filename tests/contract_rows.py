"""A szerződések felépítése egy mai adatbázisból: a tesztek és a mérés segédje. A motor nem
használja; a modulok saját lekérdező függvényei az architektúra későbbi lépésében jönnek."""
from __future__ import annotations

from collections import defaultdict

import duckdb

from aaa2 import contracts
from aaa2.contracts import Contract

# szerződés → (tábla, lekérdezés); a `rendered_html` (a renderelt DOM tömörítve) nem szerződés
SOURCES: dict[type[Contract], tuple[str, str]] = {
    contracts.LLMCall: ("llm_calls", "SELECT * FROM llm_calls ORDER BY call_id"),
    contracts.Page: ("pages", "SELECT * EXCLUDE (rendered_html) FROM pages ORDER BY page_id"),
    contracts.Link: ("links", "SELECT * FROM links ORDER BY from_page_id, ordinal, to_url"),
    contracts.StructuredData: ("schema_blocks",
                               "SELECT * FROM schema_blocks ORDER BY page_id, ordinal"),
    contracts.Block: ("blocks", "SELECT * FROM blocks ORDER BY block_id"),
    contracts.Mention: ("page_entities", "SELECT * FROM page_entities ORDER BY mention_id"),
    contracts.Candidate: ("soft_checks",
                          "SELECT * FROM soft_checks ORDER BY run_id, page_id, canonical, type"),
    contracts.Entity: ("entities", "SELECT * FROM entities ORDER BY entity_id"),
    contracts.KbLink: ("entities", "SELECT * FROM entities ORDER BY entity_id"),
    contracts.Alias: ("entity_aliases",
                      "SELECT * FROM entity_aliases ORDER BY entity_id, alias, source"),
    contracts.Relation: ("entity_relations",
                         "SELECT * FROM entity_relations ORDER BY from_id, to_id, type"),
    contracts.MergeRecord: ("merge_log", "SELECT * FROM merge_log ORDER BY merge_id"),
    contracts.PageNode: ("page_nodes", "SELECT * FROM page_nodes ORDER BY page_id"),
    contracts.Edge: ("edges", "SELECT * FROM edges ORDER BY edge_id"),
    contracts.MainEntity: ("page_main_entity",
                           "SELECT * FROM page_main_entity ORDER BY page_id, rank"),
    contracts.EntityWeight: ("entity_weights", "SELECT * FROM entity_weights ORDER BY entity_id"),
    contracts.Finding: ("findings", "SELECT * FROM findings ORDER BY finding_id"),
}


def table_rows(con: duckdb.DuckDBPyConnection, query: str) -> list[dict]:
    cursor = con.execute(query)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {name for (name,) in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()}


def build_all(con: duckdb.DuckDBPyConnection) -> dict[str, tuple[int, list[Contract]]]:
    """Szerződésnév → (a forrástábla sorainak száma, a felépített szerződések). A hiányzó
    tábla szerződése kimarad. A `Mention` a forrásaival, a `PageMeta` az oldal strukturált
    adataival együtt épül; a `StructuredData` a JSON-LD blokkokból (`schema_blocks`) és a
    microdata-, RDFa-elemekből (`structured_data`)."""
    present = tables(con)
    built: dict[str, tuple[int, list[Contract]]] = {}
    for model, (table, query) in SOURCES.items():
        if table not in present:
            continue
        rows = table_rows(con, query)
        built[model.__name__] = (len(rows), [model.from_row(row) for row in rows])
    if "Mention" in built and "mention_sources" in present:
        sources = defaultdict(list)
        for row in table_rows(con, "SELECT * FROM mention_sources ORDER BY mention_id, source, "
                                   "run_id, llm_call_id"):
            sources[row["mention_id"]].append(contracts.MentionSource.from_row(row))
        count, mentions = built["Mention"]
        built["Mention"] = (count, [m.model_copy(update={"sources": sources[m.mention_id]})
                                    for m in mentions])
        built["MentionSource"] = (sum(len(s) for s in sources.values()),
                                  [s for group in sources.values() for s in group])
    if "StructuredData" in built and "structured_data" in present:
        rows = table_rows(con, "SELECT * FROM structured_data ORDER BY page_id, ordinal, syntax")
        count, items = built["StructuredData"]
        built["StructuredData"] = (count + len(rows), items + [
            contracts.StructuredData.from_row(row) for row in rows])
    if "Page" in built:
        structured = defaultdict(list)
        for item in built.get("StructuredData", (0, []))[1]:
            structured[item.page_id].append(item)
        rows = table_rows(con, "SELECT page_id, canonical, hreflang FROM pages ORDER BY page_id")
        built["PageMeta"] = (len(rows), [
            contracts.PageMeta(**row, structured_data=structured[row["page_id"]])
            for row in rows])
    return built
