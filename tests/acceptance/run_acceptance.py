"""Elfogadási futás: Screaming Frog, rögtön utána aaa crawl, majd összevetés.

    python -m tests.acceptance.run_acceptance all --resume-test 5
    python -m tests.acceptance.run_acceptance materia --resume-test 5
    python -m tests.acceptance.run_acceptance kk-coach --sf-csv kezi/internal_html.csv

A site-ok egymás után futnak. Site-onként: előbb az SF CLI (`--headless --save-crawl
--config … --export-tabs "Internal:HTML,Response Codes:All"`), a vége után azonnal az
`aaa crawl` egy friss adatbázisba; a kettő nem osztozik a gépen, az oldal/mp tiszta mérés. Utána
a `compare.py`. Az ngx-konfigurációt indulás előtt a közösből generálja
(`make_ngx_config.py`). A fülneveket futás előtt a
telepített SF `--help export-tabs` és `--help bulk-export` listájából ellenőrzi, mert elírt
névnél az export csendben elmarad. A `Links:All Outlinks` bulk export a linkszintű összevetéshez
kell.

Futáskönyvtár: `tests/acceptance/out/<site>-<időbélyeg>/` (gitignore):

- `sf/`: az SF exportjai (`internal_html.csv`, `response_codes_all.csv`, `all_outlinks.csv`);
- `aaa/data/<domain>.duckdb`: az aaa crawl; a meglévő `data/<domain>.duckdb`-t nem érinti;
- `sf.log`, `aaa.log`, `run.json` (parancsok, időpontok, kilépési kódok, az aaa UA-ja és
  commitja), a `compare.py` kimenete, és az `acceptance.md` a jegybe és a PR-be.

`--resume-test N`: a `--resume-on` site-okon (alapból a Materián) egy második, friss aaa crawl
N kész oldal után leáll (a folyamat kilövése, lezárás és checkpoint nélkül), majd a `--resume`
befejezi. Az így kapott adatbázist mezőre összeveti a megszakítás nélküli futáséval
(`resume_diff.csv`).

Kilépési kód: 0, ha minden site összevetése és `--resume` tesztje rendben van.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from aaa2.db.connect import db_path
from aaa2.engine.normalize import UrlPolicy
from aaa2.engine.render import Renderer
from tests.acceptance import compare, make_ngx_config

ACCEPTANCE_DIR = Path(__file__).parent
OUT_DIR = ACCEPTANCE_DIR / "out"
SF_CLI_DEFAULT = Path(r"C:\Program Files (x86)\Screaming Frog SEO Spider\ScreamingFrogSEOSpiderCli.exe")
SF_EXPORT_TABS = ("Internal:HTML", "Response Codes:All")
SF_BULK_EXPORTS = ("Links:All Outlinks",)
SF_EXPORT_FILES = {"Internal:HTML": "internal_html.csv", "Response Codes:All": "response_codes_all.csv",
                   "Links:All Outlinks": "all_outlinks.csv"}
PAGE_FIELDS = ("status", "final_url", "noindex", "canonical", "title", "meta_description", "h1",
               "lang", "hreflang")


@dataclass(frozen=True)
class Site:
    seed: str
    include: str | None
    sf_config: str


SITES = {
    "kk-coach": Site("https://kk.coach/", None, "aaa2-acceptance.seospiderconfig"),
    "materia": Site("https://materia-tm.com/", None, "aaa2-acceptance.seospiderconfig"),
    "ngx": Site("https://valor-software.com/ngx-bootstrap/components", "/ngx-bootstrap/",
                "aaa2-acceptance-ngx.seospiderconfig"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("sites", nargs="+", choices=[*sorted(SITES), "all"])
    parser.add_argument("--sf-csv", type=Path, help="kézi SF internal_html.csv (egy site-hoz)")
    parser.add_argument("--resume-test", type=int, metavar="N",
                        help="második crawl, N kész oldal után kilőve, majd --resume")
    parser.add_argument("--resume-on", nargs="+", default=["materia"], choices=sorted(SITES),
                        help="a --resume-test site-jai (alapból: materia)")
    args = parser.parse_args(argv)
    names = sorted(SITES) if "all" in args.sites else args.sites
    if args.sf_csv and len(names) > 1:
        parser.error("a --sf-csv egy site-hoz tartozik")

    sf_cli = None
    if args.sf_csv is None:
        if "ngx" in names:
            make_ngx_config.main()
        sf_cli = find_sf_cli()
        if sf_cli is None:
            print("Nincs SF CLI; futtasd kézzel (screamingfrog.md), és add meg: --sf-csv")
            return 2
        missing_tabs = [tab for tab in SF_EXPORT_TABS if tab not in sf_help(sf_cli, "export-tabs")]
        missing_tabs += [name for name in SF_BULK_EXPORTS
                         if name not in sf_help(sf_cli, "bulk-export")]
        missing_configs = [ACCEPTANCE_DIR / SITES[name].sf_config for name in names
                           if not (ACCEPTANCE_DIR / SITES[name].sf_config).exists()]
        if missing_tabs or missing_configs:
            print(f"Hiányzó SF-export: {missing_tabs}; hiányzó konfiguráció: "
                  f"{[str(path) for path in missing_configs]}")
            return 2

    user_agent = asyncio.run(aaa_user_agent())
    results = [run_site(name, sf_cli, args.sf_csv,
                        args.resume_test if name in args.resume_on else None, user_agent)
               for name in names]
    print("\n".join(f"{name}: {'rendben' if ok else 'NEM felel meg'} ({run_dir})"
                    for name, ok, run_dir in results))
    return 0 if all(ok for _, ok, _ in results) else 1


def run_site(name: str, sf_cli: Path | None, sf_csv: Path | None, resume_pages: int | None,
             user_agent: str) -> tuple[str, bool, Path]:
    site = SITES[name]
    run_dir = OUT_DIR / f"{name}-{local_now():%Y%m%d-%H%M%S}"
    sf_dir, aaa_dir = run_dir / "sf", run_dir / "aaa"
    sf_dir.mkdir(parents=True)
    aaa_dir.mkdir()
    aaa_cmd = aaa_crawl_command(site)
    sf_cmd = None
    if sf_cli is not None:
        sf_cmd = [str(sf_cli), "--crawl", site.seed, "--headless", "--save-crawl",
                  "--config", str((ACCEPTANCE_DIR / site.sf_config).resolve()),
                  "--output-folder", str(sf_dir.resolve()), "--overwrite",
                  "--export-format", "csv", "--export-tabs", ",".join(SF_EXPORT_TABS),
                  "--bulk-export", ",".join(SF_BULK_EXPORTS)]
    record: dict[str, object] = {
        "site": name, "seed": site.seed, "include": site.include, "aaa_user_agent": user_agent,
        "aaa_commit": git_commit(), "sf_command": sf_cmd, "aaa_command": aaa_cmd,
    }
    print(f"\n=== {name}: {site.seed}", flush=True)

    if sf_cmd:
        with (run_dir / "sf.log").open("w", encoding="utf-8") as sf_log:
            record["sf_started"] = now()
            record["sf_exit"] = subprocess.run(sf_cmd, stdout=sf_log, stderr=subprocess.STDOUT,
                                               check=False).returncode
            record["sf_finished"] = now()
    else:
        shutil.copyfile(sf_csv, sf_dir / "internal_html.csv")
        record["sf_csv"] = str(sf_csv)
    with (run_dir / "aaa.log").open("w", encoding="utf-8") as aaa_log:
        record["aaa_started"] = now()
        record["aaa_exit"] = subprocess.run(aaa_cmd, cwd=aaa_dir, stdout=aaa_log,
                                            stderr=subprocess.STDOUT, check=False).returncode
        record["aaa_finished"] = now()

    db = aaa_dir / db_path(UrlPolicy.from_seed(site.seed).domain)
    internal_html = sf_dir / SF_EXPORT_FILES["Internal:HTML"]
    response_codes = sf_dir / SF_EXPORT_FILES["Response Codes:All"]
    failure = None
    if record.get("sf_exit") or not internal_html.exists():
        failure = f"Az SF nem adott exportot (kilépési kód {record.get('sf_exit')}).\n" + tail(
            run_dir / "sf.log")
    elif record["aaa_exit"] or not db.exists():
        failure = "Az aaa crawl hibával állt le.\n" + tail(run_dir / "aaa.log")
    if failure:
        write_record(run_dir, record)
        print(failure)
        return name, False, run_dir

    compare_args = ["--sf-csv", str(internal_html), "--db", str(db), "--out", str(run_dir),
                    "--quiet"]
    if response_codes.exists():
        compare_args += ["--sf-response-codes", str(response_codes)]
    outlinks = sf_dir / SF_EXPORT_FILES["Links:All Outlinks"]
    if outlinks.exists():
        compare_args += ["--sf-outlinks", str(outlinks)]
    if site.include:
        compare_args += ["--include", site.include]
    record["compare_exit"] = compare.main(compare_args)
    ok = record["compare_exit"] == 0
    if resume_pages:
        record["resume_test"] = resume_test(site, run_dir, db, resume_pages)
        ok = ok and record["resume_test"]["passed"]
    write_record(run_dir, record)
    report = acceptance_report(record, (run_dir / "summary.md").read_text(encoding="utf-8"))
    (run_dir / "acceptance.md").write_text(report, encoding="utf-8")
    print(report)
    return name, ok, run_dir


def resume_test(site: Site, run_dir: Path, reference_db: Path, pages: int) -> dict:
    """Második crawl, `pages` kész oldal után kilőve, majd `--resume`; mezőre összevetve a
    megszakítás nélküli futás adatbázisával. A kész oldalakat az aaa oldalankénti kimeneti sorai
    számolják."""
    work = run_dir / "resume"
    work.mkdir()
    db = work / db_path(UrlPolicy.from_seed(site.seed).domain)
    result: dict[str, object] = {"interrupt_after_pages": pages, "interrupted": False}
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with (run_dir / "resume.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            aaa_crawl_command(site, quiet=False), cwd=work, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
        seen = 0
        for line in process.stdout:
            log.write(line)
            if "  http" in line:
                seen += 1
                if seen >= pages:
                    process.kill()
                    result["interrupted"] = True
                    break
        process.wait()
        process.stdout.close()
        before = query(db, "SELECT status, count(*) FROM crawl_queue GROUP BY status",
                       read_only=False) if db.exists() else []
        result["queue_before_resume"] = dict(before)
        result["pages_before_resume"] = query(db, "SELECT count(*) FROM pages")[0][0] if (
            db.exists()) else 0
        log.write("\n--- --resume ---\n")
        log.flush()
        result["resume_exit"] = subprocess.run(
            aaa_crawl_command(site) + ["--resume"], cwd=work, stdout=log,
            stderr=subprocess.STDOUT, check=False).returncode
    result["queue_after_resume"] = dict(
        query(db, "SELECT status, count(*) FROM crawl_queue GROUP BY status"))
    differences = compare_databases(reference_db, db)
    with (run_dir / "resume_diff.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["url", "field", "uninterrupted", "resumed"])
        writer.writeheader()
        writer.writerows(differences)
    result["differences"] = len(differences)
    result["differences_by_field"] = dict(Counter(d["field"] for d in differences))
    left = sum(n for state, n in result["queue_after_resume"].items() if state not in ("done", "failed"))
    result["left_in_queue"] = left
    result["passed"] = bool(result["interrupted"]) and result["resume_exit"] == 0 and left == 0 and (
        not differences)
    return result


def compare_databases(reference: Path, candidate: Path) -> list[dict[str, object]]:
    """Mezőnkénti eltérés két crawl adatbázisa között: az oldalak halmaza, a `PAGE_FIELDS`,
    oldalanként a linkek (cél, pozíció, nofollow) és a címsorok (szint, szöveg) multihalmaza."""
    left, right = snapshot(reference), snapshot(candidate)
    differences: list[dict[str, object]] = []
    for url in sorted(set(left) | set(right)):
        if url not in right or url not in left:
            differences.append({"url": url, "field": "oldal",
                                "uninterrupted": url in left, "resumed": url in right})
            continue
        for name, value in left[url].items():
            if value != right[url][name]:
                differences.append({"url": url, "field": name, "uninterrupted": _brief(value),
                                    "resumed": _brief(right[url][name])})
    return differences


def snapshot(db: Path) -> dict[str, dict[str, object]]:
    pages = {}
    rows = query(db, f"SELECT page_id, url, {', '.join(PAGE_FIELDS)} FROM pages")
    ids = {}
    for page_id, url, *values in rows:
        ids[page_id] = url
        pages[url] = {name: (sorted(value) if isinstance(value, list) else value)
                      for name, value in zip(PAGE_FIELDS, values, strict=True)}
        pages[url]["links"] = Counter()
        pages[url]["headings"] = Counter()
    for page_id, to_url, position, nofollow in query(
            db, "SELECT from_page_id, to_url, position, nofollow FROM links"):
        pages[ids[page_id]]["links"][(to_url, position, bool(nofollow))] += 1
    for page_id, level, text in query(db, "SELECT page_id, level, text FROM headings"):
        pages[ids[page_id]]["headings"][(level, text)] += 1
    return pages


def aaa_crawl_command(site: Site, *, quiet: bool = True) -> list[str]:
    command = [sys.executable, "-m", "aaa2.cli.main", "crawl", site.seed]
    command += ["--quiet"] if quiet else []
    return command + (["--include", site.include] if site.include else [])


def acceptance_report(record: dict, summary: str) -> str:
    lines = [summary.rstrip(), "", "### Futás", ""]
    if record.get("sf_started"):
        gap = (datetime.fromisoformat(record["aaa_started"])
               - datetime.fromisoformat(record["sf_finished"])).total_seconds()
        lines.append(f"- **SF indult:** {record['sf_started']}, kész {record.get('sf_finished')}; "
                     f"az aaa {gap:.0f} mp-cel utána indult.")
    else:
        lines.append(f"- **SF:** kézi export ({record.get('sf_csv')}).")
    lines += [
        f"- **aaa indult:** {record['aaa_started']}, kész {record['aaa_finished']}.",
        f"- **aaa UA:** `{record['aaa_user_agent']}`; commit {record['aaa_commit']}.",
    ]
    test = record.get("resume_test")
    if test:
        if not test["interrupted"]:
            verdict = "a crawl a megszakítás előtt végzett, a teszt nem döntő"
        else:
            verdict = (
                f"{test['pages_before_resume']} oldalsor a kilövéskor, a --resume kilépési kódja "
                f"{test['resume_exit']}, várakozó sor utána {test['left_in_queue']}; eltérés a "
                f"megszakítás nélküli adatbázistól: {test['differences']}"
                + (f" ({test['differences_by_field']})" if test["differences"] else ""))
        lines.append(f"- **--resume teszt** ({test['interrupt_after_pages']} kész oldal után "
                     f"kilőve): {verdict}. " + ("Rendben." if test["passed"] else "NEM felel meg."))
    return "\n".join(lines) + "\n"


def sf_help(sf_cli: Path, topic: str) -> set[str]:
    """A telepített SF `--help <topic>` listája soronként; a név karakterre egyezzen."""
    completed = subprocess.run([str(sf_cli), "--help", topic], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", check=False)
    return {line.strip() for line in (completed.stdout + completed.stderr).splitlines()}


async def aaa_user_agent() -> str:
    async with Renderer(concurrency=1) as renderer:
        return renderer.user_agent


def find_sf_cli() -> Path | None:
    for candidate in (os.environ.get("SF_CLI"), shutil.which("ScreamingFrogSEOSpiderCli"),
                      str(SF_CLI_DEFAULT)):
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return None


def query(db: Path, sql: str, *, read_only: bool = True) -> list[tuple]:
    """Lekérdezés egy lezárt crawl adatbázisán; kilőtt folyamat után `read_only=False`, hogy a
    WAL visszajátszódjon."""
    con = duckdb.connect(str(db), read_only=read_only)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def git_commit() -> str:
    completed = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                               text=True, check=False)
    return completed.stdout.strip() or "?"


def now() -> str:
    return local_now().isoformat(timespec="seconds")


def local_now() -> datetime:
    return datetime.now(UTC).astimezone()


def tail(path: Path, lines: int = 25) -> str:
    if not path.exists():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def write_record(run_dir: Path, record: dict) -> None:
    (run_dir / "run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2,
                                                 default=str), encoding="utf-8")


def _brief(value: object) -> str:
    text = repr(dict(value) if isinstance(value, Counter) else value)
    return text if len(text) <= 300 else text[:300] + "…"


if __name__ == "__main__":
    sys.exit(main())
