"""Az API lekérdezései: egy site tárolt adatai szerződésként (`aaa2/contracts`), és a
parancssornak kellő állapot- és költségadatok. A szerződést itt építi az API a modulok lekérdező
függvényeinek és az entitástárnak a soraiból."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from aaa2.api.site import ApiError, Site
from aaa2.contracts import (
    CrawlRun,
    Edge,
    Entity,
    EntityWeight,
    Finding,
    KbLink,
    Link,
    LLMCall,
    MainEntity,
    Page,
    PageMeta,
    PageNode,
    StructuredData,
)
from aaa2.contracts import Site as SiteProfile
from aaa2.engine import queries as crawl_queries
from aaa2.entities import store
from aaa2.functions import findings as findings_module
from aaa2.functions import graph_queries
from aaa2.llm import calls as llm_calls
from aaa2.llm import ledger
from aaa2.llm.config import load_config
from aaa2.resolver import queries as resolver_queries

EXPORT_TABLES = (
    "pages", "links", "headings", "schema_blocks", "crawl_queue", "crawl_runs", "site",
    "entities", "page_entities", "mention_sources", "blocks", "llm_calls", "entity_runs",
)


# --- crawl ---------------------------------------------------------------------------------

def site_profile(site: Site) -> SiteProfile | None:
    """A site profilja a crawlból; None, ha még nincs crawl."""
    return crawl_queries.site(site.con)


def pages(site: Site) -> list[Page]:
    """Minden bejárt oldal, `page_id` szerint."""
    return crawl_queries.pages(site.con)


def page_metas(site: Site) -> list[PageMeta]:
    """Oldalanként a canonical és a hreflang."""
    return crawl_queries.page_metas(site.con)


def links(site: Site) -> list[Link]:
    """A belső linkek a forrásoldal és a DOM-sorrend szerint."""
    return crawl_queries.links(site.con)


def structured_data(site: Site) -> list[StructuredData]:
    """A strukturált adat minden eleme: előbb az érvényes JSON-LD blokkok, utána a microdata-,
    RDFa- és Open Graph-elemek, az oldal és a sorszám szerint."""
    return crawl_queries.json_ld(site.con) + crawl_queries.structured_data(site.con)


def latest_crawl_run(site: Site) -> CrawlRun | None:
    return crawl_queries.latest_crawl_run(site.con)


# --- entitások -----------------------------------------------------------------------------

def entities(site: Site) -> list[Entity]:
    """A site entitásai, `entity_id` szerint."""
    return [Entity.from_row(row) for row in store.entity_rows(site.con)]


def kb_links(site: Site) -> list[KbLink]:
    """Az entitások tudásbázis-kapcsolatai (Wikidata, Wikipedia, Knowledge Graph)."""
    return [KbLink.from_row(row) for row in store.entity_rows(site.con)]


# --- gráf ----------------------------------------------------------------------------------

def page_nodes(site: Site) -> list[PageNode]:
    """Az oldal-csomópontok a szerepükkel és a canonical-döntéssel."""
    return graph_queries.page_nodes(site.con)


def edges(site: Site, kind: str | None = None) -> list[Edge]:
    """Az élek; `kind`: csak ez az éltípus (pl. `mentions`, `offers`, `is_a`)."""
    return graph_queries.edges(site.con, kind)


def main_entities(site: Site) -> list[MainEntity]:
    """Az oldalak fő és másodlagos entitásai a bizonyítékokkal, az oldal és a rangsor szerint."""
    return graph_queries.main_entities(site.con)


def weights(site: Site) -> list[EntityWeight]:
    """Az entitások súlya a site-on."""
    return graph_queries.entity_weights(site.con)


# --- megállapítások ------------------------------------------------------------------------

def findings(site: Site) -> list[Finding]:
    """A tárolt SEO-megállapítások, az azonosítójuk szerint."""
    return findings_module.stored_findings(site.con)


# --- állapot és költség (a parancssornak) --------------------------------------------------

@dataclass(frozen=True)
class SiteStatus:
    """A site állapota: oldalszám, hibás oldalak, státuszosztályok (rendezve), a crawl-sor
    állapotonként, a profil, az utolsó crawl és módszerenként a legutóbbi entitás-futás sora."""

    pages: int
    errors: int
    by_class: list[tuple[str, int]]
    queue: dict[str, int]
    profile: SiteProfile | None
    last_crawl: CrawlRun | None
    entity_runs: list[tuple]


def status(site: Site) -> SiteStatus:
    con = site.con
    crawled = crawl_queries.pages(con)
    by_class = sorted(Counter(
        f"{page.status // 100}xx" if page.status is not None else "nincs válasz"
        for page in crawled).items())
    return SiteStatus(
        pages=len(crawled), errors=sum(page.error is not None for page in crawled),
        by_class=by_class, queue=dict(crawl_queries.queue_status_counts(con)),
        profile=crawl_queries.site(con), last_crawl=crawl_queries.latest_crawl_run(con),
        entity_runs=store.latest_runs_by_method(con))


def entity_run(site: Site, run_id: int) -> tuple | None:
    """Egy entitás-futás sora (`store.ENTITY_RUN_COLUMNS` oszlopai)."""
    return store.entity_run(site.con, run_id)


def entity_run_skipped(site: Site, run_id: int) -> dict:
    """A futás kimaradásai ok szerint."""
    return json.loads(store.entity_runs_for_entities(site.con, run_id)[0])


def entity_type_counts(site: Site) -> list[tuple[str, int, int]]:
    """Típusonként: (típus, entitások száma, említéssorok száma), az entitásszám szerint."""
    return store.entities_for_entities(site.con)


def kg_calls_today(site: Site) -> int:
    """A mai Knowledge Graph-hívások száma ezen a site-on."""
    return resolver_queries.kg_calls_today(site.con)


def llm_calls_of(site: Site) -> list[LLMCall]:
    """A site-adatbázisban könyvelt LLM-hívások."""
    return llm_calls.calls(site.con)


@dataclass(frozen=True)
class ProviderSpend:
    """Egy szolgáltató halmozott költsége: modellenként (modell, USD, aktív-e), az összeg, a
    leállási küszöb és a keret."""

    name: str
    models: list[tuple[str, float, bool]]
    total: float
    stop_usd: float
    budget_usd: float


@dataclass(frozen=True)
class LLMSpend:
    """A halmozott LLM-költség a főkönyvből (minden site): szolgáltatónként, a konfigurációban
    nem szereplő modellek külön; `site_rows`: az adott site-on könyvelt hívások modellenként
    (modell, hívás, USD, újrapróba, hibakódok), None, ha nincs site megadva."""

    ledger_path: str
    providers: list[ProviderSpend]
    unconfigured: list[tuple[str, float]]
    site_rows: list[tuple] | None = field(default=None)


def llm_spend(site: Site | None = None) -> LLMSpend:
    config = load_config()
    spent = ledger.spent_by_model()
    providers = []
    for provider in config.providers.values():
        providers.append(ProviderSpend(
            name=provider.name,
            models=[(model, spent.get(model, 0.0),
                     bool(provider.fallback and model == provider.active_model))
                    for model in provider.models],
            total=sum(spent.get(model, 0.0) for model in provider.models),
            stop_usd=provider.stop_usd, budget_usd=provider.budget_usd))
    known = {m for p in config.providers.values() for m in p.models}
    return LLMSpend(
        ledger_path=str(ledger.default_path()), providers=providers,
        unconfigured=[(model, spent[model]) for model in sorted(set(spent) - known)],
        site_rows=llm_calls.spend_by_model(site.con) if site is not None else None)


def table_rows(site: Site, table: str) -> tuple[list[str], list[tuple]]:
    """Egy tábla minden sora (a BLOB-oszlopok nélkül) az oszlopnevekkel; `table` az
    `EXPORT_TABLES` egyike, különben `ApiError`."""
    if table not in EXPORT_TABLES:
        raise ApiError(f"ismeretlen tábla: {table}; lehet: {', '.join(EXPORT_TABLES)}")
    con = site.con
    columns = [
        name for name, kind in con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position", [table]
        ).fetchall()
        if kind != "BLOB"
    ]
    rows = con.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY ALL").fetchall()
    return columns, rows
