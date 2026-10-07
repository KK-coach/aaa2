"""Entitástár: az öt közösen használt tábla (`entities`, `page_entities`, `mention_sources`,
`soft_checks`, `entity_runs`) minden SQL-je. A tár az extract modulé; az extract és a resolve is
ezen keresztül ír és olvas, más modul (gráf, megállapítások, jelentés, CLI) ezen keresztül olvas.

A függvények egy-egy korábbi SQL-utasítást hordoznak változatlanul (a nevük a táblából és a
hívó függvényből áll), és egyszerű sorokat adnak vissza. A `Hívja:` sor a hívókat sorolja."""
from __future__ import annotations

import duckdb


def entities_for_entities(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities, page_entities. Hívja: cli/main.py: entities."""
    return con.execute(
        "SELECT e.type, count(DISTINCT e.entity_id), count(*) FROM entities e JOIN "
        "page_entities pe USING (entity_id) GROUP BY e.type ORDER BY count(DISTINCT "
        "e.entity_id) DESC, e.type").fetchall()


def entity_runs_for_entities(con: duckdb.DuckDBPyConnection, run_id) -> tuple | None:
    """Lekérdezés: entity_runs. Hívja: cli/main.py: entities."""
    return con.execute(
        "SELECT skipped FROM entity_runs WHERE run_id = ? ORDER BY ALL", [run_id]).fetchone()


def delete_mention_sources_in_drop_page_blocks(con: duckdb.DuckDBPyConnection, page_id):
    """Törlés: mention_sources, page_entities. Hívja: entities/dom.py: drop_page_blocks."""
    con.execute(
        "DELETE FROM mention_sources WHERE mention_id IN (SELECT mention_id FROM "
        "page_entities WHERE page_id = ?)", [page_id])


def delete_page_entities_in_drop_page_blocks(con: duckdb.DuckDBPyConnection, page_id):
    """Törlés: page_entities. Hívja: entities/dom.py: drop_page_blocks."""
    con.execute(
        "DELETE FROM page_entities WHERE page_id = ?", [page_id])


def delete_soft_checks_in_drop_page_blocks(con: duckdb.DuckDBPyConnection, page_id):
    """Törlés: soft_checks. Hívja: entities/dom.py: drop_page_blocks; entities/v3.py: store_soft_checks."""
    con.execute(
        "DELETE FROM soft_checks WHERE page_id = ?", [page_id])


def delete_all_in_clear_entities(con: duckdb.DuckDBPyConnection):
    """Törlés: mention_sources, soft_checks, page_entities, entities. Hívja:
    entities/extract.py: clear_entities."""
    for table in ("mention_sources", "soft_checks", "page_entities", "entities"):
        con.execute(f"DELETE FROM {table}")


def entity_run_pages_for_stored_records(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entity_run_pages, entity_runs. Hívja: entities/extract.py: stored_records."""
    return con.execute(
        "SELECT p.page_id, r.model, p.extraction, p.refined, p.chunks, p.input_hash, "
        "p.raw_html_hash, p.run_id, p.fabricated, p.blocks_hash FROM entity_run_pages p "
        "JOIN entity_runs r USING (run_id) WHERE "
        "r.method = 'llm' AND p.status = 'done' AND p.extraction IS NOT NULL ORDER BY "
        "p.run_id DESC, p.finished_at DESC, p.page_id").fetchall()


def delete_entities_in_run_llm(con: duckdb.DuckDBPyConnection):
    """Törlés: entities, page_entities. Hívja: entities/extract.py: run_llm."""
    con.execute(
        "DELETE FROM entities WHERE source = 'llm' AND entity_id NOT IN (SELECT "
        "entity_id FROM page_entities)")


def update_entity_runs_in_finish(con: duckdb.DuckDBPyConnection, value, done, pages_with, entities, rows, value_2, cost, fabricated, value_3, value_4, seconds, run_id):
    """Módosítás: entity_runs. Hívja: entities/extract.py: finish."""
    con.execute(
        "UPDATE entity_runs SET finished_at = ?, pages = ?, pages_with_entities = ?, "
        "entities = ?, row_count = ?, llm_calls = ?, cost_usd = ?, fabricated = ?, "
        "by_position = ?, skipped = ?, seconds = coalesce(seconds, 0) + ? WHERE run_id "
        "= ?", [value, done, pages_with, entities, rows, value_2, cost, fabricated, value_3, value_4, seconds, run_id])


def update_entity_runs_in_refresh(con: duckdb.DuckDBPyConnection, pages_with, entities, rows, positions, run_id):
    """Módosítás: entity_runs. Hívja: entities/extract.py: refresh."""
    con.execute(
        "UPDATE entity_runs SET pages_with_entities = ?, entities = ?, row_count = ?, "
        "by_position = ? WHERE run_id = ?", [pages_with, entities, rows, positions, run_id])


def lowercase_word_entities(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: page_entities, entities. Hívja: functions/findings.py: _Site.__init__."""
    return con.execute(
        "SELECT DISTINCT entity_id FROM page_entities WHERE surface_form = lower(surface_form) "
        "AND surface_form NOT LIKE '% %' AND surface_form <> upper(surface_form) UNION "
        "SELECT entity_id FROM entities, unnest(aliases) AS a(alias) WHERE alias = lower(alias) "
        "AND alias NOT LIKE '% %' AND alias <> upper(alias) ORDER BY 1").fetchall()


def delete_mention_sources_in_store(con: duckdb.DuckDBPyConnection, page_id, value):
    """Törlés: mention_sources, page_entities. Hívja: entities/extract.py: store."""
    con.execute(
        "DELETE FROM mention_sources WHERE source = 'llm' AND mention_id IN (SELECT "
        "mention_id FROM page_entities WHERE page_id = ?) AND list_contains(?, "
        "llm_call_id)", [page_id, value])


def delete_page_entities_in_store(con: duckdb.DuckDBPyConnection, page_id):
    """Törlés: mention_sources, page_entities. Hívja: entities/extract.py: store."""
    con.execute(
        "DELETE FROM page_entities WHERE page_id = ? AND mention_id NOT IN (SELECT "
        "mention_id FROM mention_sources)", [page_id])


def insert_mention_sources_in_store(con: duckdb.DuckDBPyConnection, mention_id, run_id, extract_call):
    """Beszúrás: mention_sources. Hívja: entities/extract.py: store."""
    con.execute(
        "INSERT INTO mention_sources (mention_id, source, run_id, llm_call_id) VALUES "
        "(?, 'llm', ?, ?)", [mention_id, run_id, extract_call])


def update_page_entities_in_store_mention(con: duckdb.DuckDBPyConnection, description, value):
    """Módosítás: page_entities. Hívja: entities/extract.py: store_mention."""
    con.execute(
        "UPDATE page_entities SET description = coalesce(description, ?) WHERE "
        "mention_id = ?", [description, value])


def update_entities_in_entityindex_resolve(con: duckdb.DuckDBPyConnection, form, entity_id, form_2, form_3):
    """Módosítás: entities. Hívja: entities/extract.py: entityindex_resolve."""
    con.execute(
        "UPDATE entities SET aliases = list_append(coalesce(aliases, []), ?) WHERE "
        "entity_id = ? AND name <> ? AND NOT list_contains(coalesce(aliases, []), ?)", [form, entity_id, form_2, form_3])


def entity_runs_for_reusable_pages(con: duckdb.DuckDBPyConnection, model) -> list[tuple]:
    """Lekérdezés: entity_run_pages, entity_runs. Hívja: entities/extract.py: reusable_pages."""
    return con.execute(
        "SELECT p.page_id, p.input_hash, p.extraction, p.refined FROM entity_run_pages "
        "p JOIN entity_runs r USING (run_id) WHERE r.method = 'llm' AND r.model = ? "
        "AND p.status IN ('done', 'extracted') AND p.input_hash IS NOT NULL AND "
        "p.extraction IS NOT NULL ORDER BY p.run_id DESC, p.finished_at DESC", [model]).fetchall()


def mention_sources_for_finish(con: duckdb.DuckDBPyConnection, run_id) -> tuple | None:
    """Lekérdezés: mention_sources, page_entities. Hívja: entities/extract.py: finish."""
    return con.execute(
        "SELECT count(DISTINCT pe.entity_id), count(DISTINCT pe.page_id) FROM "
        "mention_sources ms JOIN page_entities pe USING (mention_id) WHERE ms.run_id = "
        "? AND ms.source = 'llm' ORDER BY ALL", [run_id]).fetchone()


def page_entities_for_store_mention(con: duckdb.DuckDBPyConnection, page_id, value, start, end, entity_id) -> tuple | None:
    """Lekérdezés: page_entities. Hívja: entities/extract.py: store_mention; entities/rules.py: store_mention; resolver/navigation.py: add_mention."""
    return con.execute(
        "SELECT mention_id FROM page_entities WHERE page_id = ? AND block_id = ? AND "
        "char_start = ? AND char_end = ? AND entity_id = ? ORDER BY ALL", [page_id, value, start, end, entity_id]).fetchone()


def insert_page_entities_in_store_mention(con: duckdb.DuckDBPyConnection, page_id, entity_id, value, start, end, value_2, position, description) -> tuple | None:
    """Beszúrás: page_entities. Hívja: entities/extract.py: store_mention."""
    return con.execute(
        "INSERT INTO page_entities (page_id, entity_id, block_id, char_start, "
        "char_end, surface_form, position, description) VALUES (?, ?, ?, ?, ?, ?, ?, "
        "?) RETURNING mention_id", [page_id, entity_id, value, start, end, value_2, position, description]).fetchone()


def update_entities_in_entityindex_write_votes(con: duckdb.DuckDBPyConnection, value, suggested, entity_id):
    """Módosítás: entities. Hívja: entities/extract.py: entityindex_write_votes."""
    con.execute(
        "UPDATE entities SET type_votes = ?, type_suggested = ? WHERE entity_id = ?", [value, suggested, entity_id])


def entities_for_majority_types(con: duckdb.DuckDBPyConnection, protected) -> list[tuple]:
    """Lekérdezés: entities. Hívja: entities/extract.py: apply_majority_types."""
    return con.execute(
        "SELECT entity_id, type, subtype, type_votes FROM entities WHERE type_votes IS NOT NULL "
        "AND NOT list_contains(?, source) ORDER BY entity_id", [list(protected)]).fetchall()


def update_entities_in_apply_majority_types(con: duckdb.DuckDBPyConnection, kind, subtype,
                                            entity_id):
    """Módosítás: entities. Hívja: entities/extract.py: apply_majority_types."""
    con.execute(
        "UPDATE entities SET type_changed_from = type, type = ?, subtype = ? WHERE "
        "entity_id = ?", [kind, subtype, entity_id])


def entity_runs_for_resumable_run(con: duckdb.DuckDBPyConnection, model) -> tuple | None:
    """Lekérdezés: entity_runs. Hívja: entities/extract.py: resumable_run."""
    return con.execute(
        "SELECT max(run_id) FROM entity_runs WHERE method = 'llm' AND model = ? ORDER "
        "BY ALL", [model]).fetchone()


def insert_entity_runs_in_run_llm(con: duckdb.DuckDBPyConnection, started, model) -> tuple | None:
    """Beszúrás: entity_runs. Hívja: entities/extract.py: run_llm."""
    return con.execute(
        "INSERT INTO entity_runs (started_at, method, model, llm_calls) VALUES (?, "
        "'llm', ?, 0) RETURNING run_id", [started, model]).fetchone()


def mention_sources_for_finish_2(con: duckdb.DuckDBPyConnection, run_id) -> list[tuple]:
    """Lekérdezés: mention_sources, page_entities. Hívja: entities/extract.py: finish."""
    return con.execute(
        "SELECT pe.position, count(*) FROM mention_sources ms JOIN page_entities pe "
        "USING (mention_id) WHERE ms.run_id = ? AND ms.source = 'llm' GROUP BY "
        "pe.position ORDER BY pe.position", [run_id]).fetchall()


def entities_for_entityindex___init__(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: entities/extract.py: entityindex___init__."""
    return con.execute(
        "SELECT entity_id, name, type, source, aliases, type_votes FROM entities ORDER "
        "BY entity_id").fetchall()


def insert_entities_in_entityindex_resolve(con: duckdb.DuckDBPyConnection, form, lang, kind, subtype, created_at) -> tuple | None:
    """Beszúrás: entities. Hívja: entities/extract.py: entityindex_resolve."""
    return con.execute(
        "INSERT INTO entities (name, lang, type, subtype, aliases, source, created_at) "
        "VALUES (?, ?, ?, ?, [], 'llm', ?) RETURNING entity_id", [form, lang, kind, subtype, created_at]).fetchone()


def page_entities_for_entity_table(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: blocks, page_entities. Hívja: entities/report.py: entity_table."""
    return con.execute(
        "SELECT pe.entity_id, pe.page_id, b.kind, b.region FROM page_entities pe LEFT "
        "JOIN blocks b USING (block_id) ORDER BY ALL").fetchall()


def entities_for_entity_table(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: entities/report.py: entity_table."""
    return con.execute(
        "SELECT e.entity_id, e.name, e.type, e.subtype, e.tier, e.flags, e.source, "
        "e.anchor_page_id, e.wikidata_id, e.wikidata_status, e.wikipedia, e.type_votes "
        "FROM entities e ORDER BY e.entity_id").fetchall()


def entity_runs_for_latest(con: duckdb.DuckDBPyConnection, method) -> tuple | None:
    """Lekérdezés: entity_runs. Hívja: entities/report.py: latest."""
    return con.execute(
        "SELECT run_id, model, started_at, finished_at, seconds, pages, llm_calls, "
        "cost_usd, skipped FROM entity_runs WHERE method = ? ORDER BY run_id DESC "
        "LIMIT 1", [method]).fetchone()


def entities_for_entities_section(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities, page_entities. Hívja: entities/report.py: entities_section."""
    return con.execute(
        "SELECT e.type, count(DISTINCT e.entity_id), count(*), count(DISTINCT "
        "pe.page_id), count(DISTINCT e.entity_id) FILTER (WHERE e.wikidata_id IS NOT "
        "NULL AND e.wikidata_status = 'confident'), count(DISTINCT e.entity_id) FILTER "
        "(WHERE e.wikipedia IS NOT NULL AND e.wikidata_status = 'confident') "
        "FROM entities e JOIN page_entities pe USING (entity_id) GROUP BY e.type ORDER "
        "BY count(DISTINCT e.entity_id) DESC, e.type").fetchall()


def probable_link_count(con: duckdb.DuckDBPyConnection) -> int:
    """Lekérdezés: entities, page_entities. Hívja: entities/report.py: entities_section. Az
    említéssel bíró entitások száma, amelyek Wikidata-kapcsolása csak valószínű."""
    return con.execute(
        "SELECT count(*) FROM entities WHERE wikidata_status = 'probable' AND wikidata_id IS "
        "NOT NULL AND entity_id IN (SELECT entity_id FROM page_entities)").fetchone()[0]


def entities_for_entities_section_2(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities, page_entities. Hívja: entities/report.py: entities_section."""
    return con.execute(
        "SELECT source, count(DISTINCT entity_id) FROM entities WHERE entity_id IN "
        "(SELECT entity_id FROM page_entities) GROUP BY source ORDER BY source").fetchall()


def entities_for_entities_section_3(con: duckdb.DuckDBPyConnection) -> tuple | None:
    """Lekérdezés: entities, page_entities. Hívja: entities/report.py: entities_section."""
    return con.execute(
        "SELECT count(*) FROM entities WHERE knowledge_checked_at IS NULL AND "
        "entity_id IN (SELECT entity_id FROM page_entities) ORDER BY ALL").fetchone()


def entities_for_site_section(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: entities/report.py: site_section."""
    return con.execute(
        "SELECT e.type, coalesce(e.subtype, ''), count(*) FROM entities e WHERE "
        "e.anchor_page_id IS NOT NULL GROUP BY ALL ORDER BY ALL").fetchall()


def soft_checks_for_soft_section(con: duckdb.DuckDBPyConnection) -> tuple | None:
    """Lekérdezés: soft_checks. Hívja: entities/report.py: soft_section."""
    return con.execute(
        "SELECT count(*), count(*) FILTER (WHERE kept), count(*) FILTER (WHERE "
        "structure IS NULL OR structure = 'anchor'), count(*) FILTER (WHERE sol = "
        "false), count(*) FILTER (WHERE sol IS NULL AND kept), count(DISTINCT "
        "lower(canonical)) FILTER (WHERE kept) FROM soft_checks WHERE type = 'service' "
        "ORDER BY ALL").fetchone()


def soft_checks_for_soft_section_2(con: duckdb.DuckDBPyConnection) -> tuple | None:
    """Lekérdezés: soft_checks. Hívja: entities/report.py: soft_section."""
    return con.execute(
        "SELECT count(*), count(DISTINCT entity_id), count(*) FILTER (WHERE "
        "prominent), count(*) FILTER (WHERE structure IS NOT NULL), count(*) FILTER "
        "(WHERE blocks >= 2), count(*) FILTER (WHERE knowledge IS NOT NULL), "
        "count(DISTINCT page_id), count(*) FILTER (WHERE type_changed_from = "
        "'service') FROM soft_checks WHERE type = 'concept' ORDER BY ALL").fetchone()


def soft_checks_for_entity_table(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: soft_checks. Hívja: entities/report.py: entity_table."""
    return con.execute(
        "SELECT entity_id, count(DISTINCT page_id) FROM soft_checks WHERE type = "
        "'concept' AND type_changed_from = 'service' AND entity_id IS NOT NULL GROUP "
        "BY 1 ORDER BY ALL").fetchall()


def entities_for_site_section_2(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: entities/report.py: site_section."""
    return con.execute(
        "SELECT tier, count(*) FROM entities WHERE tier IS NOT NULL GROUP BY tier "
        "ORDER BY ALL").fetchall()


def entities_for_site_section_3(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: entities/report.py: site_section."""
    return con.execute(
        "SELECT flag, count(*) FROM (SELECT unnest(flags) AS flag FROM entities) GROUP "
        "BY flag ORDER BY ALL").fetchall()


def delete_mention_sources_in_run_rules(con: duckdb.DuckDBPyConnection):
    """Törlés: mention_sources. Hívja: entities/rules.py: run_rules."""
    con.execute(
        "DELETE FROM mention_sources WHERE source IN ('schema', 'rule')")


def delete_page_entities_in_run_rules(con: duckdb.DuckDBPyConnection):
    """Törlés: mention_sources, page_entities. Hívja: entities/rules.py: run_rules."""
    con.execute(
        "DELETE FROM page_entities WHERE mention_id NOT IN (SELECT mention_id FROM "
        "mention_sources)")


def delete_entities_in_run_rules(con: duckdb.DuckDBPyConnection):
    """Törlés: entities, page_entities. Hívja: entities/rules.py: run_rules."""
    con.execute(
        "DELETE FROM entities WHERE source IN ('schema', 'rule') AND entity_id NOT IN "
        "(SELECT entity_id FROM page_entities)")


def update_entity_runs_in_run_rules(con: duckdb.DuckDBPyConnection, value, value_2, value_3, value_4, rows, value_5, value_6, run_id):
    """Módosítás: entity_runs. Hívja: entities/rules.py: run_rules."""
    con.execute(
        "UPDATE entity_runs SET finished_at = ?, pages = ?, pages_with_entities = ?, "
        "entities = ?, row_count = ?, by_position = ?, skipped = ? WHERE run_id = ?", [value, value_2, value_3, value_4, rows, value_5, value_6, run_id])


def insert_page_entities_in_store_mention_2(con: duckdb.DuckDBPyConnection, page_id, entity_id, block_id, start, end, surface, position, context) -> tuple | None:
    """Beszúrás: page_entities. Hívja: entities/rules.py: store_mention."""
    return con.execute(
        "INSERT INTO page_entities (page_id, entity_id, block_id, char_start, "
        "char_end, surface_form, position, context) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "RETURNING mention_id", [page_id, entity_id, block_id, start, end, surface, position, context]).fetchone()


def entities_for_existing_entities(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: entities/rules.py: existing_entities."""
    return con.execute(
        "SELECT entity_id, name, type, aliases, type_changed_from FROM entities ORDER "
        "BY entity_id").fetchall()


def insert_entity_runs_in_run_rules(con: duckdb.DuckDBPyConnection, started) -> tuple | None:
    """Beszúrás: entity_runs. Hívja: entities/rules.py: run_rules."""
    return con.execute(
        "INSERT INTO entity_runs (started_at, method, llm_calls) VALUES (?, 'rules', "
        "0) RETURNING run_id", [started]).fetchone()


def update_entities_in_run_rules(con: duckdb.DuckDBPyConnection, aliases, lang, value, role, entity_id):
    """Módosítás: entities. Hívja: entities/rules.py: run_rules."""
    con.execute(
        "UPDATE entities SET aliases = "
        "list_sort(list_distinct(list_concat(coalesce(aliases, []), ?))), lang = "
        "coalesce(lang, ?), source = ?, role = coalesce(?, role) WHERE entity_id = ?", [aliases, lang, value, role, entity_id])


def insert_mention_sources_in_run_rules(con: duckdb.DuckDBPyConnection, mention_id, source, run_id, count):
    """Beszúrás: mention_sources. Hívja: entities/rules.py: run_rules."""
    con.execute(
        "INSERT INTO mention_sources (mention_id, source, run_id, count) VALUES (?, ?, "
        "?, ?)", [mention_id, source, run_id, count])


def page_entities_for_store_mention_2(con: duckdb.DuckDBPyConnection, page_id, entity_id) -> tuple | None:
    """Lekérdezés: page_entities. Hívja: entities/rules.py: store_mention."""
    return con.execute(
        "SELECT mention_id FROM page_entities WHERE page_id = ? AND entity_id = ? AND "
        "position = 'schema' ORDER BY ALL", [page_id, entity_id]).fetchone()


def insert_entities_in_run_rules(con: duckdb.DuckDBPyConnection, name, lang, type, aliases, source, role, started) -> tuple | None:
    """Beszúrás: entities. Hívja: entities/rules.py: run_rules."""
    return con.execute(
        "INSERT INTO entities (name, lang, type, aliases, source, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING entity_id", [name, lang, type, aliases, source, role, started]).fetchone()


def entities_for_run_rules(con: duckdb.DuckDBPyConnection, entity_id) -> tuple | None:
    """Lekérdezés: entities. Hívja: entities/rules.py: run_rules."""
    return con.execute(
        "SELECT source FROM entities WHERE entity_id = ? ORDER BY ALL", [entity_id]).fetchone()


def insert_soft_checks_in_store_soft_checks(con: duckdb.DuckDBPyConnection, rows):
    """Beszúrás: soft_checks. Hívja: entities/v3.py: store_soft_checks."""
    con.executemany(
        "INSERT INTO soft_checks (run_id, page_id, entity_id, canonical, type, "
        "structure, blocks, mentions, prominent, rank, knowledge, sol, kept, "
        "type_changed_from, type_change_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?)", rows)


def entities_for_site___init__(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: functions/findings.py: site___init__."""
    return con.execute(
        "SELECT entity_id, name, type, subtype, aliases, anchor_page_id, role, source "
        "FROM entities ORDER BY ALL").fetchall()


def entities_for_graph___init__(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: functions/graph.py: graph___init__."""
    return con.execute(
        "SELECT entity_id, name, type, subtype, aliases, flags, role, anchor_page_id, "
        "wikidata_id, wikidata_status FROM entities ORDER BY ALL").fetchall()


def page_entities_for_graph___init__(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: blocks, page_entities. Hívja: functions/graph.py: graph___init__."""
    return con.execute(
        "SELECT pe.page_id, pe.entity_id, pe.position, pe.flags, b.region, b.kind, "
        "b.cells, pe.char_end FROM page_entities pe LEFT JOIN blocks b USING "
        "(block_id) ORDER BY ALL").fetchall()


def entity_runs_for_primary_empty_pages(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entity_run_pages, entity_runs. Hívja: entities/report.py: primary_empty_pages."""
    return con.execute(
        "SELECT p.page_id, json_extract(coalesce(p.refined, p.extraction), "
        "'$.primary_entities') FROM entity_run_pages p JOIN entity_runs r USING "
        "(run_id) WHERE r.method = 'llm' AND p.status = 'done' ORDER BY p.run_id DESC, "
        "p.finished_at DESC, p.page_id").fetchall()


def entity_runs_for_graph__primary(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entity_run_pages, entity_runs. Hívja: functions/graph.py: graph__primary."""
    return con.execute(
        "SELECT p.page_id, json_extract(coalesce(p.refined, p.extraction), "
        "'$.primary_entities') FROM entity_run_pages p JOIN entity_runs r USING "
        "(run_id) WHERE r.method = 'llm' AND p.status = 'done' ORDER BY p.run_id DESC, "
        "p.finished_at DESC").fetchall()


def entities_for_export_csv(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: functions/graph.py: export_csv."""
    return con.execute(
        "SELECT entity_id, name FROM entities ORDER BY ALL").fetchall()


def entities_for_export_csv_2(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: functions/graph.py: export_csv."""
    return con.execute(
        "SELECT entity_id, type, subtype FROM entities ORDER BY ALL").fetchall()


def entities_for_export_rejected(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: functions/graph.py: export_rejected."""
    return con.execute(
        "SELECT entity_id, name, type, wikidata_id FROM entities ORDER BY ALL").fetchall()


def update_page_entities_in_template(con: duckdb.DuckDBPyConnection):
    """Módosítás: page_entities. Hívja: resolver/flags.py: template."""
    con.execute(
        "UPDATE page_entities SET flags = list_filter(flags, x -> x <> 'template') "
        "WHERE list_contains(flags, 'template')")


def update_entities_in_set_flag(con: duckdb.DuckDBPyConnection, flag, flag_2):
    """Módosítás: entities. Hívja: resolver/flags.py: set_flag."""
    con.execute(
        "UPDATE entities SET flags = list_filter(flags, x -> x <> ?) WHERE "
        "list_contains(flags, ?)", [flag, flag_2])


def update_entities_in_set_flag_2(con: duckdb.DuckDBPyConnection):
    """Módosítás: entities. Hívja: resolver/flags.py: set_flag."""
    con.execute(
        "UPDATE entities SET flags = NULL WHERE len(flags) = 0")


def update_page_entities_in_template_2(con: duckdb.DuckDBPyConnection, flagged):
    """Módosítás: page_entities. Hívja: resolver/flags.py: template."""
    con.execute(
        "UPDATE page_entities SET flags = "
        "list_sort(list_distinct(list_append(coalesce(flags, []), 'template'))) WHERE "
        "list_contains(?, mention_id)", [flagged])


def update_entities_in_set_flag_3(con: duckdb.DuckDBPyConnection, flag, entity_ids):
    """Módosítás: entities. Hívja: resolver/flags.py: set_flag."""
    con.execute(
        "UPDATE entities SET flags = "
        "list_sort(list_distinct(list_append(coalesce(flags, []), ?))) WHERE "
        "list_contains(?, entity_id)", [flag, entity_ids])


def page_entities_for_demo(con: duckdb.DuckDBPyConnection, value) -> list[tuple]:
    """Lekérdezés: blocks, entities, page_entities. Hívja: resolver/flags.py: demo."""
    return con.execute(
        "SELECT pe.entity_id, pe.page_id, b.kind, b.text FROM page_entities pe JOIN "
        "entities e USING (entity_id) LEFT JOIN blocks b USING (block_id) WHERE "
        "list_contains(?, e.type) ORDER BY ALL", [value]).fetchall()


def page_entities_for_template(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: blocks, page_entities. Hívja: resolver/flags.py: template."""
    return con.execute(
        "SELECT pe.mention_id, pe.entity_id, b.kind, b.text, pe.char_start FROM "
        "page_entities pe JOIN blocks b USING (block_id) WHERE b.region = 'content' "
        "AND b.kind <> 'title' ORDER BY ALL").fetchall()


def entities_for_demo(con: duckdb.DuckDBPyConnection, flagged) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/flags.py: demo."""
    return con.execute(
        "SELECT name FROM entities WHERE list_contains(?, entity_id) ORDER BY name", [flagged]).fetchall()


def update_entities_in_store(con: duckdb.DuckDBPyConnection, qid, wiki, status, value, entity_id):
    """Módosítás: entities. Hívja: resolver/knowledge.py: store."""
    con.execute(
        "UPDATE entities SET wikidata_id = ?, wikipedia = ?, wikidata_status = ?, "
        "knowledge_checked_at = ? WHERE entity_id = ?", [qid, wiki, status, value, entity_id])


def update_entities_in_clear_person_links(con: duckdb.DuckDBPyConnection, value) -> list[tuple]:
    """Módosítás: entities. Hívja: resolver/knowledge.py: clear_person_links."""
    return con.execute(
        "UPDATE entities SET wikidata_id = NULL, wikipedia = NULL, wikidata_status = "
        "'none', knowledge_checked_at = coalesce(knowledge_checked_at, ?) WHERE type = "
        "'person' AND (wikidata_id IS NOT NULL OR wikipedia IS NOT NULL OR "
        "wikidata_status IS NULL) RETURNING entity_id", [value]).fetchall()


def entities_for_link_entities(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities, page_entities. Hívja: resolver/knowledge.py: link_entities."""
    return con.execute(
        "SELECT entity_id, name, type, subtype, lang, flags FROM entities WHERE "
        "wikidata_status IS NULL AND entity_id IN (SELECT entity_id FROM "
        "page_entities) ORDER BY entity_id").fetchall()


def heading_mentions(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: blocks, page_entities. Hívja: functions/headings.py: page_headings. A
    heading-blokkokban álló említések: (oldal, blokk, entitás), az oldal, a blokk és az említés
    helye szerint."""
    return con.execute(
        "SELECT pe.page_id, pe.block_id, pe.entity_id FROM page_entities pe JOIN blocks b "
        "USING (block_id) WHERE b.kind = 'heading' ORDER BY pe.page_id, pe.block_id, "
        "pe.char_start, pe.entity_id").fetchall()


def entity_alias_lists(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/knowledge.py: _LongForms. (entitás, az `aliases`
    oszlop listája) a nem üres listákra."""
    return con.execute(
        "SELECT entity_id, aliases FROM entities WHERE aliases IS NOT NULL AND len(aliases) > 0 "
        "ORDER BY entity_id").fetchall()


def entities_for_merge_confident(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/knowledge.py: merge_confident."""
    return con.execute(
        "SELECT entity_id, type, wikidata_id FROM entities WHERE wikidata_status = "
        "'confident' AND wikidata_id IS NOT NULL ORDER BY entity_id").fetchall()


def update_entities_in_retype_tech_classes(con: duckdb.DuckDBPyConnection, value) -> list[tuple]:
    """Módosítás: entities. Hívja: resolver/knowledge.py: retype_tech_classes."""
    return con.execute(
        "UPDATE entities SET type = 'tech', type_changed_from = type WHERE type = "
        "'concept' AND wikidata_status = 'confident' AND list_contains(?, wikidata_id) "
        "RETURNING entity_id", [value]).fetchall()


def entities_for_corroborated(con: duckdb.DuckDBPyConnection, entity_id) -> tuple | None:
    """Lekérdezés: entities. Hívja: resolver/knowledge.py: corroborated."""
    return con.execute(
        "SELECT type FROM entities WHERE entity_id = ? ORDER BY ALL", [entity_id]).fetchone()


def entity_runs_for_merge_confident(con: duckdb.DuckDBPyConnection) -> tuple | None:
    """Lekérdezés: entity_runs. Hívja: resolver/knowledge.py: merge_confident."""
    return con.execute(
        "SELECT max(run_id) FROM entity_runs WHERE method = 'site' ORDER BY ALL").fetchone()


def entities_for_corroborated_2(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/knowledge.py: corroborated."""
    return con.execute(
        "SELECT entity_id, type, wikidata_id FROM entities WHERE wikidata_status = "
        "'confident' ORDER BY entity_id").fetchall()


def update_page_entities_in_merger_merge(con: duckdb.DuckDBPyConnection, keep, remove):
    """Módosítás: page_entities. Hívja: resolver/merge.py: merger_merge."""
    con.execute(
        "UPDATE page_entities SET entity_id = ? WHERE entity_id = ?", [keep, remove])


def update_soft_checks_in_merger_merge(con: duckdb.DuckDBPyConnection, keep, remove):
    """Módosítás: soft_checks. Hívja: resolver/merge.py: merger_merge."""
    con.execute(
        "UPDATE soft_checks SET entity_id = ? WHERE entity_id = ?", [keep, remove])


def update_entities_in_merger_merge(con: duckdb.DuckDBPyConnection, forms, value, keep):
    """Módosítás: entities. Hívja: resolver/merge.py: merger_merge."""
    con.execute(
        "UPDATE entities SET aliases = "
        "list_sort(list_distinct(list_filter(list_concat(coalesce(aliases, []), ?), x "
        "-> x <> name))), source = ? WHERE entity_id = ?", [forms, value, keep])


def delete_entities_in_merger_merge(con: duckdb.DuckDBPyConnection, remove):
    """Törlés: entities. Hívja: resolver/merge.py: merger_merge."""
    con.execute(
        "DELETE FROM entities WHERE entity_id = ?", [remove])


def insert_mention_sources_in_merger_merge(con: duckdb.DuckDBPyConnection, new, old):
    """Beszúrás: mention_sources. Hívja: resolver/merge.py: merger_merge; resolver/offers.py: move_mention."""
    con.execute(
        "INSERT INTO mention_sources (mention_id, source, run_id, llm_call_id, count) "
        "SELECT ?, source, run_id, llm_call_id, count FROM mention_sources WHERE "
        "mention_id = ? ON CONFLICT DO NOTHING", [new, old])


def delete_mention_sources_in_merger_merge(con: duckdb.DuckDBPyConnection, old):
    """Törlés: mention_sources. Hívja: resolver/merge.py: merger_merge; resolver/offers.py: move_mention."""
    con.execute(
        "DELETE FROM mention_sources WHERE mention_id = ?", [old])


def delete_page_entities_in_merger_merge(con: duckdb.DuckDBPyConnection, old):
    """Törlés: page_entities. Hívja: resolver/merge.py: merger_merge; resolver/offers.py: move_mention."""
    con.execute(
        "DELETE FROM page_entities WHERE mention_id = ?", [old])


def entities_for_normalized_merges(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities, page_entities. Hívja: resolver/merge.py: normalized_merges; resolver/merge.py: abbreviation_merges."""
    return con.execute(
        "SELECT entity_id, name, type, aliases FROM entities WHERE type <> 'person' "
        "AND (anchor_page_id IS NOT NULL OR entity_id IN (SELECT entity_id FROM "
        "page_entities)) ORDER BY entity_id").fetchall()


def page_entities_for_only_headings(con: duckdb.DuckDBPyConnection, entity_id) -> tuple | None:
    """Lekérdezés: blocks, page_entities. Hívja: resolver/merge.py: only_headings."""
    return con.execute(
        "SELECT count(*) FROM page_entities pe JOIN blocks b USING (block_id) WHERE "
        "pe.entity_id = ? AND b.kind <> 'heading' ORDER BY ALL", [entity_id]).fetchone()


def entities_for_merger_merge(con: duckdb.DuckDBPyConnection, keep) -> tuple | None:
    """Lekérdezés: entities. Hívja: resolver/merge.py: merger_merge."""
    return con.execute(
        "SELECT name, aliases, source FROM entities WHERE entity_id = ? ORDER BY ALL", [keep]).fetchone()


def page_entities_for_merger_merge(con: duckdb.DuckDBPyConnection, remove, keep) -> list[tuple]:
    """Lekérdezés: page_entities. Hívja: resolver/merge.py: merger_merge."""
    return con.execute(
        "SELECT r.mention_id, k.mention_id FROM page_entities r JOIN page_entities k "
        "ON k.page_id = r.page_id AND k.block_id IS NOT DISTINCT FROM r.block_id AND "
        "k.char_start IS NOT DISTINCT FROM r.char_start AND k.char_end IS NOT DISTINCT "
        "FROM r.char_end AND (k.position = r.position OR r.block_id IS NOT NULL) WHERE "
        "r.entity_id = ? AND k.entity_id = ? ORDER BY ALL", [remove, keep]).fetchall()


def entities_for_place_pair(con: duckdb.DuckDBPyConnection, value) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/merge.py: place_pair."""
    return con.execute(
        "SELECT entity_id, type FROM entities WHERE list_contains(?, entity_id) ORDER "
        "BY ALL", [value]).fetchall()


def page_entities_for_entity_rows(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities, page_entities. Hívja: resolver/merge.py: entity_rows."""
    return con.execute(
        "SELECT e.entity_id, e.anchor_page_id, e.tier, e.source, (SELECT count(*) FROM "
        "page_entities pe WHERE pe.entity_id = e.entity_id), e.type, e.subtype FROM "
        "entities e ORDER BY ALL").fetchall()


def page_entities_for_heading_entity(con: duckdb.DuckDBPyConnection, block_id) -> list[tuple]:
    """Lekérdezés: page_entities. Hívja: resolver/merge.py: heading_entity."""
    return con.execute(
        "SELECT entity_id, surface_form FROM page_entities WHERE block_id = ? ORDER BY "
        "ALL", [block_id]).fetchall()


def entities_for_exists(con: duckdb.DuckDBPyConnection, entity_id) -> tuple | None:
    """Lekérdezés: entities. Hívja: resolver/names.py: exists."""
    return con.execute(
        "SELECT count(*) FROM entities WHERE entity_id = ? ORDER BY ALL", [entity_id]).fetchone()


def insert_mention_sources_in_add_mention(con: duckdb.DuckDBPyConnection, mention_id, run_id) -> list[tuple]:
    """Beszúrás: mention_sources. Hívja: resolver/navigation.py: add_mention."""
    return con.execute(
        "INSERT INTO mention_sources (mention_id, source, run_id) VALUES (?, 'rule', "
        "?) ON CONFLICT DO NOTHING RETURNING mention_id", [mention_id, run_id]).fetchall()


def insert_page_entities_in_add_mention(con: duckdb.DuckDBPyConnection, page_id, entity_id, value, start, end, value_2, position) -> tuple | None:
    """Beszúrás: page_entities. Hívja: resolver/navigation.py: add_mention."""
    return con.execute(
        "INSERT INTO page_entities (page_id, entity_id, block_id, char_start, "
        "char_end, surface_form, position) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING "
        "mention_id", [page_id, entity_id, value, start, end, value_2, position]).fetchone()


def update_entities_in_package_entity(con: duckdb.DuckDBPyConnection, primary, name, primary_2, name_2, entity_id):
    """Módosítás: entities. Hívja: resolver/offers.py: package_entity."""
    con.execute(
        "UPDATE entities SET tier = 'package', name = CASE WHEN ? THEN ? ELSE name "
        "END, aliases = "
        "list_sort(list_distinct(list_filter(list_append(coalesce(aliases, []), name), "
        "x -> x <> CASE WHEN ? THEN ? ELSE name END))) WHERE entity_id = ?", [primary, name, primary_2, name_2, entity_id])


def update_entities_in_page_entities(con: duckdb.DuckDBPyConnection, value):
    """Módosítás: entities. Hívja: resolver/offers.py: page_entities."""
    con.execute(
        "UPDATE entities SET anchor_page_id = NULL, tier = NULL WHERE list_contains(?, "
        "anchor_page_id)", [value])


def update_entities_in_page_entities_2(con: duckdb.DuckDBPyConnection, canonical, kind, subtype, value, page_id, value_2, forms, canonical_2, entity_id):
    """Módosítás: entities. Hívja: resolver/offers.py: page_entities."""
    con.execute(
        "UPDATE entities SET name = ?, type = ?, subtype = coalesce(?, subtype), tier "
        "= ?, anchor_page_id = ?, lang = coalesce(lang, ?), aliases = "
        "list_sort(list_distinct(list_filter(list_concat(coalesce(aliases, []), ?), x "
        "-> x <> ?))) WHERE entity_id = ?", [canonical, kind, subtype, value, page_id, value_2, forms, canonical_2, entity_id])


def update_page_entities_in_move_mention(con: duckdb.DuckDBPyConnection, entity_id, mention_id):
    """Módosítás: page_entities. Hívja: resolver/offers.py: move_mention."""
    con.execute(
        "UPDATE page_entities SET entity_id = ? WHERE mention_id = ?", [entity_id, mention_id])


def update_entities_in_apply_overrides(con: duckdb.DuckDBPyConnection, tier, keep):
    """Módosítás: entities. Hívja: resolver/offers.py: apply_overrides."""
    con.execute(
        "UPDATE entities SET type = 'service', subtype = CASE WHEN type = 'service' "
        "THEN subtype END, tier = ?, type_changed_from = CASE WHEN type <> 'service' "
        "THEN type ELSE type_changed_from END WHERE entity_id = ?", [tier, keep])


def page_entities_for_find_page_entity(con: duckdb.DuckDBPyConnection, value) -> list[tuple]:
    """Lekérdezés: entities, page_entities. Hívja: resolver/offers.py: find_page_entity."""
    return con.execute(
        "SELECT e.entity_id, e.name, e.type, e.aliases, e.source, e.anchor_page_id, "
        "(SELECT count(*) FROM page_entities pe WHERE pe.entity_id = e.entity_id) FROM "
        "entities e WHERE list_contains(?, e.type) ORDER BY e.entity_id", [value]).fetchall()


def entities_for_position_identity(con: duckdb.DuckDBPyConnection, entity_id, value) -> list[tuple]:
    """Lekérdezés: entities, page_entities. Hívja: resolver/offers.py: position_identity."""
    return con.execute(
        "SELECT e.entity_id, e.name, e.aliases, list_sort(list(DISTINCT pe.block_id)) "
        "FROM entities e JOIN page_entities pe USING (entity_id) WHERE e.entity_id <> "
        "? AND pe.block_id IS NOT NULL AND NOT list_contains(?, e.type) GROUP BY "
        "e.entity_id, e.name, e.aliases ORDER BY ALL", [entity_id, value]).fetchall()


def entities_for_packages(con: duckdb.DuckDBPyConnection) -> tuple | None:
    """Lekérdezés: entities. Hívja: resolver/offers.py: packages."""
    return con.execute(
        "SELECT count(*) FROM entities WHERE tier = 'package' ORDER BY ALL").fetchone()


def entities_for_package_entity(con: duckdb.DuckDBPyConnection, core) -> tuple | None:
    """Lekérdezés: entities. Hívja: resolver/offers.py: package_entity."""
    return con.execute(
        "SELECT name, aliases FROM entities WHERE entity_id = ? ORDER BY ALL", [core]).fetchone()


def update_entities_in_steps(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Módosítás: entities. Hívja: resolver/offers.py: steps."""
    return con.execute(
        "UPDATE entities SET type = 'concept', subtype = 'method', tier = 'step', "
        "type_changed_from = 'service' WHERE type = 'service' AND source = 'llm' AND "
        "tier IS NULL RETURNING entity_id").fetchall()


def entity_runs_for_llm_types(con: duckdb.DuckDBPyConnection) -> tuple | None:
    """Lekérdezés: entity_runs. Hívja: resolver/offers.py: llm_types."""
    return con.execute(
        "SELECT max(run_id) FROM entity_runs WHERE method = 'llm' ORDER BY ALL").fetchone()


def page_entities_for_type_split(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: blocks, entities, mention_sources, page_entities. Hívja: resolver/offers.py: type_split."""
    return con.execute(
        "SELECT pe.mention_id, pe.entity_id, pe.page_id, pe.block_id, pe.char_start, "
        "pe.char_end, b.kind, b.text FROM page_entities pe JOIN entities e USING "
        "(entity_id) JOIN blocks b USING (block_id) WHERE e.type = 'service' AND "
        "pe.mention_id IN (SELECT mention_id FROM mention_sources WHERE source = "
        "'llm') ORDER BY pe.mention_id").fetchall()


def entities_for_service_names(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/offers.py: service_names."""
    return con.execute(
        "SELECT entity_id, name, aliases FROM entities WHERE type = 'service' ORDER BY "
        "ALL").fetchall()


def entities_for_concept_index(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/offers.py: concept_index."""
    return con.execute(
        "SELECT entity_id, name, aliases FROM entities WHERE type = 'concept' ORDER BY "
        "entity_id").fetchall()


def page_entities_for_move_mention(con: duckdb.DuckDBPyConnection, mention_id) -> tuple | None:
    """Lekérdezés: page_entities. Hívja: resolver/offers.py: move_mention."""
    return con.execute(
        "SELECT page_id, block_id, char_start, char_end FROM page_entities WHERE "
        "mention_id = ? ORDER BY ALL", [mention_id]).fetchone()


def page_entities_for_move_mention_2(con: duckdb.DuckDBPyConnection, place, entity_id) -> tuple | None:
    """Lekérdezés: page_entities; `place`: (oldal, blokk, kezdet, vég). Hívja: resolver/offers.py:
    move_mention."""
    return con.execute(
        "SELECT mention_id FROM page_entities WHERE page_id = ? AND block_id IS NOT "
        "DISTINCT FROM ? AND char_start IS NOT DISTINCT FROM ? AND char_end IS NOT "
        "DISTINCT FROM ? AND entity_id = ? ORDER BY ALL", [*place, entity_id]).fetchone()


def entities_for_offers(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/offers.py: offers."""
    return con.execute(
        "SELECT entity_id, name, aliases FROM entities WHERE type = 'service' AND tier "
        "IN ('core', 'package', 'work_mode') ORDER BY ALL").fetchall()


def update_entities_in_page_entities_3(con: duckdb.DuckDBPyConnection, loose, kind, subtype, subtype_2) -> list[tuple]:
    """Módosítás: entities. Hívja: resolver/offers.py: page_entities."""
    return con.execute(
        "UPDATE entities SET anchor_page_id = NULL, tier = NULL WHERE list_contains(?, "
        "anchor_page_id) AND type = ? AND (? IS NULL OR subtype = ?) RETURNING "
        "entity_id, name", [loose, kind, subtype, subtype_2]).fetchall()


def update_entities_in_page_entities_4(con: duckdb.DuckDBPyConnection, value, name, value_2, entity_id):
    """Módosítás: entities. Hívja: resolver/offers.py: page_entities."""
    con.execute(
        "UPDATE entities SET name = ?, aliases = "
        "list_sort(list_distinct(list_filter(list_concat(coalesce(aliases, []), [?]), "
        "x -> x <> ?))) WHERE entity_id = ?", [value, name, value_2, entity_id])


def update_entities_in_page_entities_5(con: duckdb.DuckDBPyConnection, entity_id, value, stale_kind, stale_subtype, stale_subtype_2):
    """Módosítás: entities. Hívja: resolver/offers.py: page_entities."""
    con.execute(
        "UPDATE entities SET anchor_page_id = NULL, tier = NULL WHERE entity_id <> ? "
        "AND list_contains(?, anchor_page_id) AND type = ? AND (? IS NULL OR subtype = "
        "?)", [entity_id, value, stale_kind, stale_subtype, stale_subtype_2])


def insert_entities_in_package_entity(con: duckdb.DuckDBPyConnection, name, value, value_2) -> tuple | None:
    """Beszúrás: entities. Hívja: resolver/offers.py: package_entity."""
    return con.execute(
        "INSERT INTO entities (name, lang, type, aliases, source, created_at) VALUES "
        "(?, ?, 'service', [], 'rule', ?) RETURNING entity_id", [name, value, value_2]).fetchone()


def entities_for_type_split(con: duckdb.DuckDBPyConnection, entity_id) -> tuple | None:
    """Lekérdezés: entities. Hívja: resolver/offers.py: type_split; resolver/shop.py: orphan_brands."""
    return con.execute(
        "SELECT name FROM entities WHERE entity_id = ? ORDER BY ALL", [entity_id]).fetchone()


def entities_for_apply_overrides(con: duckdb.DuckDBPyConnection, keep) -> tuple | None:
    """Lekérdezés: entities. Hívja: resolver/offers.py: apply_overrides."""
    return con.execute(
        "SELECT name, type, tier FROM entities WHERE entity_id = ? ORDER BY ALL", [keep]).fetchone()


def entities_for_override_entities(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities, page_entities. Hívja: resolver/offers.py: override_entities."""
    return con.execute(
        "SELECT entity_id, name, aliases FROM entities WHERE type <> 'person' AND "
        "(anchor_page_id IS NOT NULL OR entity_id IN (SELECT entity_id FROM "
        "page_entities)) ORDER BY ALL").fetchall()


def insert_entities_in_page_entities(con: duckdb.DuckDBPyConnection, canonical, value, kind, subtype, value_2) -> tuple | None:
    """Beszúrás: entities. Hívja: resolver/offers.py: page_entities; resolver/shop.py: anchored_entity."""
    return con.execute(
        "INSERT INTO entities (name, lang, type, subtype, aliases, source, created_at) "
        "VALUES (?, ?, ?, ?, [], 'rule', ?) RETURNING entity_id", [canonical, value, kind, subtype, value_2]).fetchone()


def page_entities_for_position_identity(con: duckdb.DuckDBPyConnection, entity_id) -> list[tuple]:
    """Lekérdezés: page_entities. Hívja: resolver/offers.py: position_identity."""
    return con.execute(
        "SELECT DISTINCT block_id FROM page_entities WHERE entity_id = ? AND block_id "
        "IS NOT NULL ORDER BY ALL", [entity_id]).fetchall()


def insert_entities_in_type_split(con: duckdb.DuckDBPyConnection, value, value_2) -> tuple | None:
    """Beszúrás: entities. Hívja: resolver/offers.py: type_split."""
    return con.execute(
        "INSERT INTO entities (name, type, aliases, source, created_at) VALUES (?, "
        "'concept', [], 'llm', ?) RETURNING entity_id", [value, value_2]).fetchone()


def entities_for_package_entity_2(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/offers.py: package_entity."""
    return con.execute(
        "SELECT entity_id, name, aliases FROM entities WHERE type = 'service' AND "
        "coalesce(tier, '') <> 'core' ORDER BY entity_id").fetchall()


def entities_for_override_entities_2(con: duckdb.DuckDBPyConnection, members) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/offers.py: override_entities."""
    return con.execute(
        "SELECT entity_id FROM entities WHERE list_contains(?, anchor_page_id) ORDER "
        "BY ALL", [members]).fetchall()


def entity_runs_for_service_pages(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entity_run_pages, entity_runs. Hívja: resolver/pages.py: service_pages."""
    return con.execute(
        "SELECT p.page_id, coalesce(p.refined, p.extraction) FROM entity_run_pages p "
        "JOIN entity_runs r USING (run_id) WHERE r.method = 'llm' AND p.status = "
        "'done' ORDER BY p.run_id DESC, p.finished_at DESC").fetchall()


def update_entities_in_anchored_entity(con: duckdb.DuckDBPyConnection, canonical, kind, subtype, page_id, value, forms, canonical_2, entity_id):
    """Módosítás: entities. Hívja: resolver/shop.py: anchored_entity."""
    con.execute(
        "UPDATE entities SET name = ?, type = ?, subtype = ?, anchor_page_id = ?, lang "
        "= coalesce(lang, ?), aliases = "
        "list_sort(list_distinct(list_filter(list_concat(coalesce(aliases, []), ?), x "
        "-> x <> ?))) WHERE entity_id = ?", [canonical, kind, subtype, page_id, value, forms, canonical_2, entity_id])


def update_entities_in_brand_entity(con: duckdb.DuckDBPyConnection, canonical, aliases, canonical_2, entity_id):
    """Módosítás: entities. Hívja: resolver/shop.py: brand_entity."""
    con.execute(
        "UPDATE entities SET name = ?, type = 'brand', aliases = "
        "list_sort(list_distinct(list_filter(list_concat(coalesce(aliases, []), ?), x "
        "-> x <> ?))) WHERE entity_id = ?", [canonical, aliases, canonical_2, entity_id])


def update_entities_in_family_entity(con: duckdb.DuckDBPyConnection, name, aliases, name_2, found):
    """Módosítás: entities. Hívja: resolver/shop.py: family_entity."""
    con.execute(
        "UPDATE entities SET name = ?, aliases = "
        "list_sort(list_distinct(list_filter(list_concat(coalesce(aliases, []), ?, "
        "[name]), x -> x <> ?))) WHERE entity_id = ?", [name, aliases, name_2, found])


def update_entities_in_run_shop(con: duckdb.DuckDBPyConnection, value, entity_id):
    """Módosítás: entities. Hívja: resolver/shop.py: run_shop."""
    con.execute(
        "UPDATE entities SET subtype = 'variant', attributes = ? WHERE entity_id = ?", [value, entity_id])


def update_entities_in_site_name_cuts(con: duckdb.DuckDBPyConnection, name, site_org):
    """Módosítás: entities. Hívja: resolver/shop.py: site_name_cuts."""
    con.execute(
        "UPDATE entities SET aliases = list_filter(aliases, x -> x <> ?) WHERE "
        "entity_id = ?", [name, site_org])


def entities_for_category_products(con: duckdb.DuckDBPyConnection, category_ids) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/shop.py: category_products; resolver/shop.py: orphan_brands."""
    return con.execute(
        "SELECT entity_id, name, aliases FROM entities WHERE list_contains(?, "
        "entity_id) ORDER BY entity_id", [category_ids]).fetchall()


def entities_for_category_products_2(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/shop.py: category_products."""
    return con.execute(
        "SELECT entity_id, name FROM entities WHERE type = 'product' AND "
        "anchor_page_id IS NULL AND coalesce(subtype, '') NOT IN ('variant', 'line') "
        "ORDER BY entity_id").fetchall()


def entities_for_site_name_cuts(con: duckdb.DuckDBPyConnection, site_org) -> list[tuple]:
    """Lekérdezés: entities, page_entities. Hívja: resolver/shop.py: site_name_cuts."""
    return con.execute(
        "SELECT e.entity_id, e.name FROM entities e WHERE e.type = 'org' AND "
        "e.anchor_page_id IS NULL AND e.entity_id <> ? AND EXISTS (SELECT 1 FROM "
        "page_entities pe WHERE pe.entity_id = e.entity_id) AND NOT EXISTS (SELECT 1 "
        "FROM page_entities pe WHERE pe.entity_id = e.entity_id AND pe.position <> "
        "'title') ORDER BY e.entity_id", [site_org]).fetchall()


def entities_for_anchored_entity(con: duckdb.DuckDBPyConnection, kind) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/shop.py: anchored_entity."""
    return con.execute(
        "SELECT entity_id, name, aliases, anchor_page_id FROM entities WHERE type = ? "
        "ORDER BY entity_id", [kind]).fetchall()


def entities_for_brand_entity(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/shop.py: brand_entity."""
    return con.execute(
        "SELECT entity_id, name, aliases, type FROM entities WHERE type IN ('brand', "
        "'org') AND anchor_page_id IS NULL ORDER BY entity_id").fetchall()


def entities_for_run_shop(con: duckdb.DuckDBPyConnection, group) -> tuple | None:
    """Lekérdezés: entities. Hívja: resolver/shop.py: run_shop."""
    return con.execute(
        "SELECT entity_id, name FROM entities WHERE list_contains(?, anchor_page_id) "
        "AND type = 'product' ORDER BY entity_id", [group]).fetchone()


def entities_for_site_name_cuts_2(con: duckdb.DuckDBPyConnection) -> tuple | None:
    """Lekérdezés: entities. Hívja: resolver/shop.py: site_name_cuts; resolver/shop.py: orphan_brands."""
    return con.execute(
        "SELECT min(entity_id) FROM entities WHERE type = 'org' AND role = 'brand' "
        "ORDER BY ALL").fetchone()


def insert_entities_in_brand_entity(con: duckdb.DuckDBPyConnection, canonical, site_lang, value) -> tuple | None:
    """Beszúrás: entities. Hívja: resolver/shop.py: brand_entity."""
    return con.execute(
        "INSERT INTO entities (name, lang, type, aliases, source, created_at) VALUES "
        "(?, ?, 'brand', [], 'rule', ?) RETURNING entity_id", [canonical, site_lang, value]).fetchone()


def insert_entities_in_family_entity(con: duckdb.DuckDBPyConnection, name, site_lang) -> tuple | None:
    """Beszúrás: entities. Hívja: resolver/shop.py: family_entity."""
    return con.execute(
        "INSERT INTO entities (name, lang, type, subtype, aliases, source, created_at) "
        "VALUES (?, ?, 'product', 'line', [], 'rule', current_timestamp) RETURNING "
        "entity_id", [name, site_lang]).fetchone()


def entities_for_family_entity(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/shop.py: family_entity."""
    return con.execute(
        "SELECT entity_id, name, aliases FROM entities WHERE type = 'product' AND "
        "subtype = 'line' AND anchor_page_id IS NULL ORDER BY entity_id").fetchall()


def update_entity_runs_in_run_site(con: duckdb.DuckDBPyConnection, value, value_2, value_3, anchor_mentions, value_4, run_id):
    """Módosítás: entity_runs. Hívja: resolver/site.py: run_site."""
    con.execute(
        "UPDATE entity_runs SET finished_at = ?, pages = ?, entities = ?, row_count = "
        "?, skipped = ? WHERE run_id = ?", [value, value_2, value_3, anchor_mentions, value_4, run_id])


def insert_entity_runs_in_run_site(con: duckdb.DuckDBPyConnection, started) -> tuple | None:
    """Beszúrás: entity_runs. Hívja: resolver/site.py: run_site."""
    return con.execute(
        "INSERT INTO entity_runs (started_at, method, llm_calls) VALUES (?, 'site', 0) "
        "RETURNING run_id", [started]).fetchone()


def update_entities_in_validate_entities(con: duckdb.DuckDBPyConnection, value, entity_id):
    """Módosítás: entities. Hívja: resolver/validate.py: validate_entities."""
    con.execute(
        "UPDATE entities SET validated_at = ? WHERE entity_id = ?", [value, entity_id])


def update_entities_in_validate_entities_2(con: duckdb.DuckDBPyConnection, value, entity_id):
    """Módosítás: entities. Hívja: resolver/validate.py: validate_entities."""
    con.execute(
        "UPDATE entities SET kg_status = 'stub', kg_reason = 'navigational', kg_id = "
        "NULL, kg_type = NULL, kg_type_mismatch = NULL, wikipedia_url = NULL, "
        "validated_at = ? WHERE entity_id = ?", [value, entity_id])


def update_entities_in_validate_entities_3(con: duckdb.DuckDBPyConnection, status, kg_id, kg_type, mismatch, new_type, changed_from, entity_id):
    """Módosítás: entities. Hívja: resolver/validate.py: validate_entities."""
    con.execute(
        "UPDATE entities SET kg_status = ?, kg_reason = NULL, kg_id = ?, kg_type = ?, "
        "kg_type_mismatch = ?, type = ?, type_changed_from = coalesce(?, "
        "type_changed_from) WHERE entity_id = ?", [status, kg_id, kg_type, mismatch, new_type, changed_from, entity_id])


def update_entities_in_validate_entities_4(con: duckdb.DuckDBPyConnection, url, entity_id):
    """Módosítás: entities. Hívja: resolver/validate.py: validate_entities."""
    con.execute(
        "UPDATE entities SET wikipedia_url = ? WHERE entity_id = ?", [url, entity_id])


def page_entities_for_navigational_concepts(con: duckdb.DuckDBPyConnection, value) -> list[tuple]:
    """Lekérdezés: entities, page_entities. Hívja: resolver/validate.py: navigational_concepts."""
    return con.execute(
        "SELECT pe.entity_id FROM page_entities pe JOIN entities e USING (entity_id) "
        "WHERE e.type = 'concept' GROUP BY pe.entity_id HAVING "
        "bool_and(list_contains(?, pe.position)) ORDER BY ALL", [value]).fetchall()


# --- kézzel írt függvények: ahol a korábbi lekérdezés más gazda tábláját is kapcsolta ----------
# (a másik gazda adatát a hívó kéri le annak lekérdező függvényével, és paraméterként adja át)

ENTITY_RUN_COLUMNS = (
    "run_id, method, model, finished_at, pages, pages_with_entities, entities, row_count, "
    "llm_calls, cost_usd, fabricated, by_position"
)


def latest_runs_by_method(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Módszerenként a legutóbbi futás (`ENTITY_RUN_COLUMNS` oszlopai), `run_id` szerint."""
    return con.execute(
        f"SELECT {ENTITY_RUN_COLUMNS} FROM entity_runs "
        "WHERE run_id IN (SELECT max(run_id) FROM entity_runs GROUP BY method) ORDER BY run_id"
    ).fetchall()


def entity_run(con: duckdb.DuckDBPyConnection, run_id: int) -> tuple | None:
    """Egy futás sora (`ENTITY_RUN_COLUMNS` oszlopai)."""
    return con.execute(
        f"SELECT {ENTITY_RUN_COLUMNS} FROM entity_runs WHERE run_id = ?", [run_id]).fetchone()


def entities_for_validation(con: duckdb.DuckDBPyConnection, limit: int | None) -> list[tuple]:
    """Az entitások a validáláshoz, `entity_id` szerint; `limit`: legfeljebb ennyi."""
    return con.execute(
        "SELECT entity_id, name, type, type_suggested, aliases, lang FROM entities "
        "ORDER BY entity_id" + (" LIMIT ?" if limit else ""), [limit] if limit else [],
    ).fetchall()


def entity_ids(con: duckdb.DuckDBPyConnection) -> list[int]:
    return [entity_id for (entity_id,) in con.execute(
        "SELECT entity_id FROM entities ORDER BY entity_id").fetchall()]


def entity_types(con: duckdb.DuckDBPyConnection) -> dict[int, str]:
    return dict(con.execute("SELECT entity_id, type FROM entities ORDER BY entity_id").fetchall())


def anchored_entity_ids(con: duckdb.DuckDBPyConnection) -> set[int]:
    """Az oldalhoz kötött entitások."""
    return {entity_id for (entity_id,) in con.execute(
        "SELECT entity_id FROM entities WHERE anchor_page_id IS NOT NULL").fetchall()}


def site_entity_ids(con: duckdb.DuckDBPyConnection, product_brands: list[int]) -> list[tuple]:
    """A site saját entitásai: a brand szerepűek és a szabályból vagy schemából jött brand
    típusúak, a termékmárkák (`product_brands`: `brand_of` kapcsolattal bíró entitások) nélkül."""
    return con.execute(
        "SELECT entity_id FROM entities e WHERE role = 'brand' OR (type = 'brand' AND "
        "source IN ('rule', 'schema') AND NOT list_contains(CAST(? AS INTEGER[]), e.entity_id)) ORDER BY ALL",
        [product_brands]).fetchall()


def site_name_rows(con: duckdb.DuckDBPyConnection, product_brands: list[int]) -> list[tuple]:
    """A site neveit adó entitások neve és aliasai, a termékmárkák nélkül."""
    return con.execute(
        "SELECT name, aliases FROM entities e WHERE (role = 'brand' "
        "OR (type = 'brand' AND source IN ('rule', 'schema'))) AND NOT list_contains(?, "
        "e.entity_id) ORDER BY ALL", [product_brands]).fetchall()


def unanchored_brands(con: duckdb.DuckDBPyConnection, known_brands: list[int],
                      product_brands: list[int]) -> list[tuple]:
    """Az oldalhoz nem kötött brand entitások, amelyek nem ismert márkák és nem termékmárkák:
    (azonosító, név), név szerint."""
    return con.execute(
        "SELECT entity_id, name FROM entities e WHERE type = 'brand' "
        "AND anchor_page_id IS NULL AND NOT list_contains(CAST(? AS INTEGER[]), entity_id) AND NOT "
        "list_contains(CAST(? AS INTEGER[]), e.entity_id) ORDER BY name, entity_id",
        [known_brands, product_brands]).fetchall()


def mention_placement(con: duckdb.DuckDBPyConnection, page_ids: list[int]) -> list[tuple]:
    """Entitásonként a megadott oldalakon: (entitás, említések száma, ebből chrome-régióban
    vagy sablonjelöléssel)."""
    return con.execute(
        "SELECT pe.entity_id, count(*), count(*) FILTER (WHERE b.region = 'chrome' "
        "OR list_contains(coalesce(pe.flags, []), 'template')) FROM page_entities pe "
        "LEFT JOIN blocks b USING (block_id) WHERE list_contains(CAST(? AS INTEGER[]), pe.page_id) "
        "GROUP BY 1 ORDER BY ALL", [page_ids]).fetchall()


def entity_and_mention_counts_by_type(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Típusonként az említéssel bíró entitások és az említések száma."""
    return con.execute(
        "SELECT e.type, count(DISTINCT e.entity_id), count(*) FROM entities e "
        "JOIN page_entities pe USING (entity_id) GROUP BY e.type ORDER BY e.type").fetchall()


def entity_rows(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Az entitások minden oszlopa oszlopnév → érték sorokként, `entity_id` szerint (az API ebből
    építi az `Entity` és a `KbLink` szerződést)."""
    cursor = con.execute("SELECT * FROM entities ORDER BY entity_id")
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def entities_for_display_names(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/display.py: DisplayNames."""
    return con.execute(
        "SELECT entity_id, name, type, lang, anchor_page_id, aliases FROM entities ORDER BY "
        "entity_id"
    ).fetchall()


def entities_for_language_pairs(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entities. Hívja: resolver/language.py: Topics."""
    return con.execute(
        "SELECT entity_id, name, type, subtype, aliases, flags, anchor_page_id FROM entities "
        "ORDER BY entity_id").fetchall()


def page_entities_for_language_pairs(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: page_entities. Hívja: resolver/language.py: Topics."""
    return con.execute(
        "SELECT page_id, entity_id, count(*) FROM page_entities GROUP BY page_id, entity_id "
        "ORDER BY page_id, entity_id").fetchall()


def entity_runs_for_language_pairs(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Lekérdezés: entity_run_pages, entity_runs. Hívja: resolver/language.py: Topics."""
    return con.execute(
        "SELECT p.page_id, json_extract(coalesce(p.refined, p.extraction), "
        "'$.primary_entities') FROM entity_run_pages p JOIN entity_runs r USING "
        "(run_id) WHERE r.method = 'llm' AND p.status = 'done' ORDER BY p.run_id DESC, "
        "p.finished_at DESC, p.page_id").fetchall()
