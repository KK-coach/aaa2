"""Az M2/6 elfogadása (M2 spec, „M2/6”, 12. pont) a site-kör utáni adatbázisokon, hálózat nélkül.

    python -m tests.acceptance.site_eval --kk data/m26/kk-coach.duckdb \\
        --ngx data/m26/ngx-bootstrap.duckdb --materia data/m26/materia.duckdb

- kk.coach, a `kk_coach_offerings.json` ellen: a referencia minden neve (magyar, angol, alias)
  a site entitásainak nevére, aliasára vagy `entity_aliases` sorára képezve (`alias_key`):
  - fő ajánlat: a service típusú találat egyetlen, `tier = core`, oldalhoz kötött entitás, és
    minden neve erre mutat (az azonos nevű fogalom a spec 9. pontja szerint külön entitás);
  - csomag és munkamód: egyetlen service típusú találat, minden neve erre mutat, `part_of` a fő
    ajánlat entitásához;
  - módszertani lépés: nincs service típusú találata;
  - `offers`: a fő ajánlat → a felsorolt fogalmak;
  - téves összevonás: két különböző referenciatétel ugyanarra az entitásra mutat;
  - nyelvi párok (`LANGUAGE_PAIRS`): a fogalom minden alakja egy entitás;
  - személy Wikidata-linkje a JSON-LD `sameAs` nélkül; menüpont-entitás segédoldalra.
- ngx: komponenscsoportonként egy tech/component entitás; a demó-listán (`NGX_DEMO`) nem jelölt
  entitás; Wikidata-link API-szimbólumon; a sablonjelölés a példa-szakaszcímeken és az
  importokon.
- Materia: a márkák (`MATERIA_BRANDS`) külön entitások `brand_of` kapcsolattal; a jogszabályok
  (`MATERIA_LAWS`) work / legislation típusúak.

Halasztva (Krisztián döntése, 2026-09-29; a spec pontjai érvényesek, az M2/7 előtt kerülnek
sorra): a termék–márka (9a), a jogszabály (9b) és a Wikidata-státusz (8. pont; kivéve a
személyek automatikus kapcsolásának kikapcsolását). Ezek sorai „halasztva” jelöléssel
mérnek, nem hibaként.

A kimenet Markdown a `--out` alá; a konzolra a fájl útvonala. `--merge-sample PATH`: 30 tételes
kézi összevonási minta a `merge_log`-ból (`merge_sample`), verdikt nélkül.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from aaa2.entities.pages import entity_groups, page_roles
from aaa2.entities.rules import alias_key

OFFERINGS = Path(__file__).parent / "kk_coach_offerings.json"
OUT_DIR = Path(__file__).parent / "out" / "m26"
LANGUAGE_PAIRS = (("AI", "Artificial intelligence", "Mesterséges intelligencia"),
                  ("UX", "User experience", "felhasználói élmény"),
                  ("Mérés", "Measurement"),
                  ("Konverzió", "Conversion"),
                  ("SEO", "Keresőoptimalizálás", "Search engine optimization"))
NGX_DEMO_TYPES = ("place",)
NGX_DEMO = ("Mr. O", "Tomato", "Bombasto", "Magneta", "Tornado", "Windstorm", "Letraset")
NGX_TEMPLATE_HEADINGS = ("Configuring defaults", "Installation", "Custom triggers")
MATERIA_BRANDS = ("Abracadabra", "Autentico Nativo", "Caffé Cabaret", "Club Cordiale",
                  "Le Vigne di Don Peppino", "Manfredi", "Martell")
MATERIA_LAWS = ("Act CXII of 2011", "Act V of 2013", "Act XLVIII of 2008",
                "General Data Protection Regulation")


@dataclass
class Check:
    name: str
    ok: bool | None                  # None: halasztva (a spec pontja később kerül sorra)
    detail: str = ""


@dataclass
class Result:
    site: str
    checks: list[Check] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)

    def add(self, name: str, ok: bool | None, detail: str = "") -> None:
        self.checks.append(Check(name, ok, detail))

    def deferred(self, name: str, detail: str = "") -> None:
        self.checks.append(Check(name, None, detail))


class Index:
    """Név-kulcs → entitások (név, `aliases`, `entity_aliases`); csak az említéses vagy
    oldalhoz kötött entitások."""

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con
        self.info: dict[int, dict] = {}
        self.by_key: dict[str, set[int]] = defaultdict(set)
        for entity_id, name, kind, subtype, tier, anchor, aliases, flags in con.execute(
                "SELECT entity_id, name, type, subtype, tier, anchor_page_id, aliases, flags "
                "FROM entities e WHERE anchor_page_id IS NOT NULL OR entity_id IN "
                "(SELECT entity_id FROM page_entities)").fetchall():
            self.info[entity_id] = {"name": name, "type": kind, "subtype": subtype,
                                    "tier": tier, "anchor": anchor, "flags": flags or []}
            for form in [name, *(aliases or [])]:
                self.by_key[alias_key(form)].add(entity_id)
        for entity_id, alias in con.execute("SELECT entity_id, alias FROM entity_aliases"
                                            ).fetchall():
            if entity_id in self.info:
                self.by_key[alias_key(alias)].add(entity_id)

    def find(self, name: str) -> set[int]:
        return set(self.by_key.get(alias_key(name), set()))

    def label(self, entity_id: int) -> str:
        i = self.info[entity_id]
        return f"{i['name']} ({i['type']}{'/' + i['tier'] if i['tier'] else ''})"

    def relations(self, kind: str) -> set[tuple[int, int]]:
        return set(self.con.execute("SELECT from_id, to_id FROM entity_relations "
                                    "WHERE type = ?", [kind]).fetchall())


def _names(item: dict) -> list[str]:
    return [n for n in (item.get("hu"), item.get("en"), *item.get("aliases", [])) if n]


def evaluate_kk(con: duckdb.DuckDBPyConnection, reference: dict) -> Result:
    result = Result("kk.coach")
    index = Index(con)
    part_of = index.relations("part_of")
    offers = index.relations("offers")
    owners: dict[int, set[str]] = defaultdict(set)
    rows = ["| tétel | szint | találat | rendben | megjegyzés |", "|---|---|---|---|---|"]

    def resolve(label: str, names: list[str]) -> tuple[set[int], list[str]]:
        found: set[int] = set()
        missing = []
        for name in names:
            hit = index.find(name)
            if not hit:
                missing.append(name)
            found |= hit
        for entity_id in found:
            owners[entity_id].add(label)
        return found, missing

    core_ok = pkg_ok = pkg_total = 0
    for core in reference["core"]:
        found, missing = resolve(f"core:{core['id']}", _names(core))
        ids = [i for i in found if index.info[i]["type"] == "service"]
        cores = [i for i in ids if index.info[i]["tier"] == "core"]
        unassigned = [n for n in _names(core) if not cores or cores[0] not in index.find(n)]
        ok = len(cores) == 1 and ids == cores and index.info[cores[0]]["anchor"] is not None \
            and not unassigned
        missing = unassigned
        core_ok += ok
        rows.append(f"| {core['hu']} / {core['en']} | fő ajánlat | "
                    f"{', '.join(index.label(i) for i in sorted(found)) or '—'} | "
                    f"{'igen' if ok else 'NEM'} | "
                    f"{'nem az ajánlathoz rendelt név: ' + ', '.join(missing) if missing else ''} |")
        core_id = cores[0] if len(cores) == 1 else None
        for kind, items in (("csomag", core.get("packages", [])),
                            ("munkamód", core.get("work_modes", []))):
            for item in items:
                pkg_total += 1
                found_p, missing_p = resolve(f"{kind}:{item.get('en') or item.get('hu')}",
                                             _names(item))
                services = [i for i in found_p if index.info[i]["type"] == "service"]
                linked = core_id is not None and any((i, core_id) in part_of for i in services)
                unassigned = [n for n in _names(item)
                              if len(services) != 1 or services[0] not in index.find(n)]
                ok = len(services) == 1 and linked and not unassigned
                missing_p = unassigned
                pkg_ok += ok
                note = []
                if missing_p:
                    note.append("nem a csomaghoz rendelt név: " + ", ".join(missing_p))
                if services and not linked:
                    note.append("nincs part_of a fő ajánlathoz")
                if item.get("note"):
                    note.append("ref: " + item["note"])
                rows.append(f"| {item.get('hu') or '—'} / {item.get('en') or '—'} | {kind} | "
                            f"{', '.join(index.label(i) for i in sorted(found_p)) or '—'} | "
                            f"{'igen' if ok else 'NEM'} | {'; '.join(note)} |")
    result.add("fő ajánlat: egy entitás, service/core, oldalhoz kötve, minden neve",
               core_ok == len(reference["core"]), f"{core_ok}/{len(reference['core'])}")
    result.add("csomag és munkamód: egy entitás, service, part_of", pkg_ok == pkg_total,
               f"{pkg_ok}/{pkg_total}")
    step_bad = []
    for step in reference["method_steps_not_offerings"]:
        found, _ = resolve(f"lépés:{step.get('en') or step.get('hu')}", _names(step))
        step_bad += [f"{step.get('hu') or step.get('en')} → {index.label(i)}" for i in found
                     if index.info[i]["type"] == "service"]
    result.add("módszertani lépés nem service", not step_bad,
               f"{len(step_bad)} hiba" + (": " + "; ".join(step_bad) if step_bad else ""))
    offer_miss = []
    for core in reference["core"]:
        core_ids = [i for i in index.find(core["hu"]) | index.find(core["en"])
                    if index.info[i]["type"] == "service"]
        for concept in core.get("offers_concepts", []):
            targets = index.find(concept)
            if not any((c, t) in offers for c in core_ids for t in targets):
                offer_miss.append(f"{core['id']} → {concept}")
    total_offers = sum(len(c.get("offers_concepts", [])) for c in reference["core"])
    result.add("offers: fő ajánlat → fogalom", not offer_miss,
               f"{total_offers - len(offer_miss)}/{total_offers}"
               + (" hiányzik: " + "; ".join(offer_miss) if offer_miss else ""))
    wrong = {i: labels for i, labels in owners.items()
             if len({label.split(":", 1)[0] + ":" + label.split(":", 1)[1] for label in labels})
             > 1 and not _same_item(labels)}
    result.add("téves összevonás (két referenciatétel egy entitáson)", not wrong,
               "; ".join(f"{index.label(i)} ← {', '.join(sorted(v))}" for i, v in wrong.items())
               or "0")
    pair_bad = []
    for forms in LANGUAGE_PAIRS:
        ids = {i for f in forms for i in index.find(f) if index.info[i]["type"] == "concept"}
        if len(ids) != 1:
            pair_bad.append(f"{' / '.join(forms)}: {', '.join(index.label(i) for i in ids) or '—'}")
    result.add("nyelvi párok egy fogalom-entitásként", not pair_bad,
               f"{len(LANGUAGE_PAIRS) - len(pair_bad)}/{len(LANGUAGE_PAIRS)}"
               + (" — " + "; ".join(pair_bad) if pair_bad else ""))
    persons = con.execute(
        "SELECT name FROM entities WHERE type = 'person' AND (wikidata_id IS NOT NULL "
        "OR wikipedia IS NOT NULL) AND coalesce(wikidata_status, '') <> 'confident'").fetchall()
    result.add("személy Wikidata-link sameAs nélkül", not persons,
               ", ".join(n for (n,) in persons) or "0")
    roles = page_roles(con)
    support = {info.page_id for info in roles.values() if info.role == "support"}
    nav = con.execute(
        "SELECT DISTINCT e.name FROM entities e JOIN page_entities pe USING (entity_id) "
        "WHERE e.source = 'rule' AND e.type = 'concept' AND pe.position = 'anchor' "
        "AND e.entity_id NOT IN (SELECT entity_id FROM page_entities WHERE position <> 'anchor')"
    ).fetchall()
    result.add("menüpont-entitás (csak anchor-említésű szabály-fogalom)", not nav,
               ", ".join(n for (n,) in nav) or f"0 (segédoldal: {len(support)})")
    result.tables = ["## Ajánlatok és csomagok", "", *rows, ""]
    return result


def _same_item(labels: set[str]) -> bool:
    return len(labels) == 1


def evaluate_ngx(con: duckdb.DuckDBPyConnection) -> Result:
    result = Result("ngx-bootstrap")
    roles = page_roles(con)
    groups = {g: m for g, m in entity_groups(roles).items() if m[0].role == "component"}
    rows = ["| komponens | entitás | rendben |", "|---|---|---|"]
    ok_count = 0
    for group, members in sorted(groups.items()):
        page_ids = [m.page_id for m in members]
        found = con.execute(
            "SELECT name, type, subtype FROM entities WHERE list_contains(?, anchor_page_id)",
            [page_ids]).fetchall()
        ok = len(found) == 1 and found[0][1] == "tech" and found[0][2] == "component"
        ok_count += ok
        rows.append(f"| {members[0].h1} | {', '.join(f'{n} ({t}/{s})' for n, t, s in found)} | "
                    f"{'igen' if ok else 'NEM'} |")
    result.add("komponenscsoportonként egy tech/component entitás", ok_count == len(groups),
               f"{ok_count}/{len(groups)}")
    graph = con.execute(
        "SELECT name, type FROM entities WHERE entity_id IN (SELECT entity_id FROM "
        "page_entities) AND NOT coalesce(list_contains(flags, 'demo'), false) AND "
        "(list_contains(?, type) OR list_contains(?, name))",
        [list(NGX_DEMO_TYPES), list(NGX_DEMO)]).fetchall()
    result.add("demó-entitás a gráfban (hely, példahősök, Letraset)", not graph,
               ", ".join(f"{n} ({t})" for n, t in graph) or "0")
    api = con.execute(
        "SELECT name FROM entities WHERE subtype = 'api_symbol' AND (wikidata_id IS NOT NULL "
        "OR wikipedia IS NOT NULL)").fetchall()
    result.deferred("Wikidata- vagy Wikipedia-link API-szimbólumon (8. pont, halasztva)",
                    f"{len(api)}: " + ", ".join(n for (n,) in api[:20]) if api else "0")
    template = []
    for heading in NGX_TEMPLATE_HEADINGS:
        total, flagged = con.execute(
            "SELECT count(*), count(*) FILTER (WHERE list_contains(pe.flags, 'template')) "
            "FROM page_entities pe JOIN blocks b USING (block_id) WHERE b.kind = 'heading' "
            "AND b.text = ?", [heading]).fetchone()
        template.append(f"{heading} {flagged}/{total}")
    total, flagged = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE list_contains(pe.flags, 'template')) "
        "FROM page_entities pe JOIN blocks b USING (block_id) WHERE b.kind = 'code' "
        "AND regexp_matches(substr(b.text, pe.char_start - 60, 80), '^|\\bimport\\b')"
    ).fetchone()
    template.append(f"kód (import-környezet) {flagged}/{total}")
    result.add("sablonjelölés: példa-szakaszcímek és importok", True, "; ".join(template))
    result.tables = ["## Komponensek", "", *rows, ""]
    return result


def evaluate_materia(con: duckdb.DuckDBPyConnection) -> Result:
    result = Result("Materia")
    index = Index(con)
    brand_of = index.relations("brand_of")
    brand_rows = []
    ok = 0
    for brand in MATERIA_BRANDS:
        ids = [i for i in index.find(brand) if index.info[i]["type"] == "brand"]
        products = [t for i in ids for f, t in brand_of if f == i]
        good = len(ids) == 1 and bool(products)
        ok += good
        brand_rows.append(f"{brand}: {len(ids)} márka, {len(products)} brand_of")
    result.deferred("márka külön entitás brand_of-fal (9a, halasztva)",
                    f"{ok}/{len(MATERIA_BRANDS)} — " + "; ".join(brand_rows))
    law_rows = []
    good_laws = 0
    for law in MATERIA_LAWS:
        found = con.execute(
            "SELECT name, type, subtype FROM entities WHERE (name ILIKE ? OR list_has_any("
            "aliases, [?])) AND entity_id IN (SELECT entity_id FROM page_entities)",
            [f"%{law}%", law]).fetchall()
        good = bool(found) and all(t in ("work", "concept") and (t == "concept" or s ==
                                                                 "legislation")
                                   for _, t, s in found)
        good_laws += good and any(t == "work" for _, t, _ in found)
        law_rows.append(f"{law}: " + (", ".join(f"{n} ({t}/{s})" for n, t, s in found) or "—"))
    result.deferred("jogszabály work/legislation (9b, halasztva)",
                    f"{good_laws}/{len(MATERIA_LAWS)} — " + "; ".join(law_rows))
    return result


SAMPLE_FILE = Path(__file__).parent / "verdicts" / "merge_sample.json"
SAMPLE_SIZE = 30
SAMPLE_ALL = ("hreflang_place", "page_identity_position", "type_split")


def merge_sample(sites: dict[str, duckdb.DuckDBPyConnection], size: int = SAMPLE_SIZE) -> list:
    """Kézi mintához `size` összevonás a `merge_log`-ból: a ritka szabályok (`SAMPLE_ALL`)
    minden sora, a többi szabályból arányosan, egyenletes lépésközzel (determinisztikusan).
    Tételenként a két entitás neve, a szabály, a bizonyíték és a megtartott entitás típusa;
    `verdict`: correct / wrong (üresen)."""
    rows = []
    for site, con in sites.items():
        for merge_id, rule, kept_id, kept, removed, evidence in con.execute(
                "SELECT merge_id, rule, kept_id, kept_name, removed_name, evidence "
                "FROM merge_log ORDER BY merge_id").fetchall():
            kind = con.execute("SELECT type FROM entities WHERE entity_id = ?",
                               [kept_id]).fetchone()
            rows.append({"site": site, "merge_id": merge_id, "rule": rule, "kept": kept,
                         "removed": removed, "kept_type": kind[0] if kind else None,
                         "evidence": json.loads(evidence or "{}"), "verdict": None,
                         "note": ""})
    fixed = [r for r in rows if r["rule"] in SAMPLE_ALL]
    rest = [r for r in rows if r["rule"] not in SAMPLE_ALL]
    need = max(0, size - len(fixed))
    by_rule: dict[str, list] = defaultdict(list)
    for row in rest:
        by_rule[row["rule"]].append(row)
    picked = []
    for rule, items in sorted(by_rule.items()):
        share = max(1, round(need * len(items) / max(1, len(rest))))
        step = max(1, len(items) // share)
        picked += items[::step][:share]
    return (fixed + picked)[:size] if len(fixed) < size else fixed[:size]


def markdown(results: list[Result]) -> str:
    lines = ["# M2/6 elfogadás", ""]
    for result in results:
        lines += [f"## {result.site}", "", "| ellenőrzés | rendben | részlet |", "|---|---|---|"]
        lines += [f"| {c.name} | {'halasztva' if c.ok is None else 'igen' if c.ok else 'NEM'} "
                  f"| {c.detail} |" for c in result.checks]
        lines += ["", *result.tables]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--kk", type=Path)
    parser.add_argument("--ngx", type=Path)
    parser.add_argument("--materia", type=Path)
    parser.add_argument("--out", type=Path, default=OUT_DIR / "acceptance.md")
    parser.add_argument("--merge-sample", type=Path, default=None,
                        help="a kézi összevonási minta (JSON) ide, a megadott adatbázisokból")
    args = parser.parse_args(argv)
    results = []
    for path, evaluate in ((args.kk, lambda c: evaluate_kk(c, json.loads(
            OFFERINGS.read_text(encoding="utf-8")))), (args.ngx, evaluate_ngx),
            (args.materia, evaluate_materia)):
        if path is not None:
            con = duckdb.connect(str(path), read_only=True)
            try:
                results.append(evaluate(con))
            finally:
                con.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown(results), encoding="utf-8")
    print(args.out)
    if args.merge_sample is not None:
        sites = {name: duckdb.connect(str(path), read_only=True) for name, path in
                 (("kk.coach", args.kk), ("ngx-bootstrap", args.ngx), ("materia", args.materia))
                 if path is not None}
        try:
            sample = merge_sample(sites)
        finally:
            for con in sites.values():
                con.close()
        args.merge_sample.parent.mkdir(parents=True, exist_ok=True)
        args.merge_sample.write_text(json.dumps(
            {"about": "M2/6 kézi összevonási minta; verdict: correct / wrong",
             "items": sample}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{args.merge_sample} ({len(sample)} tétel)")


if __name__ == "__main__":
    main()
