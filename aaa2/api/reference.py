"""API-referencia a docstringekből: `python -m aaa2.api.reference docs/api` kiírja a
`reference.md`-t az `aaa2.api` nyilvános neveiről (aláírás és leírás). A repóban lévő fájl
naprakészségét teszt őrzi."""
from __future__ import annotations

import dataclasses
import inspect
import sys
from pathlib import Path

from aaa2 import api

GROUPS = (
    ("Site", ("open_site", "Site", "domain_of", "ApiError", "SiteNotFound", "GraphMissing")),
    ("Lépések", ("crawl", "sitemap", "extract", "resolve", "rebuild_entities", "build_graph", "find", "entity_report",
                 "validate", "check_models")),
    ("A lépések eredményei", ("CrawlResult", "SitemapResult", "ExtractResult", "ResolveResult", "RebuildResult", "GraphResult",
                              "FindResult")),
    ("Lekérdezések (szerződések)", ("site_profile", "pages", "page_metas", "links",
                                    "structured_data", "latest_crawl_run", "entities",
                                    "kb_links", "page_nodes", "edges", "main_entities",
                                    "weights", "findings", "llm_calls_of")),
    ("A riport bemenete", ("views", "views_json", "export_views_json")),
    ("Állapot és költség", ("status", "SiteStatus", "entity_run", "entity_run_skipped",
                            "entity_type_counts", "kg_calls_today", "llm_spend", "LLMSpend",
                            "ProviderSpend", "table_rows")),
    ("Állandók", ("CONCURRENCY", "EXPORT_TABLES", "FINDING_TYPE_LABELS", "KG_DAILY_QUOTA",
                  "MAX_PAGES", "RENDER_TIMEOUT")),
)


def _doc(value: object) -> str:
    return inspect.cleandoc(getattr(value, "__doc__", None) or "").strip()


def _entry(name: str) -> list[str]:
    value = getattr(api, name)
    if inspect.isclass(value):
        lines = [f"### `{name}`", "", _doc(value) or "—", ""]
        if dataclasses.is_dataclass(value):
            lines += ["Mezők:", ""]
            lines += [f"- `{f.name}`: `{f.type}`" for f in dataclasses.fields(value)]
            lines.append("")
        return lines
    if callable(value):
        signature = str(inspect.signature(value))
        return [f"### `{name}`", "", "```python", f"{name}{signature}", "```", "",
                _doc(value) or "—", ""]
    return [f"### `{name}`", "", f"`{value!r}`", ""]


def reference() -> str:
    """A referencia szövege (Markdown)."""
    grouped = {name for _, names in GROUPS for name in names}
    missing = sorted(set(api.__all__) - grouped)
    if missing:
        raise ValueError(f"a referencia csoportjaiból hiányzik: {', '.join(missing)}")
    lines = ["# aaa2 API-referencia", "",
             ("Az `aaa2.api` nyilvános nevei. A fájl a docstringekből készül "
              "(`python -m aaa2.api.reference docs/api`), kézzel ne szerkeszd."), "",
             inspect.cleandoc(api.__doc__ or ""), ""]
    for title, names in GROUPS:
        lines += [f"## {title}", ""]
        for name in names:
            lines += _entry(name)
    return "\n".join(lines).rstrip("\n") + "\n"


def write(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "reference.md"
    path.write_text(reference(), encoding="utf-8", newline="\n")
    return path


if __name__ == "__main__":
    print(write(Path(sys.argv[1])))
