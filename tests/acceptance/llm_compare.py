"""Párhuzamos modellteszt: ugyanaz a prompt, séma és bemenet mindhárom modellnek, 30 oldalon a
három rögzített készletből (`SAMPLE`); a kimenetek egymás mellett a `page_entities`-ben, az
`llm_call_id` különbözteti meg őket.

    python -m tests.acceptance.llm_compare run [--providers gemini,openai,anthropic]
                                               [--missing-only]
    python -m tests.acceptance.llm_compare report

`run` (élő, költséges): készletenként a felvett crawl visszajátszása egy fájl-adatbázisba
(`data/compare/<készlet>.duckdb`, egyszer) és a szabály-kör; modellenként az LLM-kör a mintán;
végül a validálás (KG és Wikipedia, a site- és a shared-cache-sel). A modellek nyers kimenete,
az eldobott sorokkal együtt, `data/compare/<készlet>.<szolgáltató>.jsonl`-be kerül, oldalanként
egy sor (a hiba is).

`report` (hálózat nélkül) a `tests/acceptance/out/llm-compare/` alá ír:

- `report.md`, modellenként:
  1. fabrikáció: a fabrikált (evidence nincs az oldalon) sorok aránya a visszaadott összeshez;
  2. egyezés: páronkénti Jaccard az (oldal, entitás) párokon, és a három metszete; az entitás az
     összevonás utáni azonosító, azaz kis-nagybetű, ékezet és alias szerint egyeztetett név;
  3. KG-találati arány: a modell sorai mögötti entitások közül high + medium;
  4. USD/oldal, tokenek, medián késleltetés, újrapróbált hívások (`attempts` > 1, `last_error`),
     sikertelen oldalak (séma-eltérés, hívás-hiba).
  A költség mért adat, nem döntési feltétel.
- `spotcheck.md`: `SPOTCHECK` öt oldala, a három kimenet egymás mellett, az eldobott sorokkal, a
  kézi szúrópróbához (mi hiányzik, ami a szövegben ott van; mi felesleges).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

import duckdb

from aaa2.db.connect import DATA_DIR, connect, shared_path
from aaa2.entities.llm import check_evidence, normalize_text, run_llm
from aaa2.entities.rules import alias_key, run_rules
from aaa2.entities.validate import RECOGNIZED, validate_entities
from aaa2.llm.client import LLMError, SchemaMismatch, open_clients

PROVIDERS = ("gemini", "openai", "anthropic")
LABELS = {"gemini": "Gemini", "openai": "OpenAI", "anthropic": "Anthropic"}
OUT_DIR = Path(__file__).parent / "out" / "llm-compare"
SPOT_TEXT_CHARS = 6000

# Készletenként 10 oldal, típus szerint vegyesen. home: kezdőoldal (nyelvenként); service /
# product: szolgáltatás-, étlap- és borlap-, komponens-dokumentáció; article: blog, esettanulmány;
# other: rólam, kapcsolat, adatkezelés, köszönőoldal, API-referencia, példák.
SAMPLE: dict[str, list[tuple[str, str]]] = {
    "kk-coach-crawl": [
        ("https://kk.coach/", "home"),
        ("https://kk.coach/hu/", "home"),
        ("https://kk.coach/solutions/seo/", "service"),
        ("https://kk.coach/solutions/geo-ai-visibility/", "service"),
        ("https://kk.coach/hu/megoldasok/meres/", "service"),
        (("https://kk.coach/organic-clicks-dropped-by-14-while-rankings-stayed-stable-a-b2b-saas"
          "-case-study-on-the-impact-of-ai-search/"), "article"),
        ("https://kk.coach/hu/miert-epitenek-sajat-serp-figyelot/", "article"),
        (("https://kk.coach/hu/az-ai-search-nem-csak-seo-problema-a-b2b-vasarloi-ut-mashova-"
          "koltozik/"), "article"),
        ("https://kk.coach/about/", "other"),
        ("https://kk.coach/hu/kapcsolat/", "other"),
    ],
    "materia-crawl": [
        ("https://materia-tm.com/", "home"),
        ("https://materia-tm.com/hu/", "home"),
        ("https://materia-tm.com/it/", "home"),
        ("https://materia-tm.com/menu/", "product"),
        ("https://materia-tm.com/hu/etlap/", "product"),
        ("https://materia-tm.com/it/menu/", "product"),
        ("https://materia-tm.com/wine-list/", "product"),
        ("https://materia-tm.com/hu/borlap/", "product"),
        ("https://materia-tm.com/privacy-policy/", "other"),
        ("https://materia-tm.com/hu/thank-you/", "other"),
    ],
    "ngx-bootstrap-crawl": [
        ("https://valor-software.com/ngx-bootstrap/components", "home"),
        ("https://valor-software.com/ngx-bootstrap/components/accordion", "product"),
        ("https://valor-software.com/ngx-bootstrap/components/alerts", "product"),
        ("https://valor-software.com/ngx-bootstrap/components/buttons", "product"),
        ("https://valor-software.com/ngx-bootstrap/components/collapse", "product"),
        ("https://valor-software.com/ngx-bootstrap/components/modals", "product"),
        ("https://valor-software.com/ngx-bootstrap/components/pagination", "product"),
        ("https://valor-software.com/ngx-bootstrap/components/tooltip?tab=api", "other"),
        ("https://valor-software.com/ngx-bootstrap/components/rating?tab=api", "other"),
        ("https://valor-software.com/ngx-bootstrap/components/progressbar?tab=examples", "other"),
    ],
}
SPOTCHECK: list[tuple[str, str]] = [
    ("kk-coach-crawl", "https://kk.coach/"),
    ("kk-coach-crawl", "https://kk.coach/hu/megoldasok/meres/"),
    ("kk-coach-crawl", "https://kk.coach/hu/miert-epitenek-sajat-serp-figyelot/"),
    ("materia-crawl", "https://materia-tm.com/hu/etlap/"),
    ("ngx-bootstrap-crawl", "https://valor-software.com/ngx-bootstrap/components/accordion"),
]


# ---------------------------------------------------------------------------
# futás
# ---------------------------------------------------------------------------


class Recorder:
    """Az LLM-kliens, a nyers kimenet (és a hiba) oldalankénti rögzítésével."""

    def __init__(self, client, provider: str):
        self.client, self.provider = client, provider
        self.records: list[dict] = []

    @property
    def model(self) -> str:
        return self.client.model

    def extract(self, schema, prompt, input, **kwargs):
        record = {"provider": self.provider, "model": self.model, "page_id": kwargs.get("page_id"),
                  "call_id": None, "entities": None, "error": None}
        self.records.append(record)
        try:
            result = self.client.extract(schema, prompt, input, **kwargs)
        except SchemaMismatch as exc:
            record.update(call_id=exc.call_id, error=f"schema_mismatch: {exc}"[:500])
            raise
        except LLMError as exc:
            record.update(error=f"call_error: {exc}"[:500])
            raise
        record.update(call_id=result.call_id,
                      entities=[entity.model_dump() for entity in result.parsed.entities])
        return result


def sample_page_ids(con: duckdb.DuckDBPyConnection, name: str) -> dict[str, int]:
    """A minta URL-jei → page_id; hiányzó URL-nél kivétel."""
    urls = [url for url, _ in SAMPLE[name]]
    found = dict(con.execute("SELECT url, page_id FROM pages WHERE list_contains(?, url)",
                             [urls]).fetchall())
    missing = [url for url in urls if url not in found]
    if missing:
        raise SystemExit(f"{name}: a minta URL-je nincs a készletben: {missing}")
    return {url: found[url] for url in urls}


def prepare(name: str, data_dir: Path) -> Path:
    """A felvett crawl visszajátszva `data_dir/<név>.duckdb`-be, a szabály-körrel; egyszer."""
    from tests.recorded import REFERENCE_SETS, replay_crawl

    path = data_dir / f"{name}.duckdb"
    if path.exists():
        return path
    data_dir.mkdir(parents=True, exist_ok=True)
    seed, options = REFERENCE_SETS[name]
    replayed = asyncio.run(replay_crawl(name, seed, options))
    if replayed is None:
        raise SystemExit(f"nincs felvétel: {name}")
    memory = replayed[1]
    memory.execute(f"ATTACH '{path.as_posix()}' AS target")
    memory.execute("COPY FROM DATABASE memory TO target")
    memory.execute("DETACH target")
    memory.close()
    con = connect(path)
    run_rules(con)
    con.close()
    return path


def run(providers: list[str], data_dir: Path, missing_only: bool = False) -> None:
    """`missing_only`: modellenként csak a még kimenet nélküli mintaoldalak; a meglévő
    kimenetek maradnak."""
    shared = connect(shared_path())
    try:
        for name in SAMPLE:
            con = connect(prepare(name, data_dir))
            ids = sample_page_ids(con, name)
            clients, skipped = open_clients(con)
            for provider in providers:
                if provider not in clients:
                    print(f"{name}: {provider} kihagyva ({skipped.get(provider)})")
                    continue
                path = data_dir / f"{name}.{provider}.jsonl"
                kept = [r for r in read_jsonl(path) if r["entities"] is not None] \
                    if missing_only else []
                done = {r["page_id"] for r in kept}
                todo = [page_id for page_id in ids.values() if page_id not in done]
                if not todo:
                    print(f"{name}: {provider} mind a {len(ids)} oldalon van kimenet")
                    continue
                recorder = Recorder(clients[provider], provider)
                result = run_llm(con, recorder, page_ids=todo)
                write_jsonl(path, kept + recorder.records)
                print(f"{name}: {provider} {result.model}: {result.pages} oldal, "
                      f"{result.rows} sor, fabrikált {result.fabricated}, "
                      f"{result.cost_usd:.4f} USD, kimaradt {result.skipped}")
            validation = validate_entities(con, shared)
            print(f"{name}: validálás {validation.statuses}, navigációs {validation.navigational}, "
                  f"hiba {validation.errors}")
            con.close()
    finally:
        shared.close()


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                    encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------------------
# mérés
# ---------------------------------------------------------------------------


@dataclass
class ModelStats:
    """Egy modell mérése. Az oldalhoz kötött számok oldalanként, hogy az összevetés a közös
    oldalakra szűkíthető legyen (`pages` paraméter: (készlet, page_id) halmaz, None = mind)."""

    provider: str
    model: str = ""
    pages: int = 0                  # a minta oldalai, amelyekre hívás indult
    # (készlet, page_id) → [visszaadott sor, fabrikált, 3–15 szón kívüli evidence]; csak a
    # séma szerinti kimenettel rendelkező oldalak
    per_page: dict = field(default_factory=dict)
    fabricated_logged: int = 0      # llm_calls.fabricated_count összege (ellenőrzés)
    pairs: set = field(default_factory=set)          # (készlet, page_id, entity_id)
    kg_status: dict = field(default_factory=dict)    # (készlet, entity_id) → kg_status
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    latencies: list = field(default_factory=list)
    retried: int = 0
    retry_errors: Counter = field(default_factory=Counter)
    failures: Counter = field(default_factory=Counter)

    @property
    def answered(self) -> set:
        return set(self.per_page)

    def counts(self, pages: set | None = None) -> tuple[int, int, int]:
        keys = [k for k in self.per_page if pages is None or k in pages]
        return tuple(sum(self.per_page[k][i] for k in keys) for i in range(3))

    def pairs_on(self, pages: set | None = None) -> set:
        return {p for p in self.pairs if pages is None or p[:2] in pages}

    def entities_on(self, pages: set | None = None) -> set:
        return {(name, entity_id) for name, _, entity_id in self.pairs_on(pages)}

    def recognized_on(self, pages: set | None = None) -> set:
        return {k for k in self.entities_on(pages) if self.kg_status.get(k) in RECOGNIZED}

    def fabrication_rate(self, pages: set | None = None) -> float | None:
        returned, fabricated, _ = self.counts(pages)
        return fabricated / returned if returned else None

    def kg_rate(self, pages: set | None = None) -> float | None:
        entities = self.entities_on(pages)
        return len(self.recognized_on(pages)) / len(entities) if entities else None


def page_sources(con: duckdb.DuckDBPyConnection, page_id: int) -> list[str]:
    """Az evidence-ellenőrzés forrásai, ahogy az LLM-kör látja: main content, title, headingek."""
    title, main = con.execute("SELECT title, main_content FROM pages WHERE page_id = ?",
                              [page_id]).fetchone()
    headings = [text for (text,) in con.execute(
        "SELECT text FROM headings WHERE page_id = ? ORDER BY ordinal", [page_id]).fetchall()]
    return [normalize_text(main), normalize_text(title), *(normalize_text(h) for h in headings)]


def collect(con: duckdb.DuckDBPyConnection, name: str, records: list[dict],
            stats: ModelStats) -> None:
    """Egy készlet egy modelljének rekordjai a `stats`-ba."""
    call_ids = [r["call_id"] for r in records if r["call_id"] is not None]
    for record in records:
        stats.model = stats.model or record["model"]
        stats.pages += 1
        if record["error"]:
            stats.failures[record["error"].split(":", 1)[0]] += 1
        if record["entities"] is None:
            continue
        counts = stats.per_page.setdefault((name, record["page_id"]), [0, 0, 0])
        sources = page_sources(con, record["page_id"])
        for entity in record["entities"]:
            reason = check_evidence(_Entity(entity), sources)
            counts[0] += 1
            counts[1] += reason == "fabricated"
            counts[2] += reason == "evidence_length"
    for cost, tokens_in, tokens_out, latency, attempts, last_error, fabricated in con.execute(
        "SELECT cost_usd, tokens_in, tokens_out, latency_ms, attempts, last_error, "
        "fabricated_count FROM llm_calls WHERE list_contains(?, call_id)", [call_ids],
    ).fetchall():
        stats.cost_usd += cost or 0.0
        stats.tokens_in += tokens_in or 0
        stats.tokens_out += tokens_out or 0
        stats.latencies.append(latency or 0)
        stats.fabricated_logged += fabricated or 0
        if (attempts or 1) > 1:
            stats.retried += 1
            stats.retry_errors[last_error or "?"] += 1
    for page_id, entity_id, status in con.execute(
        "SELECT pe.page_id, pe.entity_id, e.kg_status FROM page_entities pe "
        "JOIN entities e USING (entity_id) WHERE list_contains(?, pe.llm_call_id)", [call_ids],
    ).fetchall():
        stats.pairs.add((name, page_id, entity_id))
        stats.kg_status[(name, entity_id)] = status


class _Entity:
    """A JSONL-sor az `ExtractedEntity` helyett (`check_evidence` csak az evidence-et olvassa)."""

    def __init__(self, data: dict):
        self.evidence = data.get("evidence") or ""


def jaccard(a: set, b: set) -> float | None:
    union = a | b
    return len(a & b) / len(union) if union else None


def measure(data_dir: Path, providers=PROVIDERS) -> dict[str, ModelStats]:
    stats = {provider: ModelStats(provider) for provider in providers}
    for name in SAMPLE:
        path = data_dir / f"{name}.duckdb"
        if not path.exists():
            continue
        con = duckdb.connect(str(path), read_only=True)
        try:
            for provider in providers:
                collect(con, name, read_jsonl(data_dir / f"{name}.{provider}.jsonl"),
                        stats[provider])
        finally:
            con.close()
    return stats


# ---------------------------------------------------------------------------
# kimenet
# ---------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def report_markdown(stats: dict[str, ModelStats]) -> str:
    models = [s for s in stats.values() if s.pages]
    lines = ["# Párhuzamos modellteszt (M2/5)", "",
             f"Minta: {sum(len(v) for v in SAMPLE.values())} oldal, "
             + ", ".join(f"{name} {len(pages)}" for name, pages in SAMPLE.items())
             + ". Ugyanaz a prompt, séma és bemenet mindhárom modellnek.", "",
             "A költség mért adat, nem döntési feltétel.", ""]
    header = ["| | " + " | ".join(f"{LABELS[s.provider]} (`{s.model}`)" for s in models) + " |",
              "|---|" + "---|" * len(models)]

    def row(label, values):
        lines.append(f"| {label} | " + " | ".join(values) + " |")

    def quality(pages):
        counts = [s.counts(pages) for s in models]
        row("visszaadott sor", [str(c[0]) for c in counts])
        row("**fabrikáció**", [f"**{_pct(s.fabrication_rate(pages))}** ({c[1]})"
                               for s, c in zip(models, counts, strict=True)])
        row("3–15 szón kívüli evidence", [str(c[2]) for c in counts])
        row("(oldal, entitás) pár", [str(len(s.pairs_on(pages))) for s in models])
        row("entitás", [str(len(s.entities_on(pages))) for s in models])
        row("KG high + medium", [f"{_pct(s.kg_rate(pages))} ({len(s.recognized_on(pages))})"
                                 for s in models])

    lines += ["## Modellenként, a saját megválaszolt oldalain", "", *header]
    row("oldal (hívás / kimenet)", [f"{s.pages} / {len(s.answered)}" for s in models])
    quality(None)
    row("fabrikált a naplóban (ellenőrzés)", [str(s.fabricated_logged) for s in models])
    row("USD / megválaszolt oldal", [f"{s.cost_usd / len(s.answered):.4f}" if s.answered
                                     else "—" for s in models])
    row("USD összesen", [f"{s.cost_usd:.4f}" for s in models])
    row("token be / ki hívásonként",
        [f"{s.tokens_in // max(len(s.latencies), 1)} / {s.tokens_out // max(len(s.latencies), 1)}"
         for s in models])
    row("medián késleltetés", [f"{statistics.median(s.latencies) / 1000:.1f} mp"
                               if s.latencies else "—" for s in models])
    row("újrapróbált hívás", [str(s.retried) for s in models])
    row("sikertelen oldal", [", ".join(f"{k} {v}" for k, v in sorted(s.failures.items())) or "0"
                             for s in models])
    common = set.intersection(*(s.answered for s in models)) if models else set()
    lines += ["", (f"## Összevetés a közös oldalakon ({len(common)} oldal: mindegyik modell "
                   "adott kimenetet)"), "", *header]
    quality(common)
    lines += ["", "## Egyezés (Jaccard az (oldal, entitás) párokon)", "",
              ("Páronként a két modell közös megválaszolt oldalain; a hármas metszet a fenti "
               "közös oldalakon."), ""]
    for a, b in combinations(models, 2):
        pages = a.answered & b.answered
        pa, pb = a.pairs_on(pages), b.pairs_on(pages)
        value = jaccard(pa, pb)
        lines.append(f"- {LABELS[a.provider]} – {LABELS[b.provider]}, {len(pages)} oldal: "
                     f"{'—' if value is None else f'{value:.3f}'} "
                     f"(közös {len(pa & pb)}, együtt {len(pa | pb)})")
    if len(models) == 3:
        sets = [s.pairs_on(common) for s in models]
        both, union = set.intersection(*sets), set.union(*sets)
        lines.append(f"- mindhárom, {len(common)} oldal: közös {len(both)}, együtt {len(union)} "
                     f"({_pct(len(both) / len(union) if union else None)})")
    errors = [(s, error, count) for s in models for error, count in s.retry_errors.items()]
    if errors:
        lines += ["", "## Újrapróbák utolsó hibája", ""]
        lines += [f"- {LABELS[s.provider]}: {error} ×{count}" for s, error, count in errors]
    lines += ["", "## A minta", ""]
    for name, pages in SAMPLE.items():
        lines.append(f"- {name}:")
        lines += [f"  - {kind}: {url}" for url, kind in pages]
    return "\n".join(lines) + "\n"


def spotcheck_markdown(data_dir: Path, providers=PROVIDERS) -> str:
    lines = ["# Szúrópróba: öt oldal, három kimenet", "",
             ("Oldalanként a modellek sorai egy táblában, a név kulcsa (kis-nagybetű, ékezet "
              "nélkül) szerint egymás mellett: a modell által adott típus, vagy „—”, ha nem adta. "
              "A fabrikált sorokat (az evidence nincs az oldalon) a kód eldobja; itt áthúzva "
              "szerepelnek."), "",
             "Kérdés oldalanként: mi hiányzik, ami a szövegben ott van; mi felesleges.", ""]
    for name, url in SPOTCHECK:
        path = data_dir / f"{name}.duckdb"
        if not path.exists():
            continue
        con = duckdb.connect(str(path), read_only=True)
        try:
            page_id, lang, words, main = con.execute(
                "SELECT page_id, lang, word_count, main_content FROM pages WHERE url = ?",
                [url]).fetchone()
            sources = page_sources(con, page_id)
        finally:
            con.close()
        outputs = {}
        for provider in providers:
            record = next((r for r in read_jsonl(data_dir / f"{name}.{provider}.jsonl")
                           if r["page_id"] == page_id), None)
            outputs[provider] = record
        by_key: dict[str, dict[str, list[tuple[str, str, bool]]]] = defaultdict(dict)
        names: dict[str, str] = {}
        for provider, record in outputs.items():
            for entity in (record or {}).get("entities") or []:
                key = alias_key(entity["name"])
                names.setdefault(key, entity["name"])
                fabricated = check_evidence(_Entity(entity), sources) == "fabricated"
                by_key[key].setdefault(provider, []).append(
                    (entity["type"], entity["evidence"], fabricated))
        lines += [f"## {url}", "", f"Nyelv: {lang}, {words} szó.", ""]
        present = [p for p in providers if (outputs[p] or {}).get("entities") is not None]
        for provider in providers:
            record = outputs[provider]
            if record is None:
                lines.append(f"- {LABELS[provider]}: nem futott")
            elif record["error"]:
                lines.append(f"- {LABELS[provider]}: hiba — {record['error']}")
            else:
                lines.append(f"- {LABELS[provider]} (`{record['model']}`): "
                             f"{len(record['entities'])} sor")
        lines += ["", "| név | " + " | ".join(LABELS[p] for p in present) + " | evidence |",
                  "|---|" + "---|" * len(present) + "---|"]
        for key in sorted(by_key, key=lambda k: (-len(by_key[k]), names[k].casefold())):
            cells, evidence = [], ""
            for provider in present:
                found = by_key[key].get(provider)
                if not found:
                    cells.append("—")
                    continue
                kind, quote, fabricated = found[0]
                cells.append(f"~~{kind}~~" if fabricated else kind)
                evidence = evidence or quote
            lines.append(f"| {names[key]} | " + " | ".join(cells) + f" | {_cell(evidence)} |")
        lines += ["", "<details><summary>Az oldal szövege (main content, az eleje)</summary>", "",
                  "```text", (main or "")[:SPOT_TEXT_CHARS], "```", "", "</details>", "",
                  "Hiányzik: ", "", "Felesleges: ", ""]
    return "\n".join(lines)


def _cell(text: str) -> str:
    return " ".join((text or "").split()).replace("|", "\\|")


def report(data_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text(report_markdown(measure(data_dir)), encoding="utf-8")
    (out_dir / "spotcheck.md").write_text(spotcheck_markdown(data_dir), encoding="utf-8")
    print(f"{out_dir / 'report.md'}\n{out_dir / 'spotcheck.md'}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["run", "report"])
    parser.add_argument("--providers", default=",".join(PROVIDERS))
    parser.add_argument("--missing-only", action="store_true",
                        help="csak a még kimenet nélküli mintaoldalak (run)")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    if args.command == "run":
        providers = [p for p in args.providers.split(",") if p]
        unknown = set(providers) - set(PROVIDERS)
        if unknown:
            parser.error(f"ismeretlen szolgáltató: {sorted(unknown)}")
        run(providers, args.data_dir, args.missing_only)
    report(args.data_dir, args.out)


if __name__ == "__main__":
    main()
