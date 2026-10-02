"""Mérés: két kimeneti mappa tartalma azonos-e a sorrendtől függetlenül.

`python -m tests.acceptance.content_equal <régi mappa> <új mappa>`: fájlonként
- `azonos`: bájtra egyezik (a futásjelentésben a futás sorszáma és időpontja nélkül);
- `csak sorrend`: a CSV sorai multihalmazként egyeznek, ha a cellák JSON-jában a kulcsok és a
  listák rendezve vannak; más fájlban a sorok multihalmaza egyezik;
- `soron belüli sorrend`: a sorok nem, de a fájl jelei multihalmazként egyeznek (pl. egy
  HTML-sorban felsorolt nevek sorrendje);
- `ELTÉR`: a tartalom más; az első eltérő sorok kiírva.
A kilépési kód 1, ha van `ELTÉR` vagy hiányzó fájl."""
from __future__ import annotations

import csv
import io
import json
import sys
from collections import Counter
from pathlib import Path

from tests.acceptance.determinism import stable_bytes


def canonical(value):
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return sorted((canonical(item) for item in value),
                      key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    return value


def cell(text: str) -> str:
    try:
        value = json.loads(text)
    except ValueError:
        return text
    if isinstance(value, (dict, list)):
        return json.dumps(canonical(value), sort_keys=True, ensure_ascii=False)
    return text


def rows(data: bytes) -> Counter:
    reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    return Counter(tuple(cell(c) for c in row) for row in reader)


def compare(old: Path, new: Path) -> tuple[str, list[str]]:
    a, b = stable_bytes(old), stable_bytes(new)
    if a == b:
        return "azonos", []
    if old.suffix == ".csv":
        one, two = rows(a), rows(b)
        if one == two:
            return "csak sorrend", []
        gone, come = list((one - two).elements()), list((two - one).elements())
        examples = [f"- {' | '.join(row)[:300]}" for row in gone[:3]] \
            + [f"+ {' | '.join(row)[:300]}" for row in come[:3]]
        return f"ELTÉR ({len(gone)} sor csak a régiben, {len(come)} csak az újban)", examples
    one, two = Counter(a.splitlines()), Counter(b.splitlines())
    if one == two:
        return "csak sorrend", []
    if Counter(a) == Counter(b):
        return "soron belüli sorrend", []
    gone, come = list((one - two).elements()), list((two - one).elements())
    examples = [f"- {line[:300].decode('utf-8', 'replace')}" for line in gone[:3]] \
        + [f"+ {line[:300].decode('utf-8', 'replace')}" for line in come[:3]]
    return f"ELTÉR ({len(gone)} sor csak a régiben, {len(come)} csak az újban)", examples


def main() -> None:
    old, new = Path(sys.argv[1]), Path(sys.argv[2])
    names = sorted({p.name for p in old.iterdir() if p.is_file()}
                   | {p.name for p in new.iterdir() if p.is_file()})
    verdicts: Counter = Counter()
    failed = False
    for name in names:
        if not (old / name).exists() or not (new / name).exists():
            verdict, examples = "HIÁNYZIK az egyik mappából", []
        else:
            verdict, examples = compare(old / name, new / name)
        verdicts[verdict.split(" (")[0]] += 1
        failed = failed or verdict.startswith(("ELTÉR", "HIÁNYZIK"))
        if verdict != "azonos":
            print(f"{name}: {verdict}")
            for example in examples:
                print(f"    {example}")
    print(f"{len(names)} fájl: " + ", ".join(f"{k} {v}" for k, v in sorted(verdicts.items())))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
