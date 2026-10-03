"""aaa — CLI: crawl, status, entities, validate, models, export. Vékony réteg az `aaa2.api`
fölött: a paramétereket olvassa, az API-t hívja, és az eredményt kiírja."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from aaa2 import api

app = typer.Typer(no_args_is_help=True, help="AAA v2 — sitewide SEO/GEO elemzőmotor")


@app.command()
def crawl(
    url: Annotated[str, typer.Argument(help="Seed URL")],
    sitemap: Annotated[
        str | None, typer.Option(help="Sitemap URL; alapból robots.txt / sitemap.xml")
    ] = None,
    max_pages: Annotated[int, typer.Option(help="keményhatár a sor méretére")] = api.MAX_PAGES,
    concurrency: Annotated[
        int | None,
        typer.Option(help=f"párhuzamos Playwright-contextek (alapból {api.CONCURRENCY})")
    ] = None,
    render_timeout: Annotated[
        float | None, typer.Option(help=f"másodperc oldalanként (alapból {api.RENDER_TIMEOUT})")
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

    def on_options(options, scoped: bool) -> None:
        if scoped:
            typer.echo(f"site-fájl: include={options.include!r} exclude={options.exclude!r}")

    def progress(page_url: str, page_status: int | None, error: str | None) -> None:
        if not quiet:
            label = page_status if page_status is not None else "—"
            typer.echo(f"{label}  {page_url}" + (f"  [{error}]" if error else ""))

    try:
        result = api.crawl(
            url, sitemap=sitemap, max_pages=max_pages, concurrency=concurrency,
            render_timeout=render_timeout, respect_robots=respect_robots, resume=resume,
            include=include, exclude=exclude, progress=progress, on_options=on_options)
    except api.ApiError as exc:
        _fail(exc)
    summary = result.summary
    typer.echo(
        f"kész: {summary.pages_done} oldal rendben, {summary.pages_failed} hibás, "
        f"{summary.pages_skipped} kihagyva (hash egyezett), {summary.pages_per_sec:.2f} oldal/mp, "
        f"{summary.bytes_stored} bájt renderelt HTML; {result.path}"
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
    site = _open(domain)
    state = api.status(site)
    queue = state.queue
    typer.echo(f"{api.domain_of(domain)}: {state.pages} oldal, {state.errors} hibával")
    typer.echo("  státusz: " + ", ".join(f"{name} {count}" for name, count in state.by_class))
    typer.echo(
        f"  sor: {queue.get('queued', 0)} várakozik, {queue.get('done', 0)} kész, "
        f"{queue.get('failed', 0)} hibás"
    )
    profile = state.profile
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
    last = state.last_crawl
    if last:
        run_id, started, finished, done, failed, skipped, rate, notes = (
            last.run_id, last.started_at, last.finished_at, last.pages_done, last.pages_failed,
            last.pages_skipped, last.pages_per_sec, last.notes)
        finished_state = f"kész {finished:%Y-%m-%d %H:%M}" if finished else "nincs lezárva"
        typer.echo(
            f"  utolsó crawl #{run_id} ({notes}): indult {started:%Y-%m-%d %H:%M}, "
            f"{finished_state}; "
            f"{done} rendben, {failed} hibás, {skipped} kihagyva, {rate or 0:.2f} oldal/mp"
        )
    for entity_run in state.entity_runs:
        typer.echo("  " + _entity_run_line(*entity_run))
    _llm_spend(site)


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
    site = _open(domain, db)
    try:
        extracted = api.extract(
            site, llm=llm, knowledge=knowledge, extraction_model=extraction_model,
            verify_model=verify_model, limit=limit, resume=resume, estimate=estimate,
            max_usd=max_usd, workers=workers, fresh=fresh, notify=typer.echo)
    except api.ApiError as exc:
        _fail(exc)
    if extracted.estimate_only:
        return
    resolved = api.resolve(site, knowledge=knowledge)
    for run_id in extracted.run_ids:
        typer.echo(_entity_run_line(*api.entity_run(site, run_id)))
        for reason, value in api.entity_run_skipped(site, run_id).items():
            shown = (", ".join(f"{k} {v}" for k, v in list(value.items())[:8])
                     if isinstance(value, dict) else ", ".join(value)
                     if isinstance(value, list) else value)
            typer.echo(f"  kimaradt, {reason}: {shown}")
    site_run, linked = resolved.site_run, resolved.linked
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
    for kind, count, rows in api.entity_type_counts(site):
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
    site = _open(domain, db)
    report, table, count = api.entity_report(site, out, baseline)
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
    site = _open(domain, db)
    result = api.build_graph(site, wikidata=wikidata, out=out)
    run, paths = result.run, result.paths
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
    site = _open(domain, db)
    try:
        result = api.find(site, out=out)
    except api.GraphMissing as exc:
        raise typer.BadParameter(exc.message) from exc
    run, paths = result.run, result.paths
    counts = run.by_type()
    typer.echo("megállapítások: " + (", ".join(
        f"{label} {counts[kind]} (" + ", ".join(
            f"{severity} {run.counts[(kind, severity)]}" for severity in ("high", "medium", "low")
            if run.counts[(kind, severity)]) + ")"
        for kind, label in api.FINDING_TYPE_LABELS.items() if counts.get(kind)) or "0"))
    typer.echo(f"lefedetlen téma, a szó szerinti jelöltek: {run.missing_literal}; kizárt "
               f"kontextus-entitás: {', '.join(run.context) or '0'}")
    for path in paths.values():
        typer.echo(str(path))


@app.command()
def validate(
    domain: Annotated[str, typer.Argument(help="registrable domain vagy egy URL a site-ról")],
    limit: Annotated[int | None, typer.Option(help="legfeljebb ennyi entitás")] = None,
    wikipedia: Annotated[bool, typer.Option(help="Wikipedia-szócikk keresése")] = True,
) -> None:
    """Az entitások validálása: Google Knowledge Graph (öt kategória, a KG-típus összevetése) és
    Wikipedia-szócikk; a csak anchorban és title-ben álló concept hívás nélkül stub (navigációs).
    A válaszok a site-adatbázisban és a shared.duckdb-ben cache-elve."""
    site = _open(domain)
    run = api.validate(site, limit=limit, wikipedia=wikipedia)
    kg = "kihagyva (nincs GOOGLE_KG_API_KEY)" if run.kg_skipped else ", ".join(
        f"{status} {count}" for status, count in sorted(run.statuses.items())) or "—"
    typer.echo(f"validálás: {run.entities} entitás; KG: {kg}; navigációs stub: {run.navigational}; "
               f"Wikipedia-szócikk: {run.wikipedia}")
    typer.echo(f"  típusváltás a KG szerint: {len(run.type_changes)}, KG-típuseltérés jelölve: "
               f"{run.mismatches}")
    for name, before, after in run.type_changes:
        typer.echo(f"    {name}: {before} → {after}")
    kg_today = api.kg_calls_today(site)
    typer.echo(
        f"  API-hívás: KG {run.calls.get('kg', 0)}, Wikipedia {run.calls.get('wikipedia', 0)}; "
        f"cache: site {run.cache_site}, shared {run.cache_shared}; hiba: "
        + (", ".join(f"{k} {v}" for k, v in sorted(run.errors.items())) or "0")
        + f"; KG ma ezen a site-on {kg_today} / napi kvóta {api.KG_DAILY_QUOTA}")


@app.command()
def models() -> None:
    """A konfigurált LLM-modellek azonosítói a szolgáltatók modell-listáján (kulcs kell)."""
    missing = False
    for check in api.check_models():
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
    table: Annotated[str, typer.Option(help=" | ".join(api.EXPORT_TABLES))] = "pages",
    csv_format: Annotated[
        bool, typer.Option("--csv", help="CSV (jelenleg az egyetlen formátum)")
    ] = True,
    out: Annotated[Path | None, typer.Option(help="CSV útvonal; alapból stdout")] = None,
) -> None:
    """Egy tábla CSV-be. A BLOB-oszlopok (a tömörített renderelt HTML) kimaradnak."""
    if table not in api.EXPORT_TABLES:
        typer.echo(f"ismeretlen tábla: {table}; lehet: {', '.join(api.EXPORT_TABLES)}", err=True)
        raise typer.Exit(code=1)
    site = _open(domain)
    columns, rows = api.table_rows(site, table)
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


def _llm_spend(site: api.Site | None) -> None:
    """Modellenként a halmozott USD a főkönyvből (minden site), szolgáltatónként a leállási
    küszöbbel és a kerettel; ha van site-adatbázis, az ott könyvelt hívások is."""
    spend = api.llm_spend(site)
    typer.echo(f"  LLM-költség, minden site ({spend.ledger_path}):")
    for provider in spend.providers:
        for model, usd, active in provider.models:
            typer.echo(f"    {model}{' (aktív)' if active else ''}: {usd:.4f} USD")
        typer.echo(
            f"    {provider.name} összesen {provider.total:.4f} USD; leállás "
            f"{provider.stop_usd:.2f} USD "
            f"felett, keret {provider.budget_usd:.2f} USD"
            + ("; LEÁLLVA" if provider.total > provider.stop_usd else "")
        )
    for model, usd in spend.unconfigured:
        typer.echo(f"    {model} (nincs a konfigurációban): {usd:.4f} USD")
    if spend.site_rows is not None:
        typer.echo(
            "  LLM ezen a site-on: "
            + (", ".join(f"{model} {calls} hívás {usd or 0:.4f} USD"
                         + (f" ({retries} újrapróba" + (f": {codes})" if codes else ")")
                            if retries else "")
                         for model, calls, usd, retries, codes in spend.site_rows)
               or "nincs hívás")
        )


def _fail(exc: api.ApiError):
    typer.echo(exc.message, err=True)
    raise typer.Exit(code=exc.code) from exc


def _open(domain: str, db: Path | None = None) -> api.Site:
    try:
        return api.open_site(domain, db)
    except api.SiteNotFound as exc:
        _fail(exc)


if __name__ == "__main__":
    app()
