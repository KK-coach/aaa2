"""Az idegen site-ok (M2/7 B) felvétele: élő crawl az M1 motorral, közben minden válasz felvéve
(`tests/fixtures/<név>/`), a kapcsolat a `data/compare/<név>.duckdb`-be; utána visszajátszás
hálózat nélkül, és a két crawl URL-halmazának összevetése (a felvétel teljes-e). A `report`
site-onként az oldalszámot adja oldaltípusonként (`pages.page_types`, a site-fájl mintáival), és
a nem oldal-válaszokat (kép, PDF) és a hibákat.

    python -m tests.acceptance.record_sites record marketinglens-crawl duex-crawl
    python -m tests.acceptance.record_sites verify duex-crawl
    python -m tests.acceptance.record_sites report marketinglens-crawl duex-crawl
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import duckdb

from aaa2.db.connect import DATA_DIR
from aaa2.resolver.overrides import load_site_config
from aaa2.resolver.pages import PAGE_TYPES, page_types
from tests.recorded import REFERENCE_SETS, SITE_SETS, record_crawl, replay_crawl

OUT_DIR = DATA_DIR / "compare"
REPORT = OUT_DIR / "m27-crawl-report.md"
TYPE_LABELS = {"product": "termék", "category": "kategória", "brand_category": "márka × kategória",
               "blog": "blog", "service": "szolgáltatás", "other": "egyéb"}
# A válasz nélkül maradt URL-ek (időtúllépés, hálózati hiba) ennyiszer kerülnek újra sorra.
RETRIES = 2


def save(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    """A memóriabeli kapcsolat egy fájlba (a meglévő fájl helyére)."""
    for old in (path, Path(str(path) + ".wal")):
        old.unlink(missing_ok=True)
    con.execute(f"ATTACH '{path.as_posix()}' AS target")
    con.execute("COPY FROM DATABASE memory TO target")
    con.execute("DETACH target")


def urls(con: duckdb.DuckDBPyConnection) -> set[tuple[str, int | None]]:
    return set(con.execute("SELECT url, status FROM pages").fetchall())


def record(name: str) -> None:
    seed, options = REFERENCE_SETS[name]
    recording, con, summary = asyncio.run(record_crawl(name, seed, options, retries=RETRIES))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    save(con, OUT_DIR / f"{name}.duckdb")
    print(f"{name}: {summary}; válasz {len(recording.responses)}; "
          f"oldal {len(urls(con))} → {OUT_DIR / f'{name}.duckdb'}")
    verify(name, con)


def verify(name: str, live: duckdb.DuckDBPyConnection | None = None) -> None:
    """A visszajátszott crawl URL-jei és státuszai egyeznek-e a felvételkoriakkal."""
    live = live or duckdb.connect(str(OUT_DIR / f"{name}.duckdb"), read_only=True)
    seed, options = REFERENCE_SETS[name]
    replayed = asyncio.run(replay_crawl(name, seed, options))
    if replayed is None:
        raise SystemExit(f"nincs felvétel: {name}")
    before, after = urls(live), urls(replayed[1])
    print(f"{name}: felvétel {len(before)} oldal, visszajátszás {len(after)} oldal; "
          f"csak élőben {len(before - after)}, csak visszajátszva {len(after - before)}")
    for url, status in sorted(before ^ after, key=lambda row: (row[0], row[1] or 0))[:20]:
        print(f"   eltér: {url} {status}")


def report(names: list[str]) -> str:
    """Site-onként: oldaltípusonkénti oldalszám, nem oldal-válaszok és hibák (markdown)."""
    lines = ["# M2/7 B: crawl-jelentés", ""]
    for name in names:
        con = duckdb.connect(str(OUT_DIR / f"{name}.duckdb"), read_only=True)
        seed, options = REFERENCE_SETS[name]
        kinds = page_types(con, load_site_config(SITE_SETS[name]).page_types)
        counts = {kind: sum(1 for k in kinds.values() if k == kind) for kind in PAGE_TYPES}
        non_html = con.execute("SELECT count(*) FROM pages WHERE error LIKE 'non_html%'").fetchone()[0]
        errors = con.execute(
            "SELECT url, status, error FROM pages WHERE NOT (error IS NULL "
            "AND status BETWEEN 200 AND 299) AND (error IS NULL OR error NOT LIKE 'non_html%') "
            "ORDER BY url").fetchall()
        (run,) = con.execute("SELECT concurrency FROM crawl_runs ORDER BY run_id LIMIT 1"
                             ).fetchone()
        setup = (f"- crawl: párhuzamosság {run}, render-időkorlát {options.render_timeout} mp, "
                 f"include `{options.include}`, exclude `{options.exclude}`")
        lines += [f"## {name} ({seed})", "", setup,
                  f"- HTML-oldal (2xx, hiba nélkül): {len(kinds)}"]
        lines += [f"  - {TYPE_LABELS[kind]}: {counts[kind]}" for kind in PAGE_TYPES]
        lines += [f"- nem oldal (kép, PDF; linkcélként került sorra): {non_html}",
                  f"- hibás: {len(errors)}"]
        lines += [f"  - {url} ({status}, {error})" for url, status, error in errors]
        lines.append("")
        con.close()
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["record", "verify", "report"])
    parser.add_argument("names", nargs="+", choices=sorted(SITE_SETS))
    args = parser.parse_args(argv)
    if args.command == "report":
        text = report(args.names)
        REPORT.write_text(text, encoding="utf-8")
        print(text)
        return
    for name in args.names:
        (record if args.command == "record" else verify)(name)


if __name__ == "__main__":
    main()
