"""Elfogadási futás: Screaming Frog és aaa crawl ugyanabban a percben, majd összevetés.

A referencia-site-ok egyikén fut; az összevetés a `compare_sf.py`.

    python -m tests.acceptance.run_acceptance materia
    python -m tests.acceptance.run_acceptance ngx --resume-check 20
    python -m tests.acceptance.run_acceptance kk-coach --sf-csv kezi/internal_html.csv

Futáskönyvtár: `data/acceptance/<site>-<időbélyeg>/`:

- `aaa/data/<domain>.duckdb`: friss aaa crawl; a meglévő `data/<domain>.duckdb`-t nem érinti;
- `sf/internal_html.csv`: az SF exportja, vagy a `--sf-csv` másolata;
- `aaa.log`, `sf.log`: a két folyamat kimenete;
- `run.json`: parancsok, indulás és befejezés, kilépési kódok, az aaa UA-ja és commitja;
- `url_diff.csv`, `status_diff.csv`, `link_diff.csv`, `summary.md`: az összevetés;
- `acceptance.md`: futásadatok és összegzés együtt, a jegybe és a PR-leírásba.

Az SF a CLI-jével (`ScreamingFrogSEOSpiderCli`, vagy az `SF_CLI` környezeti változó) és a
site `.seospiderconfig`-jával fut a `tests/acceptance/sf/` alól (lásd `screamingfrog.md`).
Előbb az SF indul, rögtön utána az aaa; a két crawl párhuzamosan fut. `--sf-csv` esetén az SF
nem indul.

`--resume-check N`: egy második, friss aaa crawl N kész oldal után leáll (a folyamat kilövése),
majd a `--resume` befejezi. Ellenőrzés: nem maradt várakozó sor, és az URL-halmaz egyezik az
első crawléval.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from aaa2.db.connect import db_path
from aaa2.engine.normalize import UrlPolicy
from aaa2.engine.render import Renderer
from tests.acceptance import compare_sf

ACCEPTANCE_DIR = Path(__file__).parent
SF_CONFIG_DIR = ACCEPTANCE_DIR / "sf"
RUNS_DIR = Path("data") / "acceptance"
SF_CLI_DEFAULT = Path(r"C:\Program Files (x86)\Screaming Frog SEO Spider\ScreamingFrogSEOSpiderCli.exe")
SF_EXPORT_TABS = "Internal:HTML,Response Codes:Blocked by Robots.txt"


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
    parser.add_argument("site", choices=sorted(SITES))
    parser.add_argument("--sf-csv", type=Path, help="kézzel exportált SF internal_html.csv")
    parser.add_argument("--sf-config", type=Path, help="a site .seospiderconfig-ja helyett")
    parser.add_argument("--resume-check", type=int, metavar="N",
                        help="második crawl, N kész oldal után kilőve, majd --resume")
    args = parser.parse_args(argv)
    site = SITES[args.site]

    sf_cmd = None
    if args.sf_csv is None:
        sf_cli = find_sf_cli()
        config = args.sf_config or SF_CONFIG_DIR / site.sf_config
        if sf_cli is None:
            print("Nincs SF CLI; futtasd kézzel (screamingfrog.md), és add meg: --sf-csv")
            return 2
        if not config.exists():
            print(f"Hiányzik az SF-konfiguráció: {config}\nA beállítások: "
                  f"{ACCEPTANCE_DIR / 'screamingfrog.md'}")
            return 2

    run_dir = RUNS_DIR / f"{args.site}-{local_now():%Y%m%d-%H%M%S}"
    (run_dir / "aaa").mkdir(parents=True)
    (run_dir / "sf").mkdir()
    if args.sf_csv is None:
        sf_cmd = [str(sf_cli), "--crawl", site.seed, "--headless", "--config", str(config.resolve()),
                  "--output-folder", str((run_dir / "sf").resolve()), "--overwrite",
                  "--export-format", "csv", "--export-tabs", SF_EXPORT_TABS]
    aaa_cmd = aaa_crawl_command(site)
    record: dict[str, object] = {
        "site": args.site, "seed": site.seed, "include": site.include,
        "aaa_user_agent": asyncio.run(aaa_user_agent()), "aaa_commit": git_commit(),
        "sf_command": sf_cmd, "aaa_command": aaa_cmd,
    }

    sf_proc = sf_log = None
    if sf_cmd:
        sf_log = (run_dir / "sf.log").open("w", encoding="utf-8")
        record["sf_started"] = now()
        sf_proc = subprocess.Popen(sf_cmd, stdout=sf_log, stderr=subprocess.STDOUT)
    with (run_dir / "aaa.log").open("w", encoding="utf-8") as aaa_log:
        record["aaa_started"] = now()
        aaa_proc = subprocess.Popen(aaa_cmd, cwd=run_dir / "aaa", stdout=aaa_log,
                                    stderr=subprocess.STDOUT)
        record["aaa_exit"] = aaa_proc.wait()
        record["aaa_finished"] = now()
    if sf_proc:
        record["sf_exit"] = sf_proc.wait()
        record["sf_finished"] = now()
        sf_log.close()
    else:
        shutil.copyfile(args.sf_csv, run_dir / "sf" / "internal_html.csv")
        record["sf_csv"] = str(args.sf_csv)

    db = run_dir / "aaa" / db_path(UrlPolicy.from_seed(site.seed).domain)
    sf_csv = run_dir / "sf" / "internal_html.csv"
    if record.get("sf_exit") or not sf_csv.exists():
        write_record(run_dir, record)
        print(f"Az SF nem adott exportot (kilépési kód {record.get('sf_exit')}); napló: "
              f"{run_dir / 'sf.log'}\n" + tail(run_dir / "sf.log"))
        return 1
    if record["aaa_exit"] or not db.exists():
        write_record(run_dir, record)
        print(f"Az aaa crawl hibával állt le; napló: {run_dir / 'aaa.log'}\n"
              + tail(run_dir / "aaa.log"))
        return 1

    compare_args = ["--sf-csv", str(sf_csv), "--db", str(db), "--out", str(run_dir)]
    if site.include:
        compare_args += ["--include", site.include]
    compare_sf.main(compare_args)
    if args.resume_check:
        record["resume_check"] = resume_check(site, run_dir, db, args.resume_check)
    write_record(run_dir, record)
    report = acceptance_report(record, (run_dir / "summary.md").read_text(encoding="utf-8"))
    (run_dir / "acceptance.md").write_text(report, encoding="utf-8")
    print(f"\n{report}\nFutáskönyvtár: {run_dir}")
    return 0


def resume_check(site: Site, run_dir: Path, reference_db: Path, pages: int) -> dict:
    """Második crawl, `pages` kész oldal után kilőve, majd `--resume`; az eredmény a
    `run.json`-ba. A kész oldalakat az aaa oldalankénti kimeneti sorai számolják."""
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
        result["pages_before_resume"] = (
            query(db, "SELECT count(*) FROM pages", read_only=False)[0][0] if db.exists() else 0)
        log.write("\n--- --resume ---\n")
        log.flush()
        result["resume_exit"] = subprocess.run(
            aaa_crawl_command(site) + ["--resume"], cwd=work, stdout=log,
            stderr=subprocess.STDOUT, check=False).returncode
    urls, queue = page_urls(db), queue_states(db)
    reference = page_urls(reference_db)
    result.update({
        "pages_after_resume": len(urls), "queue": queue,
        "left_in_queue": sum(n for state, n in queue.items() if state not in ("done", "failed")),
        "only_in_reference": sorted(reference - urls), "only_in_resumed": sorted(urls - reference),
    })
    return result


def aaa_crawl_command(site: Site, *, quiet: bool = True) -> list[str]:
    command = [sys.executable, "-m", "aaa2.cli.main", "crawl", site.seed]
    command += ["--quiet"] if quiet else []
    return command + (["--include", site.include] if site.include else [])


def acceptance_report(record: dict, summary: str) -> str:
    lines = [summary.rstrip(), "", "### Futás", ""]
    if record.get("sf_started"):
        lines.append(f"- **SF indult:** {record['sf_started']}, kész {record.get('sf_finished')}.")
    else:
        lines.append(f"- **SF:** kézi export ({record.get('sf_csv')}).")
    lines += [
        f"- **aaa indult:** {record['aaa_started']}, kész {record['aaa_finished']}.",
        f"- **aaa UA:** `{record['aaa_user_agent']}`; commit {record['aaa_commit']}.",
    ]
    check = record.get("resume_check")
    if check:
        verdict = ("a crawl a megszakítás előtt végzett, a próba nem döntő"
                   if not check["interrupted"] else
                   f"kilövéskor {check['pages_before_resume']} oldalsor, a --resume (kilépési kód "
                   f"{check['resume_exit']}) után {check['pages_after_resume']}; várakozó sor "
                   f"{check['left_in_queue']}; az első crawlhoz képest hiányzik "
                   f"{len(check['only_in_reference'])}, többlet {len(check['only_in_resumed'])} URL")
        lines.append(f"- **--resume próba** ({check['interrupt_after_pages']} oldal után): "
                     f"{verdict}.")
    return "\n".join(lines) + "\n"


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


def page_urls(db: Path) -> set[str]:
    return {url for (url,) in query(db, "SELECT url FROM pages")}


def queue_states(db: Path) -> dict[str, int]:
    return dict(query(db, "SELECT status, count(*) FROM crawl_queue GROUP BY status"))


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
    (run_dir / "run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                      encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
