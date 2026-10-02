"""Bizonyíték a puha típusok (concept, service) döntéséhez: keresési volumen, entitás-áttekintő,
bizonyíték-tábla és döntési kombinációk, a tárolt kimeneteken.

    python -m tests.acceptance.evidence volume [--source cp-g]
    python -m tests.acceptance.evidence report [--source cp-g] [--gate cp-g-e1]
        [--luna cp-g-e2-luna] [--sol cp-g-e2-sol]

Az oldalak a valódi fejlesztési oldalak (`--pages-dir`, alapból a `dev_pages/`); a kinyerés
modellje `--model` (alapból a `[pipeline] extraction`).

- Entitás-áttekintő, oldalanként: a referencialista kötelező és opcionális tételei és a modell
  kinyerései (a `--source` kör nem kitalált említései kanonikus kulcs szerint csoportosítva),
  összevonva: a modell csoportja a referencia-tételhez tartozik, ha a kanonikus nevének vagy egy
  szöveg szerinti alakjának kulcsa a tétel nevének vagy egy aliasának kulcsa (előbb a kötelező,
  aztán az opcionális tételek; mint a pontozás precizitásánál). Oszlopok: oldal, entitás, típus /
  altípus, forrás, a modell nevei, felismerve (a referencia blokkjában), elnevezés, verdikt,
  Wikipedia, Wikidata, Google KG, volumen HU / EN. Kimenet: `tests/acceptance/out/
  entities_overview_<oldal>.csv` és Markdown a jelentésben.
- Tudásbázis, a meglévő validálás gyorsítótárán és naplóján át (`<data-dir>/gate.duckdb`), a
  sor nevein (a kanonikus név és a referencia aliasai), az oldal nyelvén, majd angolul:
  Wikipedia pontos cím vagy átirányítás (`gate.KnowledgeBase.wikipedia`), Wikidata címke vagy
  alias (`gate.KnowledgeBase.wikidata`), Google KG (`validate.classify_kg`, conceptnél a
  `validate.concept_status` a saját nyelvű Wikipedia-találattal).
- Keresési volumen: DataForSEO Google Ads `search_volume`, normál sor (`task_post`, majd
  `task_get`), két feladat: HU (Magyarország, magyar nyelv; a magyar oldalak kulcsszavai) és EN
  (angol nyelv, hely nélkül, azaz világszint; minden oldal kulcsszavai). Kulcsszó: a referencia
  neve és aliasai, és a modell minden csoportjának kanonikus neve; kisbetűs, a Google Ads-ben
  nem használható jelek szóközre cserélve; a 10 szónál vagy 80 karakternél hosszabb kimarad. Az entitás
  volumene a nevei közül a legnagyobb. Gyorsítótár: `<data-dir>/volume/<piac>.json` (a
  feladat azonosítója és a válasz); ha megvan, nincs új kérés. A költség a válaszból.
- Bizonyíték-tábla (a modell concept- és service-tételeire, `gate.soft_items`): verdikt, volumen
  HU / EN, tudásbázis, szerkezet, ismétlődés (a `--gate` kör döntései), Luna- és Sol-ellenőrzés
  (a `--luna` és a `--sol` kör döntései). Itt a volumen és a tudásbázis a modell kanonikus
  nevére szól (a referencia aliasai nélkül), ahogy éles futásban is.
- Döntési kombinációk, hálózat nélkül (vol = a HU és az EN volumen közül a nagyobb):
  (a) vol ≥ küszöb VAGY tudásbázis VAGY szerkezet VAGY ismétlődés; (b) (a) ÉS nincs Sol-vétó;
  (c) (a) ÉS (nincs Sol-vétó VAGY title- / heading-helyű VAGY szolgáltatásnév VAGY vol ≥ küszöb);
  (d) nincs Sol-vétó. Szolgáltatásnév: service típusú tétel szerkezeti helyen (heading, kártya,
  táblázatsor, navigáció). Küszöbök: > 0, ≥ 10, ≥ 50 havi keresés; mellette az (a)–(c) volumen
  nélkül. Kombinációnként: concept- és service-precizitás (verdikt), végső recall, a kiejtett
  referenciatételek, költség.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

import tests.acceptance.gate_eval as ge
import tests.acceptance.synthetic_eval as se
from aaa2.db.connect import DATA_DIR, connect
from aaa2.entities.blocks import check_surface, surface_spans
from aaa2.entities.gate import (
    PROMINENT_KINDS,
    KnowledgeBase,
    PageContext,
    base_language,
    soft_items,
    structure,
)
from aaa2.entities.rules import alias_key
from aaa2.llm.client import Retry, api_key
from aaa2.llm.config import load_config
from aaa2.llm.schemas import BlockEntity
from aaa2.resolver.validate import (
    KG_ENDPOINT,
    KG_KEY_ENV,
    KG_LIMIT,
    _Api,
    classify_kg,
    concept_status,
    load_kg_types,
)

OUT = Path(__file__).parent / "out"
DFS_API = "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume"
MARKETS = {"hu": {"location_code": 2348, "language_code": "hu"},
           "en": {"language_code": "en"}}
THRESHOLDS = (1, 10, 50)                 # > 0, ≥ 10, ≥ 50
ADS_UNSAFE = re.compile(r"[^\w\s'&.+-]")


# ---------------------------------------------------------------------------
# sorok: referencia + modell
# ---------------------------------------------------------------------------


@dataclass
class Group:
    key: str
    canonical: str
    type: str
    subtype: str | None
    mentions: list[dict] = field(default_factory=list)

    def keys(self) -> set[str]:
        return {self.key, *(alias_key(m["surface_form"]) for m in self.mentions)}


@dataclass
class Row:
    page_id: str
    canonical: str
    type: str | None
    subtype: str | None
    source: str
    names: list[str]                                   # a sor nevei (volumen, tudásbázis)
    models: list[Group] = field(default_factory=list)
    recognized: str = "–"
    naming: str = "–"
    verdicts: list[str] = field(default_factory=list)
    wikipedia: dict | None = None
    wikidata: dict | None = None
    kg: dict | None = None
    volume: dict = field(default_factory=dict)         # piac → volumen (None: nincs adat)


def groups_of(record: Mapping, blocks: Mapping[str, Mapping]) -> list[Group]:
    """A nem kitalált említések kanonikus kulcs szerint, az első említés sorrendjében."""
    groups: dict[str, Group] = {}
    for raw in record.get("entities") or []:
        if not check_surface(BlockEntity.model_construct(**raw), blocks):
            continue
        key = alias_key(raw["canonical_name"])
        groups.setdefault(key, Group(key, raw["canonical_name"], raw["type"], raw.get("subtype"))
                          ).mentions.append(raw)
    return list(groups.values())


def outcomes(page: Mapping, record: Mapping, entries: Sequence[Mapping]
             ) -> list[tuple[bool, bool]]:
    """Tételenként (felismerve a referencia blokkjában, jól elnevezve), a pontozás szabályával
    (`synthetic_eval.score_page`)."""
    blocks = {b["id"]: b for b in page["blocks"]}
    spans = []
    for group in groups_of(record, blocks):
        for raw in group.mentions:
            spans += [(raw["block_id"], span, group.key)
                      for span in surface_spans(raw["surface_form"], blocks[raw["block_id"]])]
    out = []
    for item, entry in zip(se._items(list(entries)), entries, strict=True):
        gold_spans = [(s["block"], span) for s in entry.get("surface_forms", [])
                      if s["block"] in blocks
                      for span in surface_spans(s["text"], blocks[s["block"]])]
        keys = [key for block, span, key in spans
                if any(block == gb and se._overlap(span, gs) for gb, gs in gold_spans)]
        out.append((bool(keys), any(key in item.naming_keys for key in keys)))
    return out


def page_rows(page: Mapping, record: Mapping, verdicts: Mapping) -> list[Row]:
    gold = page["gold"]
    blocks = {b["id"]: b for b in page["blocks"]}
    groups = groups_of(record, blocks)
    rows: list[Row] = []
    items: list[tuple[Row, se.Item]] = []
    for section, label in (("entities", "kötelező"), ("optional", "opcionális")):
        entries = gold.get(section, [])
        for entry, item, (recognized, named) in zip(
                entries, se._items(entries), outcomes(page, record, entries), strict=True):
            row = Row(page["page_id"], entry["canonical"], entry.get("type"),
                      entry.get("subtype"), f"referencia ({label})",
                      [entry["canonical"], *entry.get("aliases", [])],
                      recognized="igen" if recognized else "nem",
                      naming=("helyes" if named else "hibás") if recognized else "–")
            rows.append(row)
            items.append((row, item))
    for group in groups:
        keys = group.keys()
        owner = next((row for row, item in items if item.keys & keys
                      and row.source.endswith("(kötelező)")), None) \
            or next((row for row, item in items if item.keys & keys), None)
        if owner is not None:
            owner.models.append(group)
            if "mindkettő" not in owner.source:
                owner.source = owner.source.replace("referencia", "mindkettő")
        else:
            rows.append(Row(page["page_id"], group.canonical, group.type, group.subtype,
                            "modell", [group.canonical], [group]))
    for row in rows:
        row.verdicts = [verdicts.get(ge.verdict_key(page["page_id"], g.canonical, g.type))
                        or "megítéletlen" for g in row.models if g.type in ("concept", "service")]
    return rows


# ---------------------------------------------------------------------------
# tudásbázis
# ---------------------------------------------------------------------------


def lookup_rows(rows: Sequence[Row], lang: str, api: _Api, kg_key: str | None) -> None:
    kb = KnowledgeBase(api.get)
    mapping = load_kg_types()
    langs = list(dict.fromkeys([base_language(lang), "en"]))
    for row in rows:
        for name in row.names:
            for code in langs:
                if row.wikipedia is None and (hit := kb.wikipedia(name, code)):
                    title = hit["title"]
                    row.wikipedia = {**hit, "lang": code, "name": name,
                                     "url": f"https://{code}.wikipedia.org/wiki/"
                                            + title.replace(" ", "_")}
                if row.wikidata is None and (hit := kb.wikidata(name, code)):
                    row.wikidata = {**hit, "lang": code, "name": name}
        if kg_key:
            keys = {alias_key(n) for n in row.names}
            params = [("query", row.canonical), ("limit", str(KG_LIMIT)),
                      *[("languages", code) for code in langs], ("key", kg_key)]
            _, body = api.get("kg", KG_ENDPOINT, params)
            if body is not None:
                kg = classify_kg(body, keys, {row.type} - {None}, mapping)
                own = bool(row.wikipedia and row.wikipedia["lang"] == langs[0])
                status = concept_status(kg, own) if row.type == "concept" else kg.status
                row.kg = {"status": status or kg.status, "id": kg.kg_id}


# ---------------------------------------------------------------------------
# keresési volumen
# ---------------------------------------------------------------------------


def ads_keyword(name: str) -> str | None:
    """A név Google Ads-kulcsszóként; None, ha üres, vagy 10 szónál vagy 80 karakternél
    hosszabb (nem csonkoljuk)."""
    text = " ".join(ADS_UNSAFE.sub(" ", name.lower()).split())
    if not text or len(text) > 80 or len(text.split()) > 10:
        return None
    return text


def market_keywords(pages: Sequence[Mapping], rows: Mapping[str, list[Row]]
                    ) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"hu": [], "en": []}
    for page in pages:
        names = [n for row in rows[page["page_id"]] for n in row.names]
        names += [g.canonical for row in rows[page["page_id"]] for g in row.models]
        keywords = [k for k in (ads_keyword(n) for n in names) if k]
        markets = ("hu", "en") if base_language(page.get("lang")) == "hu" else ("en",)
        for market in markets:
            out[market] += keywords
    return {m: sorted(set(ks)) for m, ks in out.items()}


def _auth() -> dict[str, str]:
    login, password = api_key("DATAFORSEO_LOGIN"), api_key("DATAFORSEO_PASSWORD")
    if not login or not password:
        raise SystemExit("volume: nincs DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD")
    token = base64.b64encode(f"{login}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def fetch_volumes(keywords: Mapping[str, list[str]], cache_dir: Path,
                  http: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
                  poll_seconds: float = 30.0, max_polls: int = 120) -> dict[str, dict]:
    """Piaconként a gyorsítótárazott vagy új feladat eredménye: {`keywords`, `task_id`,
    `cost`, `results`: kulcsszó → volumen}."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    http = http or httpx.Client(timeout=60.0)
    out: dict[str, dict] = {}
    pending: dict[str, dict] = {}
    for market, words in keywords.items():
        path = cache_dir / f"{market}.json"
        cached = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if cached and cached.get("keywords") == words and cached.get("results") is not None:
            out[market] = cached
            continue
        if cached and cached.get("keywords") == words and cached.get("task_id"):
            pending[market] = cached
            continue
        if len(words) > 1000:
            raise SystemExit(f"volume: {market}: {len(words)} kulcsszó, legfeljebb 1000")
        pending[market] = {"keywords": words, "task": {"keywords": words, "tag": market,
                                                       **MARKETS[market]}}
    new = {m: e for m, e in pending.items() if not e.get("task_id")}
    if new:
        response = http.post(f"{DFS_API}/task_post", headers=_auth(),
                             json=[e["task"] for e in new.values()]).json()
        for task in response.get("tasks") or []:
            market = (task.get("data") or {}).get("tag")
            if market in new:
                if task.get("status_code") != 20100:
                    raise SystemExit(f"volume: {market}: {task.get('status_code')} "
                                     f"{task.get('status_message')}")
                new[market].update(task_id=task["id"], cost=task.get("cost"))
                (cache_dir / f"{market}.json").write_text(
                    json.dumps(new[market], ensure_ascii=False, indent=1), encoding="utf-8")
    for market, entry in pending.items():
        for _ in range(max_polls):
            response = http.get(f"{DFS_API}/task_get/{entry['task_id']}", headers=_auth()).json()
            task = (response.get("tasks") or [{}])[0]
            if task.get("status_code") == 20000:
                entry["results"] = {r["keyword"]: r.get("search_volume")
                                    for r in task.get("result") or []}
                entry["get_cost"] = task.get("cost")
                break
            if task.get("status_code") not in (40601, 40602):          # sorban, fut
                raise SystemExit(f"volume: {market}: {task.get('status_code')} "
                                 f"{task.get('status_message')}")
            sleep(poll_seconds)
        else:
            raise SystemExit(f"volume: {market}: a feladat nem készült el ({entry['task_id']})")
        (cache_dir / f"{market}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
        out[market] = entry
    return out


def volume_of(names: Sequence[str], results: Mapping[str, int | None] | None) -> int | None:
    if results is None:
        return None
    values = [results.get(k) for k in (ads_keyword(n) for n in names) if k]
    values = [v for v in values if v is not None]
    return max(values) if values else None


# ---------------------------------------------------------------------------
# bizonyíték-tábla és kombinációk
# ---------------------------------------------------------------------------


@dataclass
class Evidence:
    page_id: str
    key: str
    canonical: str
    type: str
    verdict: str | None
    volume_hu: int | None
    volume_en: int | None
    knowledge: str | None
    structure: str | None
    blocks: int
    prominent: bool
    luna: bool | None
    sol: bool | None

    @property
    def volume(self) -> int:
        return max(v for v in (self.volume_hu, self.volume_en, 0) if v is not None)

    @property
    def service_name(self) -> bool:
        return self.type == "service" and self.structure is not None

    def site(self) -> bool:
        return self.structure is not None or self.blocks >= 2


def evidence_rows(page: Mapping, record: Mapping, decisions: Sequence[Mapping],
                  luna: Mapping | None, sol: Mapping | None, volumes: Mapping[str, dict],
                  verdicts: Mapping) -> list[Evidence]:
    context = PageContext(page["blocks"], lang=page.get("lang") or "en")
    by_key = {d["key"]: d for d in decisions}

    def verified(record_):
        if record_ is None:
            return {}
        return {(alias_key(name), kind): keep for name, kind, keep in record_["verify_decisions"]}

    luna_of, sol_of = verified(luna), verified(sol)
    hu = base_language(page.get("lang")) == "hu"
    out = []
    for item in soft_items(record.get("entities") or [], context.by_id()):
        decision = by_key.get(item.key) or {}
        out.append(Evidence(
            page["page_id"], item.key, item.canonical, item.type,
            verdicts.get(ge.verdict_key(page["page_id"], item.canonical, item.type)),
            volume_of([item.canonical], (volumes.get("hu") or {}).get("results")) if hu else None,
            volume_of([item.canonical], (volumes.get("en") or {}).get("results")),
            decision.get("knowledge"), decision.get("structure"), decision.get("blocks", 0),
            structure(item, context, PROMINENT_KINDS) is not None,
            luna_of.get((item.key, item.type)), sol_of.get((item.key, item.type))))
    return out


def rule(name: str, threshold: int | None) -> Callable[[Evidence], bool]:
    """A kombináció döntése egy tételre (marad-e); `threshold` None: volumen nélkül."""
    def outside(e):
        return (threshold is not None and e.volume >= threshold) or bool(e.knowledge)

    def a(e):
        return outside(e) or e.site()

    def veto(e):
        return e.sol is False

    rules = {
        "a": a,
        "b": lambda e: a(e) and not veto(e),
        "c": lambda e: a(e) and (not veto(e) or e.prominent or e.service_name
                                 or (threshold is not None and e.volume >= threshold)),
        "d": lambda e: not veto(e),
    }
    return rules[name]


def combos() -> list[tuple[str, str, int | None]]:
    out = [("szűrés nélkül", "none", None), ("(d) csak Sol", "d", None)]
    for name in ("a", "b", "c"):
        out += [(f"({name}) vol > 0", name, 1), (f"({name}) vol ≥ 10", name, 10),
                (f"({name}) vol ≥ 50", name, 50), (f"({name}) volumen nélkül", name, None)]
    return out


def apply(record: Mapping, evidence: Sequence[Evidence], keep: Callable[[Evidence], bool]
          ) -> dict:
    dropped = {e.key for e in evidence if not keep(e)}
    return {**record, "entities": [raw for raw in record.get("entities") or []
                                   if alias_key(raw["canonical_name"]) not in dropped]}


# ---------------------------------------------------------------------------
# jelentés
# ---------------------------------------------------------------------------

OVERVIEW_FIELDS = ("oldal", "entitás", "típus / altípus", "forrás", "modell nevei", "felismerve",
                   "elnevezés", "verdikt", "Wikipedia", "Wikipedia URL", "Wikipedia nyelv",
                   "átirányítás", "Wikidata", "QID", "Wikidata egyezés", "Google KG",
                   "KG-azonosító", "volumen HU", "volumen EN")


def overview_record(row: Row) -> dict[str, str]:
    wp, wd, kg = row.wikipedia, row.wikidata, row.kg

    def vol(market):
        value = row.volume.get(market, "–")
        return "–" if value == "–" else ("nincs adat" if value is None else str(value))

    return {
        "oldal": row.page_id, "entitás": row.canonical,
        "típus / altípus": f"{row.type or '–'} / {row.subtype or '–'}",
        "forrás": row.source, "modell nevei": "; ".join(g.canonical for g in row.models) or "–",
        "felismerve": row.recognized, "elnevezés": row.naming,
        "verdikt": "; ".join(row.verdicts) or "–",
        "Wikipedia": "igen" if wp else "nem", "Wikipedia URL": wp["url"] if wp else "",
        "Wikipedia nyelv": wp["lang"] if wp else "",
        "átirányítás": ("igen" if wp["redirect"] else "nem") if wp else "",
        "Wikidata": "igen" if wd else "nem", "QID": wd["id"] if wd else "",
        "Wikidata egyezés": f"{wd['match']} ({wd['lang']})" if wd else "",
        "Google KG": kg["status"] if kg else "–", "KG-azonosító": (kg or {}).get("id") or "",
        "volumen HU": vol("hu"), "volumen EN": vol("en"),
    }


def markdown_table(records: Sequence[Mapping[str, str]], fields: Sequence[str]) -> list[str]:
    def cell(value):
        return str(value).replace("|", "\\|")
    lines = ["| " + " | ".join(fields) + " |", "|" + "---|" * len(fields)]
    lines += ["| " + " | ".join(cell(r[f]) for f in fields) + " |" for r in records]
    return lines


def _pct(part: int, whole: int) -> str:
    return f"{se.pct_of(part, whole)} ({part}/{whole})"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["volume", "report"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--source", default="cp-g")
    parser.add_argument("--gate", default="cp-g-e1")
    parser.add_argument("--luna", default="cp-g-e2-luna")
    parser.add_argument("--sol", default="cp-g-e2-sol")
    parser.add_argument("--pages-dir", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    model = args.model or load_config().pipeline["extraction"]
    pages = ge.load_real_pages(args.pages_dir)
    verdicts = ge.verdict_index(ge.load_verdicts())
    records = {p["page_id"]: ge.read_record(args.data_dir, p["page_id"], model, args.source)
               for p in pages}
    rows = {p["page_id"]: page_rows(p, records[p["page_id"]], verdicts) for p in pages}
    keywords = market_keywords(pages, rows)
    volumes = fetch_volumes(keywords, args.data_dir / "volume")
    if args.command == "volume":
        for market, entry in volumes.items():
            print(f"{market}: {len(entry['keywords'])} kulcsszó, feladat {entry['task_id']}, "
                  f"{entry.get('cost')} USD")
        return
    from tests.acceptance.evidence_report import write_report
    con = connect(args.data_dir / "gate.duckdb")
    try:
        api = _Api(con, None, httpx.Client(timeout=20.0), Retry(),
                   lambda: datetime.now(UTC).replace(tzinfo=None), time.monotonic)
        kg_key = api_key(KG_KEY_ENV)
        for page in pages:
            lookup_rows(rows[page["page_id"]], page.get("lang") or "en", api, kg_key)
            for row in rows[page["page_id"]]:
                for market, entry in volumes.items():
                    if market == "hu" and base_language(page.get("lang")) != "hu":
                        continue
                    row.volume[market] = volume_of(row.names, entry.get("results"))
    finally:
        con.close()
    args.out.mkdir(parents=True, exist_ok=True)
    for page in pages:
        path = args.out / f"entities_overview_{page['page_id']}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, OVERVIEW_FIELDS)
            writer.writeheader()
            writer.writerows(overview_record(r) for r in rows[page["page_id"]])
    evidence = {}
    for page in pages:
        pid = page["page_id"]
        path = ge.decisions_path(args.data_dir, pid, model, args.gate)
        decisions = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        evidence[pid] = evidence_rows(
            page, records[pid], decisions,
            ge.read_record(args.data_dir, pid, model, args.luna),
            ge.read_record(args.data_dir, pid, model, args.sol), volumes, verdicts)
    out = write_report(args, model, pages, records, rows, evidence, volumes, verdicts)
    print(out)


if __name__ == "__main__":
    main()
