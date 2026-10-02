"""aaa — CLI: crawl, status, entities, validate, models, export."""
from __future__ import annotations

import asyncio
import csv
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import duckdb
import httpx
import typer

from aaa2.db.connect import connect, db_path, shared_path
from aaa2.engine import queries as crawl_queries
from aaa2.engine.crawl import CrawlOptions, run_crawl
from aaa2.engine.frontier import MAX_PAGES
from aaa2.engine.normalize import UrlPolicy
from aaa2.engine.render import CONCURRENCY, RENDER_TIMEOUT
from aaa2.entities import store
from aaa2.entities.dom import build_blocks
from aaa2.entities.extract import Worker, estimate_llm, input_models, run_llm
from aaa2.entities.gate import KnowledgeBase
from aaa2.entities.report import run_report, write_entity_table
from aaa2.entities.rules import run_rules
from aaa2.entities.v3 import V3Step, load_pipeline, v3_fingerprint
from aaa2.functions import graph_queries
from aaa2.functions.findings import TYPE_LABELS, build_findings, export_findings, export_views
from aaa2.functions.graph import build_graph, export_csv, export_rejected
from aaa2.llm import calls as llm_calls
from aaa2.llm import ledger
from aaa2.llm.client import Retry, check_models, open_clients
from aaa2.llm.config import PIPELINE_OFF, load_config, load_site_credentials
from aaa2.resolver import queries as resolver_queries
from aaa2.resolver.knowledge import link_entities
from aaa2.resolver.overrides import load_site_config
from aaa2.resolver.pages import entity_page_ids, page_roles
from aaa2.resolver.site import run_site
from aaa2.resolver.validate import KG_DAILY_QUOTA, _Api, validate_entities

app = typer.Typer(no_args_is_help=True, help="AAA v2 — sitewide SEO/GEO elemzőmotor")

EXPORT_TABLES = (
    "pages", "links", "headings", "schema_blocks", "crawl_queue", "crawl_runs", "site",
    "entities", "page_entities", "mention_sources", "blocks", "llm_calls", "entity_runs",
)


@app.command()
def crawl(
    url: Annotated[str, typer.Argument(help="Seed URL")],
    sitemap: Annotated[
        str | None, typer.Option(help="Sitemap URL; alapból robots.txt / sitemap.xml")
    ] = None,
    max_pages: Annotated[int, typer.Option(help="keményhatár a sor méretére")] = MAX_PAGES,
    concurrency: Annotated[
        int | None, typer.Option(help=f"párhuzamos Playwright-contextek (alapból {CONCURRENCY})")
    ] = None,
    render_timeout: Annotated[
        float | None, typer.Option(help=f"másodperc oldalanként (alapból {RENDER_TIMEOUT})")
    ] = None,
    respect_robots: Annotated[
        bool, typer.Option(help="robots.txt tiltásai; saját site-on kikapcsolható")
    ] = True,
    resume: Annotated[bool, typer.Option(help="folytatás a DB-ben lévő sorból")] = False,
    include: Annotated[
        str | None, typer.Option(help="regex a normalizált URL-re; csak ami illeszkedik")
    ] = None,
    exclude: Annotated[
        str | None, typer.Option(help="regex a normalizált URL-re; az exclude nyer")
    ] = None,
    quiet: Annotated[bool, typer.Option(help="oldalanként ne írjon sort")] = False,
) -> None:
    """Egy site sitewide crawlja Playwright-renderrel a data/<domain>.duckdb-be. Az include, az
    exclude és a párhuzamosság alapja a site-fájl `[crawl]` része, ha van
    (`aaa2/core/sites/<domain>.toml`); a parancssor felülírja."""
    try:
        site = load_site_config(UrlPolicy.from_seed(url).domain).crawl
    except ValueError as exc:
        typer.echo(f"hiba: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    options = CrawlOptions(
        sitemap=sitemap, max_pages=max_pages,
        concurrency=concurrency or site.concurrency or CONCURRENCY,
        render_timeout=render_timeout or site.render_timeout or RENDER_TIMEOUT,
        respect_robots=respect_robots, resume=resume,
        include=include or site.include, exclude=exclude or site.exclude_pattern,
    )
    if site.include or site.exclude:
        typer.echo(f"site-fájl: include={options.include!r} exclude={options.exclude!r}")

    def progress(page_url: str, page_status: int | None, error: str | None) -> None:
        if not quiet:
            label = page_status if page_status is not None else "—"
            typer.echo(f"{label}  {page_url}" + (f"  [{error}]" if error else ""))

    try:
        summary, path = asyncio.run(run_crawl(url, options, progress=progress))
    except ValueError as exc:
        typer.echo(f"hiba: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"kész: {summary.pages_done} oldal rendben, {summary.pages_failed} hibás, "
        f"{summary.pages_skipped} kihagyva (hash egyezett), {summary.pages_per_sec:.2f} oldal/mp, "
        f"{summary.bytes_stored} bájt renderelt HTML; {path}"
    )


