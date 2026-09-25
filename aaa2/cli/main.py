"""aaa — CLI. M1-ben: crawl, status, export."""
from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path
from typing import Annotated

import typer

from aaa2.db.connect import connect, db_path
from aaa2.engine.crawl import CrawlOptions, run_crawl
from aaa2.engine.frontier import MAX_PAGES
from aaa2.engine.normalize import UrlPolicy
from aaa2.engine.render import CONCURRENCY, RENDER_TIMEOUT

app = typer.Typer(no_args_is_help=True, help="AAA v2 — sitewide SEO/GEO elemzőmotor")

EXPORT_TABLES = (
    "pages", "links", "headings", "schema_blocks", "crawl_queue", "crawl_runs", "site",
    "entities", "page_entities",
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
    domain: Annotated[str, typer.Argument(help="registrable domain vagy egy URL a site-ról")],
) -> None:
    """Oldalak státusz szerint, hibák, a sor állapota, az utolsó crawl."""
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
