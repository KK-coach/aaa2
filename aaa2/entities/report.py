"""A pipeline kimenetei egy site-ra: futásjelentés (Markdown) és site-szintű entitástábla (CSV).

- Futásjelentés: a legutóbbi LLM-futás (és szabálykör): oldalak állapot szerint, hívások és
  költség céljuk szerint (kinyerés, elnevezés, ellenőrzés), időtartam, a kimaradás okai és a
  tudásbázis-kérések hibái, entitások és említések típusonként, a saját ajánlatok sorsa, a
  fogalmak bizonyítékai; ha van korábbi állapot (egy másik adatbázis, pl. a rögzített készlet
  a 012-es migráció előtt), a típusonkénti entitás- és említésszám mellette (regresszió).
- Entitástábla: minden említéssel bíró entitás egy sorban: név, típus, altípus, forrás, hány
  oldalon és hány említésben szerepel, és hány oldalon áll szerkezeti helyen (title, heading,
  navigáció, kártya, táblázatsor), a tudásbázis-egyezés (Wikidata QID, Wikipedia-URL).
  Navigáció: az említés a chrome-régióban áll, vagy a név (`gate.occurs`) az említés oldalának
  chrome-régiójában. Sorrend: oldalszám, említésszám, név.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

import duckdb

from aaa2.entities.gate import occurs

PLACES = ("title", "heading", "nav", "card", "table_row")
TABLE_FIELDS = ("entity", "type", "subtype", "source", "pages", "mentions", *PLACES,
                "wikidata_qid", "wikipedia")


def wikipedia_url(value: str | None) -> str:
    """`nyelv:cím` → a szócikk URL-je."""
    if not value:
        return ""
    code, title = value.split(":", 1)
    return f"https://{code}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"


def entity_table(con: duckdb.DuckDBPyConnection) -> list[dict]:
    mentions = con.execute(
        "SELECT pe.entity_id, pe.page_id, b.kind, b.region FROM page_entities pe "
        "LEFT JOIN blocks b USING (block_id)").fetchall()
    chrome: dict[int, list[str]] = defaultdict(list)
    for page_id, text in con.execute(
            "SELECT page_id, text FROM blocks WHERE region = 'chrome' ORDER BY page_id, ordinal"
    ).fetchall():
        chrome[page_id].append(text)
    pages: dict[int, set[int]] = defaultdict(set)
    count: Counter[int] = Counter()
    places: dict[int, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    for entity_id, page_id, kind, region in mentions:
        pages[entity_id].add(page_id)
        count[entity_id] += 1
        place = "nav" if region == "chrome" else kind
        if place in PLACES:
            places[entity_id][place].add(page_id)
    rows = []
    for entity_id, name, kind, subtype, source, qid, wiki in con.execute(
            "SELECT entity_id, name, type, subtype, source, wikidata_id, wikipedia FROM entities"
    ).fetchall():
        if entity_id not in pages:
            continue
        nav = places[entity_id]["nav"]
        nav |= {page_id for page_id in pages[entity_id] - nav
                if any(occurs(name, text) for text in chrome[page_id])}
        rows.append({"entity": name, "type": kind, "subtype": subtype or "", "source": source,
                     "pages": len(pages[entity_id]), "mentions": count[entity_id],
                     **{place: len(places[entity_id][place]) for place in PLACES},
                     "wikidata_qid": qid or "", "wikipedia": wikipedia_url(wiki)})
    return sorted(rows, key=lambda r: (-r["pages"], -r["mentions"], r["entity"].lower()))


def write_entity_table(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    rows = entity_table(con)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, TABLE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


# ---------------------------------------------------------------------------
# futásjelentés
# ---------------------------------------------------------------------------


def _latest(con: duckdb.DuckDBPyConnection, method: str) -> tuple | None:
    return con.execute(
        "SELECT run_id, model, started_at, finished_at, seconds, pages, llm_calls, cost_usd, "
        "skipped FROM entity_runs WHERE method = ? ORDER BY run_id DESC LIMIT 1",
        [method]).fetchone()


def _money(value: float | None) -> str:
    return f"{value or 0:.4f} USD"


def run_report(con: duckdb.DuckDBPyConnection, label: str,
               baseline: duckdb.DuckDBPyConnection | None = None) -> str:
    """A site legutóbbi futásának jelentése (lásd a modul leírását); `baseline`: a korábbi
    állapot adatbázisa (a regressziós összevetéshez)."""
    (domain,) = con.execute("SELECT coalesce(max(domain), '') FROM site").fetchone()
    (eligible,) = con.execute(
        "SELECT count(*) FROM pages WHERE status BETWEEN 200 AND 299 AND error IS NULL "
        "AND rendered_html IS NOT NULL").fetchone()
    (crawled,) = con.execute("SELECT count(*) FROM pages").fetchone()
    lines = [f"# Entitás-pipeline: {label}", "",
             f"- site: {domain or '—'}; oldal a készletben {crawled}, ebből alkalmas {eligible}"]
    rules = _latest(con, "rules")
    if rules:
        lines.append(f"- szabálykör: #{rules[0]}, {rules[3]:%Y-%m-%d %H:%M}" if rules[3]
                     else f"- szabálykör: #{rules[0]}, nincs lezárva")
    llm = _latest(con, "llm")
    if llm is None:
        lines += ["- LLM-futás: nincs", ""]
    else:
        run_id, model, started, finished, seconds, pages, calls, cost, skipped = llm
        statuses = dict(con.execute(
            "SELECT status, count(*) FROM entity_run_pages WHERE run_id = ? GROUP BY status "
            "ORDER BY status", [run_id]).fetchall())
        lines += [
            f"- LLM-futás: #{run_id}, kinyerés {model}; indult {started:%Y-%m-%d %H:%M}, "
            + (f"lezárva {finished:%Y-%m-%d %H:%M}" if finished else "nincs lezárva")
            + f"; időtartam {(seconds or 0) / 60:.1f} perc",
            f"- oldalak: {pages} feldolgozva; állapot szerint: "
            + (", ".join(f"{k} {v}" for k, v in statuses.items()) or "—"),
            f"- hívások: {calls}, költség {_money(cost)}"
            + (f" ({(cost or 0) / pages:.4f} USD / oldal)" if pages else ""), "",
            "## Hívások céljuk szerint", ""]
        by_purpose = con.execute(
            "SELECT purpose, model, count(*), sum(tokens_in), sum(tokens_out), sum(cost_usd), "
            "sum(coalesce(attempts, 1) - 1), count(*) FILTER (WHERE last_error IS NOT NULL) "
            "FROM llm_calls WHERE call_id IN (SELECT DISTINCT unnest(call_ids) "
            "FROM entity_run_pages WHERE run_id = ?) GROUP BY purpose, model "
            "ORDER BY purpose, model", [run_id]).fetchall()
        for purpose, call_model, count, t_in, t_out, usd, retries, errors in by_purpose:
            lines.append(f"- {purpose} ({call_model}): {count} hívás, token be {t_in or 0} / "
                         f"ki {t_out or 0}, {_money(usd)}; újrapróba {retries}, "
                         f"hibás válasz {errors}")
        if not by_purpose:
            lines.append("- nincs hívás")
        reasons = json.loads(skipped or "{}")
        kb_errors = con.execute(
            "SELECT service, count(*) FROM validation_calls WHERE error IS NOT NULL "
            "AND called_at >= ? GROUP BY service ORDER BY service", [started]).fetchall()
        lines += ["", "## Hibák és kimaradások", "",
                  "- oldalszinten: " + (", ".join(f"{k} {v}" for k, v in reasons.items())
                                        or "—"),
                  "- tudásbázis-kérések hibája a futás óta: "
                  + (", ".join(f"{s} {n}" for s, n in kb_errors) or "0")]
        for page_id, url, status, error in con.execute(
                "SELECT rp.page_id, p.url, rp.status, rp.error FROM entity_run_pages rp "
                "JOIN pages p USING (page_id) WHERE rp.run_id = ? AND rp.status IN "
                "('failed', 'verify_error', 'stopped') ORDER BY rp.page_id", [run_id]
        ).fetchall():
            lines.append(f"  - {url}: {status}" + (f" ({error[:160]})" if error else ""))
        lines.append("")
    lines += _entities_section(con)
    lines += _soft_section(con)
    lines += _regression_section(con, baseline)
    return "\n".join(lines) + "\n"


def _entities_section(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        "SELECT e.type, count(DISTINCT e.entity_id), count(*), count(DISTINCT pe.page_id), "
        "count(DISTINCT e.entity_id) FILTER (WHERE e.wikidata_id IS NOT NULL), "
        "count(DISTINCT e.entity_id) FILTER (WHERE e.wikipedia IS NOT NULL) "
        "FROM entities e JOIN page_entities pe USING (entity_id) GROUP BY e.type "
        "ORDER BY count(DISTINCT e.entity_id) DESC, e.type").fetchall()
    lines = ["## Entitások típusonként", "",
             "| típus | entitás | említés | oldal | Wikidata | Wikipedia |", "|---|---|---|---|---|---|"]
    lines += [f"| {kind} | {entities} | {mentions} | {pages} | {qid} | {wiki} |"
              for kind, entities, mentions, pages, qid, wiki in rows]
    total = [sum(r[i] for r in rows) for i in (1, 2, 4, 5)]
    lines.append(f"| **összesen** | {total[0]} | {total[1]} | — | {total[2]} | {total[3]} |")
    by_source = con.execute(
        "SELECT source, count(DISTINCT entity_id) FROM entities WHERE entity_id IN "
        "(SELECT entity_id FROM page_entities) GROUP BY source ORDER BY source").fetchall()
    (unchecked,) = con.execute(
        "SELECT count(*) FROM entities WHERE knowledge_checked_at IS NULL AND entity_id IN "
        "(SELECT entity_id FROM page_entities)").fetchone()
    lines += ["", "Forrás szerint: " + (", ".join(f"{s} {n}" for s, n in by_source) or "—")
              + f"; tudásbázis-ellenőrzés nélkül: {unchecked}", ""]
    return lines


def _soft_section(con: duckdb.DuckDBPyConnection) -> list[str]:
    services = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE kept), "
        "count(*) FILTER (WHERE structure IS NULL OR structure = 'anchor'), "
        "count(*) FILTER (WHERE sol = false), count(*) FILTER (WHERE sol IS NULL AND kept), "
        "count(DISTINCT lower(canonical)) FILTER (WHERE kept) "
        "FROM soft_checks WHERE type = 'service'").fetchone()
    concepts = con.execute(
        "SELECT count(*), count(DISTINCT entity_id), count(*) FILTER (WHERE prominent), "
        "count(*) FILTER (WHERE structure IS NOT NULL), count(*) FILTER (WHERE blocks >= 2), "
        "count(*) FILTER (WHERE knowledge IS NOT NULL), count(DISTINCT page_id) "
        "FROM soft_checks WHERE type = 'concept'").fetchone()
    total, kept, no_place, vetoed, unanswered, distinct = services
    c_total, c_entities, prominent, placed, repeated, known, c_pages = concepts
    return [
        "## Saját ajánlatok és fogalmak", "",
        (f"- saját ajánlat (service), oldal × tétel: {total}; marad {kept} ({distinct} "
         f"különböző név); kiesett szerkezeti hely nélkül {no_place}, az ellenőrzés vétójával "
         f"{vetoed}; megmaradt válasz nélkül {unanswered}"),
        (f"- fogalom (concept), oldal × tétel: {c_total} ({c_entities} entitás, {c_pages} "
         f"oldalon); title- vagy heading-helyen {prominent}, szerkezeti helyen {placed}, "
         f"legalább 2 blokkban {repeated}, tudásbázis-egyezéssel {known}"), ""]


def _regression_section(con: duckdb.DuckDBPyConnection,
                        baseline: duckdb.DuckDBPyConnection | None) -> list[str]:
    if baseline is None:
        return []
    query = ("SELECT e.type, count(DISTINCT e.entity_id), count(*) FROM entities e "
             "JOIN page_entities pe USING (entity_id) GROUP BY e.type")
    old = {kind: (entities, rows) for kind, entities, rows in baseline.execute(query).fetchall()}
    new = {kind: (entities, rows) for kind, entities, rows in con.execute(query).fetchall()}
    lines = ["## Regresszió: a korábbi állapot és a mostani", "",
             "| típus | korábbi entitás | most | korábbi említés | most |", "|---|---|---|---|---|"]
    for kind in sorted(set(old) | set(new), key=lambda k: (-new.get(k, (0, 0))[0], k)):
        (old_e, old_r), (new_e, new_r) = old.get(kind, (0, 0)), new.get(kind, (0, 0))
        lines.append(f"| {kind} | {old_e} | {new_e} | {old_r} | {new_r} |")
    return lines + [""]
