"""Mérés: ugyanazon az adatbázison kétszer futtatva bájtra azonos-e a kimenet.

`python -m tests.acceptance.determinism [--hashes FÁJL] [--write] [--keep MAPPA]
<domain>=<adatbázis> …`

Site-onként az adatbázis munkamásolatán kétszer egymás után lefut a site-lépés LLM nélkül
(`aaa entities --no-llm`), a gráf (`aaa graph`), a megállapítások (`aaa findings`) és az
entitásjelentés (`aaa entity-report`); a két futás kimeneti fájljai bájtra összevetve. A forrás
adatbázis nem változik. A futásjelentés a futás sorszáma és időpontja nélkül számít. `--hashes`: a kimenetek sha256-a a megadott fájl rögzített értékeivel
összevetve; `--write`-tal a fájl a mostani értékekkel íródik. `--keep`: a munkamásolatok és a
kimenetek a megadott mappában maradnak. LLM-hívás nincs."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_REPORT = "-run.md"
RUN_MARKS = (re.compile(rb"#\d+"), re.compile(rb"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?"))
STEPS = (("entities", "--no-llm"), ("graph", "--out"), ("findings", "--out"),
         ("entity-report", "--out"))


def run_once(domain: str, db: Path, out: Path) -> None:
    out.mkdir(parents=True)
    for command, flag in STEPS:
        args = [sys.executable, "-m", "aaa2.cli.main", command, domain, "--db", str(db)]
        args += [flag] if flag == "--no-llm" else [flag, str(out)]
        done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              check=False)
        if done.returncode != 0:
            raise RuntimeError(f"{command} {domain}: {done.stderr[-2000:]}")


def stable_bytes(path: Path) -> bytes:
    """A fájl tartalma; a futásjelentésből (`-run.md`) a futás sorszáma és időpontja nélkül,
    mert az futásonként szükségképpen más."""
    data = path.read_bytes()
    if path.name.endswith(RUN_REPORT):
        for pattern in RUN_MARKS:
            data = pattern.sub(b"", data)
    return data


def digests(out: Path) -> dict[str, str]:
    return {path.name: hashlib.sha256(stable_bytes(path)).hexdigest()
            for path in sorted(out.iterdir()) if path.is_file()}


def probe(name: str, domain: str, source: Path, work: Path) -> tuple[dict[str, str], list[str]]:
    """A kimenetek sha256-a az első futásból, és a két futás között eltérő fájlok."""
    db = work / f"{name}.duckdb"
    shutil.copyfile(source, db)
    first, second = work / f"{name}-1", work / f"{name}-2"
    run_once(domain, db, first)
    run_once(domain, db, second)
    one, two = digests(first), digests(second)
    differing = sorted(f for f in set(one) | set(two) if one.get(f) != two.get(f))
    return one, differing


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
    with tempfile.TemporaryDirectory() as temporary:
        tmp = keep or temporary
        for site in sites:
            domain, source = site.split("=", 1)
            name = Path(source).stem
            one, differing = probe(name, domain, Path(source), Path(tmp))
            current[name] = one
            line = f"{name}: {len(one)} fájl, a két futás között eltérő: {len(differing)}"
            if differing:
                failed += 1
                line += " (" + ", ".join(differing) + ")"
            if hashes_file and not write:
                changed = sorted(f for f in set(one) | set(recorded.get(name, {}))
                                 if one.get(f) != recorded.get(name, {}).get(f))
                line += f"; a rögzítetthez képest eltérő: {len(changed)}"
                if changed:
                    failed += 1
                    line += " (" + ", ".join(changed) + ")"
            print(line, flush=True)
    if hashes_file and write:
        hashes_file.write_text(json.dumps(current, ensure_ascii=False, indent=1, sort_keys=True)
                               + "\n", encoding="utf-8", newline="\n")
        print(f"rögzítve: {hashes_file}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
