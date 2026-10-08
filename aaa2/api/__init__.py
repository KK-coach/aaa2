"""Az aaa2 API-ja: a motor minden képessége itt érhető el Pythonból; a parancssor (és később a
riport, a HTTP API) csak ezt használja.

Egy site megnyitása: `open_site(domain, db=None)` → `Site`. A lépések site-onként hívhatók
(`crawl`, `extract`, `resolve`, `build_graph`, `find`), a lekérdezések szerződést adnak vissza
(`aaa2/contracts`: `pages`, `entities`, `edges`, `main_entities`, `weights`, `findings`, …);
a riport bemenete a `views` (verziózott JSON: `views_json`, `export_views_json`).

    from aaa2 import api

    site = api.open_site("kk.coach")
    api.extract(site, llm=False)
    api.resolve(site)
    api.build_graph(site)
    api.find(site)
    for finding in api.findings(site):
        print(finding.severity, finding.summary)

A referencia a docstringekből készül: `python -m aaa2.api.reference docs/api`."""
from aaa2.api.queries import (
    EXPORT_TABLES,
    LLMSpend,
    ProviderSpend,
    SiteStatus,
    edges,
    entities,
    entity_run,
    entity_run_skipped,
    entity_type_counts,
    export_views_json,
    findings,
    kb_links,
    kg_calls_today,
    latest_crawl_run,
    links,
    llm_calls_of,
    llm_spend,
    main_entities,
    menu_differences,
    menu_items,
    page_metas,
    page_nodes,
    pages,
    site_profile,
    status,
    structured_data,
    table_rows,
    views,
    views_json,
    weights,
)
from aaa2.api.site import ApiError, GraphMissing, Site, SiteNotFound, domain_of, open_site
from aaa2.api.steps import (
    CrawlResult,
    ExtractResult,
    FindResult,
    GraphResult,
    RebuildResult,
    ResolveResult,
    SitemapResult,
    build_graph,
    check_models,
    crawl,
    entity_report,
    extract,
    find,
    rebuild_entities,
    resolve,
    sitemap,
    validate,
)
from aaa2.engine.frontier import MAX_PAGES
from aaa2.engine.render import CONCURRENCY, RENDER_TIMEOUT
from aaa2.functions.findings import TYPE_LABELS as FINDING_TYPE_LABELS
from aaa2.resolver.validate import KG_DAILY_QUOTA

__all__ = [
    # állandók
    "CONCURRENCY",
    "EXPORT_TABLES",
    "FINDING_TYPE_LABELS",
    "KG_DAILY_QUOTA",
    "MAX_PAGES",
    "RENDER_TIMEOUT",
    # site
    "ApiError",
    # lépések
    "CrawlResult",
    "ExtractResult",
    "FindResult",
    "GraphMissing",
    "GraphResult",
    "LLMSpend",
    "ProviderSpend",
    "RebuildResult",
    "ResolveResult",
    "Site",
    "SiteNotFound",
    # állapot és költség
    "SiteStatus",
    "SitemapResult",
    "build_graph",
    "check_models",
    "crawl",
    "domain_of",
    "edges",
    "entities",
    "entity_report",
    "entity_run",
    "entity_run_skipped",
    "entity_type_counts",
    "export_views_json",
    "extract",
    "find",
    "findings",
    "kb_links",
    "kg_calls_today",
    "latest_crawl_run",
    "links",
    "llm_calls_of",
    "llm_spend",
    "main_entities",
    "menu_differences",
    "menu_items",
    "open_site",
    "page_metas",
    "page_nodes",
    "pages",
    "rebuild_entities",
    "resolve",
    # lekérdezések (szerződések)
    "site_profile",
    "sitemap",
    "status",
    "structured_data",
    "table_rows",
    "validate",
    "views",
    "views_json",
    "weights",
]
