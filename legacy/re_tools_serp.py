"""Reverse Engineering Agent tools.

- discovery_agent_tool : runs the REAL discovery_agent via a nested runner
  and harvests its full session state (AgentTool can't do this cleanly -
  see module docstring in agent.py). Big audit stored in parent state per
  URL+role; only a compact summary returned to the LLM.
- dataforseo_serp_query_tool : DataForSEO MCP SERP organic (.ai variant).
- select_competitor_urls_tool : pure Python, no API.
- compare_audits_tool : single Gemini comparison call (see comparison.py).

AAA-108 Sub-step 3 — Production wiring contract
------------------------------------------------
`persist_re_findings(audit_id, state)` is DELIBERATELY NOT a FunctionTool
and NOT registered in ALL_TOOLS. Persistence of the RE workflow output
(audit_output.re_findings) is the CALLER's responsibility, not the LLM's:
the agent never sees a "persist your work" instruction, so the LLM cannot
forget the step or rationalise skipping it.

Precedent: AAA-51 grounding_confidence + AAA-56 E-E-A-T injection — both
deterministic post-workflow injections wired by caller code, NOT
agent-driven. AAA-108 follows the same shape.

Production callers (current + future) MUST call:

    from reverse_engineering_agent.tools import persist_re_findings
    # ... after the RE agent has run to completion and session state is
    #     finalised (events consumed, runner returned) ...
    await persist_re_findings(
        audit_id=session_state["current_audit_id"],
        state=session_state,
    )

Existing caller: reverse_engineering_agent/test_agent.py::run_one (already
wired, AAA-108 Sub-step 2). When production endpoints land (e.g. AAA-96
HTTP entry-point, Cloud Run deploy code, scheduled-job invoker), they
MUST replicate this call verbatim. Failure to wire it = silent loss of
RE workflow output (the client Discovery audit still persists at workflow
step 1, but re_findings stays None and downstream UIs see no RE data).
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from urllib.parse import urlparse

logger = logging.getLogger(__name__)  # AAA-170 competitor-extension

from google.adk.tools import FunctionTool, ToolContext
from google.genai import types as genai_types

from dataforseo_mcp_test.mcp_client import (
    extract_provider_status,
    find_cost,
    iter_result_items,
    open_session,
    result_to_json,
)
from reverse_engineering_agent.comparison import run_comparison
from reverse_engineering_agent.ranking import (
    compute_client_ranking_status,
    select_competitors,
)
# AAA-294 S1-A — canonical, deterministic entitlement gate. Every dangerous
# named-competitor deep-research branch reads the policy through this module
# (never re-implements the rule). Import is pure (stdlib-only module) → no cycle.
from memory import entitlement as _entitlement

SERP_LIST_PRICE_USD = 0.003  # MCP trims cost; this is the known list price.


# --------------------------------------------------------------------------
# AAA-31 Sub-step 2 — forced CLIENT archive id (Flag-2 fix: job_id == archive_id)
# --------------------------------------------------------------------------
# The dispatcher (Sub-step 1) mints an audit_id and hands it to the worker.
# run_one(url, audit_id=...) records it here; _run_discovery_archived consumes
# it ONCE for the CLIENT audit (corpus_mode=False) so the archive doc id equals
# the dispatcher job id. Competitor corpus archives (corpus_mode=True) keep
# minting their own ids — they must never collide with the client id.
#
# Module-level state is intentional and SAFE under Cloud Run container
# concurrency = 1 (one audit per instance; see deploy_agent/worker.py). A future
# concurrency bump MUST move this (and _USAGE/_KG_USAGE) to request scope.
_FORCED_CLIENT_AUDIT_ID: str | None = None


def set_client_audit_id(audit_id: str | None) -> None:
    """Record the dispatcher job id to use as the next CLIENT archive id."""
    global _FORCED_CLIENT_AUDIT_ID
    _FORCED_CLIENT_AUDIT_ID = audit_id or None


def _consume_client_audit_id() -> str | None:
    """Return-and-clear the forced client id (one-shot, client audit only)."""
    global _FORCED_CLIENT_AUDIT_ID
    aid = _FORCED_CLIENT_AUDIT_ID
    _FORCED_CLIENT_AUDIT_ID = None
    return aid


# --------------------------------------------------------------------------
# 1. Discovery sub-agent as a FunctionTool (nested runner + state harvest)
# --------------------------------------------------------------------------
async def _run_discovery(url: str, target_country: str) -> dict:
    """Run the real discovery_agent end-to-end and return its structured
    audit (harvested from session state, not the markdown)."""
    # Imported lazily so agent.py import order stays clean.
    from google.adk.runners import InMemoryRunner
    from discovery_agent.agent import discovery_agent

    runner = InMemoryRunner(discovery_agent, app_name="re_discovery")
    session = await runner.session_service.create_session(
        app_name="re_discovery", user_id="re"
    )
    hint = f" Target country: {target_country}." if target_country else ""
    msg = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=f"Audit this URL: {url}.{hint}")],
    )
    summary = ""
    async for event in runner.run_async(
        user_id="re", session_id=session.id, new_message=msg
    ):
        if event.content and event.content.parts and event.content.role == "model":
            t = "".join(p.text for p in event.content.parts if getattr(p, "text", None))
            if t:
                summary = t
    sess = await runner.session_service.get_session(
        app_name="re_discovery", user_id="re", session_id=session.id
    )
    st = sess.state if sess else {}
    return {
        "url": url,
        "crawl": st.get("crawl") or {},
        "rendered_crawl": st.get("rendered_crawl"),
        "site_profile": st.get("site_profile") or {},
        "keywords": st.get("keywords") or {},
        "entities": st.get("entities") or {},
        "pagespeed": st.get("pagespeed") or {},
        "indexing": st.get("indexing") or {},
        "summary_markdown": summary,
        "_tools": st.get("_tools") or [],
        "_cost": st.get("_cost") or 0.0,
    }


async def _run_discovery_archived(url: str, corpus_mode: bool = False,
                                  eeat_anchor_keyword: str | None = None) -> tuple[dict, str, str]:
    """AAA-64 Sub-step 2: run the FULL Discovery audit through the standalone
    harness so AAA-56 E-E-A-T injection + AAA-61 archive (write_audit) +
    translation all run. Returns (re_audit_dict, audit_id, summary_en).

    AAA-75 Sub-step 3: corpus_mode=True archives a COMPETITOR at corpus-subset
    quality (KEEP enrichments + EN-canonical + embedding; DROP client-strategy
    steps + HU). The client still calls this with corpus_mode=False (full audit).

    Reuses 100% of Discovery+archive logic with zero duplication (same
    philosophy as discovery_agent_tool's nested-runner pattern).
    """
    from discovery_agent.test_agent import audit as _discovery_audit
    from memory.firestore_archive import read_audit

    # AAA-31 S2: the CLIENT audit (corpus_mode=False) adopts the dispatcher's
    # forced id so job_id == archive_id. Competitors (corpus_mode=True) never do.
    forced_id = _consume_client_audit_id() if not corpus_mode else None
    ao = await _discovery_audit(url, corpus_mode=corpus_mode,
                                audit_id=forced_id,
                                eeat_anchor_keyword=eeat_anchor_keyword)  # AuditOutput + AAA-56 + write_audit
    audit_id = ao.get("audit_id") or ""
    # summary_translations lives on the archived doc (source of truth);
    # "en" is the byte-identity copy of summary_markdown (S3 Issue B2).
    summary_en = ""
    if audit_id:
        archived = await read_audit(audit_id)
        if archived:
            summary_en = (
                (archived.get("audit_output") or {})
                .get("summary_translations", {})
                .get("en", "")
            )
    if not summary_en:
        summary_en = ao.get("summary_markdown") or ""

    re_audit = {
        "url": url,
        "crawl": ao.get("crawl") or {},
        "rendered_crawl": ao.get("rendered_crawl"),
        "site_profile": ao.get("site_profile") or {},
        "keywords": ao.get("target_keywords") or {},
        "entities": ao.get("entities") or {},
        "pagespeed": ao.get("pagespeed") or {},
        "indexing": ao.get("indexing") or {},
        "summary_markdown": ao.get("summary_markdown") or "",
        "_tools": (ao.get("audit_metadata") or {}).get("tools_called") or [],
        "_cost": ao.get("audit_cost_usd") or 0.0,
        # AAA-118 Sub-step 2.2: surface multi-dim classification fields for
        # downstream target_type mapping in dataforseo_serp_query_tool.
        "page_type": ao.get("page_type"),
        "business_model": ao.get("business_model"),
        "topic_domain": ao.get("topic_domain"),
        # AAA-114 Sub-step 1: surface locality for Stage 2 conditional
        # soft-filter dim (only consulted when locality == "local").
        "locality": ao.get("locality"),
        # site_profile.language is the site's natural language (AAA-110/111).
        # audit_language (archive-level) is the same value normalized to
        # hu/en for the AAA-61 translation contract; read from archived doc
        # if archive succeeded.
    }
    return re_audit, audit_id, summary_en


async def discovery_agent_tool(
    url: str, role: str, target_country: str, tool_context: ToolContext
) -> dict:
    """Run the full Discovery audit on a URL (reuses the entire Discovery
    Agent). Use role="client" for the client URL and role="competitor" for
    each competitor. Returns a compact summary; full audit is retained
    internally for the comparison step.

    role="client" additionally archives the audit (AAA-61) and exposes
    current_audit_id + client_summary_en in session state for the memory
    retrieval tool. role="competitor" stays lightweight (no archive).

    Args:
        url: Absolute URL to audit.
        role: "client" or "competitor".
        target_country: Country to force locale (skip re-detection); "" if unknown.
    """
    audits = tool_context.state.get("audits") or {}
    if role == "client":
        audit, audit_id, summary_en = await _run_discovery_archived(url)
        tool_context.state["client_url"] = url
        tool_context.state["current_audit_id"] = audit_id
        tool_context.state["client_summary_en"] = summary_en
    else:
        audit = await _run_discovery(url, target_country or "")

    audits[url] = audit
    tool_context.state["audits"] = audits
    if role == "client":
        # AAA-251 — brand-as-primary on recognized-brand homepages. Runs BEFORE the
        # primary-SERP fetch so SERP/§2/§3/§7 all re-key to the brand term. Mutates
        # audit["keywords"] in place (state + this tool's return both re-key) and
        # persists the override + the cached audited-domain ranked rows. Deterministic,
        # failure-isolated — never breaks discovery.
        try:
            from page_analysis.brand_primary import maybe_override_brand_primary
            bp_status = await maybe_override_brand_primary(audit, url, audit_id)
            tool_context.state["brand_primary_status"] = bp_status
            if bp_status.get("fired"):
                kw = audit["keywords"]  # re-bind for the return below
        except Exception as _e:  # noqa: BLE001 — never break discovery
            tool_context.state["brand_primary_status"] = {"fired": False,
                                                          "reason": "wrapper: %s" % _e}
    else:
        comp = tool_context.state.get("competitor_urls_done") or []
        tool_context.state["competitor_urls_done"] = comp + [url]

    sp = audit["site_profile"]
    kw = audit["keywords"]
    mc = (audit["crawl"].get("main_content") or {})
    return {
        "role": role,
        "url": url,
        "current_audit_id": (
            tool_context.state.get("current_audit_id")
            if role == "client" else None
        ),
        "brand": sp.get("brand"),
        "primary_keyword": kw.get("primary_keyword"),
        "category_keyword": kw.get("category_keyword"),
        "keyword_gap_severity": kw.get("keyword_gap_severity"),
        "serp_strategy": sp.get("serp_strategy"),
        "target_country": sp.get("location"),
        "target_language": sp.get("language"),
        "needs_user_confirmation": sp.get("needs_user_confirmation"),
        "main_content_confidence": mc.get("confidence"),
        "content_limited": "[CONTENT LIMITED" in (kw.get("reasoning") or ""),
        "discovery_error": audit["crawl"].get("error"),
    }


# --------------------------------------------------------------------------
# 1b. AAA-88 Sub-step 2 — competitor batch Discovery (asyncio.gather)
# --------------------------------------------------------------------------
async def audit_all_competitors_tool(tool_context: ToolContext) -> dict:
    """Audit ALL category competitor URLs in PARALLEL via asyncio.gather.

    Replaces the previous "call discovery_agent_tool with role='competitor'
    sequentially N times" pattern. The wall-clock saving is bounded by the
    slowest competitor (not the sum of all three).

    State contract (READ):
      - state["competitor_urls"]: list[str] (set by select_competitor_urls_tool)
      - state["client_url"]: str (set by discovery_agent_tool role='client')
      - state["audits"][client_url].site_profile.location: derives target_country

    State contract (WRITE):
      - state["audits"][url] = audit dict (each competitor result, success
        or {"crawl":{"error":...}} placeholder on failure)
      - state["competitor_urls_done"]: list[str] (successful URLs only)

    Per-competitor failures are caught (return_exceptions=True) and recorded
    as crawl errors so the AAA-108 competitor_audits status map can surface
    them as "error: <reason>" without crashing the batch.
    """
    # AAA-294 S1-A GATE — named-competitor crawl+audit is competitor_research.
    # When not entitled, NO competitor is fetched/audited (own guard, not just
    # the empty-URL check below). Deterministic; no provider call is issued.
    if not _entitlement.competitor_research_entitled(
            _entitlement.from_state(tool_context.state)):
        return {"count": 0, "succeeded": 0, "failed": 0, "results": [],
                "status": "not_entitled",
                "note": "competitor_research not entitled — base audit only"}
    competitor_urls = tool_context.state.get("competitor_urls") or []
    if not competitor_urls:
        return {"count": 0, "succeeded": 0, "failed": 0, "results": [],
                "note": "no competitor URLs in state"}

    client_url = tool_context.state.get("client_url") or ""
    audits = tool_context.state.get("audits") or {}
    client_audit = audits.get(client_url) or {}
    target_country = (
        (client_audit.get("site_profile") or {}).get("location") or ""
    )
    # AAA-172 (+fix): shared query anchor = the CLIENT's SERP INPUT keyword =
    # the descriptive category query the audit/SERP ran on (category_keyword),
    # NOT the drift-prone derived primary_keyword. Threaded into every
    # competitor's score_eeat so client + competitors share one common-query
    # basis. NO primary_keyword fallback (per AAA-172 fix).
    client_kw = client_audit.get("keywords") or {}
    client_anchor_kw = client_kw.get("category_keyword")

    # AAA-75 Sub-step 3: each competitor is now ARCHIVED at corpus-subset
    # quality via audit(corpus_mode=True) (KEEP enrichments + EN-canonical +
    # embedding; DROP client-strategy steps + HU). Parallel — each gets its own
    # InMemoryRunner + session, gather is safe; per-call exceptions captured.
    def _crawl_errored(a: dict) -> bool:
        cr = (a or {}).get("crawl") or {}
        return bool(cr.get("error") or cr.get("error_type"))

    async def _run_competitor_corpus(u: str) -> dict:
        re_audit, aid, _ = await _run_discovery_archived(
            u, corpus_mode=True, eeat_anchor_keyword=client_anchor_kw)  # AAA-172
        # AAA-195 FIX 2 — RETRY ONCE on a competitor crawl timeout/empty fetch.
        # If the retry succeeds, use it; if it also fails, keep the failed audit
        # (downstream skips it from the comparison + the render flags it as
        # "unavailable at the moment of the query" — NO replacement, NO zeros).
        if _crawl_errored(re_audit):
            re_audit2, aid2, _ = await _run_discovery_archived(
                u, corpus_mode=True, eeat_anchor_keyword=client_anchor_kw)
            if not _crawl_errored(re_audit2):
                re_audit, aid = re_audit2, aid2
        re_audit["audit_id"] = aid
        return re_audit

    results = await asyncio.gather(
        *(_run_competitor_corpus(u) for u in competitor_urls),
        return_exceptions=True,
    )

    summary = []
    done: list[str] = []
    competitor_cost_total = 0.0
    for url, res in zip(competitor_urls, results):
        if isinstance(res, BaseException):
            audits[url] = {
                "url": url,
                "crawl": {"error":
                          f"competitor Discovery failed: "
                          f"{type(res).__name__}: {res}"},
                "site_profile": {}, "keywords": {}, "entities": {},
                "pagespeed": {}, "indexing": {}, "summary_markdown": "",
                "_tools": [], "_cost": 0.0,
            }
            summary.append({
                "url": url, "ok": False,
                "error": f"{type(res).__name__}: {res}",
            })
        else:
            audits[url] = res
            done.append(url)
            mc = (res.get("crawl") or {}).get("main_content") or {}
            # AAA-53/AAA-75: per-competitor compute cost (agent MEASURED cost;
            # the separate enrichment/embedding costs live on the archived doc).
            comp_cost = res.get("_cost") or 0.0
            competitor_cost_total += comp_cost
            summary.append({
                "url": url, "ok": True,
                "audit_id": res.get("audit_id"),
                "competitor_cost_usd": round(comp_cost, 6),
                "discovery_error": (res.get("crawl") or {}).get("error"),
                "main_content_confidence": mc.get("confidence"),
                "content_limited": (
                    "[CONTENT LIMITED" in (
                        (res.get("keywords") or {}).get("reasoning") or ""
                    )
                ),
            })

    tool_context.state["audits"] = audits
    tool_context.state["competitor_urls_done"] = done

    return {
        "count": len(competitor_urls),
        "succeeded": sum(1 for s in summary if s["ok"]),
        "failed": sum(1 for s in summary if not s["ok"]),
        "results": summary,
        # AAA-75: per-RE-run additional competitor-archive cost (agent compute;
        # enrichment/embedding costs are separate on each archived doc).
        "competitor_archive_cost_usd": round(competitor_cost_total, 6),
        "note": (
            f"parallel via asyncio.gather; wall-clock = max competitor "
            f"latency, NOT sum; competitors archived corpus-subset (AAA-75)"
        ),
    }


# --------------------------------------------------------------------------
# 2. DataForSEO SERP (organic + AI Overview)
# --------------------------------------------------------------------------
def _domain(u: str) -> str:
    try:
        h = urlparse(u).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except ValueError:
        return ""


def _norm_kw(s: str) -> str:
    return " ".join((s or "").lower().split())


# AAA-268 follow-up — provider-status retry policy. Transient classes are
# retried once; auth_error / insufficient_funds are FATAL (a retry cannot help).
_SERP_RETRYABLE_STATUS = {
    "throttled", "quota_exhausted", "timeout", "provider_error", "parse_error",
}
_SERP_RETRY_BACKOFF_S = 0.7


def _parse_serp_payload(payload: dict, depth: int) -> tuple:
    """Extract ``(organic_records, organic_urls, ai_present, citations)`` from a
    DataForSEO payload. Pure parsing — no status interpretation."""
    organic_records: list[dict] = []
    seen_urls: set[str] = set()
    fallback_pos = 0  # 1-based list-index fallback when rank_* absent
    citations: list[str] = []
    ai_present = False

    for item in iter_result_items(payload):
        t = item.get("type")
        if t == "organic" and item.get("url"):
            url = item["url"]
            if url in seen_urls:
                # First occurrence wins (preserves its position/title/snippet);
                # do NOT bump fallback_pos for a duplicate.
                pass
            else:
                seen_urls.add(url)
                fallback_pos += 1
                pos = item.get("rank_group")
                if pos is None:
                    pos = item.get("rank_absolute")
                if pos is None:
                    pos = fallback_pos
                organic_records.append({
                    "position": pos,
                    "url": url,
                    "title": item.get("title") or "",
                    "snippet": item.get("description") or "",
                })
        if t in ("ai_overview", "ai_overview_reference") or item.get(
            "asynchronous_ai_overview"
        ):
            ai_present = True

            def _walk(n):
                if isinstance(n, dict):
                    u = n.get("url") or n.get("link")
                    if isinstance(u, str) and u.startswith("http"):
                        citations.append(u)
                    for v in n.values():
                        _walk(v)
                elif isinstance(n, list):
                    for v in n:
                        _walk(v)

            _walk(item)

    organic_records = organic_records[:depth]
    organic_urls = [r["url"] for r in organic_records]  # legacy derived view
    return organic_records, organic_urls, ai_present, citations


def _serp_error_result(keyword: str, query_metadata: dict, status: tuple) -> dict:
    """Structured provider-error SERP result. Organic stays empty BUT `error` is
    set + the provider status is preserved (internal/debug), so downstream renders
    not-captured / data-unavailable — never a false 'does not rank'."""
    code, msg, cls = status
    detail = f" (status {code}: {msg})" if (code is not None or msg) else ""
    return {
        "keyword": keyword,
        "error": f"SERP provider {cls}{detail}",
        "organic_results": [],
        "organic_urls": [],
        "ai_overview_present": False,
        "ai_overview_citations": [],
        "cost_usd": 0.0,
        "query_metadata": query_metadata,
        "serp_data_status": "provider_error",
        "provider_status": cls,
        "provider_status_code": code,
        "provider_status_message": msg,
    }


async def _run_serp(keyword: str, location: str, language: str,
                    depth: int = 10) -> dict:
    """Single DataForSEO SERP query -> {organic_results, organic_urls,
    ai_*, cost_usd}.

    AAA-108 Sub-step 1: extractor extended to keep the full
    {position, url, title, snippet} record per organic result. The legacy
    `organic_urls: list[str]` view is preserved (derived from
    organic_results) so existing consumers (ranking.py::select_competitors,
    compute_client_ranking_status, render_serp_landscape, postprocess.py)
    keep working byte-unchanged. Sub-step 2 will migrate consumers to
    `organic_results` and retire `organic_urls`.

    Field mapping from DataForSEO `serp_organic_live_advanced`:
      - position -> item["rank_group"] (organic-only 1-based rank);
        fallback chain: rank_absolute -> 1-based list index.
      - url      -> item["url"]
      - title    -> item.get("title") or "" (skip-finding)
      - snippet  -> item.get("description") or "" (DataForSEO names the
        snippet field `description`; "" when absent).
    Dedup: by URL, first-occurrence wins (keeps its position/title/snippet).
    """
    args = {
        "keyword": keyword,
        "search_engine": "google",
        "location_name": location,
        "language_code": language,
        "depth": depth,
    }
    # AAA-108 Sub-step 4 — forensic data-completeness: the exact inputs sent
    # to DataForSEO are preserved in the return dict so downstream archivers
    # (persist_re_findings) can pass them through verbatim. Populated on
    # BOTH the success and the error branches (shape symmetry).
    query_metadata = {
        "keyword": keyword,
        "location_name": location,
        "language_code": language,
    }
    # AAA-268 follow-up — provider-status hardening. A DataForSEO API-level
    # failure (auth / balance / quota / throttle / timeout) returns a non-20000
    # status that parses to zero items; previously stored as a fake "0 organic"
    # with no error. We now check the status, retry transient classes once, and
    # emit a structured error (so the report renders not-captured, never a false
    # "does not rank"). A genuine 20000/Ok empty SERP is preserved distinctly.
    last_status = (None, None, "unknown")
    for attempt in range(2):  # initial + one retry
        try:
            async with open_session() as session:
                res = await session.call_tool(
                    "serp_organic_live_advanced", arguments=args
                )
            payload = result_to_json(res)
        except Exception as exc:  # noqa: BLE001 — transport/timeout
            cls = "timeout" if "timeout" in str(exc).lower() else "provider_error"
            last_status = (None, f"{type(exc).__name__}: {exc}", cls)
            if attempt == 0:
                await asyncio.sleep(_SERP_RETRY_BACKOFF_S)
                continue
            return _serp_error_result(keyword, query_metadata, last_status)

        code, msg, cls = extract_provider_status(payload)
        last_status = (code, msg, cls)
        organic_records, organic_urls, ai_present, citations = \
            _parse_serp_payload(payload, depth)

        # Accept when the provider says OK, or when status is merely UNKNOWN
        # (trimmed shape without a status field) but real organic came back.
        if cls == "ok" or (cls == "unknown" and organic_records):
            cost = find_cost(payload)
            return {
                "keyword": keyword,
                "organic_results": organic_records,
                "organic_urls": organic_urls,
                "ai_overview_present": ai_present,
                "ai_overview_citations": list(dict.fromkeys(citations)),
                "cost_usd": cost if cost is not None else SERP_LIST_PRICE_USD,
                "query_metadata": query_metadata,  # AAA-108 S4
                # AAA-268 — distinguish a genuine empty SERP from a provider error.
                "serp_data_status": "ok" if organic_records else "available_empty",
                "provider_status": cls,
                "provider_status_code": code,
                "provider_status_message": msg,
            }

        # Suspicious empty: no status, nothing organic, and no AI Overview /
        # citations either — a normally non-empty query returning total silence
        # is more likely a soft provider failure than a real empty SERP.
        suspicious_empty = (cls == "unknown" and not organic_records
                            and not ai_present and not citations)
        retryable = cls in _SERP_RETRYABLE_STATUS or suspicious_empty
        if attempt == 0 and retryable:
            await asyncio.sleep(_SERP_RETRY_BACKOFF_S)
            continue
        # Fatal class (auth / funds) or retry exhausted → structured error.
        return _serp_error_result(keyword, query_metadata, last_status)

    return _serp_error_result(keyword, query_metadata, last_status)


async def dataforseo_serp_query_tool(
    primary_keyword: str,
    category_keyword: str,
    location: str,
    language: str,
    tool_context: ToolContext,
    depth: int = 10,
) -> dict:
    """Run TWO DataForSEO SERP queries: the branded primary_keyword AND the
    broader category_keyword. If they are the same (normalised), the second
    query is SKIPPED and reused (saves $0.003).

    Args:
        primary_keyword: The client's (often branded) primary keyword.
        category_keyword: The broader competitive category keyword.
        location: Full country name, e.g. "United States".
        language: ISO language code, e.g. "en".
        depth: Organic results per query (default 10).
    """
    branded = await _run_serp(primary_keyword, location, language, depth)
    identical = _norm_kw(primary_keyword) == _norm_kw(category_keyword) or \
        not (category_keyword or "").strip()

    if identical:
        category = dict(branded)
        category["keyword"] = category_keyword or primary_keyword
        category["reused_from_branded"] = True
        # AAA-108 Sub-step 4: re-derive query_metadata for the category side
        # (shallow dict(branded) would alias branded's query_metadata dict —
        # mutating one would mutate the other). Same locale as branded
        # (identical SERPs share locale by definition); keyword reflects
        # what category WOULD have queried.
        branded_qm = branded.get("query_metadata") or {}
        category["query_metadata"] = {
            "keyword": category_keyword or primary_keyword,
            "location_name": branded_qm.get("location_name", location),
            "language_code": branded_qm.get("language_code", language),
        }
        total_cost = branded.get("cost_usd", 0.0)
    else:
        category = await _run_serp(category_keyword, location, language, depth)
        category["reused_from_branded"] = False
        total_cost = branded.get("cost_usd", 0.0) + category.get(
            "cost_usd", 0.0
        )

    tool_context.state["serp_result_branded"] = branded
    tool_context.state["serp_result_category"] = category
    tool_context.state["serps_identical"] = identical
    # Back-compat: keep `serp_result` pointing at the category SERP (the one
    # used for deep competitor auditing).
    tool_context.state["serp_result"] = category

    # AAA-118 Sub-step 1 — Stage 1 SERP-fit classification on the fetched
    # SERPs. Scope-gap note: this implementation processes the keywords
    # AAA-50 currently fetches (2: branded + category). The AAA-118 spec
    # envisioned 5-keyword fan-out (primary + 4 secondary); AAA-50 expansion
    # is a separate scope decision. Module is forward-compatible — it
    # accepts an arbitrary-length list of (keyword, organic_results) pairs.
    # Cache hits add 0 to cost; AAA-53 separation keeps the cost out of
    # audit_cost_usd. State writes feed _assemble_re_findings via
    # persist_re_findings post-workflow dotted-path update.
    try:
        from page_analysis.serp_fit_classify import classify_all_serps
        # Build the keyword/SERP list. Reuse target context from the client
        # Discovery audit (already in state via discovery_agent_tool/role=client).
        audits = tool_context.state.get("audits") or {}
        client_url = tool_context.state.get("client_url") or ""
        client_audit = audits.get(client_url) or {}
        client_kw = client_audit.get("keywords") or {}
        client_sp = client_audit.get("site_profile") or {}
        target_topic_cluster = (
            client_kw.get("topic_cluster")
            or client_audit.get("topic_cluster")
        )
        target_category_keyword = (
            client_kw.get("category_keyword")
            or client_audit.get("category_keyword")
        )
        # topic_domain lives on the archived AuditOutput (post-AAA-111).
        # The lightweight competitor _run_discovery() shape doesn't carry
        # it, so the client audit via _run_discovery_archived is the
        # source. Best-effort read from session state's archived doc.
        target_topic_domain = (
            client_sp.get("topic_domain")  # NOT typically present here
            or client_audit.get("topic_domain")  # nor here
        )

        ks_inputs: list[dict] = [
            {
                "keyword": branded.get("query_metadata", {}).get("keyword")
                           or primary_keyword,
                "keyword_role": "primary",
                "organic_results": branded.get("organic_results") or [],
                "_error": branded.get("error"),
            },
        ]
        if not identical:
            ks_inputs.append({
                "keyword": category.get("query_metadata", {}).get("keyword")
                           or category_keyword,
                "keyword_role": "secondary",  # category SERP = secondary scope
                "organic_results": category.get("organic_results") or [],
                "_error": category.get("error"),
            })
        # else: identical → only one SERP exists. The second slot collapses
        # by design; serp_fit_analysis is a list, not a fixed-N tuple.

        sfa, sf_cost, sf_calls, sf_cache_hits = await classify_all_serps(
            target_url=client_url,
            topic_domain=target_topic_domain,
            topic_cluster=target_topic_cluster,
            category_keyword=target_category_keyword,
            keyword_serps=ks_inputs,
        )

        # AAA-118 Sub-step 2.2 — target_type mapping + verdict + conditional
        # alternative-keyword LLM call. target_type is deterministic from
        # the client's AAA-81 v3 page_type (surfaced via _run_discovery_archived).
        from page_analysis.serp_fit_classify import (
            map_target_type, enrich_with_verdicts,
        )
        target_page_type = client_audit.get("page_type")
        target_business_model = client_audit.get("business_model")
        audit_target_type = map_target_type(
            target_page_type, target_business_model
        )
        # audit_language = site language for the alternative-keyword call.
        # Read from site_profile (always present) — normalized to hu/en in
        # the archive layer but the site_profile.language is the raw value.
        audit_language = (
            (client_audit.get("site_profile") or {}).get("language")
            or "en"
        )
        sfa, alt_cost = await enrich_with_verdicts(
            sfa,
            target_type=audit_target_type,
            target_topic_domain=target_topic_domain,
            target_topic_cluster=target_topic_cluster,
            target_category_keyword=target_category_keyword,
            audit_language=audit_language,
        )
        # Spec: alternative LLM cost goes INTO audit_serp_fit_cost_usd,
        # NOT a separate field.
        sf_cost = round(sf_cost + alt_cost, 6)

        tool_context.state["serp_fit_analysis"] = sfa
        tool_context.state["audit_serp_fit_cost_usd"] = sf_cost
        tool_context.state["audit_serp_fit_call_count"] = sf_calls
        tool_context.state["audit_serp_fit_cache_hits"] = sf_cache_hits
        tool_context.state["audit_target_type"] = audit_target_type

        # AAA-114 Sub-step 1 — Stage 2 deterministic competitor filter +
        # ranker. Consumes Stage 1 enrichment + audit_target_type. NO LLM
        # calls. Coexists with the AAA-108 legacy competitor_audits path
        # (F-additive — legacy continues independently). Hard filter A
        # (business_model / audience_relationship set-match) is DEFERRED
        # to Sub-step 2 per Option A-1 (Stage 1 doesn't enrich per-candidate
        # business_model; adding a classify call would cost ~$0.15/audit
        # and defeat AAA-118 S0.5 Q3's Opció-X cost-saving).
        from reverse_engineering_agent.competitor_filter import (
            filter_and_rank_competitors,
        )
        target_locality = client_audit.get("locality")
        # Soft-filter locality dim needs the target's own host TLD when
        # locality == "local". Derive from client_url.
        target_locality_tld = None
        if target_locality == "local" and client_url:
            try:
                from urllib.parse import urlparse as _urlparse
                _host = (_urlparse(client_url).netloc or "").lower()
                if _host.startswith("www."):
                    _host = _host[4:]
                if "." in _host:
                    target_locality_tld = _host.rsplit(".", 1)[-1]
            except Exception:  # noqa: BLE001
                target_locality_tld = None
        stage2 = filter_and_rank_competitors(
            serp_fit_analysis=sfa,
            audit_target_type=audit_target_type,
            # AAA-114 Sub-step 2: pass target context for the now-active
            # topic_cluster_match + category_keyword_match dims.
            target_topic_cluster=target_topic_cluster,
            target_category_keyword=target_category_keyword,
            target_locality=target_locality,
            target_locality_tld=target_locality_tld,
            # AAA-114 Sub-step 3.5: client-URL self-match exclusion
            # (gates BEFORE hard filter B + C; vercel.com canary trigger).
            client_url=client_url,
        )
        tool_context.state["selected_competitors_v2"] = (
            stage2["selected_competitors_v2"]
        )
        tool_context.state["audit_no_comparable_competitors_found"] = (
            stage2["no_comparable_competitors_found"]
        )
        # AAA-167 S1: business-peer presence (A vs B disambiguation downstream).
        tool_context.state["competitor_business_peer_count"] = (
            stage2.get("business_peer_count")
        )
    except Exception as e:  # noqa: BLE001 — Stage 1 is non-critical
        # Stage 1 failure must not break the RE workflow. Leave state empty.
        tool_context.state["serp_fit_analysis"] = []
        tool_context.state["audit_serp_fit_cost_usd"] = 0.0
        tool_context.state["audit_serp_fit_call_count"] = 0
        tool_context.state["audit_serp_fit_cache_hits"] = 0
        tool_context.state["audit_serp_fit_error"] = (
            f"{type(e).__name__}: {e}"
        )

    return {
        "serps_identical": identical,
        "branded_keyword": primary_keyword,
        "category_keyword": category_keyword,
        "branded_organic_count": len(branded.get("organic_urls", [])),
        "category_organic_count": len(category.get("organic_urls", [])),
        "branded_ai_overview": branded.get("ai_overview_present"),
        "category_ai_overview": category.get("ai_overview_present"),
        "total_serp_cost_usd": round(total_cost, 5),
        "note": ("category query skipped (== branded), $0.003 saved"
                 if identical else "two distinct SERP queries run"),
    }


# --------------------------------------------------------------------------
# 3. Competitor selection (pure Python)
# --------------------------------------------------------------------------
async def select_competitor_urls_tool(
    client_url: str, tool_context: ToolContext
) -> dict:
    """Select competitors + client ranking for BOTH SERPs.

    - branded_competitors: top 3 from the branded SERP (often the client
      itself / GitHub / Reddit / Wikipedia) - reported, NOT deep-audited.
    - category_competitors: top 3 from the category SERP - these become the
      deep-audit set (state["competitor_urls"]).
    Also computes client_ranking_status for both SERPs.

    Args:
        client_url: The client's URL (excluded from competitors).
    """
    branded = tool_context.state.get("serp_result_branded") or {}
    category = tool_context.state.get("serp_result_category") or {}
    identical = tool_context.state.get("serps_identical", False)
    audits = tool_context.state.get("audits") or {}
    client_audit = audits.get(client_url) or {}
    main_entities = (
        (client_audit.get("site_profile") or {}).get("main_entities") or []
    )

    sel_b = select_competitors(client_url, branded, main_entities)
    sel_c = select_competitors(client_url, category, main_entities)
    rank_b = compute_client_ranking_status(
        client_url, branded.get("organic_urls", []) or []
    )
    rank_c = compute_client_ranking_status(
        client_url, category.get("organic_urls", []) or []
    )

    # Branded/category lists stay as REPORTED context ("who ranks") — the full
    # SERP top-10 is preserved elsewhere as serp_fit_analysis.
    tool_context.state["branded_competitors"] = sel_b["competitor_urls"]
    tool_context.state["category_competitors"] = sel_c["competitor_urls"]
    tool_context.state["client_ranking_status_branded"] = rank_b
    tool_context.state["client_ranking_status_category"] = rank_c

    # AAA-167 S1: the DEEP-AUDIT set now comes from the AAA-114 v2 verdict
    # (genuine business-type competitors only, Hard-C filtered + soft-ranked),
    # NOT the unfiltered legacy first-3. NO fallback: if v2 yields <3 we audit
    # only those; if it yields 0 we audit none and emit a structured status.
    from reverse_engineering_agent.competitor_filter import _BUSINESS_TYPES
    v2 = tool_context.state.get("selected_competitors_v2") or []
    v2_selected = sorted(
        [e for e in v2 if e.get("selected") and e.get("url")],
        key=lambda e: e.get("selection_rank") or 99)
    audit_urls = [e["url"] for e in v2_selected][:3]
    biz_peer_count = tool_context.state.get("competitor_business_peer_count")
    if biz_peer_count is None:
        biz_peer_count = sum(1 for e in v2 if e.get("serp_type") in _BUSINESS_TYPES)

    # AAA-294 S1-A GATE — deep competitor research is competitor_research.
    # When not entitled we STILL keep the SERP domain lists + client ranking
    # above (they are landscape "who ranks" evidence, not a competitor audit),
    # but we select NO deep-audit target and mark the status explicitly. This
    # is the primary deterministic choke point: with competitor_urls==[] the
    # downstream crawl/audit/comparison stages have nothing to act on.
    not_entitled = not _entitlement.competitor_research_entitled(
        _entitlement.from_state(tool_context.state))
    if not_entitled:
        audit_urls = []
        comp_status = "not_entitled"
        comp_reason = ("Competitor research not entitled for this audit; SERP "
                       "landscape and client ranking retained as evidence, no "
                       "competitor deep-audit performed.")
    elif audit_urls:
        comp_status, comp_reason = "ok", None
    elif not v2:
        comp_status = "comparator_unavailable"
        comp_reason = ("SERP classification did not run; no competitor "
                       "comparability verdict available.")
    elif biz_peer_count == 0:
        # (A) no comparable business-type peer in the SERP at all -> the keyword
        # is likely mistargeted for this page. Only the comparison branch stops.
        comp_status = "no_comparable_competitors"
        comp_reason = ("No comparable business-type competitor appeared in the "
                       "SERP top-10 for the target keyword; the keyword is likely "
                       "mistargeted for this page. Competitor comparison skipped; "
                       "all other audit sections run normally.")
    else:
        # (B) business peers present but none met the comparability/relevance
        # threshold -> NOT a keyword mismatch; proceed with 0 comparisons.
        below = [e.get("url") for e in v2
                 if e.get("serp_type") in _BUSINESS_TYPES and not e.get("hard_filter_pass")]
        comp_status = "business_peers_below_threshold"
        comp_reason = ("%d business-type peer(s) present but none met the "
                       "comparability/relevance threshold; proceeding with no "
                       "competitor comparison (NOT a keyword mismatch). URLs: %s"
                       % (biz_peer_count, below))
    tool_context.state["competitor_urls"] = audit_urls
    tool_context.state["competitor_comparison_status"] = comp_status
    tool_context.state["competitor_comparison_reason"] = comp_reason

    return {
        "serps_identical": identical,
        "branded_competitors": sel_b["competitor_urls"],
        "category_competitors": sel_c["competitor_urls"],
        "deep_audit_set": "v2_genuine_competitors",  # AAA-167 S1
        "deep_audit_urls": audit_urls,
        "competitor_comparison_status": comp_status,
        "client_rank_branded": rank_b["ranking_severity"],
        "client_pos_branded": rank_b["position"],
        "client_rank_category": rank_c["ranking_severity"],
        "client_pos_category": rank_c["position"],
        "v2_selected_count": len(audit_urls),
        "business_peer_count": biz_peer_count,
    }


# --------------------------------------------------------------------------
# 4. Comparison (single Gemini call)
# --------------------------------------------------------------------------
async def compare_audits_tool(tool_context: ToolContext) -> dict:
    """Compare the client audit against the competitor audits (single Gemini
    call). Reads all audits from state; returns dimension table, patterns,
    and AI Overview summary.

    AAA-108 Sub-step 2: snapshots Gemini usage delta around the comparison
    call so persist_re_findings() can store the comparison cost SEPARATELY
    in re_findings.audit_re_comparison_cost_usd (AAA-53 pattern — never
    folded into audit_cost_usd).
    """
    from site_profile.gemini_analyzer import get_usage

    # AAA-294 S1-A GATE — the comparison is a paid Gemini call over competitor
    # audits, i.e. competitor_research. When not entitled we issue NO provider
    # call and return an explicit not_entitled status (own guard, independent of
    # the empty competitor_urls set the selection gate already produces).
    if not _entitlement.competitor_research_entitled(
            _entitlement.from_state(tool_context.state)):
        tool_context.state["comparison"] = {"status": "not_entitled"}
        tool_context.state["audit_re_comparison_cost_usd"] = 0.0
        return {"pattern_count": 0, "ai_overview_summary": "",
                "status": "not_entitled", "error": None}

    audits = tool_context.state.get("audits") or {}
    client_url = tool_context.state.get("client_url")
    comp_urls = tool_context.state.get("competitor_urls") or []
    serp = tool_context.state.get("serp_result") or {}

    client_audit = audits.get(client_url) or {}
    # AAA-294 per-domain validation (review 23142, defence in depth) — failed
    # placeholder audits (crawl error) never enter the comparison input, so the
    # comparison evidence can only reference successfully-audited domains. The
    # renderer STILL validates per-domain against its own persisted evidence
    # (successful_competitor_urls ∩ measured comparators) — it does not rely on
    # this producer-side filter alone.
    def _crawl_err(a: dict) -> bool:
        cr = (a or {}).get("crawl") or {}
        return bool(cr.get("error") or cr.get("error_type"))

    competitor_audits = [audits[u] for u in comp_urls
                         if u in audits and not _crawl_err(audits[u])]

    cost_before = get_usage().get("cost", 0.0)
    comparison = await run_comparison(client_audit, competitor_audits, serp)
    cost_after = get_usage().get("cost", 0.0)

    tool_context.state["comparison"] = comparison
    tool_context.state["audit_re_comparison_cost_usd"] = round(
        cost_after - cost_before, 6
    )
    return {
        "pattern_count": len(comparison.get("patterns", [])),
        "ai_overview_summary": comparison.get("ai_overview_summary", ""),
        "error": comparison.get("error"),
    }


# --------------------------------------------------------------------------
# 6. AAA-108: persist RE findings to the client's archived audit doc
# --------------------------------------------------------------------------
def _build_competitor_audit_ids(state: dict) -> dict:
    """AAA-170 competitor-extension — {competitor_url: audit_id} for the audited
    set, from state["audits"] (each entry carries its corpus-doc audit_id).
    Deterministic linkage for the eeat read-back (no URL lookup)."""
    audits = state.get("audits") or {}
    out: dict = {}
    for url in (state.get("competitor_urls") or []):
        aid = (audits.get(url) or {}).get("audit_id")
        if aid:
            out[url] = aid
    return out


async def _read_competitor_eeat(state: dict) -> list:
    """AAA-170 competitor-extension — PURE read-back ($0, reads only, NO re-score)
    of each audited competitor's own full_content eeat_score from its corpus doc
    (by audit_id). Missing/errored eeat_score -> grounding_confidence='unavailable'
    + _error (no re-score). Returns a list shaped forward-compatibly with the
    client eeat_score competitors[] contract."""
    from memory.firestore_archive import read_audit
    ids = _build_competitor_audit_ids(state)
    audits = state.get("audits") or {}

    async def _one(url: str, aid: str) -> dict:
        state_brand = ((audits.get(url) or {}).get("site_profile") or {}).get("brand")
        try:
            doc = await read_audit(aid)
        except Exception as e:  # noqa: BLE001 — never break persist on a read
            return {"url": url, "brand": state_brand, "audit_id": aid,
                    "grounding_confidence": "unavailable",
                    "_error": "read failed: %s: %s" % (type(e).__name__, e)}
        cao = (doc or {}).get("audit_output") or {}
        brand = state_brand or (cao.get("site_profile") or {}).get("brand")
        es = cao.get("eeat_score") or {}
        c = es.get("client")
        if not c or (es.get("_meta") or {}).get("error"):
            return {"url": url, "brand": brand, "audit_id": aid,
                    "grounding_confidence": "unavailable",
                    "_error": ((es.get("_meta") or {}).get("error")
                               or "no eeat_score in competitor corpus doc")}
        return {
            "url": url, "brand": brand, "audit_id": aid,
            "experience": c.get("experience"), "expertise": c.get("expertise"),
            "authoritativeness": c.get("authoritativeness"),
            "trustworthiness": c.get("trustworthiness"),
            "total_0_40": c.get("total_0_40"),
            "grounding_confidence": c.get("grounding_confidence") or "full_content",
            # AAA-172: the query the competitor was scored against (should equal
            # the client's anchor keyword — common-query basis for §2).
            "anchor_keyword": (es.get("_meta") or {}).get("anchor_keyword"),
            "_error": None,
        }

    if not ids:
        return []
    return list(await asyncio.gather(*(_one(u, a) for u, a in ids.items())))


def _build_competitor_audits_map(state: dict) -> dict:
    """Map the actually-audited competitor set -> per-URL status.

    Sources:
      - state["competitor_urls"]: the deep-audit set (AAA-167 S1: the AAA-114
        v2 genuine-competitor verdict, NOT the legacy category_competitors).
      - state["audits"]: dict url -> audit (presence indicates the
        Discovery tool ran on that URL); within each audit,
        audit["crawl"]["error"] surfaces a Discovery-side failure.

    Status values:
      "ok"                 -> audit ran and produced no crawl error
      "error: <reason>"    -> audit ran but Discovery reported a crawl error
      "not_attempted"      -> URL was in the intended set but no audit dict
                              exists (the agent never called the tool, or
                              the workflow aborted before this competitor)
    """
    # AAA-167 S1 fix: key on the actually-audited set (competitor_urls) so the
    # status map matches the v2 deep-audit set, not the legacy first-3.
    intended = state.get("competitor_urls") or []
    audits = state.get("audits") or {}
    result: dict = {}
    for url in intended:
        a = audits.get(url)
        if a is None:
            result[url] = "not_attempted"
            continue
        err = (a.get("crawl") or {}).get("error")
        if err:
            result[url] = f"error: {err}"
        else:
            result[url] = "ok"
    return result


def _resolve_serp_locale(site_profile: dict, client_url: str) -> tuple[str, str]:
    """AAA-146 Sub-step 1 Part 3 — SERP locale with a defensive fallback so a
    blank site_profile.location never reaches DataForSEO as an empty
    location_name. Normal path: client's detected market/language. Fallback
    (location empty): derive from TLD / site language, else a sane default."""
    loc = (site_profile.get("location") or "").strip()
    lang = (site_profile.get("language") or "").strip().lower()[:2]
    if not loc:
        host = urlparse(client_url if "://" in client_url
                        else "https://" + client_url).netloc.lower()
        if host.endswith(".hu") or lang == "hu":
            loc, lang = "Hungary", (lang or "hu")
        else:
            loc, lang = "United States", (lang or "en")
    if not lang:
        lang = "hu" if loc == "Hungary" else "en"
    return loc, lang


async def _enforce_serp_chain(state: dict) -> str:
    """AAA-146 Sub-step 1 Part 2 — deterministically run the mandatory
    SERP -> select -> audit_all_competitors chain from session state when the
    LLM agent skipped emitting the SERP tool (empty query_metadata). Uses the
    already-produced keyword + client locale. Returns a status string.

    Stop-condition guard: if the keyword/url isn't in state, do NOT hardcode —
    return a 'skipped' status so the caller surfaces it (no SERP fabricated)."""
    client_url = state.get("client_url") or ""
    ca = (state.get("audits") or {}).get(client_url) or {}
    kw = ca.get("keywords") or {}
    primary = (kw.get("primary_keyword") or "").strip()
    category = (kw.get("category_keyword") or "").strip() or primary
    if not (client_url and primary):
        return "skipped (keyword/url not in state)"
    location, language = _resolve_serp_locale(ca.get("site_profile") or {}, client_url)
    ctx = SimpleNamespace(state=state)
    # Base Search & AI evidence: the SERP query + client ranking/landscape ALWAYS
    # run (select_competitor_urls_tool self-gates the deep-audit set to []).
    await dataforseo_serp_query_tool(primary, category, location, language, ctx)
    await select_competitor_urls_tool(client_url, ctx)
    # AAA-294 S1-A GATE — the deterministic chain must NOT force the competitor
    # crawl/audit stage when competitor_research is not entitled (own guard; the
    # tool also self-gates, this keeps the enforced chain honest + call-free).
    if _entitlement.competitor_research_entitled(_entitlement.from_state(state)):
        await audit_all_competitors_tool(ctx)
        return ("enforced (deterministic SERP->select->audit; locale=%s/%s)"
                % (location, language))
    return ("enforced base (deterministic SERP->select; competitor audit "
            "not entitled; locale=%s/%s)" % (location, language))


def _classify_no_competitors_reason(state: dict, serp_b: dict, serp_c: dict,
                                    serps_identical: bool) -> str | None:
    """AAA-146 Sub-step 1 Part 1 — explicit reason when the deep-audit
    competitor set is empty. None when competitors were selected. Reuses the
    existing AAA-114 audit_no_comparable_competitors_found for all_filtered."""
    if state.get("competitor_urls"):
        return None  # competitors selected — clean
    # AAA-294 S1-A — an empty competitor set caused by the ENTITLEMENT SCOPE is not a
    # measurement outcome. It must NEVER be reported as all_filtered / no_comparable /
    # a mistargeted-keyword or page/keyword-mismatch diagnosis. Surface it as an
    # explicit scope reason so downstream prose stays honest (the raw enum is never
    # rendered to the customer — it is mapped/omitted by the renderer).
    if state.get("competitor_comparison_status") == "not_entitled" or \
            not _entitlement.competitor_research_entitled(_entitlement.from_state(state)):
        return "not_entitled"
    qm_b = serp_b.get("query_metadata") or {}
    organic_n = len(serp_b.get("organic_results") or []) + (
        0 if serps_identical else len(serp_c.get("organic_results") or []))
    if not qm_b:
        return "serp_not_run"          # tool never executed
    if organic_n == 0:
        return "serp_empty"            # SERP fired but returned nothing
    return "all_filtered"              # organics present, AAA-114 filtered all out


def _assemble_re_findings(state: dict) -> dict:
    """Build the 8-dimension re_findings dict from RE session state.

    Skip-finding contract: a missing/None individual dimension stays None
    (or an empty default); never raises. The caller decides whether to
    persist a partial dict — by reaching this function the RE workflow has
    completed the assembly point, so re_workflow_completed=True is honest.
    """
    sp = (
        (state.get("audits") or {})
        .get(state.get("client_url") or "", {})
        .get("site_profile", {})
    )
    locale_strategy = sp.get("serp_strategy") or "default"

    serp_b = state.get("serp_result_branded") or {}
    serp_c = state.get("serp_result_category") or {}
    serps_identical = bool(state.get("serps_identical", False))

    # Cost (AAA-53 SEPARATE; never folded into audit_cost_usd).
    re_serp_cost = float(serp_b.get("cost_usd", 0.0)) + (
        0.0 if serps_identical else float(serp_c.get("cost_usd", 0.0))
    )
    re_comparison_cost = float(
        state.get("audit_re_comparison_cost_usd", 0.0) or 0.0
    )

    memory_retrieval = state.get("memory_retrieval") or {}

    return {
        "serp_branded": {
            "organic_results": serp_b.get("organic_results") or [],
            "ai_overview_present": serp_b.get("ai_overview_present"),
            "ai_overview_citations": serp_b.get("ai_overview_citations") or [],
            "serp_locale_strategy": locale_strategy,
            # AAA-108 Sub-step 4 — forensic capture of what was actually
            # sent to DataForSEO. Empty dict on legacy entries.
            "query_metadata": serp_b.get("query_metadata") or {},
            "_error": serp_b.get("error"),
            # AAA-268 — internal/debug provider status (never public prose).
            "serp_data_status": serp_b.get("serp_data_status"),
            "provider_status": serp_b.get("provider_status"),
            "provider_status_code": serp_b.get("provider_status_code"),
            "provider_status_message": serp_b.get("provider_status_message"),
        },
        "serp_category": {
            "organic_results": serp_c.get("organic_results") or [],
            "ai_overview_present": serp_c.get("ai_overview_present"),
            "ai_overview_citations": serp_c.get("ai_overview_citations") or [],
            "serp_locale_strategy": locale_strategy,
            "reused_from_branded": bool(serp_c.get("reused_from_branded")),
            "query_metadata": serp_c.get("query_metadata") or {},
            "_error": serp_c.get("error"),
            "serp_data_status": serp_c.get("serp_data_status"),
            "provider_status": serp_c.get("provider_status"),
            "provider_status_code": serp_c.get("provider_status_code"),
            "provider_status_message": serp_c.get("provider_status_message"),
        },
        "serps_identical": serps_identical,
        "selected_competitors": {
            "branded": state.get("branded_competitors") or [],
            "category": state.get("category_competitors") or [],
        },
        "client_ranking_status": {
            "branded": state.get("client_ranking_status_branded"),
            "category": state.get("client_ranking_status_category"),
        },
        "competitor_audits": _build_competitor_audits_map(state),
        # AAA-170 competitor-extension — deterministic url->audit_id linkage so
        # the client persist can read back each competitor's own eeat_score from
        # its corpus doc by id (no URL lookup).
        "competitor_audit_ids": _build_competitor_audit_ids(state),
        "comparison": state.get("comparison"),
        "memory_retrieval": {
            "results": memory_retrieval.get("results") or [],
            "_error": memory_retrieval.get("_error"),
        },
        "audit_re_serp_cost_usd": round(re_serp_cost, 6),
        "audit_re_comparison_cost_usd": round(re_comparison_cost, 6),
        "re_workflow_completed": True,
        # AAA-146 Sub-step 1 Part 1 — explicit reason on a 0-competitor outcome
        # (serp_not_run / serp_empty / all_filtered); None when competitors were
        # selected. Makes a skipped/empty SERP visible instead of silent success.
        "no_competitors_reason": _classify_no_competitors_reason(
            state, serp_b, serp_c, serps_identical),
        # AAA-167 S1 — refined competitor-comparison verdict from the v2 path:
        # ok / no_comparable_competitors (A: keyword likely mistargeted) /
        # business_peers_below_threshold (B: NOT a keyword mismatch) /
        # comparator_unavailable. Drives the comparison-branch skip + finding.
        "competitor_comparison_status": state.get("competitor_comparison_status"),
        "competitor_comparison_reason": state.get("competitor_comparison_reason"),
    }


async def _apply_comparative_eeat(audit_id: str, state: dict, patch: dict) -> None:
    """AAA-172 S1 — run the comparative query-anchored E-E-A-T scorer and add the
    result leaves to `patch` (dotted-path). Reads the client doc + each audited
    competitor doc (by audit_id) for full_content; scores all against the client
    category_keyword in ONE call.

    Writes (happy path, >=2 genuine competitors):
      eeat_score.client                -> comparative client score (display source)
      eeat_score.client_page_intrinsic -> the step-1 per-page score (provenance)
      eeat_score.competitors           -> comparative competitor scores
      eeat_score.ranking               -> relative order (client + competitors)
      eeat_score.scoring_mode/comparative_status/anchor_keyword/rubric_version
      eeat_score.competitors_excluded  -> sub-threshold pages (url+reason+words)
      audit_eeat_score_cost_usd        -> existing + comparative cost (AAA-53)

    Fallback (<2 genuine competitors): leaves eeat_score.client untouched
    (page-intrinsic stays the display source) and records the skip status.
    """
    # AAA-294 S1-A GATE — comparative E-E-A-T reads each COMPETITOR doc and issues a
    # paid scoring call over them (competitor_research). When not entitled we perform
    # NO competitor doc read and NO comparative provider call, but we MUST preserve
    # the client's own page-intrinsic result: promote the existing eeat_score.client
    # (produced by the per-page scorer at workflow step 1) into the canonical
    # client_page_intrinsic field that fact_base.eeat.page_intrinsic reads. Without
    # this, §5 falsely reads "not measured" though the intrinsic dimensions exist.
    # read_audit here reads the CLIENT's OWN doc only (own data — not cross-audit).
    if not _entitlement.competitor_research_entitled(_entitlement.from_state(state)):
        from memory.firestore_archive import read_audit
        client_doc = await read_audit(audit_id)
        existing_eeat = ((client_doc or {}).get("audit_output") or {}).get("eeat_score") or {}
        page_intrinsic = existing_eeat.get("client")
        if page_intrinsic is not None and existing_eeat.get("client_page_intrinsic") is None:
            patch["audit_output.eeat_score.client_page_intrinsic"] = page_intrinsic
        patch["audit_output.eeat_score.comparative_status"] = "not_entitled"
        patch["audit_output.eeat_score.scoring_mode"] = "page_intrinsic_fallback"
        patch["audit_output.eeat_score.competitors"] = []
        return
    from memory.firestore_archive import read_audit
    from page_analysis.eeat_score import score_eeat_comparative

    # client doc (already archived at workflow step 1) — full content + anchor +
    # the existing page-intrinsic score + the running eeat cost.
    client_doc = await read_audit(audit_id)
    client_ao = (client_doc or {}).get("audit_output") or {}
    tk = client_ao.get("target_keywords") or {}
    anchor = (tk.get("category_keyword") or "").strip()
    existing_eeat = client_ao.get("eeat_score") or {}
    page_intrinsic = existing_eeat.get("client")  # may be None
    prior_cost = float(client_ao.get("audit_eeat_score_cost_usd") or 0.0)

    if not anchor:
        patch["audit_output.eeat_score.comparative_status"] = \
            "skipped_no_anchor_keyword"
        patch["audit_output.eeat_score.scoring_mode"] = "page_intrinsic_fallback"
        return

    # competitor docs by audit_id (AAA-75 corpus linkage).
    ids = _build_competitor_audit_ids(state)
    competitors = []
    for url, aid in ids.items():
        cdoc = await read_audit(aid)
        competitors.append({"url": url, "audit_id": aid,
                            "audit_output": (cdoc or {}).get("audit_output") or {}})

    result, cost = score_eeat_comparative(client_ao, competitors, anchor)
    mode = result.get("scoring_mode")

    # common leaves
    patch["audit_output.eeat_score.scoring_mode"] = mode
    patch["audit_output.eeat_score.comparative_status"] = result.get("comparative_status")
    patch["audit_output.eeat_score.anchor_keyword"] = anchor
    patch["audit_output.eeat_score.rubric_version"] = result.get("rubric_version")
    patch["audit_output.eeat_score.competitors_excluded"] = \
        result.get("competitors_excluded") or []

    if mode == "comparative" and result.get("client"):
        # happy path: comparative client becomes display source; preserve the
        # page-intrinsic score for provenance.
        if page_intrinsic is not None:
            patch["audit_output.eeat_score.client_page_intrinsic"] = page_intrinsic
        patch["audit_output.eeat_score.client"] = result["client"]
        patch["audit_output.eeat_score.competitors"] = result.get("competitors") or []
        patch["audit_output.eeat_score.ranking"] = result.get("ranking") or []
        patch["audit_output.audit_eeat_score_cost_usd"] = round(prior_cost + cost, 8)
        logger.info("comparative eeat OK: anchor=%r client=%s/40 cost=$%.6f",
                    anchor, (result["client"] or {}).get("total_0_40"), cost)
    else:
        # fallback / error: keep the page-intrinsic client score as display source.
        patch["audit_output.eeat_score.competitors"] = []
        # AAA-294 — also expose the page-intrinsic score as client_page_intrinsic so
        # fact_base.eeat.page_intrinsic (which reads client_page_intrinsic, not client)
        # renders the §5 dimensions in fallback mode (e.g. JS-heavy pages with no
        # comparable competitors). Without this, §5 shows "not measured" even though the
        # per-page scorer produced dimensions.
        if page_intrinsic is not None:
            patch["audit_output.eeat_score.client_page_intrinsic"] = page_intrinsic
        logger.info("comparative eeat fallback (%s): keeping page-intrinsic client",
                    result.get("comparative_status"))


async def persist_re_findings(audit_id: str, state: dict) -> dict:
    """Attach the assembled re_findings sub-tree to an archived audit doc.

    PRODUCTION CALLERS MUST INVOKE THIS FUNCTION AFTER THE RE WORKFLOW
    COMPLETES. This persistence is deterministic by design — NOT a
    FunctionTool, NOT an agent-driven step. The LLM never sees a "persist
    your work" instruction, so it cannot forget or skip the step (see
    AAA-108 Sub-step 0 architecture decision + AAA-51 / AAA-56
    deterministic-injection precedent). Wiring it in is plumbing the
    caller code owns.

    Caller pattern (deterministic — NO `if`, NO LLM branching):

        # after RE workflow finishes (events consumed, session state finalised)
        await persist_re_findings(
            audit_id=session_state["current_audit_id"],
            state=session_state,
        )

    Current callers:
      - reverse_engineering_agent/test_agent.py::run_one  (AAA-108 S2)

    Future production callers (AAA-96 HTTP endpoint, Cloud Run deploy
    code, scheduled-job invokers, etc.) MUST replicate the same call.

    Failure handling:
      - This persistence is NON-FATAL to the underlying client audit.
        The client Discovery audit is already persisted at workflow step 1
        (via _run_discovery_archived -> write_audit), so a failure here
        only loses the re_findings sub-tree, not the entire audit.
      - Recommended caller-side handling: catch exceptions, LOG WARNING,
        DO NOT re-raise. Pattern (already adopted by test_agent.py):
            try:
                await persist_re_findings(audit_id, state)
            except Exception as e:
                log.warning("persist_re_findings failed: %s", e)
      - Idempotent: uses Firestore dotted-path update(), safe to retry on
        transient failures. Re-runs replace audit_output.re_findings
        wholesale (last-writer-wins on this leaf only; all other fields
        on the doc are untouched).

    Implementation notes:
      - Backfill-pattern persistence (mirrors backfill_industry /
        backfill_translations / backfill_embedding in
        memory/firestore_archive.py): dotted-path update() touches ONLY
        audit_output.re_findings + updated_at.
      - Architectural deviation from the AAA-108 Sub-step 2 spec wording:
        the spec's "assemble just before _run_discovery_archived writes
        the client audit" placement is INFEASIBLE — the client write
        happens at workflow step 1, while SERP / competitors / comparison
        fire at steps 3–6 (see reverse_engineering_agent/agent.py system
        prompt). At step-1 write time, none of the RE state exists. This
        function is the post-workflow update path that the established
        backfill pattern already provides.

    Returns the assembled re_findings dict (for caller inspection / tests).
    """
    import asyncio
    from memory.firestore_archive import _db, _COLLECTION, _now_iso

    re_findings = _assemble_re_findings(state)
    # AAA-118 Sub-step 1: Stage 1 outputs are top-level audit_output fields
    # (NOT under re_findings) — additive dotted-path keys persisted in the
    # same update() so the post-workflow write covers both AAA-108 + AAA-118
    # state in one Firestore round-trip.
    patch = {
        "audit_output.serp_fit_analysis":
            state.get("serp_fit_analysis") or [],
        "audit_output.audit_serp_fit_cost_usd":
            float(state.get("audit_serp_fit_cost_usd") or 0.0),
        "audit_output.audit_serp_fit_call_count":
            int(state.get("audit_serp_fit_call_count") or 0),
        "audit_output.audit_serp_fit_cache_hits":
            int(state.get("audit_serp_fit_cache_hits") or 0),
        # AAA-118 Sub-step 2.2 — audit-level target_type
        "audit_output.audit_target_type":
            state.get("audit_target_type"),
        # AAA-114 Sub-step 1 — Stage 2 deterministic competitor filter
        "audit_output.selected_competitors_v2":
            state.get("selected_competitors_v2") or [],
        "audit_output.audit_no_comparable_competitors_found":
            bool(state.get("audit_no_comparable_competitors_found", False)),
        "audit_output.re_findings": re_findings,
        "updated_at": _now_iso(),
    }
    # AAA-172 S1 — COMPARATIVE query-anchored E-E-A-T. Supersedes the AAA-170
    # per-competitor INDEPENDENT read-back for ordering (the rollout-gate showed
    # per-page-independent ordering is unstable; the single comparative call is
    # slot-swap stable across shuffled order). ONE gemini call scores the client
    # + audited competitors against each other on the client's category_keyword
    # (the SERP query the competitor set was built on). full_content gate =
    # word-count >= 150 (NOT grounding_confidence, which falsely reads
    # full_content on blocked/empty pages). <2 genuine competitors -> fallback:
    # keep the step-1 page-intrinsic client score. Pure reads + 1 call; merged
    # via dotted-path update so unrelated eeat_score siblings are preserved.
    try:
        await _apply_comparative_eeat(audit_id, state, patch)
    except Exception as e:  # noqa: BLE001 — never break re_findings persist
        patch["audit_output.eeat_score.comparative_status"] = \
            "error: %s: %s" % (type(e).__name__, e)
        logger.warning("comparative eeat scoring failed: %s: %s",
                       type(e).__name__, e)
    ref = _db().collection(_COLLECTION).document(audit_id)
    await asyncio.to_thread(ref.update, patch)
    return re_findings


# --------------------------------------------------------------------------
# 5. AAA-64: active memory retrieval (top-3 similar prior audits)
# --------------------------------------------------------------------------
async def memory_read_similar_cases_tool(
    tool_context: ToolContext,
    query_summary: str = "",
    current_audit_id: str = "",
    top_k: int = 3,
) -> dict:
    """Retrieve the top-3 most similar PRIOR audits from memory. Call this
    FIRST, immediately after the client Discovery audit has been archived
    and before the SERP step. Self-exclusion is automatic.

    query_summary / current_audit_id are read from session state
    (client_summary_en / current_audit_id) when not supplied — pass empty
    strings and the tool self-fills (the full EN summary is large and lives
    in state, not in the prompt).
    """
    # AAA-294 S1-A GATE — cross-audit similar-case retrieval embeds the query
    # (paid) and reaches into OTHER audits' data; it is part of the competitor/
    # cross-case research bundle. When not entitled we retrieve nothing, issue no
    # provider call, and record an explicit not_entitled status.
    if not _entitlement.competitor_research_entitled(
            _entitlement.from_state(tool_context.state)):
        tool_context.state["memory_retrieval"] = {"results": [], "status": "not_entitled"}
        tool_context.state["audit_memory_retrieval_calls"] = 0
        tool_context.state["audit_memory_retrieval_cost_usd"] = 0.0
        return {"result_count": 0, "self_excluded_id": None,
                "status": "not_entitled", "_error": None, "results": []}

    qs = query_summary or tool_context.state.get("client_summary_en") or ""
    cid = (
        current_audit_id
        or tool_context.state.get("current_audit_id")
        or None
    )
    # AAA-75: client URL drives cross-role (registrable-domain) self-exclusion.
    cur_url = tool_context.state.get("client_url") or None
    from memory.retrieval import memory_read_similar_cases

    res = await memory_read_similar_cases(
        query_summary=qs, current_audit_id=cid, top_k=top_k, current_url=cur_url
    )
    tool_context.state["memory_retrieval"] = res
    tool_context.state["audit_memory_retrieval_calls"] = res.get(
        "memory_call_count", 1
    )
    tool_context.state["audit_memory_retrieval_cost_usd"] = res.get(
        "memory_cost_usd", 0.0
    )
    # Compact the (large) summaries for the LLM; full text stays in state.
    return {
        "result_count": len(res.get("results") or []),
        "self_excluded_id": cid,
        "_error": res.get("_error"),
        "results": [
            {
                "audit_id": r["audit_id"],
                "audit_url": r["audit_url"],
                "brand": r["brand"],
                "industry_llm": r["industry_llm"],
                "page_type": r["page_type"],
                "audit_date": r["audit_date"],
                "similarity_score": r["similarity_score"],
                "audit_summary": r["audit_summary"],
            }
            for r in (res.get("results") or [])
        ],
    }


discovery_ft = FunctionTool(discovery_agent_tool)
audit_all_competitors_ft = FunctionTool(audit_all_competitors_tool)  # AAA-88 S2
memory_ft = FunctionTool(memory_read_similar_cases_tool)
serp_ft = FunctionTool(dataforseo_serp_query_tool)
select_ft = FunctionTool(select_competitor_urls_tool)
compare_ft = FunctionTool(compare_audits_tool)

ALL_TOOLS = [discovery_ft, audit_all_competitors_ft, memory_ft,
             serp_ft, select_ft, compare_ft]
