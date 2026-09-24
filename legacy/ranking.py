"""Shared, pure-Python helpers for client ranking + competitor selection.

Used by both the live tool (tools.py) and the post-process script so the
logic is identical in both paths.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

_TWO_LABEL_SUFFIXES = {"co.uk", "org.uk", "com.au", "net.au", "co.nz"}


def _netloc(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def registrable_domain(url: str) -> str:
    """e.g. https://www.foo.example.co.uk/x -> example.co.uk"""
    host = _netloc(url)
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return host


# AAA-223 B — first-party mega-platform / brand-owned registrable domains that are
# NEVER a competing business for our ICPs. BLANKET exclusion (regardless of
# serp_type) — the leak was a Google property MISCLASSIFIED as business_service, so a
# serp_type-scoped guard wouldn't catch it. Deliberately EXCLUDES publishing
# platforms (medium/substack/blogspot) + marketplaces (etsy/ebay) where a real
# competitor can live — those stay handled by the existing serp_type exclusion.
PLATFORM_OWNED_DOMAINS = frozenset({
    "google.com", "withgoogle.com", "goo.gle", "youtube.com", "microsoft.com",
    "apple.com", "amazon.com", "facebook.com", "meta.com", "instagram.com",
    "linkedin.com", "wikipedia.org", "wikimedia.org", "reddit.com", "x.com",
    "twitter.com", "pinterest.com", "tiktok.com",
})


def is_platform_owned(url: str) -> bool:
    """True iff the URL's registrable domain is a first-party mega-platform that is
    never a competing business (AAA-223 B). Reuses registrable_domain (DRY)."""
    return registrable_domain(url) in PLATFORM_OWNED_DOMAINS


def second_level_label(url: str) -> str:
    """The SLD label: nextjs.org -> 'nextjs', auto.jofogas.hu -> 'jofogas'."""
    reg = registrable_domain(url)
    return reg.split(".")[0] if reg else ""


