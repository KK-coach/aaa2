"""Site-szintű beállítás és felülbírálat (M2 spec, M2/6, 4. pont): `config/sites/<domain>.toml`.

- `canonical_lang`: a kanonikus név nyelve; ha nincs megadva, a site gyökér-URL-jének (a seed,
  illetve az első kezdőoldal) nyelve, ha az sincs, a site első nyelve (`canonical_language`).
- `[[offers]]`: ajánlat-felülbírálat: `names` (bármelyik nyelven) vagy `url` (az oldala), `tier`
  (`TIERS`), `part_of` (a fő ajánlat neve vagy URL-je). A site-kör alkalmazza
  (`site.apply_overrides`).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from aaa2.entities.pages import page_url, primary_lang

SITES_DIR = Path(__file__).parent / "config" / "sites"
TIERS = ("core", "package", "work_mode")


@dataclass(frozen=True)
class OfferOverride:
    names: tuple[str, ...] = ()
    url: str | None = None
    tier: str = "package"
    part_of: str | None = None


@dataclass(frozen=True)
class SiteConfig:
    canonical_lang: str | None = None
    offers: tuple[OfferOverride, ...] = field(default_factory=tuple)


def site_domain(con: duckdb.DuckDBPyConnection) -> str | None:
    row = con.execute("SELECT domain FROM site").fetchone()
    return row[0] if row else None


def load_site_config(domain: str | None, directory: Path | None = None) -> SiteConfig:
    """A site beállítása (`directory`, alapból `SITES_DIR`); ha nincs fájl, az
    alapértelmezés. Hibás szintnél és név vagy URL nélküli ajánlatnál ValueError."""
    path = (directory or SITES_DIR) / f"{domain}.toml" if domain else None
    if path is None or not path.exists():
        return SiteConfig()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    offers = []
    for entry in raw.get("offers", []):
        tier = entry.get("tier", "package")
        if tier not in TIERS:
            raise ValueError(f"{path.name}: ismeretlen szint: {tier} ({', '.join(TIERS)})")
        names = tuple(entry.get("names", ()))
        if not names and not entry.get("url"):
            raise ValueError(f"{path.name}: az ajánlatnak kell names vagy url")
        offers.append(OfferOverride(names, entry.get("url"), tier, entry.get("part_of")))
    return SiteConfig(raw.get("canonical_lang"), tuple(offers))


def canonical_language(con: duckdb.DuckDBPyConnection, config: SiteConfig | None = None
                       ) -> str | None:
    """A kanonikus név nyelve: a beállításé, különben a gyökér-URL oldaláé, különben a site
    első nyelve."""
    if config is not None and config.canonical_lang:
        return config.canonical_lang
    row = con.execute("SELECT seed_url, home_urls, languages FROM site").fetchone()
    if row is None:
        return None
    seed, homes, languages = row
    for url in [seed, *(homes or [])]:
        if not url:
            continue
        found = con.execute(
            "SELECT lang FROM pages WHERE lang IS NOT NULL AND (url = ? OR url = ?)",
            [url, page_url(url)]).fetchone()
        if found:
            return primary_lang(found[0])
    return primary_lang(languages[0]) if languages else None