@app.command()
def status(
    domain: Annotated[
        str | None, typer.Argument(help="registrable domain vagy egy URL a site-ról")
    ] = None,
) -> None:
    """Oldalak státusz szerint, hibák, a sor állapota, a site-profil, az utolsó crawl, és a
    halmozott LLM-költség modellenként. Domain nélkül csak az LLM-költség."""
    if domain is None:
        _llm_spend(None)
        return
    con = _open(domain)
    crawled = crawl_queries.pages(con)
    pages = len(crawled)
    errors = sum(page.error is not None for page in crawled)
    by_class = sorted(Counter(
        f"{page.status // 100}xx" if page.status is not None else "nincs válasz"
        for page in crawled).items())
    queue = dict(crawl_queries.queue_status_counts(con))
    typer.echo(f"{_domain(domain)}: {pages} oldal, {errors} hibával")
    typer.echo("  státusz: " + ", ".join(f"{name} {count}" for name, count in by_class))
    typer.echo(
        f"  sor: {queue.get('queued', 0)} várakozik, {queue.get('done', 0)} kész, "
        f"{queue.get('failed', 0)} hibás"
    )
    profile = crawl_queries.site(con)
    if profile:
        country, confidence, candidates, scope, city, languages, page_count, signals = (
            profile.target_country, profile.target_country_confidence,
            profile.target_country_candidates, profile.market_scope, profile.market_scope_city,
            profile.languages, profile.page_count, profile.tech_signals)
        typer.echo(
            f"  profil: célország {country or '—'}"
            + (f" ({confidence})" if confidence else "")
            + f", piaci hatókör {scope or '—'}" + (f" ({city})" if city else "")
            + f", nyelvek {', '.join(languages or []) or '—'}, {page_count or 0} sikeres oldal"
        )
        for candidate in candidates or []:
            typer.echo(
                f"    jelölt {candidate['country']} {candidate['score']:.2f}: "
                + ", ".join(candidate["signals"])
            )
        if signals:
            shown = ", ".join(signals[:8]) + (f" (+{len(signals) - 8})" if len(signals) > 8 else "")
            typer.echo(f"  tech-jelek: {shown}")
    last = crawl_queries.latest_crawl_run(con)
    if last:
        run_id, started, finished, done, failed, skipped, rate, notes = (
            last.run_id, last.started_at, last.finished_at, last.pages_done, last.pages_failed,
            last.pages_skipped, last.pages_per_sec, last.notes)
        state = f"kész {finished:%Y-%m-%d %H:%M}" if finished else "nincs lezárva"
        typer.echo(
            f"  utolsó crawl #{run_id} ({notes}): indult {started:%Y-%m-%d %H:%M}, {state}; "
            f"{done} rendben, {failed} hibás, {skipped} kihagyva, {rate or 0:.2f} oldal/mp"
        )
    for entity_run in store.latest_runs_by_method(con):
        typer.echo("  " + _entity_run_line(*entity_run))
    _llm_spend(con)


