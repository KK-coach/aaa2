"""A site-fájlok helye: `aaa2/core/sites/<domain>.toml`.

Egy site-fájl több modul beállításait hordozza, ezért a közös alapban van: `[crawl]` (seed,
include, exclude, párhuzamosság, render-időkorlát), `[llm.<szolgáltató>]` (kulcs, projekt),
`[page_types]` (URL-minták), `[[offers]]` (ajánlat-felülbírálat), `canonical_lang`. A részeket az
a modul olvassa és ellenőrzi, amelyiké (`llm.config`, `resolver.overrides`)."""
from __future__ import annotations

from pathlib import Path

SITES_DIR = Path(__file__).parent / "sites"


def site_file(domain: str | None, directory: Path | None = None) -> Path | None:
    """A site fájlja (`directory`, alapból `SITES_DIR`), ha van domain és létezik a fájl."""
    path = (directory or SITES_DIR) / f"{domain}.toml" if domain else None
    return path if path is not None and path.exists() else None
