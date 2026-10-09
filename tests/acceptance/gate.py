"""Merge előtti kapu egy paranccsal: a teljes tesztsor, utána (nem egyszerre) a hash-ellenőrzés
a nyolc készleten.

`python -m tests.acceptance.gate <futásmappa> [--update-hashes] [--jobs N] [név=útvonal …]`

A repó gyökeréből fut. A `<futásmappa>` nem létezhet (ha létezik, a kapu nem indul el), így
korábbi futás fájljából nem olvasható eredmény. A mappában: `run.txt` (a commit és az indulás
ideje), `suite.out` (a teljes sor kimenete), `hash.out` (a hash-ellenőrzés kimenete), `keep/` (a
munkamásolatok és a kimenetek), `done.txt` (lépésenként a kilépési kód és az idő).

A készletek (`SITES`): a hat referencia-készlet és a két bolt, a `data/m3/<név>.duckdb`
adatbázisból; `név=útvonal` egy készlet adatbázisát máshonnan veszi. A hash-ellenőrzés a
`tests.acceptance.determinism`: készletenként kétszer fut a munkamásolaton, a kimenetek sha256-a
a `tests/acceptance/output_hashes.json` rögzített alapjával összevetve; készletenként egy sor az
eltérő fájlokkal (vagy „0 eltérés”).

Kilépési kód: 0, ha a teljes sor zöld, és mind a nyolc készleten 0 az eltérés (a két futás között
és a rögzített alaphoz képest); különben 1.

`--update-hashes`: a rögzített alap a mostani kimenetekre cserélődik (csak ha a két futás között
nincs eltérés). E kapcsoló nélkül a kapu az alapot nem írja."""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HASHES = Path("tests") / "acceptance" / "output_hashes.json"
DATA = Path("data") / "m3"
SITES = (("kk.coach", "kk-coach"), ("valor-software.com", "ngx-bootstrap"),
         ("materia-tm.com", "materia"), ("marketinglens.com", "marketinglens"),
         ("duexhungary.hu", "duex"), ("kk.coach", "kk-coach-2026-10"),
         ("serafimszappan.hu", "serafim"), ("napviragszappan.hu", "napvirag"))


def steps(run_dir: Path, override: dict[str, str], update: bool, jobs: str | None
          ) -> list[tuple[str, list[str]]]:
    """A kapu lépései sorban: (név, parancs)."""
    unknown = sorted(set(override) - {name for _, name in SITES})
    if unknown:
        raise SystemExit(f"ismeretlen készlet: {', '.join(unknown)}")
    sites = [f"{domain}={override.get(name, DATA / f'{name}.duckdb')}" for domain, name in SITES]
    hashing = [sys.executable, "-m", "tests.acceptance.determinism", "--hashes", str(HASHES),
               "--keep", str(run_dir / "keep")]
    hashing += ["--write"] if update else []
    hashing += ["--jobs", jobs] if jobs else []
    return [("suite", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n",
                       "auto"]),
            ("hash", hashing + sites)]


def main() -> None:
    args = sys.argv[1:]
    update = "--update-hashes" in args
    jobs = args[args.index("--jobs") + 1] if "--jobs" in args else None
    plain = [a for i, a in enumerate(args)
             if not a.startswith("--") and (i == 0 or args[i - 1] != "--jobs")]
    folders = [a for a in plain if "=" not in a]
    if len(folders) != 1:
        raise SystemExit(__doc__)
    run_dir = Path(folders[0])
    if run_dir.exists():
        raise SystemExit(f"a futásmappa már létezik, a kapu nem indul: {run_dir}")
    planned = steps(run_dir, dict(a.split("=", 1) for a in plain if "=" in a), update, jobs)
    run_dir.mkdir(parents=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=False).stdout.strip()
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    (run_dir / "run.txt").write_text(f"commit {commit}\nindult {started_at}\n", encoding="utf-8")
    failed = False
    for name, command in planned:
        started = time.monotonic()
        with open(run_dir / f"{name}.out", "w", encoding="utf-8") as out:
            code = subprocess.run(command, stdout=out, stderr=subprocess.STDOUT,
                                  check=False).returncode
        failed = failed or code != 0
        line = f"{name}: kilépési kód {code}, {time.monotonic() - started:.0f} mp"
        with open(run_dir / "done.txt", "a", encoding="utf-8") as done:
            done.write(line + "\n")
        print(line, flush=True)
        tail = (run_dir / f"{name}.out").read_text(encoding="utf-8", errors="replace").splitlines()
        for shown in tail[-1:] if name == "suite" else tail:
            print(f"  {shown}", flush=True)
    result = "ELTÉRÉS VAGY HIBA" if failed else "rendben: a teljes sor zöld, 0 eltérés"
    with open(run_dir / "done.txt", "a", encoding="utf-8") as done:
        done.write(f"kész: commit {commit[:7]}, indult {started_at}; {result}\n")
    print(f"kész: {result}", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
