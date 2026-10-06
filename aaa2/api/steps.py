"""Az API site-onként hívható lépései: `crawl`, `extract`, `resolve`, `build_graph`, `find`, és
a hozzájuk tartozó kimenetek (`export_graph`, `export_findings`, `entity_report`), a validálás
(`validate`) és a modellellenőrzés (`check_models`).

A lépések a site adatbázisán dolgoznak (`Site`, lásd `open_site`), és eredményobjektumot adnak
vissza; a felhasználónak szánt folyamatjelzést a hívó `notify` függvénye kapja soronként. A
kezelhető hibák `ApiError`-ként jönnek, a parancssor kilépési kódjával."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx

from aaa2.api.site import ApiError, GraphMissing, Site
from aaa2.db.connect import connect, shared_path
from aaa2.engine import queries as crawl_queries
from aaa2.engine.crawl import CrawlOptions, CrawlSummary, run_crawl, run_sitemap_refetch
from aaa2.engine.frontier import MAX_PAGES, record_missing_mode, restore_sitemap_from_queue
from aaa2.engine.normalize import UrlPolicy
from aaa2.engine.render import CONCURRENCY, RENDER_TIMEOUT
from aaa2.entities.dom import build_blocks
from aaa2.entities.extract import (
    Restored,
    Worker,
    clear_entities,
    estimate_llm,
    input_models,
    restore_llm,
    restore_plan,
    run_llm,
)
from aaa2.entities.gate import KnowledgeBase
from aaa2.entities.report import run_report, write_entity_table
from aaa2.entities.rules import run_rules
from aaa2.entities.v3 import V3Step, load_pipeline, v3_fingerprint
from aaa2.functions import facts as facts_module
from aaa2.functions import findings as findings_module
from aaa2.functions import graph as graph_module
from aaa2.functions import graph_queries
from aaa2.functions.findings import FindingsRun
from aaa2.functions.graph import GraphRun
from aaa2.llm.client import ModelCheck, Retry, open_clients
from aaa2.llm.client import check_models as _check_models
from aaa2.llm.config import PIPELINE_OFF, load_config, load_site_credentials
from aaa2.resolver.knowledge import KnowledgeRun, link_entities
from aaa2.resolver.merge import clear_resolution
from aaa2.resolver.overrides import load_site_config
from aaa2.resolver.pages import entity_page_ids, page_roles
from aaa2.resolver.site import SiteRun, run_site
from aaa2.resolver.validate import ValidationRun, _Api, validate_entities

Notify = Callable[[str], None]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _quiet(_: str) -> None:
    return None


# --- crawl ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class CrawlResult:
    """A crawl eredménye: az összesítő, az adatbázis útvonala, a tényleges beállítás, és hogy a
    site-fájl adott-e include- vagy exclude-mintát."""

    summary: CrawlSummary
    path: Path
    options: CrawlOptions
    site_file_scope: bool


def crawl(url: str, *, sitemap: str | None = None, max_pages: int | None = None,
          concurrency: int | None = None, render_timeout: float | None = None,
          respect_robots: bool = True, resume: bool = False, include: str | None = None,
          exclude: str | None = None, sitemap_only: bool | None = None,
          progress: Callable[[str, int | None, str | None], None] | None = None,
          on_options: Callable[[CrawlOptions, bool], None] | None = None) -> CrawlResult:
    """Egy site sitewide crawlja Playwright-renderrel a `data/<domain>.duckdb`-be. Az include,
    az exclude, a párhuzamosság, a render-időkorlát, az oldalkorlát (`max_pages`) és a
    sitemap-mód (`sitemap_only`) alapja a site-fájl `[crawl]` része
    (`aaa2/core/sites/<domain>.toml`), ha van; a megadott paraméter felülírja, a site-fájl
    hiányzó értéke helyett az alapérték áll. `progress`:
    oldalanként (URL, státusz, hiba); `on_options`: a crawl indulása előtt a tényleges beállítás.
    Hibás site-fájl vagy seed: `ApiError`."""
    try:
        site = load_site_config(UrlPolicy.from_seed(url).domain).crawl
    except ValueError as exc:
        raise ApiError(f"hiba: {exc}") from exc
    options = CrawlOptions(
        sitemap=sitemap, max_pages=max_pages or site.max_pages or MAX_PAGES,
        sitemap_only=site.sitemap_only if sitemap_only is None else sitemap_only,
        concurrency=concurrency or site.concurrency or CONCURRENCY,
        render_timeout=render_timeout or site.render_timeout or RENDER_TIMEOUT,
        respect_robots=respect_robots, resume=resume,
        include=include or site.include, exclude=exclude or site.exclude_pattern,
    )
    scoped = bool(site.include or site.exclude)
    if on_options is not None:
        on_options(options, scoped)
    try:
        summary, path = asyncio.run(run_crawl(url, options, progress=progress))
    except ValueError as exc:
        raise ApiError(f"hiba: {exc}") from exc
    return CrawlResult(summary, path, options, scoped)


# --- sitemap -------------------------------------------------------------------------------

@dataclass(frozen=True)
class SitemapResult:
    """A sitemap pótlása egy tárolt crawlhoz: a sorból visszaállított címek száma (`restored`),
    hány futás kapott módot (`modes`), és ha volt lekérés: a forrás, a fájlok és a címek száma."""

    restored: int
    modes: int
    source: str | None = None
    files: int = 0
    urls: int = 0


def sitemap(site: Site, *, fetch: bool = True, sitemap_url: str | None = None) -> SitemapResult:
    """A sitemap tényeinek pótlása egy korábbi crawlhoz, oldal-crawl nélkül. (1) Ha a crawl
    idejéről nincs tárolt sitemap, a crawl-sorból visszaállítja, mely címek jöttek a sitemapből.
    (2) A módot nem rögzítő futások módja a site-fájl `[crawl] sitemap_only` beállításából.
    (3) `fetch`: a mai sitemap lekérése (néhány HTTP-kérés a sitemap-fájlokra) külön
    pillanatképbe, a lekérés idejével; `fetch=False`, ha a site azóta megváltozott, és a mai
    sitemap nem a tárolt crawlhoz tartozik."""
    con = site.con
    domain = site.domain
    sitemap_only = load_site_config(domain).crawl.sitemap_only if domain else False
    restored = restore_sitemap_from_queue(con)
    modes = record_missing_mode(con, sitemap_only)
    if not fetch:
        return SitemapResult(restored, modes)
    try:
        source, files, urls = asyncio.run(run_sitemap_refetch(con, sitemap_url))
    except ValueError as exc:
        raise ApiError(f"hiba: {exc}") from exc
    return SitemapResult(restored, modes, source, files, urls)


# --- extract -------------------------------------------------------------------------------

@dataclass
class ExtractResult:
    """A kinyerés eredménye: a futások azonosítói (szabálykör, LLM-kör) sorrendben;
    `estimate_only`: csak költségbecslés készült, a pipeline nem futott tovább."""

    run_ids: list[int] = field(default_factory=list)
    estimate_only: bool = False
    restored: Restored | None = None


def _step_model(value: str) -> str | None:
    return None if value in (PIPELINE_OFF, "none") else value


def _pipeline_client(con: duckdb.DuckDBPyConnection, model: str, credentials=None):
    provider = load_config().provider_of(model)
    if provider is None:
        raise ApiError(f"a {model} nincs a konfigurált modellek között")
    clients, skipped = open_clients(con, models={provider: model}, credentials=credentials)
    if provider not in clients:
        raise ApiError(f"nincs {model}-kliens: {skipped.get(provider, 'ismeretlen')}")
    return clients[provider]


def _knowledge(con: duckdb.DuckDBPyConnection, shared: duckdb.DuckDBPyConnection | None
               ) -> KnowledgeBase:
    return KnowledgeBase(_Api(con, shared, httpx.Client(timeout=20.0), Retry(), _utcnow,
                              time.monotonic).get)


def _site_language(con: duckdb.DuckDBPyConnection) -> str | None:
    profile = crawl_queries.site(con)
    return ((profile.languages if profile else None) or [None])[0]


def extract(site: Site, *, llm: bool | None = None, knowledge: bool | None = None,
            extraction_model: str | None = None, verify_model: str | None = None,
            limit: int | None = None, resume: bool = False, estimate: bool = False,
            max_usd: float | None = None, workers: int | None = None, fresh: bool = False,
            notify: Notify | None = None) -> ExtractResult:
    """Kinyerés a `pipeline.toml` lépéseivel: a hiányzó blokkok, a determinisztikus szabálykör,
    és ha be van kapcsolva (`llm`, alapból a `pipeline.toml` `extraction`), oldalanként az
    LLM-kinyerés a saját ajánlatok ellenőrzésével és a fogalmak bizonyítékaival. Az LLM-kör
    előtt költségbecslés megy a `notify`-nak; `estimate`: csak a becslés készül el. A becslés a
    költséghatár (`max_usd`, alapból a `pipeline.toml` `max_usd`) fölött: `ApiError` 2-es kóddal.
    `knowledge`: a tudásbázis-egyezés a fogalmak bizonyítékaihoz (alapból a `pipeline.toml`
    `knowledge`)."""
    con = site.con
    notify = notify or _quiet
    pipeline = load_pipeline()
    steps = pipeline.steps
    cap = max_usd if max_usd is not None else pipeline.max_usd
    use_llm = steps.extraction if llm is None else llm
    use_knowledge = steps.knowledge if knowledge is None else knowledge
    result = ExtractResult()
    if steps.blocks:
        build_blocks(con)
    if not use_llm:
        if estimate:
            notify("becslés: az LLM-lépések kikapcsolva, nincs hívás")
            result.estimate_only = True
            return result
        rules_run, result.restored = _project(con, steps)
        if rules_run is not None:
            result.run_ids.append(rules_run)
        return result
    if not estimate and steps.rules:           # a kinyerés utáni lépés a szabálykör entitásait látja
        run_rules(con, entity_pages=entity_page_ids(page_roles(con)))
    site_lang = _site_language(con)
    shared = connect(shared_path()) if use_knowledge and not estimate else None
    kb = _knowledge(con, shared) if shared is not None else None
    try:
        config = load_config()
        models = config.pipeline
        chosen = extraction_model or models["extraction"]
        verify_choice = (_step_model(verify_model or models["verify"])
                         if steps.services else None)
        refines = steps.services or steps.concepts
        reuse_models = None if fresh else input_models(
            chosen, v3_fingerprint(steps, verify_choice) if refines else None)
        guess = estimate_llm(con, config, chosen, verify_choice, _utcnow().date(), limit=limit,
                             resume=resume, reuse_models=reuse_models)
        notify(
            f"becslés: {guess.pages} oldal, {guess.chunks} kinyerő darab, ~{guess.tokens_in} "
            f"token be; kinyerés {guess.extract_usd:.4f} USD, ellenőrzés "
            f"{guess.verify_usd:.4f} USD ({guess.verify_pages} oldal a meglévő kinyerésből"
            + (f", {guess.verify_pending} oldalon a kinyerés után dől el"
               if guess.verify_pending else "")
            + f"); összesen {guess.total_usd:.4f} USD, határ {cap:.2f} USD"
            + (f"; változatlan, a korábbi kinyerésével: {guess.reused} oldal"
               if guess.reused else ""))
        if estimate:
            result.estimate_only = True
            return result
        if guess.total_usd > cap:
            raise ApiError("a becslés a határ fölött van, a futás nem indul", code=2)
        try:
            credentials = load_site_credentials(site.domain)
        except ValueError as exc:
            raise ApiError(f"hiba: {exc}") from exc
        for name, own in sorted(credentials.items()):
            notify(f"site-kulcs: {name}: {own.key_env or 'alapkulcs'}"
                   + (f", projekt {own.project}" if own.project else ""))
        client = _pipeline_client(con, chosen, credentials)
        verifier = None
        if verify_choice:
            verifier = (client if verify_choice == client.model
                        else _pipeline_client(con, verify_choice, credentials))
        refine = (V3Step(con, steps, verifier, kb if steps.knowledge else None, site_lang)
                  if steps.services or steps.concepts else None)

        def fork(cursor: duckdb.DuckDBPyConnection) -> Worker:
            """Egy szál kliensei a saját kurzorán (a tudásbázis gyorsítótára is)."""
            bound = client.bind(cursor)
            own = {id(client): bound}
            thread_kb = None
            if kb is not None and steps.knowledge:
                thread_kb = _knowledge(cursor, shared.cursor() if shared is not None else None)
            verifier_bound = (own.get(id(verifier))
                              or (verifier.bind(cursor) if verifier else None))
            return Worker(bound, V3Step(
                cursor, steps, verifier_bound, thread_kb, site_lang) if refine else None)

        count = workers or pipeline.workers
        notify(f"párhuzamosság: {count} oldal egyszerre")
        llm_run = run_llm(con, client, refine=refine, save=steps.save, limit=limit,
                          resume=resume, max_usd=cap, workers=count, fork=fork,
                          reuse=not fresh, records_only=True).run_id
        rules_run, result.restored = _project(con, steps)
        result.run_ids += [run_id for run_id in (rules_run, llm_run) if run_id is not None]
    finally:
        if shared is not None:
            shared.close()
    return result


RESTORE_LOSS_LIMIT = 0.10       # a rekorddal bíró oldalak ekkora hányada maradhat ki


def _project(con: duckdb.DuckDBPyConnection, steps) -> tuple[int | None, Restored | None]:
    """Az entitás-táblák levezetése a tárolt kinyerésből: üríti az entitás-, az említés-, a
    bizonyíték-, az alias-, a kapcsolat- és az összevonás-táblákat, a gráfot és a
    megállapításokat; utána a szabálykör és a tárolt kinyerés visszaírása (`restore_llm`). Az
    `aaa entities` minden futása így végződik, ezért a végállapot nem függ a korábbi
    futásoktól.

    Ürítés előtt megszámolja, hány oldal írható vissza (`restore_plan`): ha a tárolt rekorddal
    bíró oldalak több mint `RESTORE_LOSS_LIMIT` hányada kimaradna (a blokkjaik a kinyerés óta
    megváltoztak), `ApiError`-ral megáll, és az adatbázishoz nem nyúl. Visszaad: (a szabálykör
    futása, a visszaírás)."""
    plan = restore_plan(con)
    recorded = [status for status, stored, _ in plan.values()
                if stored is not None and status != "no_content_blocks"]
    changed = recorded.count("restore_input_changed")
    if changed > RESTORE_LOSS_LIMIT * len(recorded):
        raise ApiError(
            f"a tárolt kinyerés {len(recorded)} oldalából {changed} nem írható vissza (a "
            "blokkjaik a kinyerés óta megváltoztak); a futás megáll, az entitás-táblák "
            "érintetlenek. Újrakinyerés kell: aaa entities --llm", code=3)
    con.begin()
    try:
        findings_module.clear_findings(con)
        graph_module.clear_graph(con)
        clear_resolution(con)
        clear_entities(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    rules_run = (run_rules(con, entity_pages=entity_page_ids(page_roles(con))).run_id
                 if steps.rules else None)
    return rules_run, restore_llm(con, plan=plan)


# --- resolve -------------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolveResult:
    """A feloldás eredménye: a site-kör (`site_run`; None, ha a lépés ki van kapcsolva) és a
    tudásbázis-kapcsolás (`linked`; None, ha nem futott)."""

    site_run: SiteRun | None
    linked: KnowledgeRun | None


def resolve(site: Site, *, knowledge: bool | None = None) -> ResolveResult:
    """Feloldás LLM nélkül: a site-szintű entitások (oldalhoz kötés, csomagok, lépések,
    összevonás, demó- és sablonjelölés), utána a tudásbázis-kapcsolás (`knowledge`, alapból a
    `pipeline.toml` `knowledge`)."""
    con = site.con
    steps = load_pipeline().steps
    use_knowledge = steps.knowledge if knowledge is None else knowledge
    shared = connect(shared_path()) if use_knowledge else None
    try:
        site_run = run_site(con) if steps.site else None
        linked = None
        if shared is not None:
            linked = link_entities(con, _knowledge(con, shared), _utcnow, _site_language(con))
    finally:
        if shared is not None:
            shared.close()
    return ResolveResult(site_run, linked)


@dataclass(frozen=True)
class RebuildResult:
    """Az újraépítés eredménye: a szabálykör futása, a visszaírás LLM-futása (None, ha nincs
    tárolt kinyerés) és a feloldás."""

    rules_run: int | None
    restored: Restored | None
    resolved: ResolveResult


def rebuild_entities(site: Site, *, knowledge: bool | None = None) -> RebuildResult:
    """Az entitások újraépítése a tárolt kinyerésből, LLM-hívás nélkül: ugyanaz a levezetés,
    amellyel az `aaa entities` minden futása végződik (ürítés, szabálykör, a tárolt kinyerés
    visszaírása), utána a site-kör és a tudásbázis-kapcsolás (`resolve`). A blokkok, a
    futásnapló, a tárolt kinyerés és a hívásnapló marad; a gráfot és a megállapításokat utána
    az `aaa graph` és az `aaa findings` építi fel."""
    con = site.con
    steps = load_pipeline().steps
    if steps.blocks:
        build_blocks(con)
    rules_run, restored = _project(con, steps)
    return RebuildResult(rules_run, restored, resolve(site, knowledge=knowledge))


def entity_report(site: Site, out: Path, baseline: Path | None = None) -> tuple[Path, Path, int]:
    """Futásjelentés (Markdown) és site-szintű entitástábla (CSV) a legutóbbi futásról:
    `<out>/<név>-run.md`, `<out>/<név>-entities.csv`; `baseline`: a korábbi állapot adatbázisa a
    regressziós összevetéshez (csak olvasva). Visszaad: (jelentés, tábla, az entitások száma)."""
    old = duckdb.connect(str(baseline), read_only=True) if baseline is not None else None
    try:
        text = run_report(site.con, site.name, old)
    finally:
        if old is not None:
            old.close()
    out.mkdir(parents=True, exist_ok=True)
    report = out / f"{site.name}-run.md"
    report.write_text(text, encoding="utf-8")
    table = out / f"{site.name}-entities.csv"
    count = write_entity_table(site.con, table)
    return report, table, count


def validate(site: Site, *, limit: int | None = None, wikipedia: bool = True) -> ValidationRun:
    """Az entitások validálása: Google Knowledge Graph és Wikipedia-szócikk; a válaszok a
    site-adatbázisban és a `shared.duckdb`-ben gyorsítótárazva."""
    shared = connect(shared_path())
    try:
        return validate_entities(site.con, shared, limit=limit, wikipedia=wikipedia)
    finally:
        shared.close()


# --- graph ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class GraphResult:
    """A gráfépítés eredménye: a futás számai (`run`) és a kiírt fájlok (`paths`; üres, ha nem
    volt kimeneti mappa)."""

    run: GraphRun
    paths: dict[str, Path]


def build_graph(site: Site, *, wikidata: bool = True, out: Path | None = None) -> GraphResult:
    """Entitásgráf az M2 tárolt kimenetéből, LLM nélkül: oldal-csomópontok, az oldalak fő
    entitása a bizonyítékaival, élek, az entitások súlya. `wikidata`: `is_a`-élek a biztos
    Wikidata-osztályokból (a tudásbázis gyorsítótárán át). `out`: ha meg van adva, CSV-kimenet
    ide: `<név>-main-entity.csv`, `<név>-edges.csv`, `<név>-weights.csv`, és Wikidata mellett
    `<név>-is-a-rejected.csv`."""
    con = site.con
    shared = None
    paths: dict[str, Path] = {}
    try:
        superclasses = None
        if wikidata:
            shared = connect(shared_path())
            superclasses = _knowledge(con, shared).superclasses
        domain = site.domain
        patterns = load_site_config(domain).page_types if domain else {}
        run = graph_module.build_graph(con, page_type_patterns=patterns,
                                       superclasses=superclasses)
        if out is not None:
            paths = graph_module.export_csv(con, out, site.name)
            if superclasses is not None:
                paths["is_a_rejected"] = graph_module.export_rejected(con, run, out, site.name)
    finally:
        if shared is not None:
            shared.close()
    return GraphResult(run, paths)


# --- findings ------------------------------------------------------------------------------

@dataclass(frozen=True)
class FindResult:
    """A megállapítások futása (`run`) és a kiírt fájlok (`paths`; üres, ha nem volt kimeneti
    mappa)."""

    run: FindingsRun
    paths: dict[str, Path]


def find(site: Site, *, out: Path | None = None) -> FindResult:
    """SEO-megállapítások a gráfból (a `build_graph` után), LLM nélkül. `out`: ha meg van adva,
    kimenet ide: `<név>-findings.csv`, `<név>-view-site.csv`, `<név>-view-entities.csv`,
    `<név>-view-pages.csv`, `<név>-views.html`, és a tényfájlok (`<név>-view-links.csv`,
    `<név>-view-structured-data.csv`, `<név>-view-site-facts.csv`). Gráf nélkül:
    `GraphMissing`."""
    con = site.con
    if not graph_queries.page_nodes(con):
        raise GraphMissing("nincs gráf: előbb `aaa graph`")
    run = findings_module.build_findings(con)
    paths: dict[str, Path] = {}
    if out is not None:
        paths = {"findings": findings_module.export_findings(con, out, site.name),
                 **findings_module.export_views(con, out, site.name),
                 **facts_module.export_facts(con, out, site.name)}
    return FindResult(run, paths)


def check_models() -> list[ModelCheck]:
    """A konfigurált LLM-modellek azonosítói a szolgáltatók modell-listáján (kulcs kell)."""
    return _check_models()
