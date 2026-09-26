"""A rétegek határa: motor (engine) → entitás-réteg (entities) → funkciók.

Az entitás-réteg a motorból csak a `parse` publikus segédfüggvényeit importálhatja
(`anchor_text`, `schema_items`); a motor semmit az entitás-rétegből. A teszt a két csomag
modulgráfját bejárja a forrásból (ast), a köztes aaa2-modulokon (llm, db) és a szülőcsomagjaik
`__init__`-jén át is: egy tiltott él közvetett úton is bukás. A két réteg `__init__`-je nem importál
semmit (a `parse` importja a motor `__init__`-jét is lefuttatja).
"""
import ast
from collections import deque
from collections.abc import Callable, Iterator
from pathlib import Path

from aaa2.engine import parse
from aaa2.entities import rules

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_FROM_ENGINE = {"aaa2.engine.parse": {"anchor_text", "schema_items"}}


def module_name(path: Path, root: Path) -> str:
    parts = path.relative_to(root).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imports(path: Path, name: str, modules: set[str]) -> Iterator[tuple[str, tuple[str, ...]]]:
    """(importált modul, importált nevek); a függvényen belüli import is."""
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, ()
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                base = package.split(".")[:len(package.split(".")) - node.level + 1]
                module = ".".join([*base, module] if module else base)
            names = tuple(alias.name for alias in node.names)
            submodules = tuple(n for n in names if f"{module}.{n}" in modules)
            rest = tuple(n for n in names if n not in submodules)
            if rest or not submodules:
                yield module, rest
            for sub in submodules:
                yield f"{module}.{sub}", ()


def in_package(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def parents(module: str) -> list[str]:
    parts = module.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


def walk(start: str, edges: dict, forbidden: Callable[[str, tuple], bool],
         stop: Callable[[str], bool]) -> list[str]:
    """A `start`-ból elérhető tiltott élek, az úttal; az import a szülőcsomagokat is betölti."""
    found, seen, queue = [], {start}, deque([(start, [start])])
    while queue:
        node, path = queue.popleft()
        for target, names in edges.get(node, ()):
            if forbidden(target, names):
                found.append(" → ".join([*path, target + (f" ({', '.join(names)})"
                                                          if names else "")]))
                continue
            for step in [*parents(target), target]:
                if step in edges and step not in seen and not stop(step):
                    seen.add(step)
                    queue.append((step, [*path, step]))
    return found


def violations(root: Path) -> list[str]:
    paths = {module_name(p, root): p for p in (root / "aaa2").rglob("*.py")}
    edges = {name: list(imports(path, name, set(paths))) for name, path in paths.items()}

    def entity_to_engine(target: str, names: tuple) -> bool:
        allowed = ALLOWED_FROM_ENGINE.get(target)
        return in_package(target, "aaa2.engine") and not (allowed and names
                                                            and set(names) <= allowed)

    def engine_to_entity(target: str, names: tuple) -> bool:
        return in_package(target, "aaa2.entities")

    def layered(module: str) -> bool:
        return in_package(module, "aaa2.engine") or in_package(module, "aaa2.entities")

    found = [f"{package} (__init__) → {target}"
             for package in ("aaa2.engine", "aaa2.entities")
             for target, _ in edges.get(package, ())]
    for name in sorted(paths):
        if in_package(name, "aaa2.entities"):
            found += walk(name, edges, entity_to_engine, layered)
        elif in_package(name, "aaa2.engine"):
            found += walk(name, edges, engine_to_entity, layered)
    return sorted(found)


def test_no_forbidden_edge_between_engine_and_entities():
    assert violations(ROOT) == []


def test_entities_heading_tags_match_the_parser():
    assert rules.HEADING_TAGS == parse.HEADING_TAGS


def test_the_walk_finds_direct_and_indirect_edges(tmp_path):
    files = {
        "aaa2/__init__.py": "",
        "aaa2/engine/__init__.py": "",
        "aaa2/engine/parse.py": "HEADING_TAGS = 1\ndef anchor_text(): ...\n",
        "aaa2/engine/crawl.py": "",
        "aaa2/engine/deep.py": "def f():\n    from aaa2.entities import a\n",
        "aaa2/llm/__init__.py": "",
        "aaa2/llm/bridge.py": "from aaa2.engine.crawl import run\n",
        "aaa2/db/__init__.py": "from aaa2.engine import crawl\n",
        "aaa2/db/connect.py": "",
        "aaa2/entities/__init__.py": "from aaa2.entities import a\n",
        "aaa2/entities/a.py": ("from aaa2.engine.parse import anchor_text\n"
                               "from aaa2.engine.parse import HEADING_TAGS\n"),
        "aaa2/entities/b.py": "from aaa2.llm import bridge\nimport aaa2.engine.parse\n",
        "aaa2/entities/c.py": ("from ..engine import crawl\nfrom . import a\n"
                               "from aaa2.db.connect import connect\n"),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    assert violations(tmp_path) == [
        "aaa2.engine.deep → aaa2.entities.a",
        "aaa2.entities (__init__) → aaa2.entities.a",
        "aaa2.entities.a → aaa2.engine.parse (HEADING_TAGS)",
        "aaa2.entities.b → aaa2.engine.parse",
        "aaa2.entities.b → aaa2.llm.bridge → aaa2.engine.crawl (run)",
        "aaa2.entities.c → aaa2.db → aaa2.engine.crawl",
        "aaa2.entities.c → aaa2.engine.crawl",
    ]
