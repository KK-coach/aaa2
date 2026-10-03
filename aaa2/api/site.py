"""A site kezelője az API-ban: egy site adatbázisa megnyitva, a nevével együtt."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from aaa2.db.connect import connect, db_path
from aaa2.engine import queries as crawl_queries
from aaa2.engine.normalize import UrlPolicy


class ApiError(Exception):
    """Az API hívója által kezelhető hiba; `code`: a parancssor kilépési kódja ehhez a hibához."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.message = message
        self.code = code


class SiteNotFound(ApiError):
    """A site-nak nincs adatbázisa a megadott helyen."""


class GraphMissing(ApiError):
    """A megállapításokhoz előbb a gráf kell (`build_graph`)."""


@dataclass
class Site:
    """Egy megnyitott site: az adatbázis-kapcsolat (`con`), az adatbázis útvonala (`path`) és a
    kimeneti fájlok neve (`name`: az adatbázisfájl neve, ha útvonallal nyílt, különben a domain)."""

    con: duckdb.DuckDBPyConnection
    path: Path
    name: str

    @property
    def domain(self) -> str | None:
        """A site domainje a crawl profiljából; None, ha még nincs crawl."""
        profile = crawl_queries.site(self.con)
        return profile.domain if profile else None

    def close(self) -> None:
        self.con.close()


def domain_of(value: str) -> str:
    """A registrable domain egy URL-ből; a domainként adott érték kisbetűsítve."""
    return UrlPolicy.from_seed(value).domain if "://" in value else value.lower()


def open_site(domain: str, db: Path | None = None) -> Site:
    """A site megnyitása: `db` az adatbázis útvonala, alapból `data/<domain>.duckdb`. Ha a fájl
    nincs meg: `SiteNotFound`."""
    path = db if db is not None else db_path(domain_of(domain))
    if not path.exists():
        raise SiteNotFound(f"nincs adatbázis: {path}")
    return Site(connect(path), path, db.stem if db is not None else domain_of(domain))
