"""Architektúra-elemzés (architektúra-spec, 3. pont): a tiltott importok és a más modul tábláit
író vagy közvetlenül olvasó helyek listája. Az elemzés csak jelent; a `tests/test_architecture.py`
bukik, ha bármelyik lista nem üres.

- Modulok: a fájlok mai helyük szerint tartoznak modulhoz (`MODULES`; a spec 2. pontjának
  táblázata, a spec által meg nem nevezett fájlok besorolása a `docs/architecture/tables.toml`
  mellett, itt rögzül).
- Függési irány: `core` ← `llm` ← `crawl` ← `extract` ← `resolve` ← `graph` ← `findings` ←
  `report`; az `api` mindegyiket használhatja, a `cli` csak az `api`-t; a `contracts` csomagot
  bárki használhatja, az maga nem épülhet a motor moduljaira. Tiltott import: egy modul egy
  nála későbbi modult importál (`upward`), a `cli` nem az `api`-n keresztül ér el egy modult
  (`cli_direct`), vagy a `contracts` a motorra épül (`contracts_dependency`).
- Táblahozzáférés: a forráskód szövegkonstansaiban álló SQL táblanevei (FROM / JOIN: olvasás;
  INSERT INTO / UPDATE / DELETE FROM / CREATE / DROP / ALTER TABLE: írás), a
  `docs/architecture/tables.toml` tulajdonlása szerint. Idegen hozzáférés: a tábla más modulé.
  A futásidőben összerakott táblanév (pl. `f"… FROM {table}"`) nem látszik.
- `python -m tests.architecture [--list]`: az összesítő, `--list`-tel a teljes lista.
"""
from __future__ import annotations

import ast
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "aaa2"
TABLES_FILE = ROOT / "docs" / "architecture" / "tables.toml"

ORDER = ("core", "llm", "crawl", "extract", "resolve", "graph", "findings", "report")
# a fájl mai helye → modul; az első illeszkedő minta számít
MODULES: tuple[tuple[str, str], ...] = (
    ("aaa2/contracts/", "contracts"),
    ("aaa2/__init__.py", "core"),
    ("aaa2/core/", "core"),
    ("aaa2/db/", "core"),
    ("aaa2/engine/stable_hash.py", "core"),
    ("aaa2/llm/", "llm"),
    ("aaa2/engine/", "crawl"),
    ("aaa2/resolver/", "resolve"),
    ("aaa2/entities/report.py", "report"),
    ("aaa2/entities/", "extract"),
    ("aaa2/functions/graph.py", "graph"),
    ("aaa2/functions/graph_queries.py", "graph"),
    ("aaa2/functions/findings.py", "findings"),
    ("aaa2/functions/__init__.py", "graph"),
    ("aaa2/api/", "api"),
    ("aaa2/cli/", "cli"),
)

WRITE = re.compile(r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM|"
                   r"(?:CREATE|DROP|ALTER)\s+TABLE(?:\s+IF\s+(?:NOT\s+)?EXISTS)?)\s+\"?([a-z_0-9]+)")
READ = re.compile(r"\b(?:FROM|JOIN)\s+\"?([a-z_0-9]+)")
DELETE_FROM = re.compile(r"\bDELETE\s+FROM\s+\"?[a-z_0-9]+")


@dataclass(frozen=True)
class ImportIssue:
    kind: str               # upward | cli_direct | contracts_dependency
    file: str
    line: int
    module: str
    imported: str
    imported_module: str


@dataclass(frozen=True)
class TableAccess:
    file: str
    line: int
    module: str
    table: str
    owner: str
    mode: str               # read | write

    @property
    def foreign(self) -> bool:
        return self.owner != self.module


def module_of(path: str) -> str | None:
    return next((module for prefix, module in MODULES if path.startswith(prefix)), None)


def source_files() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def table_owners() -> dict[str, str]:
    data = tomllib.loads(TABLES_FILE.read_text(encoding="utf-8"))
    return {table: entry["owner"] for table, entry in data["tables"].items()}


def _imported(node: ast.AST, file: str) -> list[str]:
    """Az `aaa2` csomag importált moduljai egy import-utasításból, fájlútvonalként."""
    names = []
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        names = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
    found = []
    for name in names:
        if name != "aaa2" and not name.startswith("aaa2."):
            continue
        base = ROOT / Path(*name.split("."))
        if base.with_suffix(".py").exists():
            found.append(relative(base.with_suffix(".py")))
        elif (base / "__init__.py").exists():
            found.append(relative(base / "__init__.py"))
    # `from aaa2.x import y`: ha `y` maga modul, az számít, különben a csomag
    if isinstance(node, ast.ImportFrom) and len(found) > 1:
        found = found[1:]
    return [f for f in dict.fromkeys(found) if f != file]


