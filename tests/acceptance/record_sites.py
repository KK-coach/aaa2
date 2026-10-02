"""Az idegen site-ok (M2/7 B) felvétele: élő crawl az M1 motorral, közben minden válasz felvéve
(`tests/fixtures/<név>/`), a kapcsolat a `data/compare/<név>.duckdb`-be; utána ellenőrzés a
felvételből, hálózat nélkül. A felvétel a várt oldalszámot és időt is kiírja (a sitemap alapján),
és megáll, ha a crawl ennek kétszerese fölé megy (`tests/recorded.py`: `OVERRUN_FACTOR`).

Az ellenőrzés alapból mintavételes: `VERIFY_SAMPLE` (50) oldalt renderel újra a felvételből (a
minta a név szerinti rögzített véletlen), és a státuszt, a nyers HTML hash-ét, a title-t, a H1-et
és a canonicalt veti össze a felvételkori sorral. `verify --full`: a teljes crawl visszajátszása
és a két URL-halmaz összevetése. A `replay` a készlet mostani beállításával (például sitemap-mód)
játssza vissza a felvételt, és az eredményt a `data/compare/<név>.duckdb`-be írja; a korábbi
adatbázis `<név>-full.duckdb` néven marad meg. A `report` site-onként az oldalszámot adja
oldaltípusonként (`pages.page_types`, a site-fájl mintáival), és a nem oldal-válaszokat (kép,
PDF) és a hibákat.

    python -m tests.acceptance.record_sites record marketinglens-crawl duex-crawl
    python -m tests.acceptance.record_sites verify duex-crawl
    python -m tests.acceptance.record_sites verify --full duex-crawl
    python -m tests.acceptance.record_sites replay serafim-crawl
    python -m tests.acceptance.record_sites report marketinglens-crawl duex-crawl
"""
from __future__ import annotations

import argparse
import asyncio
import random
import time
from pathlib import Path

import duckdb

from aaa2.db.connect import DATA_DIR
from aaa2.engine.normalize import UrlPolicy
from aaa2.engine.parse import parse_page
from aaa2.resolver.overrides import load_site_config
from aaa2.resolver.pages import PAGE_TYPES, page_types
from tests.recorded import (
    REFERENCE_SETS,
    SITE_SETS,
    VERIFY_SAMPLE,
    record_crawl,
    replay_crawl,
    replay_pages,
)

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
    print(f"{name}: {expectation(summary)}")
    if summary.stopped:
        raise SystemExit(f"{name}: A CRAWL MEGÁLLT ({summary.stopped}); a készlet beállítása "
                         f"(kizárások, sitemap-mód, oldalkorlát) javításra szorul")
    verify(name, con)


def expectation(summary) -> str:
    """A sitemap alapján várt és a valós oldalszám és idő egy sorban."""
    handled = summary.pages_done + summary.pages_failed + summary.pages_skipped
    if summary.expected_pages is None:
        return f"várt oldalszám nincs (üres sitemap); valós {handled} oldal, {summary.seconds:.0f} mp"
    expected_time = (f"{summary.expected_seconds:.0f} mp" if summary.expected_seconds is not None
                     else "nincs becslés")
    return (f"várt {summary.expected_pages} oldal, {expected_time}; valós {handled} oldal, "
            f"{summary.seconds:.0f} mp")


def verify(name: str, live: duckdb.DuckDBPyConnection | None = None, *, full: bool = False,
           sample: int = VERIFY_SAMPLE) -> int:
    """A felvétel ellenőrzése hálózat nélkül; az eltérések száma. Alapból `sample` oldal
    újrarenderelése a felvételből; `full`: a teljes crawl visszajátszása."""
    live = live or duckdb.connect(str(OUT_DIR / f"{name}.duckdb"), read_only=True)
    return verify_full(name, live) if full else verify_sample(name, live, sample)