def _norm(s: str) -> str:
    """Lowercase, strip everything but a-z0-9: 'Next.js' -> 'nextjs'."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def compute_client_ranking_status(
    client_url: str, organic_urls: list[str]
) -> dict:
    client_reg = registrable_domain(client_url)
    # AAA-268 S1.1 — MISSING-DATA GUARD: an empty/absent organic SERP is NOT proof
    # the page does not rank. Without captured results we cannot verify position, so
    # emit a NEUTRAL "not_captured" status (found_in_top_10=None, NOT False) instead
    # of a false "outside top 10". A genuine outside-top-10 (SERP present, client
    # absent) still flows through below with found_in_top_10=False.
    if not organic_urls:
        return {
            "found_in_top_10": None,
            "position": None,
            "ranking_severity": "not_captured",
            "ranking_data_status": "not_captured",
            "interpretation": (
                "Ranking data for this keyword was not captured in this run, so the "
                "page's organic position cannot be verified from this report."),
            "recommended_action": None,
        }
    position = None
    for i, u in enumerate(organic_urls, start=1):
        if registrable_domain(u) == client_reg and client_reg:
            position = i
            break

    if position is not None and position <= 3:
        severity = "top_3"
        interpretation = (
            f"Strong position (#{position}) for primary keyword. Focus on "
            f"AI Overview citation, schema richness, and content depth to "
            f"maintain and expand visibility."
        )
        action = (
            "Protect and extend: pursue AI Overview citation, enrich schema, "
            "deepen topical content."
        )
    elif position is not None:
        severity = "top_10"
        interpretation = (
            f"Decent ranking at position {position}. Refinement "
            f"opportunities exist - see competitor comparison below for "
            f"specific gaps."
        )
        action = (
            "Close the specific competitor gaps identified below to move "
            "into the top 3."
        )
    else:
        severity = "outside_top_10"
        interpretation = (
            "Not in top 10 SERP results for primary keyword. This signals "
            "limited topic authority. The competitive comparison below shows "
            "what top-ranking competitors do - but before mimicking them, "
            "fundamental work is needed: content depth on the topic, internal "
            "link structure forming a topic cluster, and possibly external "
            "authority signals. Pure technical SEO won't fix a topic "
            "authority gap."
        )
        action = (
            "Prioritise topical authority (content cluster + internal links "
            "+ external signals) before competitor-mimicking tactics."
        )

    return {
        "found_in_top_10": position is not None,
        "position": position,
        "ranking_severity": severity,
        "ranking_data_status": "available",
        "interpretation": interpretation,
        "recommended_action": action,
    }


def select_competitors(
    client_url: str,
    serp_result: dict,
    main_entities: list[str] | None,
) -> dict:
    """AI-Overview citations first, then organic; dedup; exclude client
    domain AND any domain whose SLD matches a client main_entity.
    """
    client_reg = registrable_domain(client_url)
    entity_norms = {_norm(e) for e in (main_entities or []) if _norm(e)}

    ordered = list(serp_result.get("ai_overview_citations", []) or []) + list(
        serp_result.get("organic_urls", []) or []
    )
    seen: set[str] = set()
    competitors: list[str] = []
    exclusions: list[dict] = []

    for u in ordered:
        key = u.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        reg = registrable_domain(u)
        if not reg:
            continue
        if reg == client_reg:
            exclusions.append({"url": u, "reason": "client domain"})
            continue
        sld = _norm(second_level_label(u))
        hit = next((e for e in entity_norms if e and e == sld), None)
        if hit:
            exclusions.append(
                {"url": u, "reason": f"domain SLD '{sld}' matches client "
                 f"main_entity '{hit}'"}
            )
            continue
        competitors.append(u)
        if len(competitors) == 3:
            break

    return {
        "competitor_urls": competitors,
        "count": len(competitors),
        "fewer_than_3": len(competitors) < 3,
        "exclusions": exclusions,
    }


_EMOJI = {
    "top_3": "✅ Strong position",
    "top_10": "⚠️ Decent, refine",
    "outside_top_10": "🚨 Topic authority gap",
    "not_captured": "ℹ️ Ranking data not captured",
}


def _rank_line(rk: dict) -> str:
    pos = rk.get("position")
    sev = rk.get("ranking_severity", "outside_top_10")
    # AAA-268 S1.1 — MISSING-DATA GUARD: an empty SERP is not-captured, not a
    # measured "NOT in top 10".
    if sev == "not_captured":
        return f"{_EMOJI['not_captured']} - ranking position could not be verified this run"
    where = f"position {pos}" if pos else "NOT in top 10"
    return f"{_EMOJI.get(sev, sev)} - client is {where}"


def render_dual_landscape(
    primary_kw: str,
    category_kw: str,
    client_url: str,
    serp_branded: dict,
    serp_category: dict,
    rank_branded: dict,
    rank_category: dict,
    serps_identical: bool,
    branded_competitors: list[str],
    category_competitors: list[str],
) -> str:
    """Deterministic '### SERP Landscape' + '### Gap Analysis' markdown."""
    L = ['### SERP Landscape', '']
    L.append(f'**Branded query: "{primary_kw}"**')
    L.append(f"- Client ranking: {_rank_line(rank_branded)}")
    L.append(f"- Top 3: {branded_competitors or '(none)'}")
    L.append(
        f"- AI Overview: "
        f"{'present' if serp_branded.get('ai_overview_present') else 'not present'}"
    )
    L.append("")
    if serps_identical:
        L.append(
            f'**Category query: "{category_kw}"** - _identical to branded '
            f'(primary == category); second query skipped, $0.003 saved._'
        )
    else:
        L.append(f'**Category query: "{category_kw}"**')
        L.append(f"- Client ranking: {_rank_line(rank_category)}")
        L.append(f"- Top 3 competitors: {category_competitors or '(none)'}")
        L.append(
            f"- AI Overview: "
            f"{'present' if serp_category.get('ai_overview_present') else 'not present'}"
        )

    # --- Gap Analysis ---
    L += ["", "### Gap Analysis", ""]
    pb, pc = rank_branded.get("position"), rank_category.get("position")
    if serps_identical:
        L.append(
            f'Primary and category keywords are the same ("{primary_kw}"), so '
            f"there is no branded-vs-category gap to exploit. The single SERP "
            f"ranking ({_rank_line(rank_branded)}) is the whole story - "
            f"compete directly on this descriptive term."
        )
    elif pb and not pc:
        L.append(
            f'You rank for your branded query "{primary_kw}" '
            f"(position {pb}) but are **NOT in the top 10** for the category "
            f'query "{category_kw}". This is the growth opportunity - real '
            f"demand lives in the category, where competitors like "
            f"{', '.join(category_competitors[:3]) or 'others'} rank and you "
            f"do not. Compete in the category, not just on your brand name."
        )
    elif pb and pc:
        L.append(
            f'You rank for both the branded query "{primary_kw}" (pos {pb}) '
            f'and the category "{category_kw}" (pos {pc}). Solid - focus on '
            f"closing the specific competitor gaps below to climb the "
            f"category SERP."
        )
    elif not pb and not pc:
        L.append(
            f"You are not in the top 10 for EITHER the branded "
            f'"{primary_kw}" or the category "{category_kw}" query. This '
            f"signals a fundamental visibility/authority gap - prioritise "
            f"topical content depth and internal linking before competitor "
            f"tactics."
        )
    else:
        L.append(
            f'Unusual pattern: category rank (pos {pc}) without branded rank. '
            f"Verify brand-term cannibalisation or indexing issues."
        )
    return "\n".join(L)


def render_serp_landscape(
    primary_keyword: str,
    client_url: str,
    serp_result: dict,
    ranking_status: dict,
) -> str:
    organic = serp_result.get("organic_urls", []) or []
    client_reg = registrable_domain(client_url)
    cited = serp_result.get("ai_overview_citations", []) or []
    cited_regs = {registrable_domain(c) for c in cited}
    ai_present = serp_result.get("ai_overview_present", False)

    lines = [f'### SERP Landscape (top 10 for "{primary_keyword}")', ""]
    if not organic:
        lines.append("_No organic results returned._")
    for i, u in enumerate(organic, start=1):
        reg = registrable_domain(u)
        mark = ""
        if reg == client_reg:
            mark = " ★ (client)"
        if reg in cited_regs:
            mark += " ★★ (AI-Overview cited)"
        lines.append(f"{i}. {u}{mark}")

    emoji = {
        "top_3": "✅ Strong position",
        "top_10": "⚠️ Decent, refine",
        "outside_top_10": "🚨 Topic authority gap",
    }[ranking_status["ranking_severity"]]

    lines += [
        "",
        f"**Client ranking**: {emoji} - {ranking_status['interpretation']}",
        f"**Recommended action**: {ranking_status['recommended_action']}",
        f"**AI Overview**: {'present' if ai_present else 'not present'}",
    ]
    if ai_present:
        doms = sorted({registrable_domain(c) for c in cited if c})
        lines.append(
            f"**AI Overview cited domains**: {', '.join(doms) or '(none)'}"
        )
    return "\n".join(lines)