def import_issues() -> list[ImportIssue]:
    issues = []
    for path in source_files():
        file = relative(path)
        module = module_of(file)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for imported in _imported(node, file):
                target = module_of(imported)
                if target == module or target == "contracts" or module is None or target is None:
                    continue
                if module == "contracts":
                    kind = "contracts_dependency"
                elif module == "cli":
                    kind = "cli_direct" if target != "api" else None
                elif module == "api":
                    kind = None
                elif target in ("api", "cli") or ORDER.index(target) > ORDER.index(module):
                    kind = "upward"
                else:
                    kind = None
                if kind:
                    issues.append(ImportIssue(kind, file, node.lineno, module, imported, target))
    return sorted(issues, key=lambda i: (i.kind, i.file, i.line, i.imported))


def _strings(tree: ast.AST) -> list[tuple[int, str]]:
    """A szövegkonstansok; az f-string konstans részei egy szöveggé fűzve (a behelyettesített
    rész helyén szóköz)."""
    found = []
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                inside.add(id(value))
                parts.append(value.value if isinstance(value, ast.Constant)
                             and isinstance(value.value, str) else " ")
            found.append((node.lineno, "".join(parts)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in inside:
            found.append((node.lineno, node.value))
    return found


def _docstrings(tree: ast.AST) -> set[int]:
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.body and isinstance(node.body[0], ast.Expr) \
                and isinstance(node.body[0].value, ast.Constant) \
                and isinstance(node.body[0].value.value, str):
            lines.add(node.body[0].value.lineno)
    return lines


def table_accesses() -> list[TableAccess]:
    owners = table_owners()
    accesses = []
    for path in source_files():
        file = relative(path)
        module = module_of(file)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstrings(tree)
        for line, text in _strings(tree):
            if line in skip:
                continue
            written = [m.group(1) for m in WRITE.finditer(text)]
            read = [m.group(1) for m in READ.finditer(DELETE_FROM.sub(" ", text))]
            for mode, names in (("write", written), ("read", read)):
                for table in dict.fromkeys(names):
                    if table in owners:
                        accesses.append(TableAccess(file, line, module or "?", table,
                                                    owners[table], mode))
    return sorted(set(accesses), key=lambda a: (a.file, a.line, a.table, a.mode))


def unmapped_files() -> list[str]:
    return [relative(p) for p in source_files() if module_of(relative(p)) is None]


def summary() -> dict:
    issues, accesses = import_issues(), table_accesses()
    foreign = [a for a in accesses if a.foreign]
    return {
        "files": len(source_files()),
        "forbidden_imports": len(issues),
        "forbidden_imports_by_kind": dict(Counter(i.kind for i in issues)),
        "table_accesses": len(accesses),
        "foreign_table_accesses": len(foreign),
        "foreign_writes": sum(a.mode == "write" for a in foreign),
        "foreign_reads": sum(a.mode == "read" for a in foreign),
        "foreign_by_pair": dict(sorted(Counter(
            f"{a.module} → {a.owner} ({a.mode})" for a in foreign).items())),
    }


def report(full: bool = False) -> str:
    issues, accesses = import_issues(), table_accesses()
    foreign = [a for a in accesses if a.foreign]
    info = summary()
    lines = [f"fájl: {info['files']}",
             f"tiltott import: {info['forbidden_imports']} {info['forbidden_imports_by_kind']}",
             (f"táblahozzáférés: {info['table_accesses']}, ebből idegen: "
              f"{info['foreign_table_accesses']} (írás {info['foreign_writes']}, "
              f"olvasás {info['foreign_reads']})")]
    lines += [f"  {pair}: {count}" for pair, count in info["foreign_by_pair"].items()]
    if full:
        lines.append("\n# tiltott importok")
        lines += [f"{i.kind}\t{i.file}:{i.line}\t{i.module} → {i.imported_module}\t{i.imported}"
                  for i in issues]
        lines.append("\n# idegen táblahozzáférések")
        lines += [f"{a.mode}\t{a.file}:{a.line}\t{a.module} → {a.owner}\t{a.table}"
                  for a in foreign]
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(full="--list" in sys.argv))