def verify_sample(name: str, live: duckdb.DuckDBPyConnection, sample: int) -> int:
    """`sample` sikeres oldal (a név szerinti rögzített véletlen minta) újrarenderelve a
    felvételből: a státusz, a nyers HTML hash-e, a title, a H1 és a canonical egyezik-e a
    felvételkori sorral."""
    started = time.monotonic()
    _, options = REFERENCE_SETS[name]
    rows = {row[0]: row[1:] for row in live.execute(
        "SELECT url, status, raw_html_hash, title, h1, canonical FROM pages "
        "WHERE error IS NULL AND status BETWEEN 200 AND 299 AND rendered_html IS NOT NULL "
        "AND final_url IS NOT DISTINCT FROM url ORDER BY url").fetchall()}
    chosen = sorted(random.Random(name).sample(sorted(rows), min(sample, len(rows))))
    results = asyncio.run(replay_pages(name, chosen, options))
    if results is None:
        raise SystemExit(f"nincs felvétel: {name}")
    site_seed, https_redirect, trailing_slash = live.execute(
        "SELECT seed_url, https_redirect, trailing_slash FROM site").fetchone()
    policy = UrlPolicy.from_seed(site_seed, https_redirect=bool(https_redirect),
                                 trailing_slash=trailing_slash)
    different = []
    for url, result in zip(chosen, results, strict=True):
        found: tuple = (result.status, result.raw_html_hash, None, None, None)
        if result.rendered_html and result.error is None:
            parsed = parse_page(result.rendered_html, result.final_url or url, policy,
                                result.headers)
            found = (result.status, result.raw_html_hash, parsed.title, parsed.h1,
                     parsed.canonical)
        if found != rows[url]:
            fields = ("státusz", "nyers hash", "title", "H1", "canonical")
            different.append((url, [f for f, a, b in zip(fields, rows[url], found, strict=True)
                                    if a != b]))
    print(f"{name}: mintavételes ellenőrzés, {len(chosen)} oldal a {len(rows)}-ból "
          f"({time.monotonic() - started:.0f} mp); egyezik {len(chosen) - len(different)}, "
          f"eltér {len(different)}")
    for url, fields in different[:20]:
        print(f"   eltér ({', '.join(fields)}): {url}")
    return len(different)


def verify_full(name: str, live: duckdb.DuckDBPyConnection) -> int:
    """A visszajátszott crawl URL-jei és státuszai egyeznek-e a felvételkoriakkal."""
    seed, options = REFERENCE_SETS[name]
    replayed = asyncio.run(replay_crawl(name, seed, options))
    if replayed is None:
        raise SystemExit(f"nincs felvétel: {name}")
    before, after = urls(live), urls(replayed[1])
    print(f"{name}: felvétel {len(before)} oldal, visszajátszás {len(after)} oldal; "
          f"csak élőben {len(before - after)}, csak visszajátszva {len(after - before)}")
    for url, status in sorted(before ^ after, key=lambda row: (row[0], row[1] or 0))[:20]:
        print(f"   eltér: {url} {status}")
    return len(before ^ after)


def replay(name: str) -> None:
    """A felvétel visszajátszása a készlet mostani beállításával a `data/compare/<név>.duckdb`-be;
    a meglévő adatbázis `<név>-full.duckdb` néven marad meg (ha ilyen még nincs)."""
    started = time.monotonic()
    seed, options = REFERENCE_SETS[name]
    replayed = asyncio.run(replay_crawl(name, seed, options))
    if replayed is None:
        raise SystemExit(f"nincs felvétel: {name}")
    recording, con = replayed
    path, kept = OUT_DIR / f"{name}.duckdb", OUT_DIR / f"{name}-full.duckdb"
    if path.exists() and not kept.exists():
        path.rename(kept)
    save(con, path)
    (pages, ok) = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE error IS NULL AND status BETWEEN 200 AND 299) "
        "FROM pages").fetchone()
    print(f"{name}: visszajátszva {pages} oldal (sikeres {ok}), a felvételből hiányzó kérés "
          f"{len(set(recording.misses))}, {time.monotonic() - started:.0f} mp → {path}")


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
    parser.add_argument("command", choices=["record", "verify", "replay", "report"])
    parser.add_argument("names", nargs="+", choices=sorted(SITE_SETS))
    parser.add_argument("--full", action="store_true",
                        help="verify: a teljes crawl visszajátszása a mintavétel helyett")
    args = parser.parse_args(argv)
    if args.command == "report":
        text = report(args.names)
        REPORT.write_text(text, encoding="utf-8")
        print(text)
        return
    for name in args.names:
        if args.command == "record":
            record(name)
        elif args.command == "replay":
            replay(name)
        else:
            verify(name, full=args.full)


if __name__ == "__main__":
    main()
