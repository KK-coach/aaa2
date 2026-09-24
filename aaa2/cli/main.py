"""aaa — CLI. M1-ben: crawl, status, export."""
from __future__ import annotations

from pathlib import Path

import typer

from aaa2.db.connect import connect, db_path

app = typer.Typer(no_args_is_help=True, help="AAA v2 — sitewide SEO/GEO elemzőmotor")


@app.command()
def crawl(
    url: str = typer.Argument(..., help="Seed URL"),
    sitemap: str | None = typer.Option(None, help="Sitemap URL; alapból robots.txt / sitemap.xml"),
    max_pages: int = typer.Option(5000),
    concurrency: int = typer.Option(6),
    render_timeout: float = typer.Option(15.0, help="másodperc oldalanként"),
    respect_robots: bool = typer.Option(True),
    resume: bool = typer.Option(False, help="folytatás a DB-ben lévő frontierből"),
) -> None:
    """Egy site sitewide crawlja Playwright-renderrel a data/<domain>.duckdb-be."""
    raise typer.Exit(code=_todo("engine.crawl — M1 első szelet: normalizálás + crawl_queue + egy oldal render"))


@app.command()
def status(domain: str) -> None:
    """Oldalak, hibák, frontier állapota."""
    path = db_path(domain)
    if not path.exists():
        typer.echo(f"nincs adatbázis: {path}")
        raise typer.Exit(code=1)
    con = connect(path)
    pages, = con.execute("SELECT count(*) FROM pages").fetchone()
    failed, = con.execute("SELECT count(*) FROM pages WHERE error IS NOT NULL").fetchone()
    queued, = con.execute("SELECT count(*) FROM crawl_queue WHERE status = 'queued'").fetchone()
    typer.echo(f"{domain}: {pages} oldal, {failed} hibás, {queued} a sorban")


@app.command()
def export(
    domain: str,
    table: str = typer.Option("pages", help="pages | links | headings | schema_blocks | entities | page_entities"),
    out: Path | None = typer.Option(None, help="CSV útvonal; alapból stdout"),
) -> None:
    """Egy tábla CSV-be."""
    con = connect(db_path(domain))
    target = str(out) if out else "/dev/stdout"
    con.execute(f"COPY (SELECT * FROM {table}) TO '{target}' (HEADER, DELIMITER ',')")


def _todo(what: str) -> int:
    typer.echo(f"még nincs kész: {what}", err=True)
    return 2


if __name__ == "__main__":
    app()
