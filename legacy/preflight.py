# -*- coding: utf-8 -*-
"""AAA-255 (Gate A) — pre-flight reachability gate for the audit run-graph.

Runs ONCE in run_one BEFORE the discovery agent. Fetches the client URL and
decides — per the locked escalation ladder — whether the page is reachable. If
not, run_one short-circuits the WHOLE audit to an honest terminal "page not
reachable" result (no discovery, SERP, sections, eeat, indexing, or fabricated
summary). The fetched content is passed THROUGH (crawler.set_prefetched_crawl) so
the reachable path does not re-fetch.

Locked ladder:
  - httpx 200 / parse-only failure ⇒ reachable (the page responded).
  - 404 / 410                       ⇒ unreachable "page not found".
  - 403                             ⇒ ONE real-browser render_url attempt; usable
                                       content ⇒ reachable (use the RENDERED crawl);
                                       still blocked ⇒ unreachable "blocks access".
  - 5xx / timeout                   ⇒ bounded retry (2, short backoff); still failing
                                       ⇒ unreachable "server unreachable".
  - other 4xx                       ⇒ unreachable "page not reachable".
  - DNS / connection / network      ⇒ unreachable "site could not be reached".

Deterministic, NO LLM. Reachability cost is negligible (one httpx + at most one
render or two retries). Never raises — any internal failure degrades to a clean
unreachable verdict.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_RETRY_BACKOFF = (1.5, 3.0)   # 5xx/timeout: up to 2 retries
_RENDER_MIN_WORDS = 50        # rendered content must clear this to count as "rescued"


def _unreachable(status_code, error_type, reason_code: str, reason: str) -> dict:
    return {"reachable": False, "status_code": status_code,
            "error_type": error_type, "reason_code": reason_code, "reason": reason}


def _reachable(crawl: dict, via: str) -> dict:
    return {"reachable": True, "crawl": crawl, "via": via}


async def _retry_fetch(url: str):
    """5xx/timeout bounded retry. Returns a reachable crawl dict or None."""
    from crawler.crawler import crawl_html
    for backoff in _RETRY_BACKOFF:
        await asyncio.sleep(backoff)
        try:
            r = await crawl_html(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("preflight retry fetch error: %s", e)
            continue
        et = r.get("error_type")
        if not r.get("error") or et == "parse_error":
            return r  # responded
        # keep retrying only on transient classes
        if et not in ("timeout",) and not (
                et == "http_error" and isinstance(r.get("status_code"), int)
                and 500 <= r["status_code"] < 600):
            return None
    return None


async def _try_render(url: str):
    """ONE real-browser render attempt to rescue a 403 bot-block. Returns a
    parsed crawl dict (RENDERED) if it yields usable content, else None."""
    try:
        from crawler.crawler import get_requested_locale, parse_rendered_html
        from playwright_poc.render import render_url
        rendered = await render_url(url, locale=get_requested_locale())
    except Exception as e:  # noqa: BLE001 — render infra absent/failed → no rescue
        logger.warning("preflight render rescue error: %s", e)
        return None
    if not isinstance(rendered, dict) or rendered.get("error"):
        return None
    sc = rendered.get("status_code")
    if isinstance(sc, int) and (sc == 403 or sc == 404 or sc == 410 or sc >= 500):
        return None  # the browser also got blocked / error page
    parsed = parse_rendered_html(
        url, rendered.get("rendered_html", "") or "",
        rendered.get("status_code", 200) or 200,
        rendered.get("render_time_ms", 0) or 0)
    if parsed.get("error"):
        return None
    words = ((parsed.get("main_content") or {}).get("words")) or 0
    if words < _RENDER_MIN_WORDS:
        return None  # a near-empty block/challenge shell, not the real page
    return parsed


async def preflight_reachability(url: str) -> dict:
    """Returns either {reachable:True, crawl, via} or {reachable:False,
    status_code, error_type, reason_code, reason}. Never raises."""
    from crawler.crawler import crawl_html
    try:
        result = await crawl_html(url)
    except Exception as e:  # noqa: BLE001 — fetch itself blew up → unreachable
        return _unreachable(None, "network_error", "unreachable",
                            "the site could not be reached")

    et = result.get("error_type")
    sc = result.get("status_code")

    # responded (200) or a post-200 parse failure → reachable (parse is downstream)
    if not result.get("error") or et == "parse_error":
        return _reachable(result, "httpx")

    if et == "http_error":
        if sc in (404, 410):
            return _unreachable(sc, et, "not_found", "page not found")
        if sc == 403:
            rescued = await _try_render(url)
            if rescued is not None:
                return _reachable(rescued, "render")
            return _unreachable(sc, et, "blocked", "the site blocks automated access")
        if isinstance(sc, int) and 500 <= sc < 600:
            r2 = await _retry_fetch(url)
            if r2 is not None:
                return _reachable(r2, "retry")
            return _unreachable(sc, et, "server_unreachable", "server unreachable")
        if sc == 429:  # rate-limited: transient → retry, else treat as a block
            r2 = await _retry_fetch(url)
            if r2 is not None:
                return _reachable(r2, "retry")
            return _unreachable(sc, et, "blocked", "the site blocks automated access")
        return _unreachable(sc, et, "unreachable", "page not reachable")

    if et == "timeout":
        r2 = await _retry_fetch(url)
        if r2 is not None:
            return _reachable(r2, "retry")
        return _unreachable(None, et, "server_unreachable", "server unreachable")

    # network_error (DNS / connection / redirect-loop) → unreachable
    return _unreachable(None, et or "network_error", "unreachable",
                        "the site could not be reached")