@app.command()
def entities(
    domain: Annotated[str, typer.Argument(help="registrable domain vagy egy URL a site-ról")],
    db: Annotated[Path | None, typer.Option(
        help="a site-adatbázis útvonala (alapból data/<domain>.duckdb)")] = None,
    llm: Annotated[bool | None, typer.Option(
        "--llm/--no-llm", help="az LLM-lépések (alapból a pipeline.toml extraction)")] = None,
    knowledge: Annotated[bool | None, typer.Option(
        "--knowledge/--no-knowledge",
        help="tudásbázis-egyezés (alapból a pipeline.toml knowledge)")] = None,
    extraction_model: Annotated[str | None, typer.Option(
        help="a kinyerés modellje (alapból a models.toml [pipeline] extraction)")] = None,
    verify_model: Annotated[str | None, typer.Option(
        help="a saját ajánlatok ellenőrzésének modellje (alapból a [pipeline] verify); off: "
             "csak a szerkezeti hely")] = None,
    limit: Annotated[int | None, typer.Option(help="legfeljebb ennyi oldal az LLM-körben")
                     ] = None,
    resume: Annotated[bool, typer.Option(
        help="a kinyerő modell legutóbbi LLM-futásának folytatása")] = False,
    estimate: Annotated[bool, typer.Option(
        help="csak a költségbecslés (a szabálykör és a hívások nélkül)")] = False,
    max_usd: Annotated[float | None, typer.Option(
        help="költséghatár (alapból a pipeline.toml max_usd): e fölötti becslésnél nem indul, "
             "a futás közben itt áll meg")] = None,
    workers: Annotated[int | None, typer.Option(
        min=1, help="ennyi oldal LLM-lépései futnak egyszerre (alapból a pipeline.toml "
                    "workers)")] = None,
    fresh: Annotated[bool, typer.Option(
        help="minden oldal újra kinyerve; alapból a változatlan oldal (azonos bemenet és "
             "modell) a korábbi kinyerését kapja, hívás nélkül")] = False,
) -> None:
    """Entitás-pipeline a megközelítés v3 szerint, az `entities/config/pipeline.toml`
    lépéseivel: a hiányzó blokkok, a determinisztikus szabálykör (JSON-LD, a site neve a
    title-ben és a H1-ben, legalább 3 oldalon azonos anchorok), utána oldalanként a
    content-régió blokkjainak LLM-kinyerése (darabolva, a kitalált említések kiszűrésével), a
    saját ajánlatok szerkezeti helye és ellenőrző hívása, a fogalmak bizonyítékai, a mentés;
    utána a site-szintű entitások (oldalhoz kötés, csomagok, lépések, demó- és sablonjelölés,
    LLM nélkül), a végén a tudásbázis-egyezés az entitásokra. Az LLM-kör előtt
    költségbecslés."""
    con = _open(domain, db)
    pipeline = load_pipeline()
    steps = pipeline.steps
    cap = max_usd if max_usd is not None else pipeline.max_usd
    use_llm = steps.extraction if llm is None else llm
    use_knowledge = steps.knowledge if knowledge is None else knowledge
    if steps.blocks:
        build_blocks(con)
    runs = [] if estimate or not steps.rules else [
        run_rules(con, entity_pages=entity_page_ids(page_roles(con))).run_id]
    profile = crawl_queries.site(con)
    site_lang = ((profile.languages if profile else None) or [None])[0]
    kb = shared = linked = site_run = None
    if use_knowledge and not estimate:
        shared = connect(shared_path())
        api = _Api(con, shared, httpx.Client(timeout=20.0), Retry(), _utcnow, time.monotonic)
        kb = KnowledgeBase(api.get)
    try:
        if use_llm:
            config = load_config()
            models = config.pipeline
            chosen = extraction_model or models["extraction"]
            verify_choice = (_step_model(verify_model or models["verify"])
                             if steps.services else None)
            refines = steps.services or steps.concepts
            reuse_models = None if fresh else input_models(
                chosen, v3_fingerprint(steps, verify_choice) if refines else None)
            guess = estimate_llm(con, config, chosen, verify_choice,
                                 _utcnow().date(), limit=limit, resume=resume,
                                 reuse_models=reuse_models)
            typer.echo(
                f"becslés: {guess.pages} oldal, {guess.chunks} kinyerő darab, ~{guess.tokens_in} "
                f"token be; kinyerés {guess.extract_usd:.4f} USD, ellenőrzés "
                f"{guess.verify_usd:.4f} USD ({guess.verify_pages} oldal a meglévő kinyerésből"
                + (f", {guess.verify_pending} oldalon a kinyerés után dől el"
                   if guess.verify_pending else "")
                + f"); összesen {guess.total_usd:.4f} USD, határ {cap:.2f} USD"
                + (f"; változatlan, a korábbi kinyerésével: {guess.reused} oldal"
                   if guess.reused else ""))
            if estimate:
                return
            if guess.total_usd > cap:
                typer.echo("a becslés a határ fölött van, a futás nem indul", err=True)
                raise typer.Exit(code=2)
            try:
                credentials = load_site_credentials(_site_domain(con))
            except ValueError as exc:
                typer.echo(f"hiba: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            for name, own in sorted(credentials.items()):
                typer.echo(f"site-kulcs: {name}: {own.key_env or 'alapkulcs'}"
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
                    thread_kb = KnowledgeBase(_Api(
                        cursor, shared.cursor() if shared is not None else None,
                        httpx.Client(timeout=20.0), Retry(), _utcnow, time.monotonic).get)
                verifier_bound = (own.get(id(verifier))
                                  or (verifier.bind(cursor) if verifier else None))
                return Worker(bound, V3Step(
                    cursor, steps, verifier_bound, thread_kb, site_lang) if refine else None)

            count = workers or pipeline.workers
            typer.echo(f"párhuzamosság: {count} oldal egyszerre")
            runs.append(run_llm(con, client, refine=refine,
                                save=steps.save, limit=limit, resume=resume,
                                max_usd=cap, workers=count, fork=fork,
                                reuse=not fresh).run_id)
        elif estimate:
            typer.echo("becslés: az LLM-lépések kikapcsolva, nincs hívás")
            return
        site_run = run_site(con) if steps.site else None
        if kb is not None:
            linked = link_entities(con, kb, _utcnow, site_lang)
    finally:
        if shared is not None:
            shared.close()
    for run_id in runs:
        entity_run = store.entity_run(con, run_id)
        typer.echo(_entity_run_line(*entity_run))
        for reason, value in json.loads(store.entity_runs_for_entities(con, run_id)[0]
                ).items():
            shown = (", ".join(f"{k} {v}" for k, v in list(value.items())[:8])
                     if isinstance(value, dict) else ", ".join(value)
                     if isinstance(value, list) else value)
            typer.echo(f"  kimaradt, {reason}: {shown}")
    if site_run is not None:
        typer.echo(
            f"site-kör #{site_run.run_id}: {site_run.page_entities} oldalhoz kötött entitás, "
            f"{site_run.packages} csomag, {site_run.steps} lépés, összevonás "
            f"{sum(site_run.merges.values())}, demó {len(site_run.demo)}, sablon-említés "
            f"{site_run.template_mentions}")
    if linked is not None:
        typer.echo(f"tudásbázis: {linked.entities} entitás; Wikidata biztos {linked.confident}, "
                   f"valószínű {linked.probable}, nincs {linked.none}; összevonás "
                   f"{linked.merged}; hibás lekérdezés miatt ellenőrizetlen {linked.errors}; "
                   f"technológiai osztály → tech {linked.retyped}")
    for kind, count, rows in store.entities_for_entities(con):
        typer.echo(f"  {kind}: {count} entitás, {rows} sor")


@app.command("entity-report")
def entity_report(
    domain: Annotated[str, typer.Argument(help="registrable domain vagy egy URL a site-ról")],
    db: Annotated[Path | None, typer.Option(
        help="a site-adatbázis útvonala (alapból data/<domain>.duckdb)")] = None,
    out: Annotated[Path, typer.Option(help="a kimeneti mappa")] = Path("data/reports"),
    baseline: Annotated[Path | None, typer.Option(
        help="a korábbi állapot adatbázisa (regressziós összevetés, csak olvasva)")] = None,
) -> None:
    """Futásjelentés (Markdown) és site-szintű entitástábla (CSV) a legutóbbi futásról:
    `<out>/<név>-run.md`, `<out>/<név>-entities.csv`."""
    con = _open(domain, db)
    stem = db.stem if db is not None else _domain(domain)
    old = duckdb.connect(str(baseline), read_only=True) if baseline is not None else None
    try:
        text = run_report(con, stem, old)
    finally:
        if old is not None:
            old.close()
    out.mkdir(parents=True, exist_ok=True)
    report = out / f"{stem}-run.md"
    report.write_text(text, encoding="utf-8")
    table = out / f"{stem}-entities.csv"
    count = write_entity_table(con, table)
    typer.echo(f"{report}")
    typer.echo(f"{table} ({count} entitás)")


@app.command()
def graph(
    domain: Annotated[str, typer.Argument(help="registrable domain vagy egy URL a site-ról")],
    db: Annotated[Path | None, typer.Option(
        help="a site-adatbázis útvonala (alapból data/<domain>.duckdb)")] = None,
    out: Annotated[Path, typer.Option(help="a kimeneti mappa")] = Path("data/reports"),
    wikidata: Annotated[bool, typer.Option(
        "--wikidata/--no-wikidata",
        help="is_a-élek a biztos Wikidata-osztályokból (a tudásbázis gyorsítótárán át)")] = True,
) -> None:
    """Entitásgráf az M2 tárolt kimenetéből, LLM nélkül: oldal-csomópontok, az oldal fő
    entitása a bizonyítékaival, élek, az entitások súlya; CSV-ben: `<out>/<név>-main-entity.csv`,
    `<név>-edges.csv`, `<név>-weights.csv`."""
    con = _open(domain, db)
    stem = db.stem if db is not None else _domain(domain)
    shared = None
    try:
        superclasses = None
        if wikidata:
            shared = connect(shared_path())
            api = _Api(con, shared, httpx.Client(timeout=20.0), Retry(), _utcnow,
                       time.monotonic)
            superclasses = KnowledgeBase(api.get).superclasses
        site = _site_domain(con)
        patterns = load_site_config(site).page_types if site else {}
        run = build_graph(con, page_type_patterns=patterns, superclasses=superclasses)
        paths = export_csv(con, out, stem)
        if superclasses is not None:
            paths["is_a_rejected"] = export_rejected(con, run, out, stem)
    finally:
        if shared is not None:
            shared.close()
    typer.echo(f"oldalak: {run.pages}; fő entitással {run.main} "
               f"({', '.join(f'{k} {v}' for k, v in sorted(run.confidence.items()))}), "
               f"segédoldal {run.support}, bizonyíték nélkül {run.none}")
    typer.echo(f"canonical-duplikátum: {run.duplicates}; nem számító canonical: "
               + (", ".join(f"{k} {v}" for k, v in sorted(run.canonical_issues.items()))
                  or "0"))
    typer.echo("élek: " + ", ".join(f"{k} {v}" for k, v in sorted(run.edges.items()))
               + f"; Wikidata-osztály: {run.class_lookups} lekérdezés, "
                 f"{run.class_failures} hibás")
    typer.echo(f"súlyozott entitás: {run.weights}")
    for path in paths.values():
        typer.echo(str(path))


@app.command()
def findings(
    domain: Annotated[str, typer.Argument(help="registrable domain vagy egy URL a site-ról")],
    db: Annotated[Path | None, typer.Option(
        help="a site-adatbázis útvonala (alapból data/<domain>.duckdb)")] = None,
    out: Annotated[Path, typer.Option(help="a kimeneti mappa")] = Path("data/reports"),
) -> None:
    """SEO-megállapítások és ellenőrző nézetek a gráfból (az `aaa graph` után), LLM nélkül:
    H1/title-eltérés, kannibalizáció és közös téma, hiányzó oldal és lefedetlen téma, nem
    egyértelmű téma; kimenet:
    `<out>/<név>-findings.csv`, `<név>-view-site.csv`, `<név>-view-entities.csv`,
    `<név>-view-pages.csv`, `<név>-views.html`."""
    con = _open(domain, db)
    stem = db.stem if db is not None else _domain(domain)
    if not graph_queries.page_nodes(con):
        raise typer.BadParameter("nincs gráf: előbb `aaa graph`")
    run = build_findings(con)
    paths = {"findings": export_findings(con, out, stem), **export_views(con, out, stem)}
    counts = run.by_type()
    typer.echo("megállapítások: " + (", ".join(
        f"{label} {counts[kind]} (" + ", ".join(
            f"{severity} {run.counts[(kind, severity)]}" for severity in ("high", "medium", "low")
            if run.counts[(kind, severity)]) + ")"
        for kind, label in TYPE_LABELS.items() if counts.get(kind)) or "0"))
    typer.echo(f"lefedetlen téma, a szó szerinti jelöltek: {run.missing_literal}; kizárt "
               f"kontextus-entitás: {', '.join(run.context) or '0'}")
    for path in paths.values():
        typer.echo(str(path))


def _step_model(value: str) -> str | None:
    return None if value in (PIPELINE_OFF, "none") else value


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _site_domain(con) -> str | None:
    profile = crawl_queries.site(con)
    return profile.domain if profile else None


def _pipeline_client(con, model: str, credentials=None):
    provider = load_config().provider_of(model)
    if provider is None:
        typer.echo(f"a {model} nincs a konfigurált modellek között", err=True)
        raise typer.Exit(code=1)
    clients, skipped = open_clients(con, models={provider: model}, credentials=credentials)
    if provider not in clients:
        typer.echo(f"nincs {model}-kliens: {skipped.get(provider, 'ismeretlen')}", err=True)
        raise typer.Exit(code=1)
    return clients[provider]


@app.command()
def validate(
    domain: Annotated[str, typer.Argument(help="registrable domain vagy egy URL a site-ról")],
    limit: Annotated[int | None, typer.Option(help="legfeljebb ennyi entitás")] = None,
    wikipedia: Annotated[bool, typer.Option(help="Wikipedia-szócikk keresése")] = True,
) -> None:
    """Az entitások validálása: Google Knowledge Graph (öt kategória, a KG-típus összevetése) és
    Wikipedia-szócikk; a csak anchorban és title-ben álló concept hívás nélkül stub (navigációs).
    A válaszok a site-adatbázisban és a shared.duckdb-ben cache-elve."""
    con = _open(domain)
    shared = connect(shared_path())
    try:
        run = validate_entities(con, shared, limit=limit, wikipedia=wikipedia)
    finally:
        shared.close()
    kg = "kihagyva (nincs GOOGLE_KG_API_KEY)" if run.kg_skipped else ", ".join(
        f"{status} {count}" for status, count in sorted(run.statuses.items())) or "—"
    typer.echo(f"validálás: {run.entities} entitás; KG: {kg}; navigációs stub: {run.navigational}; "
               f"Wikipedia-szócikk: {run.wikipedia}")
    typer.echo(f"  típusváltás a KG szerint: {len(run.type_changes)}, KG-típuseltérés jelölve: "
               f"{run.mismatches}")
    for name, before, after in run.type_changes:
        typer.echo(f"    {name}: {before} → {after}")
    kg_today = resolver_queries.kg_calls_today(con)
    typer.echo(
        f"  API-hívás: KG {run.calls.get('kg', 0)}, Wikipedia {run.calls.get('wikipedia', 0)}; "
        f"cache: site {run.cache_site}, shared {run.cache_shared}; hiba: "
        + (", ".join(f"{k} {v}" for k, v in sorted(run.errors.items())) or "0")
        + f"; KG ma ezen a site-on {kg_today} / napi kvóta {KG_DAILY_QUOTA}")


@app.command()
def models() -> None:
    """A konfigurált LLM-modellek azonosítói a szolgáltatók modell-listáján (kulcs kell)."""
    missing = False
    for check in check_models():
        if check.found is None:
            state = f"nem ellenőrizhető ({check.note})"
        elif check.found:
            state = f"a listán ({check.note})"
        else:
            missing = True
            state = f"NINCS a listán ({check.note})"
            if check.similar:
                state += "; hasonló: " + ", ".join(check.similar)
        typer.echo(f"{check.provider} {check.model}: {state}")
    if missing:
        raise typer.Exit(code=1)


@app.command()
def export(
    domain: Annotated[str, typer.Argument(help="registrable domain vagy egy URL a site-ról")],
    table: Annotated[str, typer.Option(help=" | ".join(EXPORT_TABLES))] = "pages",
    csv_format: Annotated[
        bool, typer.Option("--csv", help="CSV (jelenleg az egyetlen formátum)")
    ] = True,
    out: Annotated[Path | None, typer.Option(help="CSV útvonal; alapból stdout")] = None,
) -> None:
    """Egy tábla CSV-be. A BLOB-oszlopok (a tömörített renderelt HTML) kimaradnak."""
    if table not in EXPORT_TABLES:
        typer.echo(f"ismeretlen tábla: {table}; lehet: {', '.join(EXPORT_TABLES)}", err=True)
        raise typer.Exit(code=1)
    con = _open(domain)
    columns = [
        name for name, kind in con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position", [table]
        ).fetchall()
        if kind != "BLOB"
    ]
    rows = con.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY ALL").fetchall()
    handle = out.open("w", encoding="utf-8", newline="") if out else sys.stdout
    try:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)
    finally:
        if out:
            handle.close()


