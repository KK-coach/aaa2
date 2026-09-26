"""aaa — CLI: crawl, status, entities, models, export."""
from __future__ import annotations

import asyncio
import csv
import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from aaa2.db.connect import connect, db_path
from aaa2.engine.crawl import CrawlOptions, run_crawl
from aaa2.engine.entities_llm import run_llm
from aaa2.engine.entities_rules import run_rules
from aaa2.engine.frontier import MAX_PAGES
from aaa2.engine.normalize import UrlPolicy
from aaa2.engine.render import CONCURRENCY, RENDER_TIMEOUT
from aaa2.llm import ledger
from aaa2.llm.client import check_models, open_clients
from aaa2.llm.config import load_config

app = typer.Typer(no_args_is_help=True, help="AAA v2 — sitewide SEO/GEO elemzőmotor")

EXPORT_TABLES = (
    "pages", "links", "headings", "schema_blocks", "crawl_queue", "crawl_runs", "site",
    "entities", "page_entities", "llm_calls", "entity_runs",
)
ENTITY_RUN_COLUMNS = (
    "run_id, method, model, finished_at, pages, pages_with_entities, entities, row_count, "
    "llm_calls, cost_usd, fabricated, by_position"
)


@app.command()
def crawl(
    url: Annotated[str, typer.Argument(help="Seed URL")],
    sitemap: Annotated[
        str | None, typer.Option(help="Sitemap URL; alapból robots.txt / sitemap.xml")
    ] = None,
    max_pages: Annotated[int, typer.Option(help="keményhatár a sor méretére")] = MAX_PAGES,
    concurrency: Annotated[int, typer.Option(help="párhuzamos Playwright-contextek")] = CONCURRENCY,
    render_timeout: Annotated[float, typer.Option(help="másodperc oldalanként")] = RENDER_TIMEOUT,
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
    """Egy site sitewide crawlja Playwright-renderrel a data/<domain>.duckdb-be."""
    options = CrawlOptions(
        sitemap=sitemap, max_pages=max_pages, concurrency=concurrency,
        render_timeout=render_timeout, respect_robots=respect_robots, resume=resume,
        include=include, exclude=exclude,
    )

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
    (pages,) = con.execute("SELECT count(*) FROM pages").fetchone()
    (errors,) = con.execute("SELECT count(*) FROM pages WHERE error IS NOT NULL").fetchone()
    by_class = con.execute(
        "SELECT coalesce(CAST(status // 100 AS VARCHAR) || 'xx', 'nincs válasz') AS class, "
        "count(*) FROM pages GROUP BY class ORDER BY class"
    ).fetchall()
    queue = dict(con.execute("SELECT status, count(*) FROM crawl_queue GROUP BY status").fetchall())
    typer.echo(f"{_domain(domain)}: {pages} oldal, {errors} hibával")
    typer.echo("  státusz: " + ", ".join(f"{name} {count}" for name, count in by_class))
    typer.echo(
        f"  sor: {queue.get('queued', 0)} várakozik, {queue.get('done', 0)} kész, "
        f"{queue.get('failed', 0)} hibás"
    )
    profile = con.execute(
        "SELECT target_country, target_country_confidence, target_country_candidates, "
        "market_scope, market_scope_city, languages, page_count, tech_signals FROM site"
    ).fetchone()
    if profile:
        country, confidence, candidates, scope, city, languages, page_count, signals = profile
        typer.echo(
            f"  profil: célország {country or '—'}"
            + (f" ({confidence})" if confidence else "")
            + f", piaci hatókör {scope or '—'}" + (f" ({city})" if city else "")
            + f", nyelvek {', '.join(languages or []) or '—'}, {page_count or 0} sikeres oldal"
        )
        for candidate in json.loads(candidates or "[]"):
            typer.echo(
                f"    jelölt {candidate['country']} {candidate['score']:.2f}: "
                + ", ".join(candidate["signals"])
            )
        if signals:
            shown = ", ".join(signals[:8]) + (f" (+{len(signals) - 8})" if len(signals) > 8 else "")
            typer.echo(f"  tech-jelek: {shown}")
    last = con.execute(
        "SELECT run_id, started_at, finished_at, pages_done, pages_failed, pages_skipped, "
        "pages_per_sec, notes FROM crawl_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    if last:
        run_id, started, finished, done, failed, skipped, rate, notes = last
        state = f"kész {finished:%Y-%m-%d %H:%M}" if finished else "nincs lezárva"
        typer.echo(
            f"  utolsó crawl #{run_id} ({notes}): indult {started:%Y-%m-%d %H:%M}, {state}; "
            f"{done} rendben, {failed} hibás, {skipped} kihagyva, {rate or 0:.2f} oldal/mp"
        )
    for entity_run in con.execute(
        f"SELECT {ENTITY_RUN_COLUMNS} FROM entity_runs "
        "WHERE run_id IN (SELECT max(run_id) FROM entity_runs GROUP BY method) ORDER BY run_id"
    ).fetchall():
        typer.echo("  " + _entity_run_line(*entity_run))
    _llm_spend(con)


@app.command()
def entities(
    domain: Annotated[str, typer.Argument(help="registrable domain vagy egy URL a site-ról")],
    llm: Annotated[bool, typer.Option(help="a szabályok után LLM-kinyerés a main contentre")
                   ] = False,
    provider: Annotated[str, typer.Option(help="anthropic | openai | gemini")] = "gemini",
    limit: Annotated[int | None, typer.Option(help="legfeljebb ennyi oldal az LLM-körben")
                     ] = None,
) -> None:
    """Entitás-kör: a determinisztikus szabályok (JSON-LD, a site neve a title-ben és a
    H1-ben, legalább 3 oldalon azonos anchorok), `--llm`-mel utána oldalanként egy LLM-hívás a
    main contentre, fabrikáció-szűréssel és összevonással."""
    con = _open(domain)
    client = None
    if llm:
        clients, skipped = open_clients(con)
        client = clients.get(provider)
        if client is None:
            typer.echo(f"nincs {provider}-kliens: {skipped.get(provider, 'ismeretlen')}",
                       err=True)
            raise typer.Exit(code=1)
    runs = [run_rules(con).run_id]
    if client is not None:
        runs.append(run_llm(con, client, limit=limit).run_id)
    for run_id in runs:
        entity_run = con.execute(
            f"SELECT {ENTITY_RUN_COLUMNS} FROM entity_runs WHERE run_id = ?", [run_id]
        ).fetchone()
        typer.echo(_entity_run_line(*entity_run))
        for reason, value in json.loads(con.execute(
                "SELECT skipped FROM entity_runs WHERE run_id = ?", [run_id]).fetchone()[0]
                ).items():
            shown = (", ".join(f"{k} {v}" for k, v in list(value.items())[:8])
                     if isinstance(value, dict) else ", ".join(value)
                     if isinstance(value, list) else value)
            typer.echo(f"  kimaradt, {reason}: {shown}")
    for kind, count, rows in con.execute(
        "SELECT e.type, count(DISTINCT e.entity_id), count(*) FROM entities e "
        "JOIN page_entities pe USING (entity_id) "
        "GROUP BY e.type ORDER BY count(DISTINCT e.entity_id) DESC, e.type"
    ).fetchall():
        typer.echo(f"  {kind}: {count} entitás, {rows} sor")


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
    rows = con.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
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
        rows = con.execute(
            "SELECT model, count(*), sum(cost_usd), sum(coalesce(attempts, 1) - 1), "
            "array_to_string(list_sort(list(DISTINCT split_part(last_error, ':', 1)) "
            "FILTER (WHERE last_error IS NOT NULL)), ', ') "
            "FROM llm_calls GROUP BY model ORDER BY model"
        ).fetchall()
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


def _open(domain: str):
    path = db_path(_domain(domain))
    if not path.exists():
        typer.echo(f"nincs adatbázis: {path}", err=True)
        raise typer.Exit(code=1)
    return connect(path)


if __name__ == "__main__":
    app()
