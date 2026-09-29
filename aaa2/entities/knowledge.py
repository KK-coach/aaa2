"""Tudásbázis-kapcsolás a site entitásaira (M2 spec, M2/6, 8. pont, minimális rész).

- Kapcsolható típus: `LINK_TYPES` (concept, tech, org). Soha nincs automatikus kapcsolás:
  személy, `api_symbol` altípus, demó-jelölés, `MIN_NAME_CHARS`-nál rövidebb név; ezeknél és a
  többi típusnál a link törlődik, a státusz none. A rövid nevű (concept, tech, org) entitás
  csak megerősítéssel olvad össze: ha a találata egy hosszabb nevű, azonos típusú entitás
  biztos QID-je (`wikidata_short_name`).
- Egyezés: a név pontos Wikidata-címkéje vagy aliasa (`gate.wikidata_hit`) az entitás (ha nincs,
  a site) nyelvén, majd angolul. A kétértelmű találat legfeljebb probable: csak alias-egyezés,
  miközben egy másik, a típussal nem összeférhetetlen elemnek a név a címkéje (`_ambiguous`).
- Típus-kompatibilitás: a találat „instance of” osztályainak angol címkéi és az angol leírása
  (`gate.KnowledgeBase.classes`). Ha bármelyik összeférhetetlen kifejezést tartalmaz
  (`INCOMPATIBLE`, típusonként `TYPE_INCOMPATIBLE`) → none; ha kompatibilist (`COMPATIBLE`) →
  confident; különben probable.
- A Wikipedia-link (a korábbi címegyezés) csak confident és probable státusznál marad.
- Összevonás (7. pont, 4. lépés): az azonos típusú, azonos biztos (confident) QID-jű entitások
  egy entitás (`wikidata_confident`, a `merge_log`-ban, a legutóbbi site-kör futásához kötve).
- Ha egy kérés hibára fut, az entitás ellenőrizetlen marad (a következő futás újra kérdezi).
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import duckdb

from aaa2.entities.gate import KnowledgeBase, base_language
from aaa2.entities.site import Merger, _entity_rows, _mergeable, _rank, resolve

LINK_TYPES = ("concept", "tech", "org")
NO_LINK_SUBTYPES = ("api_symbol",)
MIN_NAME_CHARS = 3
INCOMPATIBLE = ("human", "given name", "family name", "surname", "disambiguation",
                "wikimedia", "fictional", "film", "album", "song", "single", "television",
                "video game", "musical group", "band", "taxon", "species", "painting", "novel",
                "literary work", "episode", "village", "city", "town", "municipality", "river",
                "mountain", "asteroid", "gene", "protein", "chemical compound", "musician",
                "cell line")
TYPE_INCOMPATIBLE = {
    "concept": ("organization", "organisation", "company", "business", "software", "website"),
    "tech": ("organization", "organisation", "company", "business"),
    "org": ("software", "programming language", "field of study", "academic discipline"),
}
COMPATIBLE = {
    "concept": ("concept", "discipline", "field", "branch", "academic", "practice", "skill",
                "method", "technique", "process", "activity", "theory", "strategy", "metric",
                "measure", "type of", "profession", "phenomenon", "principle", "approach"),
    "tech": ("software", "framework", "library", "programming language", "technology",
             "protocol", "standard", "file format", "website", "service on internet",
             "online service", "platform", "application", "operating system", "markup language",
             "application programming interface", "database", "algorithm", "specification",
             "web browser", "analytics"),
    "org": ("company", "business", "organization", "organisation", "enterprise", "agency",
            "foundation", "university", "institution", "nonprofit", "corporation",
            "subsidiary", "online service"),
}


@dataclass(frozen=True)
class KnowledgeRun:
    entities: int
    confident: int
    probable: int
    none: int
    errors: int
    merged: int


def clear_person_links(con: duckdb.DuckDBPyConnection,
                       clock: Callable[[], datetime]) -> int:
    """A személyek automatikus Wikidata- és Wikipedia-kapcsolása ki van kapcsolva: a meglévő
    linkjük törlődik, a státuszuk none. Visszaad: hány személy változott."""
    rows = con.execute(
        "UPDATE entities SET wikidata_id = NULL, wikipedia = NULL, wikidata_status = 'none', "
        "knowledge_checked_at = coalesce(knowledge_checked_at, ?) WHERE type = 'person' "
        "AND (wikidata_id IS NOT NULL OR wikipedia IS NOT NULL OR wikidata_status IS NULL) "
        "RETURNING entity_id", [clock()]).fetchall()
    return len(rows)


def status_of(kind: str, labels: list[str], description: str) -> str:
    """confident / probable / none a típus és a találat szerint: az összeférhetetlenség az
    osztálycímkékből, a kompatibilitás az osztálycímkékből és a leírásból (a leírás szövege
    más fogalmakat is említhet, pl. „…studies software…”)."""
    classes = " | ".join(labels).lower()
    if any(word in classes for word in (*INCOMPATIBLE, *TYPE_INCOMPATIBLE.get(kind, ()))):
        return "none"
    text = f"{classes} | {description.lower()}"
    if any(word in text for word in COMPATIBLE.get(kind, ())):
        return "confident"
    return "probable"


def link_entities(con: duckdb.DuckDBPyConnection, knowledge: KnowledgeBase,
                  clock: Callable[[], datetime], site_lang: str | None = None) -> KnowledgeRun:
    """A még státusz nélküli, említéssel bíró entitások kapcsolása (lásd a modul leírását), és
    az összevonás a biztos QID szerint. A keresés nyelvei: az entitásé, a `site_lang`, a site
    nyelvei, végül angol."""
    clear_person_links(con, clock)
    site_langs = [base_language(code) for code in (
        con.execute("SELECT languages FROM site").fetchone() or [None])[0] or []]
    short: list[tuple[int, str, list[str]]] = []
    rows = con.execute(
        "SELECT entity_id, name, type, subtype, lang, flags FROM entities "
        "WHERE wikidata_status IS NULL AND entity_id IN (SELECT entity_id FROM page_entities) "
        "ORDER BY entity_id").fetchall()
    counts = defaultdict(int)
    for entity_id, name, kind, subtype, lang, flags in rows:
        codes = list(dict.fromkeys([base_language(lang or site_lang), base_language(site_lang),
                                    *site_langs, "en"]))
        if kind not in LINK_TYPES or subtype in NO_LINK_SUBTYPES or "demo" in (flags or []) \
                or len(name.strip()) < MIN_NAME_CHARS:
            _store(con, entity_id, None, None, "none", clock)
            counts["none"] += 1
            if kind in LINK_TYPES and subtype not in NO_LINK_SUBTYPES \
                    and "demo" not in (flags or []):
                short.append((entity_id, name, codes))
            continue
        before = knowledge.failures
        hit = next((found for code in codes if (found := knowledge.wikidata(name, code))), None)
        status, qid, wiki = "none", None, None
        if hit is not None:
            classes = knowledge.classes(hit["id"])
            if classes is not None:
                status = status_of(kind, *classes)
            if status == "confident" and _ambiguous(knowledge, kind, hit):
                status = "probable"
            if status != "none":
                qid = hit["id"]
                wiki = next((f"{code}:{page['title']}" for code in codes
                             if (page := knowledge.wikipedia(name, code))), None)
        if knowledge.failures > before:
            counts["errors"] += 1
            continue
        _store(con, entity_id, qid, wiki, status, clock)
        counts[status] += 1
    merged = _merge_confident(con, clock, _corroborated(con, knowledge, short))
    return KnowledgeRun(len(rows), counts["confident"], counts["probable"], counts["none"],
                        counts["errors"], merged)


def _ambiguous(knowledge: KnowledgeBase, kind: str, hit: dict) -> bool:
    """Az alias-találat kétértelmű, ha egy vetélytárs címke-találat (`gate.wikidata_hit`
    `rivals`) sem az osztályai, sem a leírása szerint nem összeférhetetlen a típussal (pl.
    „forgalom”: a közlekedési forgalom; nem az a „Seo” családnév és indonéz falu a „SEO”
    mellett). Hibás kérésnél kétértelmű."""
    blocked = (*INCOMPATIBLE, *TYPE_INCOMPATIBLE.get(kind, ()))
    for qid in hit.get("rivals", ()):
        classes = knowledge.classes(qid)
        if classes is None:
            return True
        text = " | ".join([*classes[0], classes[1]]).lower()
        if not any(word in text for word in blocked):
            return True
    return False


def _corroborated(con: duckdb.DuckDBPyConnection, knowledge: KnowledgeBase,
                  short: list[tuple[int, str, list[str]]]) -> list[tuple[int, int, str]]:
    """A `MIN_NAME_CHARS`-nál rövidebb nevű entitás önmagában nem kap linket; ha a pontos
    címke- vagy alias-találata ugyanaz a QID, amelyet egy azonos típusú, hosszabb nevű entitás
    biztosan (confident) kapott, ahhoz olvad (`wikidata_short_name`). (rövid, cél, QID)."""
    confident = {(kind, qid): entity_id for entity_id, kind, qid in con.execute(
        "SELECT entity_id, type, wikidata_id FROM entities WHERE wikidata_status = "
        "'confident' ORDER BY entity_id").fetchall()}
    pairs = []
    for entity_id, name, codes in short:
        (kind,) = con.execute("SELECT type FROM entities WHERE entity_id = ?",
                              [entity_id]).fetchone()
        hit = next((found for code in codes if (found := knowledge.wikidata(name, code))), None)
        if hit is not None and (kind, hit["id"]) in confident                 and not _ambiguous(knowledge, kind, hit):
            pairs.append((entity_id, confident[(kind, hit["id"])], hit["id"]))
    return pairs


def _store(con: duckdb.DuckDBPyConnection, entity_id: int, qid: str | None, wiki: str | None,
           status: str, clock: Callable[[], datetime]) -> None:
    con.execute("UPDATE entities SET wikidata_id = ?, wikipedia = ?, wikidata_status = ?, "
                "knowledge_checked_at = ? WHERE entity_id = ?",
                [qid, wiki, status, clock(), entity_id])


def _merge_confident(con: duckdb.DuckDBPyConnection, clock: Callable[[], datetime],
                     short: list[tuple[int, int, str]] = ()) -> int:
    """Az azonos típusú, azonos biztos QID-jű entitások összevonása, és a megerősített rövid
    nevek (`short`) beolvasztása, a legutóbbi site-körhöz kötött `merge_log`-gal; ha nincs
    site-kör, nincs összevonás."""
    run = con.execute("SELECT max(run_id) FROM entity_runs WHERE method = 'site'").fetchone()[0]
    if run is None:
        return 0
    merger = Merger(con, run, clock)
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for entity_id, kind, qid in con.execute(
            "SELECT entity_id, type, wikidata_id FROM entities WHERE wikidata_status = "
            "'confident' AND wikidata_id IS NOT NULL ORDER BY entity_id").fetchall():
        groups[(kind, qid)].append(entity_id)
    rows = _entity_rows(con)
    for (kind, qid), ids in sorted(groups.items()):
        if len(ids) < 2:
            continue
        ordered = sorted((rows[i] for i in ids), key=_rank)
        keep = ordered[0]
        for other in ordered[1:]:
            if _mergeable(keep, other):
                merger.merge(keep[0], other[0], "wikidata_confident", {"qid": qid, "type": kind})
    for entity_id, target, qid in short:
        target = resolve(con, target)
        if target is not None and target != entity_id \
                and _mergeable(_entity_rows(con)[target], _entity_rows(con)[entity_id]):
            merger.merge(target, entity_id, "wikidata_short_name", {"qid": qid})
    return merger.counts["wikidata_confident"] + merger.counts["wikidata_short_name"]