def _entity_run_line(run_id, method, model, finished, pages, pages_with, entity_count, rows,
                     llm_calls, cost_usd, fabricated, by_position) -> str:
    counts = sorted(json.loads(by_position or "{}").items())
    positions = ", ".join(f"{k} {v}" for k, v in counts)
    label = f"{method} {model}" if model else method
    when = f", {finished:%Y-%m-%d %H:%M}" if finished else ""
    line = (f"entitás-futás #{run_id} ({label}{when}): {entity_count} entitás "
            f"{pages_with}/{pages} oldalról, {rows} sor ({positions or '—'}), "
            f"LLM-hívás {llm_calls}")
    if method == "llm" and pages:
        line += (f"; oldalanként {llm_calls / pages:.2f} hívás, {(cost_usd or 0) / pages:.4f} USD, "
                 f"{rows / pages:.2f} sor, {(fabricated or 0) / pages:.2f} fabrikált")
    return line


def _llm_spend(con) -> None:
    """Modellenként a halmozott USD a főkönyvből (minden site), szolgáltatónként a leállási
    küszöbbel és a kerettel; ha van site-adatbázis, az ott könyvelt hívások is."""
    config = load_config()
    spent = ledger.spent_by_model()
    typer.echo(f"  LLM-költség, minden site ({ledger.default_path()}):")
    for provider in config.providers.values():
        total = sum(spent.get(model, 0.0) for model in provider.models)
        for model in provider.models:
            active = " (aktív)" if provider.fallback and model == provider.active_model else ""
            typer.echo(f"    {model}{active}: {spent.get(model, 0.0):.4f} USD")
        typer.echo(
            f"    {provider.name} összesen {total:.4f} USD; leállás {provider.stop_usd:.2f} USD "
            f"felett, keret {provider.budget_usd:.2f} USD"
            + ("; LEÁLLVA" if total > provider.stop_usd else "")
        )
    for model in sorted(set(spent) - {m for p in config.providers.values() for m in p.models}):
        typer.echo(f"    {model} (nincs a konfigurációban): {spent[model]:.4f} USD")
    if con is not None:
        rows = llm_calls.spend_by_model(con)
        typer.echo(
            "  LLM ezen a site-on: "
            + (", ".join(f"{model} {calls} hívás {usd or 0:.4f} USD"
                         + (f" ({retries} újrapróba" + (f": {codes})" if codes else ")")
                            if retries else "")
                         for model, calls, usd, retries, codes in rows)
               or "nincs hívás")
        )


def _domain(value: str) -> str:
    return UrlPolicy.from_seed(value).domain if "://" in value else value.lower()


def _open(domain: str, db: Path | None = None):
    path = db if db is not None else db_path(_domain(domain))
    if not path.exists():
        typer.echo(f"nincs adatbázis: {path}", err=True)
        raise typer.Exit(code=1)
    return connect(path)


if __name__ == "__main__":
    app()
