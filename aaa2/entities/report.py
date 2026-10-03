"""A pipeline kimenetei egy site-ra: futásjelentés (Markdown) és site-szintű entitástábla (CSV).

- Futásjelentés: a legutóbbi LLM-futás (és szabálykör): oldalak állapot szerint, hívások és
  költség céljuk szerint (kinyerés, elnevezés, ellenőrzés), időtartam, a kimaradás okai és a
  tudásbázis-kérések hibái, entitások és említések típusonként, a saját ajánlatok sorsa, a
  fogalmak bizonyítékai; ha van korábbi állapot (egy másik adatbázis, pl. a rögzített készlet
  a 012-es migráció előtt), a típusonkénti entitás- és említésszám mellette (regresszió).
- Entitástábla: minden említéssel bíró entitás egy sorban: név, típus, altípus, szint (tier),
  jelölések (flags), forrás, hány oldalon és hány említésben szerepel, és hány oldalon áll
  szerkezeti helyen (title, heading, navigáció, kártya, táblázatsor), az oldala (oldalhoz
  kötött entitásnál), a tudásbázis-egyezés (Wikidata QID és státusz, Wikipedia-URL), és hány
  oldalon jött fogalomként elvetett service-jelöltből (`from_service_pages`, `soft_checks`).
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

from aaa2.engine import queries as crawl
from aaa2.entities import queries as extract_queries
from aaa2.entities import store
from aaa2.entities.extract import run_call_ids
from aaa2.entities.gate import occurs
from aaa2.llm import calls as llm_calls
from aaa2.resolver import queries as resolver_queries

PLACES = ("title", "heading", "nav", "card", "table_row")
TABLE_FIELDS = ("entity", "type", "subtype", "tier", "flags", "source", "pages", "mentions",
                *PLACES, "anchor_page", "wikidata_qid", "wikidata_status", "wikipedia",
                "from_service_pages")


def wikipedia_url(value: str | None) -> str:
    """`nyelv:cím` → a szócikk URL-je."""
    if not value:
        return ""
    code, title = value.split(":", 1)
    return f"https://{code}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"


def entity_table(con: duckdb.DuckDBPyConnection) -> list[dict]:
    mentions = store.page_entities_for_entity_table(con)
    chrome: dict[int, list[str]] = defaultdict(list)
    for found in extract_queries.blocks(con):
        if found.region == "chrome":
            chrome[found.page_id].append(found.text)
    converted = dict(store.soft_checks_for_entity_table(con))
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
    urls = {page.page_id: page.url for page in crawl.pages(con)}
    for entity_id, name, kind, subtype, tier, flags, source, anchor_id, qid, status, wiki in \
            store.entities_for_entity_table(con):
        anchor = urls.get(anchor_id)
        if entity_id not in pages:
            continue
        nav = places[entity_id]["nav"]
        nav |= {page_id for page_id in pages[entity_id] - nav
                if any(occurs(name, text) for text in chrome[page_id])}
        rows.append({"entity": name, "type": kind, "subtype": subtype or "", "tier": tier or "",
                     "flags": " ".join(flags or []), "source": source,
                     "pages": len(pages[entity_id]), "mentions": count[entity_id],
                     **{place: len(places[entity_id][place]) for place in PLACES},
                     "anchor_page": anchor or "", "wikidata_qid": qid or "",
                     "wikidata_status": status or "", "wikipedia": wikipedia_url(wiki),
                     "from_service_pages": converted.get(entity_id, 0)})
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


def primary_empty_pages(con: duckdb.DuckDBPyConnection) -> list[int]:
    """Az oldalak, amelyeknek a legutóbbi kész LLM-rekordja nem nevez meg fő témát (üres
    `primary_entities`), oldal szerint."""
    found: dict[int, bool] = {}
    for page_id, value in store.entity_runs_for_primary_empty_pages(con):
        if page_id not in found and value is not None:
            found[page_id] = not json.loads(value)
    return sorted(page_id for page_id, empty in found.items() if empty)


def _latest(con: duckdb.DuckDBPyConnection, method: str) -> tuple | None:
    return store.entity_runs_for_latest(con, method)


def _money(value: float | None) -> str:
    return f"{value or 0:.4f} USD"


def run_report(con: duckdb.DuckDBPyConnection, label: str,
               baseline: duckdb.DuckDBPyConnection | None = None) -> str:
    """A site legutóbbi futásának jelentése (lásd a modul leírását); `baseline`: a korábbi
    állapot adatbázisa (a regressziós összevetéshez)."""
    site = crawl.site(con)
    domain = site.domain if site else ""
    all_pages = crawl.pages(con)
    eligible = sum(page.renderable for page in all_pages)
    crawled = len(all_pages)
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
        statuses = dict(extract_queries.run_page_status_counts(con, run_id))
        lines += [
            f"- LLM-futás: #{run_id}, kinyerés {model}; indult {started:%Y-%m-%d %H:%M}, "
            + (f"lezárva {finished:%Y-%m-%d %H:%M}" if finished else "nincs lezárva")
            + f"; időtartam {(seconds or 0) / 60:.1f} perc",
            f"- oldalak: {pages} feldolgozva; állapot szerint: "
            + (", ".join(f"{k} {v}" for k, v in statuses.items()) or "—"),
            f"- hívások: {calls}, költség {_money(cost)}"
            + (f" ({(cost or 0) / pages:.4f} USD / oldal)" if pages else ""), "",
            "## Hívások céljuk szerint", ""]
        by_purpose = llm_calls.usage_by_purpose(con, run_call_ids(con, run_id))
        for purpose, call_model, count, t_in, t_out, usd, retries, errors in by_purpose:
            lines.append(f"- {purpose} ({call_model}): {count} hívás, token be {t_in or 0} / "
                         f"ki {t_out or 0}, {_money(usd)}; újrapróba {retries}, "
                         f"hibás válasz {errors}")
        if not by_purpose:
            lines.append("- nincs hívás")
        reasons = json.loads(skipped or "{}")
        kb_errors = resolver_queries.knowledge_errors_since(con, started)
        lines += ["", "## Hibák és kimaradások", "",
                  "- oldalszinten: " + (", ".join(f"{k} {v}" for k, v in reasons.items())
                                        or "—"),
                  "- tudásbázis-kérések hibája a futás óta: "
                  + (", ".join(f"{s} {n}" for s, n in kb_errors) or "0")]
        page_urls = {page.page_id: page.url for page in all_pages}
        for page_id, status, error in extract_queries.unfinished_run_pages(con, run_id):
            if page_id not in page_urls:
                continue
            url = page_urls[page_id]
            lines.append(f"  - {url}: {status}" + (f" ({error[:160]})" if error else ""))
        empty = sorted(page_urls[page_id] for page_id in primary_empty_pages(con)
                       if page_id in page_urls)
        lines.append(f"- a kinyerés nem nevezett meg fő témát: {len(empty)} oldal"
                     + (": " + "; ".join(empty) if empty else ""))
        lines.append("")
    lines += _entities_section(con)
    lines += _site_section(con)
    lines += _soft_section(con)
    lines += _regression_section(con, baseline)
    return "\n".join(lines) + "\n"


def _entities_section(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = store.entities_for_entities_section(con)
    lines = ["## Entitások típusonként", "",
             "| típus | entitás | említés | oldal | Wikidata (biztos) | Wikipedia (biztos) |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {kind} | {entities} | {mentions} | {pages} | {qid} | {wiki} |"
              for kind, entities, mentions, pages, qid, wiki in rows]
    total = [sum(r[i] for r in rows) for i in (1, 2, 4, 5)]
    lines.append(f"| **összesen** | {total[0]} | {total[1]} | — | {total[2]} | {total[3]} |")
    by_source = store.entities_for_entities_section_2(con)
    (unchecked,) = store.entities_for_entities_section_3(con)
    lines += ["", "Forrás szerint: " + (", ".join(f"{s} {n}" for s, n in by_source) or "—")
              + f"; tudásbázis-ellenőrzés nélkül: {unchecked}"
              + f"; valószínű Wikidata-kapcsolás (csak tárolva, a riport nem számol vele): "
                f"{store.probable_link_count(con)}", ""]
    return lines


def _site_section(con: duckdb.DuckDBPyConnection) -> list[str]:
    run = _latest(con, "site")
    if run is None:
        return []
    detail = json.loads(run[8] or "{}")
    tiers = dict(store.entities_for_site_section_2(con))
    anchored = store.entities_for_site_section(con)
    flags = dict(store.entities_for_site_section_3(con))
    merges = resolver_queries.merge_counts(con, run[0])
    relations = resolver_queries.relation_counts(con)
    thresholds = detail.get("thresholds", {})
    return [
        "## Site-szintű entitások", "",
        f"- site-kör: #{run[0]}; oldalszerepek: "
        + (", ".join(f"{k} {v}" for k, v in sorted(detail.get("roles", {}).items())) or "—"),
        "- oldalhoz kötött entitások: "
        + (", ".join(f"{t}{'/' + s if s else ''} {n}" for t, s, n in anchored) or "—"),
        "- szintek: " + (", ".join(f"{k} {v}" for k, v in sorted(tiers.items())) or "—"),
        "- jelölések: " + (", ".join(f"{k} {v}" for k, v in sorted(flags.items())) or "—"),
        "- összevonás szabályonként: " + (", ".join(f"{r} {n}" for r, n in merges) or "0"),
        "- kapcsolatok: " + (", ".join(f"{t} {n}" for t, n in relations) or "0"),
        (f"- sablonküszöb: legalább {thresholds.get('template_min_groups', '—')} oldalcsoport "
         f"és a csoportok {thresholds.get('template_min_share', 0):.0%}-a "
         f"({thresholds.get('template_groups', '—')} csoportból "
         f"{thresholds.get('template_needed', '—')}); demóküszöb: az említések "
         f"{thresholds.get('demo_share', 0):.0%}-a demó-környezetben"), ""]


def _soft_section(con: duckdb.DuckDBPyConnection) -> list[str]:
    services = store.soft_checks_for_soft_section(con)
    concepts = store.soft_checks_for_soft_section_2(con)
    total, kept, no_place, vetoed, unanswered, distinct = services
    c_total, c_entities, prominent, placed, repeated, known, c_pages, converted = concepts
    return [
        "## Saját ajánlatok és fogalmak", "",
        (f"- saját ajánlat (service), oldal × tétel: {total}; marad {kept} ({distinct} "
         f"különböző név); fogalomként marad szerkezeti hely nélkül {no_place}, az "
         f"ellenőrzés vétójával {vetoed}; megmaradt válasz nélkül {unanswered}"),
        (f"- fogalom (concept), oldal × tétel: {c_total} ({c_entities} entitás, {c_pages} "
         f"oldalon); title- vagy heading-helyen {prominent}, szerkezeti helyen {placed}, "
         f"legalább 2 blokkban {repeated}, tudásbázis-egyezéssel {known}; service-jelöltből "
         f"{converted}"), ""]


def _regression_section(con: duckdb.DuckDBPyConnection,
                        baseline: duckdb.DuckDBPyConnection | None) -> list[str]:
    if baseline is None:
        return []
    old = {kind: (entities, rows)
           for kind, entities, rows in store.entity_and_mention_counts_by_type(baseline)}
    new = {kind: (entities, rows)
           for kind, entities, rows in store.entity_and_mention_counts_by_type(con)}
    lines = ["## Regresszió: a korábbi állapot és a mostani", "",
             "| típus | korábbi entitás | most | korábbi említés | most |", "|---|---|---|---|---|"]
    for kind in sorted(set(old) | set(new), key=lambda k: (-new.get(k, (0, 0))[0], k)):
        (old_e, old_r), (new_e, new_r) = old.get(kind, (0, 0)), new.get(kind, (0, 0))
        lines.append(f"| {kind} | {old_e} | {new_e} | {old_r} | {new_r} |")
    return lines + [""]
