"""Mérés: ugyanazon az adatbázison kétszer futtatva bájtra azonos-e a kimenet.

`python -m tests.acceptance.determinism [--hashes FÁJL] [--write] [--keep MAPPA] [--jobs N]
<domain>=<adatbázis> …`

Site-onként az adatbázis munkamásolatán kétszer egymás után lefut a site-lépés LLM nélkül
(`aaa entities --no-llm`), a gráf (`aaa graph`), a megállapítások (`aaa findings`) és az
entitásjelentés (`aaa entity-report`); a két futás kimeneti fájljai bájtra összevetve. A forrás
adatbázis nem változik. A hash a futásfüggő sorok rögzített alakjával számol (`RUN_LINES`: a
futásjelentésben a szabálykör sorszáma és időpontja, és a site-kör sorszáma); a kimeneti fájl
nem változik, és a futásjelentés minden más sora (a tárolt LLM-futás sorszáma és ideje is)
beleszámít. `--hashes`: a kimenetek sha256-a a megadott fájl rögzített értékeivel összevetve;
`--write`-tal a lefutott site-ok rögzített értékei a mostaniakra cserélődnek (a többi site-é
marad), de csak ha a két futás között nincs eltérés. Kilépési kód: 0, ha sehol nincs eltérés.
`--keep`: a munkamásolatok és a
kimenetek a megadott mappában maradnak. `--jobs`: ennyi site fut egyszerre (alapból mind; a
site-ok külön munkamásolaton, külön folyamatokban futnak, a sorok a megadott sorrendben jelennek
meg, a végén site-onként az idővel). A site-ok lépései saját munkamappából futnak, a közös
adatbázis (`data/shared.duckdb`) ottani másolatával; az eredeti nem változik. LLM-hívás nincs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SHARED = Path("data") / "shared.duckdb"
RUN_REPORT = "-run.md"
# a futásjelentés futásfüggő sorai: (minta, rögzített alak); csak a sor eleji, illesztett rész
# cserélődik, a sor többi része beleszámít a hash-be
RUN_LINES = (
    (re.compile(rb"^- szab\xc3\xa1lyk\xc3\xb6r: #\d+, (?:\d{4}-\d{2}-\d{2} \d{2}:\d{2}"
                rb"|nincs lez\xc3\xa1rva)", re.MULTILINE),
     "- szabálykör: #N, <időpont>".encode()),
    (re.compile(rb"^- site-k\xc3\xb6r: #\d+", re.MULTILINE), "- site-kör: #N".encode()),
)
STEPS = (("entities", "--no-llm"), ("graph", "--out"), ("findings", "--out"),
         ("entity-report", "--out"))


def run_once(domain: str, db: Path, out: Path, cwd: Path) -> None:
    """Egy futás a `cwd` munkamappából: a közös adatbázis (`data/shared.duckdb`) a mappa saját
    példánya, így több site futhat egyszerre (a fájlt egyszerre egy folyamat tarthatja)."""
    out.mkdir(parents=True)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        filter(None, [str(REPO), os.environ.get("PYTHONPATH")]))}
    for command, flag in STEPS:
        args = [sys.executable, "-m", "aaa2.cli.main", command, domain, "--db", str(db)]
        args += [flag] if flag == "--no-llm" else [flag, str(out)]
        done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              check=False, cwd=cwd, env=env)
        if done.returncode != 0:
            raise RuntimeError(f"{command} {domain}: {done.stderr[-2000:]}")


def stable_bytes(path: Path) -> bytes:
    """A fájl tartalma a hash-hez; a futásjelentés (`-run.md`) futásfüggő sorai (`RUN_LINES`)
    rögzített alakban, mert a szabálykör és a site-kör sorszáma és ideje futásonként más. A fájl
    maga nem változik."""
    data = path.read_bytes()
    if path.name.endswith(RUN_REPORT):
        for pattern, fixed in RUN_LINES:
            data = pattern.sub(fixed, data)
    return data


def differences(one: dict[str, str], other: dict[str, str]) -> list[str]:
    """A fájlok, amelyeknek a hash-e a két készletben más, vagy csak az egyikben szerepelnek."""
    return sorted(name for name in set(one) | set(other) if one.get(name) != other.get(name))


def shown(files: list[str]) -> str:
    return f"{len(files)} eltérés" + (" (" + ", ".join(files) + ")" if files else "")


def digests(out: Path) -> dict[str, str]:
    return {path.name: hashlib.sha256(stable_bytes(path)).hexdigest()
            for path in sorted(out.iterdir()) if path.is_file()}


def probe(name: str, domain: str, source: Path, work: Path
          ) -> tuple[dict[str, str], list[str], float]:
    """A kimenetek sha256-a az első futásból, a két futás között eltérő fájlok, és az idő (mp)."""
    started = time.monotonic()
    work = work.resolve()
    db = work / f"{name}.duckdb"
    shutil.copyfile(source, db)
    cwd = work / f"{name}-cwd"
    (cwd / "data").mkdir(parents=True)
    if SHARED.exists():
        shutil.copyfile(SHARED, cwd / "data" / SHARED.name)
    first, second = work / f"{name}-1", work / f"{name}-2"
    run_once(domain, db, first, cwd)
    run_once(domain, db, second, cwd)
    one, two = digests(first), digests(second)
    return one, differences(one, two), time.monotonic() - started


def main() -> None:
    args = sys.argv[1:]
    write = "--write" in args
    hashes_file = None
    if "--hashes" in args:
        hashes_file = Path(args[args.index("--hashes") + 1])
    sites = [a for a in args if "=" in a]
    recorded = json.loads(hashes_file.read_text(encoding="utf-8")) \
        if hashes_file and hashes_file.exists() else {}
    current: dict[str, dict[str, str]] = {}
    failed = 0
    keep = Path(args[args.index("--keep") + 1]) if "--keep" in args else None
    if keep is not None:
        shutil.rmtree(keep, ignore_errors=True)
        keep.mkdir(parents=True)
    jobs = int(args[args.index("--jobs") + 1]) if "--jobs" in args else len(sites)
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as temporary, \
            ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        tmp = keep or temporary
        named = [(Path(source).stem, domain, Path(source))
                 for domain, source in (site.split("=", 1) for site in sites)]
        if len({name for name, _, _ in named}) != len(named):
            raise SystemExit("két site adatbázisának azonos a neve")
        running = [pool.submit(probe, name, domain, source, Path(tmp))
                   for name, domain, source in named]
        for (name, _, _), future in zip(named, running, strict=True):
            one, differing, seconds = future.result()
            current[name] = one
            line = f"{name}: {len(one)} fájl; a két futás között: {shown(differing)}"
            failed += bool(differing)
            if hashes_file and not write:
                if name not in recorded:
                    failed += 1
                    line += "; nincs rögzített alapja"
                else:
                    changed = differences(one, recorded[name])
                    failed += bool(changed)
                    line += f"; a rögzítetthez képest: {shown(changed)}"
            print(f"{line} [{seconds:.0f} mp]", flush=True)
    print(f"összesen {time.monotonic() - started:.0f} mp, {jobs} site egyszerre", flush=True)
    if hashes_file and write:
        if failed:
            print(f"nem rögzítve (a két futás között eltérés van): {hashes_file}")
        else:
            hashes_file.write_text(
                json.dumps({**recorded, **current}, ensure_ascii=False, indent=1, sort_keys=True)
                + "\n", encoding="utf-8", newline="\n")
            print(f"rögzítve: {hashes_file} ({', '.join(sorted(current))})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
