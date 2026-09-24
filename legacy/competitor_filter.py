"""AAA-114 Sub-step 1 — Stage 2 competitor filtering + ranking.

Production module. Consumes AAA-118 Stage 1 output (per-keyword
serp_top10_classifications with serp_type + topic_relevance_score) +
the audit-level audit_target_type, applies the locked formula C v1
filter + soft scoring, and emits a deterministic top-3 competitor list.

Sub-step 0.5 design lockdown (2026-05-26) honored:
  - Hard filter B: topic_relevance_score >= 0.7
  - Hard filter C: target-type-dependent comparable serp_type list
    (10 always-excluded host-platforms + 2 conditional + 10 business_*
    always-included; the spec's "11 business_*" was off-by-one — the
    AAA-118 taxonomy has exactly 10 business_* leaves)
  - Soft filter weights: C-egyenlő (equal weights, binary 0/1 per dim)
  - Cap: 3 MAX (graceful <3 + zero-candidate handling)

Sub-step 1 implementation decisions (methodological-honesty section in
the Sub-step 1 report explains these in detail):
  - Hard filter A (business_model + audience_relationship set-match,
    formula C v1 lockup) DEFERRED: Stage 1 does NOT classify per-candidate
    business_model/audience_relationship. Adding it here would cost
    ~$0.05/candidate × 3 = ~$0.15/audit and defeat AAA-118 S0.5 Q3's
    Opció-X cost-saving. Revisit in Sub-step 2 if empirical canary
    indicates necessity.
  - Soft filter dimension 1 (topic_domain_match): proxy via candidate
    topic_relevance_score >= 0.8 (threshold above the >=0.7 hard floor
    indicates STRONG topic alignment beyond mere admission).
  - Soft filter dimension 2 (page_type_match): coarser serp_type direct
    match (candidate.serp_type == audit_target_type). The parent_intent_group
    layer requires per-candidate page_type classification which Stage 1
    doesn't emit.
  - Soft filter dimensions 3+4 (topic_cluster_match, category_keyword_match):
    DROPPED in Sub-step 1 — not Stage-1-emitted. Surface as breakdown
    sentinels ("not_implemented_substep_1") so Sub-step 2 reviewers see
    explicit holes.
  - Soft filter dimension 5 (locality_match): only applies when
    target.locality == "local". Implemented as a coarse TLD heuristic
    (host TLD match) when target_locality_tld is provided; otherwise
    skipped with audit-trail sentinel.

No LLM calls. Pure deterministic dict-lookup + arithmetic. $0 incremental.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from reverse_engineering_agent.ranking import is_platform_owned, registrable_domain

# ---------------------------------------------------------------------------
# AAA-118 22-leaf SERP type taxonomy — partitioned for Hard filter C
# ---------------------------------------------------------------------------
# 10 business-owned content types — always comparable as competitors
_BUSINESS_TYPES = frozenset({
    "business_homepage", "business_category", "business_product",
    "business_service", "business_pricing", "business_documentation",
    "business_case_study_or_resource",
    "business_contact_or_form", "business_legal",
})

# 10 host-platform / aggregator types — always EXCLUDED as competitors
# (these are platforms hosting third-party content, NOT competing
# businesses; or content types where the URL isn't a competitor at all)
_EXCLUDED_TYPES = frozenset({
    "marketplace_listing", "marketplace_category", "directory_listing",
    "social_profile_or_page", "social_post", "forum_or_qa_thread",
    "video_or_audio_listing", "wiki_or_reference",
    "aggregator_or_personal_blog", "other_or_unknown",
})

# conditional types — comparable only when target_type is in the allowed-target
# set for that conditional type
_CONDITIONAL_TYPES: dict[str, frozenset[str]] = {
    # News/publisher articles are comparable competitors only when the
    # target is itself a publisher-content shape
    "news_or_publisher_article": frozenset({
        "news_or_publisher_article", "business_blog_or_article",
    }),
    # AAA-195 — a business blog/article (e.g. a "TOP N agencies" listicle or a
    # "how to choose an agency" guide) is NOT a competing business for a normal
    # business target — it's informational content ABOUT the category. Comparable
    # ONLY when the target is itself an article (mirrors news_or_publisher_article).
    # Previously in _BUSINESS_TYPES (always-included); under SERP-order selection
    # that let listicles displace genuine competitor pages (MA regression).
    "business_blog_or_article": frozenset({
        "business_blog_or_article", "news_or_publisher_article",
    }),
    # gov_or_education is comparable only for explicit gov_or_education targets
    # (target_type=gov_or_education is rare today — AAA-118 S2.2 mapping
    # tends to flatten government homepages to business_homepage; flagged
    # in AAA-118 S3 deviation #1 as a Sub-step 2 candidate)
    "gov_or_education": frozenset({"gov_or_education"}),
}

_TOPIC_RELEVANCE_HARD_THRESHOLD = 0.7
_TOPIC_RELEVANCE_SOFT_PROXY_THRESHOLD = 0.8
_CAP = 3  # MAX selected competitors (graceful <3 OK)


# ---------------------------------------------------------------------------
# Hard filter C helpers
# ---------------------------------------------------------------------------
def _is_comparable_serp_type(candidate_type: str | None,
                              target_type: str) -> bool:
    """Hard filter C: is the candidate's serp_type comparable to the target?

    Returns True iff:
      - candidate is in the 10 business_* always-included set, OR
      - candidate is one of the 2 conditional types AND target_type
        is in that conditional's allowed-target set
    Returns False for the 10 excluded host-platforms / aggregators, for
    None/missing types, and for any unrecognized type (defense-in-depth).
    """
    if not candidate_type:
        return False
    if candidate_type in _BUSINESS_TYPES:
        return True
    if candidate_type in _EXCLUDED_TYPES:
        return False
    if candidate_type in _CONDITIONAL_TYPES:
        return target_type in _CONDITIONAL_TYPES[candidate_type]
    return False  # unknown type — defense-in-depth


def _serp_type_fail_reason(candidate_type: str | None,
                          target_type: str) -> str:
    """Audit-trail reason a candidate failed Hard filter C."""
    if not candidate_type:
        return "serp_type_excluded"
    if candidate_type in _CONDITIONAL_TYPES \
       and target_type not in _CONDITIONAL_TYPES[candidate_type]:
        return "serp_type_conditional_excluded"
    return "serp_type_excluded"  # default for excluded set + unknown


# ---------------------------------------------------------------------------
# Soft filter (5-dim C-egyenlő, Sub-step 1 implements 2-3 cleanly)
# ---------------------------------------------------------------------------
def _candidate_tld(url: str) -> str:
    """Extract TLD from candidate URL host. '.hu' → 'hu'."""
    try:
        host = (urlparse(url).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if "." in host:
            return host.rsplit(".", 1)[-1]
    except Exception:  # noqa: BLE001
        pass
    return ""


def _norm_str(s: str | None) -> str:
    """Case-insensitive whitespace-stripped normalization for string match."""
    return (s or "").strip().lower()


def _compute_soft_score(
    *,
    candidate_serp_type: str,
    candidate_relev: float,
    candidate_url: str,
    candidate_topic_cluster: str | None,
    candidate_category_keyword: str | None,
    target_type: str,
    target_topic_cluster: str | None,
    target_category_keyword: str | None,
    target_locality: str | None,
    target_locality_tld: str | None,
) -> tuple[float, dict]:
    """Compute C-egyenlő soft score across the 5 spec dims.

    AAA-114 Sub-step 2 (Option X-extend): dims 3 + 4 (topic_cluster_match,
    category_keyword_match) activated via Stage 1's new per-candidate
    fields. Strict case-insensitive whitespace-trimmed equality. Fuzzy
    matching deferred to Sub-step 3 if empirically needed.

    Effective max score (binary 0/1 per dim):
      - non-local target:    4.0 (dims 1+2+3+4)
      - local target w/ TLD: 5.0 (all 5 dims)
      - local target w/o TLD: 4.0 (dim 5 sentinel)
    """
    breakdown: dict[str, object] = {}
    score = 0.0

    # Dim 1: topic_domain_match (proxy via topic_relevance >= 0.8 threshold)
    # Hard filter B required >=0.7; >=0.8 indicates STRONG topic alignment.
    td_match = (
        1.0 if (candidate_relev or 0.0) >= _TOPIC_RELEVANCE_SOFT_PROXY_THRESHOLD
        else 0.0
    )
    breakdown["topic_domain_match_proxy"] = td_match
    score += td_match

    # Dim 2: page_type_match (coarser serp_type-level match — Stage 1
    # doesn't emit page_type; serp_type is the closest surrogate)
    pt_match = 1.0 if candidate_serp_type == target_type else 0.0
    breakdown["serp_type_match"] = pt_match
    score += pt_match

    # Dim 3: topic_cluster_match (AAA-114 S2 — was "not_implemented" sentinel).
    # Strict case-insensitive whitespace-trimmed equality. None on either
    # side → 0.0 (no information = no match credit, per Sub-step 1.5 Q3 lock).
    cand_tc = _norm_str(candidate_topic_cluster)
    tgt_tc = _norm_str(target_topic_cluster)
    tc_match = 1.0 if (cand_tc and tgt_tc and cand_tc == tgt_tc) else 0.0
    breakdown["topic_cluster_match"] = tc_match
    score += tc_match

    # Dim 4: category_keyword_match (AAA-114 S2 — was "not_implemented"
    # sentinel). Same comparison shape as Dim 3.
    cand_ck = _norm_str(candidate_category_keyword)
    tgt_ck = _norm_str(target_category_keyword)
    ck_match = 1.0 if (cand_ck and tgt_ck and cand_ck == tgt_ck) else 0.0
    breakdown["category_keyword_match"] = ck_match
    score += ck_match

    # Dim 5: locality_match (conditional)
    if target_locality == "local" and target_locality_tld:
        cand_tld = _candidate_tld(candidate_url)
        loc_match = 1.0 if (cand_tld and cand_tld == target_locality_tld) else 0.0
        breakdown["locality_match"] = loc_match
        score += loc_match
    elif target_locality == "local":
        breakdown["locality_match"] = "not_applied_missing_target_tld"
    else:
        breakdown["locality_match"] = "not_applicable"

    return score, breakdown


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def filter_and_rank_competitors(
    *,
    serp_fit_analysis: list[dict],
    audit_target_type: str | None,
    target_topic_cluster: str | None = None,
    target_category_keyword: str | None = None,
    target_locality: str | None = None,
    target_locality_tld: str | None = None,
    client_url: str | None = None,
) -> dict:
    """AAA-114 Sub-step 1 Stage 2 deterministic competitor filter + ranker.

    Inputs:
      - serp_fit_analysis: AAA-118 Stage 1 output, list of per-keyword
        entries each with serp_top10_classifications + serp_type +
        topic_relevance_score per candidate URL
      - audit_target_type: target site's mapped SERP type (AAA-118 S2.2)
      - target_locality: target site's AAA-113 locality (local/regional/...);
        only consulted when == "local"
      - target_locality_tld: target host's TLD (e.g., "hu" for agrobook.hu);
        consulted only when target_locality == "local"

    Returns:
      {
        "selected_competitors_v2": list[dict],  # flat across keywords,
                                                # all candidates with audit-trail
        "no_comparable_competitors_found": bool,
      }

    The audit-trail dict for each candidate has the schema specified in the
    AAA-114 Sub-step 1 instruction (url, position, keyword, keyword_role,
    serp_type, topic_relevance_score, hard_filter_pass,
    hard_filter_fail_reason, soft_score, soft_score_breakdown, selected,
    selection_rank).
    """
    if not audit_target_type:
        audit_target_type = "other_or_unknown"

    # AAA-114 Sub-step 3.5 — client-URL self-match exclusion.
    # Pre-compute target's registrable domain ONCE (per-call constant).
    # vercel.com canary (Sub-step 3) surfaced that a target site ranking
    # its own homepage in its own SERP top-10 would be selected as its
    # own competitor (rank 1, soft_score=4.0). AAA-108 legacy
    # select_competitors excludes via SLD comparison; this exclusion was
    # missing from Stage 2 until Sub-step 3.5.
    client_domain = registrable_domain(client_url) if client_url else None

    # Step 1: flatten + per-candidate hard-filter + soft-score
    flat: list[dict] = []
    for kw_entry in (serp_fit_analysis or []):
        if kw_entry.get("_error"):
            continue
        keyword = kw_entry.get("keyword") or ""
        keyword_role = kw_entry.get("keyword_role") or "secondary"
        for c in (kw_entry.get("serp_top10_classifications") or []):
            url = c.get("url")
            if not url:
                continue
            serp_type = c.get("serp_type")
            relev = float(c.get("topic_relevance_score") or 0.0)

            entry: dict = {
                "url": url,
                "position": c.get("position"),
                "keyword": keyword,
                "keyword_role": keyword_role,
                "serp_type": serp_type,
                "topic_relevance_score": relev,
                "hard_filter_pass": False,
                "hard_filter_fail_reason": None,
                "soft_score": None,
                "soft_score_breakdown": None,
                "selected": False,
                "selection_rank": None,
            }

            # AAA-114 Sub-step 3.5 client-URL exclusion (gates BEFORE
            # existing hard filters B + C). Matches by registrable
            # domain so https://www.vercel.com/blog correctly hits the
            # same domain as https://vercel.com.
            if client_domain and registrable_domain(url) == client_domain:
                entry["hard_filter_fail_reason"] = "client_url_self_match"
                flat.append(entry)
                continue

            # AAA-223 B — Hard filter A0: platform/brand-owned host exclusion.
            # BLANKET (regardless of serp_type) — first-party mega-platforms
            # (google.*, youtube.*, …) are never a competing business; this catches
            # the leak where a platform property was misclassified as a comparable
            # business_* serp_type (e.g. business.google.com/become-a-partner tagged
            # business_service). Publishing platforms / marketplaces are NOT on the
            # list (a real competitor can live there → serp_type handles those).
            if is_platform_owned(url):
                entry["hard_filter_fail_reason"] = "platform_owned_host"
                flat.append(entry)
                continue

            # Hard filter B: topic_relevance >= 0.7
            if relev < _TOPIC_RELEVANCE_HARD_THRESHOLD:
                entry["hard_filter_fail_reason"] = "topic_relevance_below_threshold"
                flat.append(entry)
                continue

            # Hard filter C: comparable serp_type
            if not _is_comparable_serp_type(serp_type, audit_target_type):
                entry["hard_filter_fail_reason"] = _serp_type_fail_reason(
                    serp_type, audit_target_type
                )
                flat.append(entry)
                continue

            # Passed both hard filters → compute soft score
            entry["hard_filter_pass"] = True
            score, breakdown = _compute_soft_score(
                candidate_serp_type=serp_type,
                candidate_relev=relev,
                candidate_url=url,
                candidate_topic_cluster=c.get("candidate_topic_cluster"),
                candidate_category_keyword=c.get("candidate_category_keyword"),
                target_type=audit_target_type,
                target_topic_cluster=target_topic_cluster,
                target_category_keyword=target_category_keyword,
                target_locality=target_locality,
                target_locality_tld=target_locality_tld,
            )
            entry["soft_score"] = round(score, 4)
            entry["soft_score_breakdown"] = breakdown
            flat.append(entry)

    # Step 2: dedupe by URL across keywords.
    # AAA-195 FIX 1 — when a URL appears in BOTH keyword SERPs, PREFER the
    # primary-keyword entry (the entered keyword = serp_branded, the SERP §2
    # displays), NOT the highest soft_score. This anchors the §6 set on the same
    # SERP as the §2 markers (AAA-194), keeping them consistent. Tie-break:
    # hard_filter_pass, then primary, then earliest position. soft_score is still
    # computed/persisted (transparency) but NO LONGER influences dedup/order.
    def _is_primary(e):
        return (e.get("keyword_role") or "") == "primary"

    by_url: dict[str, dict] = {}
    order: list[str] = []
    for entry in flat:
        url = entry["url"]
        if url not in by_url:
            by_url[url] = entry
            order.append(url)
            continue
        existing = by_url[url]
        # Prefer hard-filter pass, then primary-keyword, then earliest position.
        def _rank_key(e):
            return (0 if e["hard_filter_pass"] else 1,
                    0 if _is_primary(e) else 1,
                    e.get("position") or 9999)
        if _rank_key(entry) < _rank_key(existing):
            by_url[url] = entry
    deduped = [by_url[u] for u in order if u in by_url]

    # Step 3: select the FIRST 3 gate-passers in SERP ORDER (AAA-195 FIX 1 —
    # decision b). Primary-SERP passers first (by position), then secondary-only
    # passers (by their secondary position) to backfill if the primary SERP
    # yields <3. soft_score is NOT a ranker here.
    passers = [e for e in deduped if e["hard_filter_pass"]]
    passers.sort(key=lambda e: (
        0 if _is_primary(e) else 1,
        e.get("position") or 9999,
    ))
    for rank, entry in enumerate(passers[:_CAP], start=1):
        entry["selected"] = True
        entry["selection_rank"] = rank

    # AAA-167 S1: count comparable business-type peers PRESENT in the SERP
    # (regardless of relevance/pass). Lets the caller distinguish
    # (A) no business peer at all -> keyword likely mistargeted, from
    # (B) business peers present but none qualified -> NOT a keyword mismatch.
    business_peer_count = sum(
        1 for e in deduped if e.get("serp_type") in _BUSINESS_TYPES)

    return {
        "selected_competitors_v2": deduped,
        "no_comparable_competitors_found": (len(passers) == 0),
        "business_peer_count": business_peer_count,
    }
