"""Headless-Chromium rendering core (server-side).

This is the rendering logic formerly in ``playwright_poc/render.py`` (the
in-process POC), moved here verbatim in behaviour and renamed
``render_in_process``. It runs ONLY inside the Playwright microservice image
(the one place Chromium is bundled) and, as a local-dev convenience, as the
optional in-process fallback in ``playwright_poc.render.render_url`` when
``PLAYWRIGHT_URL`` is unset and a local browser happens to be installed.

The renderer is intentionally defensive: a flaky ``networkidle`` degrades to
usable DOM rather than failing the whole render, and ANY launch/navigation
failure is returned as ``{"url", "error", "error_type"}`` — it never raises.
"""

from __future__ import annotations

import re
import time

from selectolax.parser import HTMLParser

from playwright.async_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

# Realistic Chrome desktop UA — deliberately NOT a bot string, so sites serve
# the same markup a real user would get.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

VIEWPORT = {"width": 1920, "height": 1080}
TIMEOUT_MS = 30_000
VALID_WAIT = {"networkidle", "domcontentloaded", "load"}

# AAA-211 — screenshot capture recipe (locked in AAA-210 S0).
MOBILE_VIEWPORT = {"width": 375, "height": 667}
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 "
    "Safari/604.1"
)
_SHOT_VIEWPORTS = {
    "desktop": (VIEWPORT, USER_AGENT),
    "mobile": (MOBILE_VIEWPORT, MOBILE_USER_AGENT),
}
# UA/consent-wall signatures (AAA-210: aboutyou.hu "browser not supported").
_WALL_PHRASES = (
    "browser not supported", "unsupported browser", "not supported",
    "enable javascript", "please enable javascript",
    "nem támogatott böngésző", "böngésződ nem támogatott",
)
# Best-effort consent/cookie accept — common button text (en + hu).
_CONSENT_TEXTS = (
    "Accept all", "Accept All", "Accept", "I agree", "Agree", "Got it",
    "Allow all", "Elfogad", "Elfogadom", "Összes elfogadása", "Mindet elfogadom",
)
# Disable animations + force instant scroll for deterministic full-page shots.
_ANIM_OFF_CSS = (
    "*{animation:none!important;transition:none!important;"
    "scroll-behavior:auto!important}"
)
_WEBP_QUALITY = 80
# AAA-211 tall-page tiling: slice the full page into ~3072px-tall tiles at
# NATIVE width so each tile's long side ≈ Gemini's 3072 downscale cap (survives
# at full detail) and stays well under WebP's 16383 limit. ~128px overlap so a
# text line / UI element is never cut at a seam.
_TILE_HEIGHT = 3072
_TILE_OVERLAP = 128

# ─────────── HALT-26Y — dynamic-render stabilization (lazy loads + counters) ───────────
# A screenshot is an observation of a RENDER STATE. Before the final capture we
# (a) inventory lazy-loadable images/backgrounds (src/srcset/data-src/data-lazy-src/
#     data-bg + CSS background images) and likely animated counters,
# (b) scroll through the page deterministically so viewport-triggered lazy loaders
#     and counter animations activate,
# (c) wait for RENDER-STATE STABILITY (two identical state samples) rather than
#     only networkidle, under a bounded budget, and
# (d) return a structured `dynamic_render` record (initial vs final states, declared
#     sources, painted flags, counter targets) so downstream validation can tell a
#     genuine defect from transient capture timing. Timeouts are recorded honestly
#     (settled=false / budget_exhausted=true) — never silently dropped.
_DYN_STABLE_MS = 700        # sampling interval for the settling loop
_DYN_MAX_WAIT_MS = 6300     # bounded stabilization budget (on top of page load)
_DYN_GRACE_MS = 500         # activation grace after real input before sampling
_DYN_EVENT_WAIT_MS = 2000   # bounded wait for a known delayed-script event
_DYN_PROBE_MS = 1500        # bounded per-source load probe
# Generic delayed-script completion events (NOT site-specific): listeners are
# registered up-front so a completion fired mid-capture is still observed.
_DYN_COMPLETION_EVENTS = ("rocket-allScriptsLoaded",)
# first URL inside a CSS background-image value (url("...") / url(...)).
_CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+)")


def _url_matches(abs_candidate, current_src) -> bool:
    """26AA — canonical-URL comparison for the selected-candidate check: the
    candidate was resolved against document.baseURI at capture time, currentSrc
    is the browser's absolute URL. Exact equality only, tolerating solely the
    record's bounded 160-char truncation of currentSrc — never substring
    matching (x.png must not match x.png.webp)."""
    if not abs_candidate or not current_src:
        return False
    if abs_candidate == current_src:
        return True
    return len(current_src) == 160 and abs_candidate.startswith(current_src)

# HALT-26Z — STABLE ELEMENT IDENTITY: the initial inventory marks every candidate
# with an ephemeral data-aaa-dynamic-id; later samples and the final inventory
# address those exact elements, so identity survives attribute/class/DOM-order
# changes. Markers are removed in the final pass. Records stay bounded (no full
# customer HTML; contexts ≤80 chars, paths ≤120, sources ≤160).
_DYN_MARK_JS = r"""
(events) => {
  const w = window;
  if (!w.__aaaDynEvents) {
    w.__aaaDynEvents = {};
    for (const ev of events) {
      const rec = () => { w.__aaaDynEvents[ev] = true; };
      document.addEventListener(ev, rec, {once: true});
      w.addEventListener(ev, rec, {once: true});
    }
  }
  const domPath = (el) => {
    const parts = [];
    let n = el;
    for (let d = 0; n && n.tagName && d < 4; d++) {
      let seg = n.tagName.toLowerCase();
      if (n.id) seg += '#' + n.id;
      else if (n.classList && n.classList.length) seg += '.' + n.classList[0];
      parts.unshift(seg);
      n = n.parentElement;
    }
    return parts.join('>').slice(0, 120);
  };
  const areaCtx = (el) => (((el.closest('section,article,figure,li') || el)
    .textContent) || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  const nearCtx = (el) => (((el.parentElement && el.parentElement.textContent)
    || '')).trim().replace(/\s+/g, ' ').slice(0, 80);
  // 26Z review: ELEMENT-LOCAL counter context — own text + the adjacent text
  // node (the usual "<span>0+</span> Coaches Certified" label position), so
  // sibling counters in one container do not share each other's labels.
  const ctrCtx = (el) => {
    let s = (el.textContent || '') + ' ';
    if (el.nextSibling && el.nextSibling.textContent)
      s += el.nextSibling.textContent + ' ';
    else if (el.previousSibling && el.previousSibling.textContent)
      s += el.previousSibling.textContent + ' ';
    s = s.replace(/\s+/g, ' ').trim();
    if (s.split(' ').length < 2) s = nearCtx(el);
    return s.slice(0, 80);
  };
  const lazySel = 'img[loading=lazy], img[data-src], img[data-lazy-src], ' +
                  'img[data-srcset], img[data-lazy-srcset], ' +
                  '[data-bg], [data-lazy-bg], [data-bg-multi]';
  // 26Z review: REGULAR <img> elements are candidates too — a broken non-lazy
  // portrait/logo carries deterministic load-error state the validator needs.
  // Lazy candidates keep priority; plain images fill the remaining budget.
  const lazyOnly = Array.from(document.querySelectorAll(lazySel));
  const lazySet = new Set(lazyOnly);
  const plainImgs = Array.from(document.querySelectorAll('img'))
    .filter((el) => !lazySet.has(el));
  // 26Z review: ordinary CSS background-image elements (inline or stylesheet)
  // are candidates too — a broken url(...) background is deterministic failure
  // evidence once probed. Bounded scan; gradients-only backgrounds skipped.
  const cssBg = [];
  const scan = document.querySelectorAll(
    'div,section,header,footer,figure,a,span,li');
  for (let i = 0; i < scan.length && i < 600 && cssBg.length < 40; i++) {
    const el = scan[i];
    if (lazySet.has(el)) continue;
    const bg = getComputedStyle(el).backgroundImage;
    // layered values (gradient + url) count too — anything carrying a url(...)
    // layer is an image-bearing background; gradient-only stays excluded.
    if (bg && bg.includes('url(')) cssBg.push(el);
  }
  // 26Z review: per-class budgets — plain images must not starve CSS-background
  // slots (a broken CSS hero/testimonial background needs its candidate record).
  const lazyEls = lazyOnly.slice(0, 24).concat(plainImgs.slice(0, 10))
    .concat(cssBg.slice(0, 6));
  // data-bg / data-bg-multi values may be url(...)-wrapped (and -multi may list
  // several) — keep the FIRST resolvable URL so the declared source is never
  // silently empty for a multi-background element.
  const firstUrl = (v) => {
    if (!v) return '';
    const m = v.match(/url\(\s*['"]?([^'")]+)['"]?\s*\)/);
    return (m ? m[1] : v).trim();
  };
  // 26Z review: srcset-based sources count — take the first candidate URL so a
  // responsive/lazy image is never recorded with a silently empty declared.
  const firstSrcset = (v) => {
    if (!v) return '';
    return (v.split(',')[0].trim().split(/\s+/)[0] || '').trim();
  };
  // 26Z review: ALL srcset candidates are tracked — the browser may select any
  // of them, and an unused candidate's state must never misjudge the element.
  const allSrcset = (v) => {
    if (!v) return [];
    return v.split(',').map((s) => s.trim().split(/\s+/)[0])
      .filter(Boolean).slice(0, 5);
  };
  // 26AA: <picture> responsive candidates — sibling <source> srcsets carry the
  // INTENDED image before the child <img> placeholder src is considered.
  const pictureSets = (el) => {
    const lazyC = [], plainC = [];
    const pic = el.closest ? el.closest('picture') : null;
    if (!pic) return {lazyC: lazyC, plainC: plainC};
    for (const s of pic.querySelectorAll('source')) {
      for (const u of allSrcset(s.getAttribute('data-srcset') ||
                                s.getAttribute('data-lazy-srcset')))
        lazyC.push(u);
      for (const u of allSrcset(s.getAttribute('srcset'))) plainC.push(u);
    }
    return {lazyC: lazyC, plainC: plainC};
  };
  // 26AA: canonical URL resolution against document.baseURI — relative,
  // root-relative and protocol-relative candidates compare as absolute URLs;
  // invalid values fall back to the raw string (never throws).
  const absUrl = (u) => {
    if (!u) return '';
    try { return new URL(u, document.baseURI).href; }
    catch (e) { return String(u); }
  };
  const dedup = (arr) => {
    const out = [];
    for (const u of arr) if (u && out.indexOf(u) < 0) out.push(u);
    return out;
  };
  const lazy = lazyEls.map((el, i) => {
    const id = 'L' + i;
    el.setAttribute('data-aaa-dynamic-id', id);
    const isImg = el.tagName === 'IMG';
    const bg0 = isImg ? '' : getComputedStyle(el).backgroundImage;
    // 26Z/26AA: LAZY sources precede currentSrc/src — a lazy responsive image
    // often ships a transparent/low-res placeholder src while the REAL URL
    // lives in data-srcset OR on a sibling <picture><source>; recording the
    // placeholder would let the probe "confirm" it and hide the actual missing
    // image. Ordinary CSS backgrounds fall back to their computed url(...).
    const pics = isImg ? pictureSets(el) : {lazyC: [], plainC: []};
    const imgLazy = isImg ? allSrcset(el.getAttribute('data-srcset') ||
                                      el.getAttribute('data-lazy-srcset')) : [];
    const imgPlain = isImg ? allSrcset(el.getAttribute('srcset')) : [];
    const declared = firstUrl(el.getAttribute('data-bg') ||
      el.getAttribute('data-lazy-bg') || el.getAttribute('data-bg-multi') ||
      el.getAttribute('data-src') || el.getAttribute('data-lazy-src') ||
      (isImg ? (pics.lazyC[0] || imgLazy[0] ||
                el.currentSrc || el.getAttribute('src') ||
                pics.plainC[0] || imgPlain[0] || '')
             : firstUrl(bg0)));
    const bg = isImg ? '' : getComputedStyle(el).backgroundImage;
    const painted = isImg ? !!(el.complete && el.naturalWidth > 0)
                          : !!(bg && bg !== 'none');
    // deterministic candidate order, deduped: picture-lazy, img-lazy,
    // picture-plain, img-plain (bounded).
    const declSet = isImg
      ? dedup(pics.lazyC.concat(imgLazy, pics.plainC, imgPlain)).slice(0, 8)
      : [];
    return {id: id, kind: isImg ? 'img' : 'bg',
            declared: (declared || '').slice(0, 160),
            declared_set: declSet.map((s) => s.slice(0, 160)),
            declared_set_abs: declSet.map((s) => absUrl(s).slice(0, 300)),
            computed_initial: (isImg ? (el.currentSrc || '') : bg).slice(0, 160),
            painted: painted, context: areaCtx(el), dom_path: domPath(el)};
  });
  const ctrSel = '[data-target],[data-count],[data-to],[data-purecounter-end],' +
    '.counter,.count-up,.countup,.odometer,.purecounter,.counting,.stat-number';
  const nodes = new Set(Array.from(document.querySelectorAll(ctrSel)).slice(0, 40));
  for (const el of document.querySelectorAll('span,div,p,strong,b,h2,h3,h4')) {
    if (nodes.size >= 40) break;
    if (el.childElementCount !== 0) continue;
    const t = (el.textContent || '').trim();
    // bare numeric values AND value-plus-label nodes ("0+ Coaches Certified"):
    // a +/% suffixed leading number may carry its label in the same node.
    if (t.length <= 60 &&
        (/^\d{1,9}\s*[+%]?$/.test(t) || /^\d{1,9}\s*[+%]\s+\S/.test(t)))
      nodes.add(el);
  }
  const counters = Array.from(nodes).slice(0, 40).map((el, i) => {
    const id = 'C' + i;
    el.setAttribute('data-aaa-dynamic-id', id);
    const target = el.getAttribute && (el.getAttribute('data-target') ||
      el.getAttribute('data-count') || el.getAttribute('data-to') ||
      el.getAttribute('data-purecounter-end')) || null;
    return {id: id, text: (el.textContent || '').trim().slice(0, 24),
            target: target, context: ctrCtx(el), dom_path: domPath(el)};
  });
  return {lazy: lazy, counters: counters};
}
"""
# per-identity current state: counters → text, lazy → painted flag.
_DYN_SAMPLE_JS = r"""
() => {
  const out = {};
  for (const el of document.querySelectorAll('[data-aaa-dynamic-id]')) {
    const id = el.getAttribute('data-aaa-dynamic-id');
    if (id[0] === 'C') out[id] = (el.textContent || '').trim().slice(0, 24);
    else {
      const isImg = el.tagName === 'IMG';
      const bg = isImg ? '' : getComputedStyle(el).backgroundImage;
      out[id] = (isImg ? !!(el.complete && el.naturalWidth > 0)
                       : !!(bg && bg !== 'none')) ? '1' : '0';
    }
  }
  return out;
}
"""
# final per-identity inventory + observed completion events; removes markers.
_DYN_FINAL_JS = r"""
() => {
  const out = {lazy: {}, counters: {}, events: []};
  for (const el of Array.from(document.querySelectorAll('[data-aaa-dynamic-id]'))) {
    const id = el.getAttribute('data-aaa-dynamic-id');
    if (id[0] === 'C') {
      const target = el.getAttribute && (el.getAttribute('data-target') ||
        el.getAttribute('data-count') || el.getAttribute('data-to') ||
        el.getAttribute('data-purecounter-end')) || null;
      out.counters[id] = {text: (el.textContent || '').trim().slice(0, 24),
                          target: target};
    } else {
      const isImg = el.tagName === 'IMG';
      const bg = isImg ? '' : getComputedStyle(el).backgroundImage;
      const painted = isImg ? !!(el.complete && el.naturalWidth > 0)
                            : !!(bg && bg !== 'none');
      let status = 'unknown';
      if (isImg) {
        if (el.complete && el.naturalWidth > 0) status = 'loaded';
        else if (el.complete && el.currentSrc) status = 'error';
      }
      // 26Z review: a non-none computed background does NOT prove the asset
      // downloaded (url(missing.jpg) still computes) — background elements get
      // their real load status from the Image() probe, never from style alone.
      out.lazy[id] = {painted: painted, load_status: status,
                      computed_final: (isImg ? (el.currentSrc || '') : bg)
                        .slice(0, 160)};
    }
    el.removeAttribute('data-aaa-dynamic-id');
  }
  const evs = window.__aaaDynEvents || {};
  out.events = Object.keys(evs).filter(k => evs[k]);
  return out;
}
"""
# bounded Image() probe for declared-but-unpainted sources → loaded|error|timeout.
_DYN_PROBE_JS = r"""
(args) => new Promise(res => {
  const out = {};
  let pending = (args.sources || []).length;
  if (!pending) return res(out);
  const done = () => { if (--pending <= 0) res(out); };
  for (const s of args.sources) {
    try {
      const im = new Image();
      const to = setTimeout(() => {
        if (!(s in out)) { out[s] = 'timeout'; done(); } }, args.timeout);
      im.onload = () => { clearTimeout(to);
        if (!(s in out)) { out[s] = 'loaded'; done(); } };
      im.onerror = () => { clearTimeout(to);
        if (!(s in out)) { out[s] = 'error'; done(); } };
      im.src = s;
    } catch (e) { out[s] = 'unknown'; done(); }
  }
  setTimeout(() => res(out), args.timeout + 300);
})
"""
_DYN_SCROLL_JS = (
    "() => new Promise(r => {let y=0; const t=setInterval(()=>{"
    "window.scrollBy(0,900); y+=900; "
    "if (y>=document.body.scrollHeight){clearInterval(t);"
    "window.scrollTo(0,0); r();}}, 60);})")


def _dyn_signature(sample) -> str:
    """Stable IDENTITY-KEYED signature of the render state relevant to settling:
    {data-aaa-dynamic-id → counter text | painted flag}. Order-independent —
    DOM reordering or selector-set changes cannot fake stability. None → sentinel."""
    if not isinstance(sample, dict):
        return "<none>"
    return "|".join("%s=%s" % (k, sample[k]) for k in sorted(sample))


def assemble_dynamic_render(initial, final, viewport, scrolled, settled,
                            waited_ms, user_input_activation=False,
                            probe=None, id_settled=None) -> dict:
    """Pure assembly of the persisted dynamic_render record. Initial and final
    states are joined BY STABLE ELEMENT IDENTITY (data-aaa-dynamic-id), never by
    array position, so identity survives attribute/class/order changes. The two
    dynamic issues (lazy images vs counters) stay separate evidence lists; the
    record carries per-element context/dom_path/load_status for downstream
    element-specific association. Never raises; bounded output."""
    initial = initial if isinstance(initial, dict) else {}
    final = final if isinstance(final, dict) else {}
    probe = probe if isinstance(probe, dict) else {}
    id_settled = id_settled if isinstance(id_settled, dict) else {}
    fin_lazy = final.get("lazy") if isinstance(final.get("lazy"), dict) else {}
    cands = []
    for x in (initial.get("lazy") or [])[:40]:
        if not isinstance(x, dict):
            continue
        fid = fin_lazy.get(x.get("id")) or {}
        load = fid.get("load_status") or "unknown"
        painted_final = bool(fid.get("painted", x.get("painted")))
        cf = fid.get("computed_final") or ""
        decl = x.get("declared") or ""
        decl_set = [d for d in (x.get("declared_set") or []) if d] \
            or ([decl] if decl else [])
        if x.get("kind") == "bg":
            # 26Z review: a computed background proves nothing about the asset
            # (url(missing.jpg) still computes non-none) — a background counts
            # as painted ONLY when the real probe reports loaded, and a failed
            # probe of ANY declared/layered source is authoritative.
            keys = decl_set + _CSS_URL.findall(cf)
            statuses = [probe[k] for k in keys if k and k in probe]
            if load == "unknown" and statuses:
                load = statuses[0]
            if "error" in statuses:
                load = "error"
            if load != "loaded":
                painted_final = False
        else:
            # 26Z review: the browser may SELECT any srcset candidate — only
            # the applied candidate's state judges the element; an unused
            # candidate's 404 must never fail a correctly rendered image.
            # 26AA: the comparison uses CANONICAL absolute URLs (candidates
            # resolved against document.baseURI at capture), never substrings.
            decl_abs = [a for a in (x.get("declared_set_abs") or []) if a]
            if len(decl_abs) != len(decl_set):
                decl_abs = list(decl_set)          # legacy records
            applied = [decl_set[i] for i, a in enumerate(decl_abs)
                       if cf and _url_matches(a, cf)]
            if applied:
                sel = [probe[d] for d in applied if d in probe]
                if "error" in sel:
                    load = "error"
                elif load == "unknown" and sel:
                    load = sel[0]
            else:
                statuses = [probe[d] for d in decl_set if d in probe]
                if load == "unknown" and statuses:
                    load = statuses[0]
                if "error" in statuses:
                    load = "error"
                # even a REACHABLE declared source counts as unpainted when the
                # final currentSrc reflects NO declared candidate — the
                # intended image was never displayed (placeholder only).
                if painted_final and decl_set and cf:
                    painted_final = False
            if load == "error":
                painted_final = False
        cands.append({
            "id": x.get("id"), "kind": x.get("kind"), "viewport": viewport,
            "declared": x.get("declared"), "declared_set": decl_set[:5],
            "context": x.get("context"),
            "dom_path": x.get("dom_path"),
            "computed_initial": x.get("computed_initial"),
            "computed_final": fid.get("computed_final"),
            "painted_initial": bool(x.get("painted")),
            "painted_final": painted_final,
            "load_status": load,
        })
    fin_ctr = final.get("counters") if isinstance(final.get("counters"), dict) else {}
    counters = []
    for c in (initial.get("counters") or [])[:20]:
        if not isinstance(c, dict):
            continue
        f = fin_ctr.get(c.get("id"))
        if isinstance(f, dict):
            counters.append({
                "id": c.get("id"), "viewport": viewport,
                "initial": c.get("text"), "final": f.get("text"),
                "target": f.get("target") or c.get("target"),
                "changed": bool(f.get("text") is not None
                                and f.get("text") != c.get("text")),
                # 26Z review: ELEMENT-scoped settling — a stable counter stays
                # settled even when an unrelated element blocked page settling.
                "settled": bool(id_settled.get(c.get("id"), settled)),
                "context": c.get("context"),
                "dom_path": c.get("dom_path"),
            })
        else:
            # 26Z review: the marked element vanished before the final inventory
            # (library re-render / replacement). The final state is UNKNOWN — it
            # must never fall back to the initial placeholder as if settled, or
            # a replaced counter that reached its target would be confirmed.
            counters.append({
                "id": c.get("id"), "viewport": viewport,
                "initial": c.get("text"), "final": None,
                "target": c.get("target"), "changed": False,
                "settled": False, "identity_lost": True,
                "context": c.get("context"), "dom_path": c.get("dom_path"),
            })
    unpainted = [{"id": c["id"], "kind": c["kind"], "declared": c["declared"],
                  "context": c["context"], "dom_path": c["dom_path"],
                  "load_status": c["load_status"],
                  "initially_painted": c["painted_initial"]}
                 for c in cands
                 if c.get("declared") and not c["painted_final"]][:20]
    return {"viewport": viewport,
            "lazy_total": len(cands),
            "lazy_painted": sum(1 for c in cands if c["painted_final"]),
            "lazy_candidates": cands,
            "lazy_unpainted": unpainted, "counters": counters,
            "scrolled": bool(scrolled), "settled": bool(settled),
            "waited_ms": int(waited_ms or 0),
            "budget_exhausted": not bool(settled),
            "user_input_activation": bool(user_input_activation),
            "delayed_scripts_events": list(final.get("events") or [])}


async def stabilize_dynamic(page, viewport: str):
    """HALT-26Z dynamic-render stabilization on an already-loaded page:

    1. mark candidates with stable ephemeral identities + register completion-
       event listeners (generic list — never site-specific);
    2. bounded, non-destructive REAL user input (mouse move + wheel) so delayed
       scripts that ignore programmatic scrolling (e.g. WP Rocket Delay
       JavaScript Execution) activate;
    3. deterministic scroll pass for viewport-triggered lazy loaders;
    4. bounded wait for a known delayed-script completion event;
    5. activation grace, then settling: at least TWO consecutive unchanged
       sampling intervals after the last observed change, bounded budget,
       honest budget_exhausted;
    6. bounded load probe for declared-but-unpainted sources;
    7. final identity-keyed inventory (markers removed).

    Returns the dynamic_render record, or None when the page has no dynamic
    candidates. Never raises."""
    try:
        initial = await page.evaluate(_DYN_MARK_JS, list(_DYN_COMPLETION_EVENTS))
    except Exception:
        return None
    if not isinstance(initial, dict) or not (initial.get("lazy")
                                             or initial.get("counters")):
        return None
    activation = False
    try:
        await page.mouse.move(160, 200)
        await page.mouse.move(420, 320)
        await page.mouse.wheel(0, 700)
        activation = True
    except Exception:
        pass
    scrolled = False
    try:
        await page.evaluate(_DYN_SCROLL_JS)
        scrolled = True
    except Exception:
        pass
    try:
        await page.wait_for_function(
            "() => window.__aaaDynEvents && "
            "Object.values(window.__aaaDynEvents).some(Boolean)",
            timeout=_DYN_EVENT_WAIT_MS)
    except Exception:
        pass                       # no known completion event — proceed bounded
    try:
        await page.wait_for_timeout(_DYN_GRACE_MS)
    except Exception:
        pass
    settled, waited, streak, prev = False, 0, 0, None
    # 26Z review: PER-ELEMENT settling too — an unrelated rotating element must
    # not mark a stable matched counter unsettled. id_streak counts consecutive
    # unchanged samples per identity.
    id_streak = {}
    prev_sample = None
    while waited <= _DYN_MAX_WAIT_MS:
        try:
            sample = await page.evaluate(_DYN_SAMPLE_JS)
        except Exception:
            sample = None
        if isinstance(sample, dict):
            for k, v in sample.items():
                if prev_sample is not None and prev_sample.get(k) == v:
                    id_streak[k] = id_streak.get(k, 0) + 1
                else:
                    id_streak[k] = 0
            prev_sample = sample
        sig = _dyn_signature(sample)
        if prev is not None and sig == prev:
            streak += 1
            if streak >= 2:        # two consecutive unchanged intervals
                settled = True
                break
        else:
            streak = 0
        prev = sig
        try:
            await page.wait_for_timeout(_DYN_STABLE_MS)
        except Exception:
            break
        waited += _DYN_STABLE_MS
    id_settled = {k: v >= 2 for k, v in id_streak.items()}
    try:
        final = await page.evaluate(_DYN_FINAL_JS)
    except Exception:
        final = None
    # bounded load probe: unpainted <img> declared sources + EVERY background
    # element's declared and final computed URL (a non-none computed background
    # does not prove the asset downloaded — only a real load result does).
    probe = {}
    try:
        fin_lazy = (final or {}).get("lazy") or {}
        srcs = set()
        for x in initial.get("lazy") or []:
            if not isinstance(x, dict):
                continue
            fid = fin_lazy.get(x.get("id")) or {}
            decl = x.get("declared") or ""
            if x.get("kind") == "bg":
                if decl:
                    srcs.add(decl)
                # 26Z review: probe EVERY url(...) layer — a loaded decorative
                # top layer must not mask a broken photo layer underneath.
                for u in _CSS_URL.findall(fid.get("computed_final") or ""):
                    srcs.add(u)
            else:
                # 26Z review: srcset-aware — probe ALL declared candidates when
                # the element is unpainted, or when it painted only a
                # placeholder (no declared candidate reflected in currentSrc;
                # 26AA: canonical-URL comparison, never substrings).
                decl_set = [d for d in (x.get("declared_set") or []) if d] \
                    or ([decl] if decl else [])
                decl_abs = [a for a in (x.get("declared_set_abs") or []) if a]
                if len(decl_abs) != len(decl_set):
                    decl_abs = list(decl_set)
                cf = fid.get("computed_final") or ""
                if not fid.get("painted", x.get("painted")):
                    for d2 in decl_set:
                        srcs.add(d2)
                elif decl_set and cf and not any(_url_matches(a, cf)
                                                 for a in decl_abs):
                    for d2 in decl_set:
                        srcs.add(d2)
        # probe ANY non-empty source (the browser resolves page-relative URLs
        # like "assets/photo.jpg" against the document base); skip only values
        # that are not loadable images (gradients / explicit none).
        srcs = sorted(s for s in srcs
                      if s and not s.lower().startswith(
                          ("none", "linear-gradient", "radial-gradient",
                           "conic-gradient")))[:10]
        if srcs:
            probe = await page.evaluate(
                _DYN_PROBE_JS, {"sources": srcs, "timeout": _DYN_PROBE_MS})
    except Exception:
        probe = {}
    return assemble_dynamic_render(initial, final, viewport, scrolled, settled,
                                   waited, activation, probe,
                                   id_settled=id_settled)


def _words(text: str) -> int:
    return len(text.split())


def _dom_text(rendered_html: str) -> str:
    """All text in the rendered DOM, mirroring the crawler's visible_text.

    Same logic as crawler.crawl_html: parse the (post-JS) HTML, strip
    script/style/noscript, take body text, collapse whitespace. This is the
    apples-to-apples counterpart to httpx's content.visible_text.
    """
    tree = HTMLParser(rendered_html)
    for node in tree.css("script, style, noscript"):
        node.decompose()
    body = tree.css_first("body")
    text = (body.text(separator=" ", strip=True) if body else "") or ""
    return re.sub(r"\s+", " ", text).strip()


def _classify_error(exc: Exception) -> str:
    msg = str(exc)
    if isinstance(exc, PlaywrightTimeoutError):
        return "timeout"
    if "net::" in msg or "NS_ERROR" in msg or "ERR_" in msg:
        return "network_error"
    return "render_error"


# AAA-179: requested locale -> (BCP-47 context locale, Accept-Language header).
# Mirrors crawler.ACCEPT_LANGUAGE_BY_LOCALE; kept local to avoid the renderer
# microservice importing the crawler package.
_LOCALE_CTX = {
    "hu": ("hu-HU", "hu-HU,hu;q=0.9"),
    "en": ("en-US", "en-US,en;q=0.9"),
    "de": ("de-DE", "de-DE,de;q=0.9"),
    "es": ("es-ES", "es-ES,es;q=0.9"),
}


async def render_in_process(url: str, wait_for: str = "networkidle",
                            locale: str | None = None) -> dict:
    """Render a URL with Playwright Chromium headless and return structured data.

    ``wait_for`` is one of ``networkidle`` | ``domcontentloaded`` | ``load``.
    AAA-179: ``locale`` (e.g. 'hu') hard-sets the browser context locale +
    Accept-Language so the render fetch negotiates the right page language;
    unsupported/absent locale → omitted (site serves its own default). On
    failure returns ``{"url", "error", "error_type"}`` where ``error_type``
    is ``timeout`` | ``render_error`` | ``network_error``.
    """
    if wait_for not in VALID_WAIT:
        wait_for = "networkidle"

    ctx_kwargs = {"viewport": VIEWPORT, "user_agent": USER_AGENT}
    _lc = _LOCALE_CTX.get((locale or "").strip().lower().replace("_", "-").split("-")[0])
    if _lc:
        ctx_kwargs["locale"] = _lc[0]
        ctx_kwargs["extra_http_headers"] = {"Accept-Language": _lc[1]}

    start = time.perf_counter()
    browser = None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(**ctx_kwargs)
            page = await context.new_page()
            page.set_default_timeout(TIMEOUT_MS)

            # Two-phase load: first reach a guaranteed state so we always get a
            # Response (status code) and a parseable DOM. Then *upgrade* to the
            # requested wait state. If the upgrade (commonly "networkidle" on
            # pages with long-poll/analytics sockets) times out, we keep the
            # DOM we already have instead of failing the whole render.
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=TIMEOUT_MS
            )
            effective_strategy = "domcontentloaded"

            if wait_for != "domcontentloaded":
                elapsed_ms = (time.perf_counter() - start) * 1000
                remaining = max(1000, TIMEOUT_MS - int(elapsed_ms))
                try:
                    await page.wait_for_load_state(wait_for, timeout=remaining)
                    effective_strategy = wait_for
                except PlaywrightTimeoutError:
                    # Degrade gracefully — keep the DOM we already have.
                    effective_strategy = f"domcontentloaded (fallback: {wait_for} timed out)"

            render_time_ms = int((time.perf_counter() - start) * 1000)

            final_url = page.url
            status_code = response.status if response is not None else 0

            rendered_html = await page.content()

            body = page.locator("body")
            if await body.count() > 0:
                visible_text = await body.inner_text()
            else:
                visible_text = await page.evaluate(
                    "() => document.documentElement.innerText || ''"
                )
            visible_text = re.sub(r"\s+", " ", visible_text or "").strip()

            dom_text = _dom_text(rendered_html)

            actual_ua = await page.evaluate("() => navigator.userAgent")

            await context.close()
            await browser.close()
            browser = None

            return {
                "url": final_url,
                "status_code": status_code,
                "render_time_ms": render_time_ms,
                "page_load_strategy": effective_strategy,
                "rendered_html_bytes": len(rendered_html.encode("utf-8")),
                "rendered_html": rendered_html,
                "visible_text": visible_text,
                "visible_text_chars": len(visible_text),
                "visible_text_words": _words(visible_text),
                "dom_text": dom_text,
                "dom_text_chars": len(dom_text),
                "dom_text_words": _words(dom_text),
                "viewport": dict(VIEWPORT),
                "user_agent": actual_ua or USER_AGENT,
            }

    except (PlaywrightTimeoutError, PlaywrightError, Exception) as exc:
        return {
            "url": url,
            "error": f"{type(exc).__name__}: {exc}".strip(),
            "error_type": _classify_error(exc),
        }
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


async def _try_accept_consent(page) -> None:
    """Best-effort cookie/consent accept — click the first matching button by
    visible text. Never raises (consent UIs vary wildly; a miss is fine)."""
    for txt in _CONSENT_TEXTS:
        try:
            btn = page.get_by_role("button", name=txt, exact=False)
            if await btn.count() > 0:
                await btn.first.click(timeout=1500)
                await page.wait_for_timeout(400)
                return
        except Exception:
            continue


async def screenshot_in_process(url: str, viewport: str = "desktop",
                                full_page: bool = True,
                                locale: str | None = None) -> dict:
    """AAA-211 — capture a full-page WebP screenshot with the AAA-210 recipe.

    viewport: 'desktop' (1920x1080) | 'mobile' (375x667). device_scale_factor=1,
    animations disabled, prefers-reduced-motion, conditional lazy-scroll, a
    realistic (non-headless) UA, best-effort consent accept, and consent/UA-wall
    detection. Returns {viewport, width, height, format, webp_b64, wall_detected,
    error}. Never raises — error path returns {error, error_type}. WebP encoding
    via Pillow (worker uploads the bytes to GCS; the service stays GCS-free)."""
    import base64
    import io

    from PIL import Image

    vp_cfg, ua = _SHOT_VIEWPORTS.get(viewport, _SHOT_VIEWPORTS["desktop"])
    ctx_kwargs = {
        "viewport": dict(vp_cfg), "user_agent": ua,
        "device_scale_factor": 1, "reduced_motion": "reduce",
    }
    _lc = _LOCALE_CTX.get((locale or "").strip().lower().replace("_", "-").split("-")[0])
    if _lc:
        ctx_kwargs["locale"] = _lc[0]
        ctx_kwargs["extra_http_headers"] = {"Accept-Language": _lc[1]}

    browser = None
    try:
        async with async_playwright() as pw:
            # --disable-blink-features=AutomationControlled lowers headless
            # detectability so sites serve the same markup a real user gets.
            browser = await pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(**ctx_kwargs)
            page = await context.new_page()
            page.set_default_timeout(TIMEOUT_MS)

            # Two-phase load sharing ONE budget (TIMEOUT_MS total), so the whole
            # capture stays well under Cloud Run's request timeout — independent
            # 30s+30s waits could hit 60s and 504 on heavy pages (AAA-211 canary).
            _start = time.perf_counter()
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            _elapsed_ms = int((time.perf_counter() - _start) * 1000)
            _remaining = max(1000, TIMEOUT_MS - _elapsed_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=_remaining)
            except PlaywrightTimeoutError:
                pass  # degrade — capture what we have

            await _try_accept_consent(page)
            try:
                await page.add_style_tag(content=_ANIM_OFF_CSS)
            except Exception:
                pass

            # HALT-26Y/26Z — dynamic-render stabilization with stable element
            # identity, real-input activation and load probing (see
            # stabilize_dynamic). The final screenshot happens AFTER settling.
            try:
                dynamic_render = await stabilize_dynamic(page, viewport)
            except Exception:  # noqa: BLE001 — never fail a shot on stabilization
                dynamic_render = None

            # Wall detection (AAA-210): known wall phrases OR abnormally short
            # render with almost no text (the aboutyou.hu signature).
            try:
                body_txt = (await page.locator("body").inner_text()) if \
                    await page.locator("body").count() > 0 else ""
            except Exception:
                body_txt = ""
            txt_words = _words(body_txt)
            phrase_hit = any(p in body_txt.lower() for p in _WALL_PHRASES)

            png = await page.screenshot(full_page=full_page, type="png")
            img = Image.open(io.BytesIO(png)).convert("RGB")
            w, h = img.size
            # Wall detection (AAA-210): known wall phrase, OR a homepage-grade
            # full-page render that came back abnormally short (the aboutyou.hu
            # ~2770px UA-wall signature). Conservative — never a silent short
            # render; a flagged false-positive is acceptable (it's a finding).
            wall = bool(phrase_hit) or (full_page and h < 3000)

            # Tiling: slice top-to-bottom into native-width, ~3072px-tall WebP
            # tiles with ~128px overlap. Every page taller than the tile height
            # is tiled (uniform vision-ready tiles); a page that fits is a single
            # tile (1-element list) for schema uniformity. Coverage is exact:
            # the last tile's bottom always reaches page_height (no gaps).
            step = _TILE_HEIGHT - _TILE_OVERLAP
            bounds = []  # (top, bottom)
            if h <= _TILE_HEIGHT:
                bounds.append((0, h))
            else:
                y = 0
                while True:
                    bottom = min(y + _TILE_HEIGHT, h)
                    bounds.append((y, bottom))
                    if bottom >= h:
                        break
                    y += step
            tiles = []
            prev_bottom = 0
            for idx, (top, bottom) in enumerate(bounds):
                tile_img = img.crop((0, top, w, bottom))
                buf = io.BytesIO()
                tile_img.save(buf, "WEBP", quality=_WEBP_QUALITY)
                tiles.append({
                    "index": idx,
                    "width": w, "height": bottom - top, "y_offset": top,
                    "overlap_px": (prev_bottom - top) if idx > 0 else 0,
                    "webp_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
                })
                prev_bottom = bottom

            # AAA-244 — piggyback the rendered DOM on the SAME navigation (no
            # second render): the page is already at networkidle, so page.content()
            # is one in-memory serialization. Desktop only (one DOM per entity).
            # Mirrors render_in_process; feeds the worker's raw↔rendered diff.
            rendered_html = None
            dom_text = None
            dom_words = None
            if viewport == "desktop":
                try:
                    rendered_html = await page.content()
                    dom_text = _dom_text(rendered_html)
                    dom_words = _words(dom_text)
                except Exception:  # noqa: BLE001 — never fail a shot on DOM capture
                    rendered_html = dom_text = dom_words = None

            await context.close()
            await browser.close()
            browser = None
            return {
                "viewport": viewport, "format": "webp",
                "page_width": w, "page_height": h,
                "wall_detected": wall, "error": None, "tiles": tiles,
                # HALT-26Y — settled-state record for lazy images + counters
                # (None when the page had no dynamic candidates).
                "dynamic_render": dynamic_render,
                # AAA-244 — rendered DOM (desktop only; None on mobile / capture fail)
                "rendered_html": rendered_html,
                "dom_text": dom_text,
                "dom_text_words": dom_words,
            }
    except (PlaywrightTimeoutError, PlaywrightError, Exception) as exc:
        return {"viewport": viewport, "error": f"{type(exc).__name__}: {exc}".strip(),
                "error_type": _classify_error(exc), "wall_detected": False}
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# AAA-159 S1 — PDF rendering
#
# A separate budget from /render and /screenshot on purpose. Those two fetch a
# CUSTOMER's site, where 30 s is a deliberate ceiling on how long a stranger's
# page may cost us. This one fetches OUR OWN report: ~250 KB of script-free HTML
# plus ~1.4 MB of evidence WebP, and it must paginate 70+ A4 pages before it can
# emit anything. Reusing the 30 s budget would time out on a correct document.
#
# The ceiling is set below Cloud Run's 120 s request timeout so the service, not
# the platform, decides the failure — a platform 504 carries no error_type and
# the caller cannot distinguish it from a crash. /screenshot's budget is
# untouched.
# --------------------------------------------------------------------------
PDF_TIMEOUT_MS = 90_000

# Every image must be REAL before pagination. An evidence frame that paginates
# empty is worse than a slow render: it looks like the audit found nothing.
_PDF_FORCE_IMAGES_JS = """
async () => {
  const imgs = [...document.querySelectorAll('img')];
  imgs.forEach(i => { i.loading = 'eager'; i.setAttribute('fetchpriority', 'high'); });
  document.querySelectorAll('details').forEach(d => { d.open = true; });
  const H = document.body.scrollHeight;
  for (let y = 0; y < H; y += 900) {
    window.scrollTo(0, y);
    await new Promise(r => setTimeout(r, 40));
  }
  window.scrollTo(0, 0);
  await Promise.all(imgs.map(i => i.complete ? Promise.resolve()
    : new Promise(r => { i.onload = r; i.onerror = r; setTimeout(r, 8000); })));
  return {total: imgs.length, loaded: imgs.filter(i => i.naturalWidth > 0).length};
}
"""


async def pdf_in_process(url: str, *, print_background: bool = True) -> dict:
    """Render OUR OWN report URL to an A4 PDF. Returns
    {pdf_b64, bytes, pages_hint, images_total, images_loaded, error, error_type}.

    Never raises — every failure maps to an {error, error_type} dict, matching
    the /render and /screenshot contract so the caller branches on one key.

    `details` are forced open before pagination for a reason that outlives this
    function: the print stylesheet hides `<details>` and substitutes prose
    blocks, but the evidence SCREENSHOTS live inside those elements, and a
    collapsed element is not painted, so its images never decode. Opening them
    costs nothing in the output (print CSS still hides the disclosure widgets)
    and is the difference between evidence and empty frames."""
    import base64
    import time as _t

    browser = None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                viewport=dict(VIEWPORT), device_scale_factor=1,
                reduced_motion="reduce")
            page = await context.new_page()
            page.set_default_timeout(PDF_TIMEOUT_MS)

            # One shared budget across navigation + settle + image forcing, so
            # the whole call stays under the Cloud Run ceiling.
            _start = _t.perf_counter()
            await page.goto(url, wait_until="domcontentloaded",
                            timeout=PDF_TIMEOUT_MS)

            def _remaining(floor=2000):
                spent = int((_t.perf_counter() - _start) * 1000)
                return max(floor, PDF_TIMEOUT_MS - spent)

            try:
                await page.wait_for_load_state("networkidle",
                                               timeout=_remaining())
            except PlaywrightTimeoutError:
                pass  # degrade — the image pass below is the real gate

            try:
                img_state = await page.evaluate(_PDF_FORCE_IMAGES_JS)
            except Exception:  # noqa: BLE001 — never fail a PDF on the helper
                img_state = {}

            await page.emulate_media(media="print")

            # The sheet should be one colour. The print stylesheet paints <body>
            # white while the report's paper is a warm off-white, so the 12mm
            # margin came out as a white frame around a cream page.
            #
            # `@page{background}` is what does the work. Painting html/body is NOT
            # enough and was measured to fail: Chromium's page-margin area lies
            # outside the document canvas, so a body background stops at the
            # content box and the frame stays white. Two other approaches were
            # tried and rejected on evidence — zero margins with body padding
            # bleeds correctly but loses the top/bottom inset on every page after
            # the first (vertical padding does not repeat across fragments, so
            # text ran to the sheet edge), and header/footer templates did not
            # paint their bands at all. `@page` keeps real 12mm margins on every
            # page AND colours them.
            #
            # The colour is READ FROM THE PAGE, never hard-coded: the skin owns
            # it, and a literal here would drift silently the day it changes —
            # and a wrong colour that looks deliberate is worse than a white one.
            try:
                paper = await page.evaluate(
                    "() => { const e = document.querySelector('.v2q-paper')"
                    " || document.querySelector('main') || document.body;"
                    " return getComputedStyle(e).backgroundColor; }")
                if paper and paper not in ("rgba(0, 0, 0, 0)", "transparent"):
                    await page.add_style_tag(content=(
                        "@media print{@page{background:%s}"
                        "html,body{background:%s !important}}" % (paper, paper)))
            except Exception:  # noqa: BLE001 — a white frame is not worth failing over
                pass

            # No per-call timeout argument: this Playwright's Page.pdf() does not
            # accept one. The budget still applies — set_default_timeout above
            # governs the whole page, and the Cloud Run ceiling sits above it.
            pdf = await page.pdf(
                format="A4", print_background=bool(print_background),
                margin={"top": "12mm", "bottom": "12mm",
                        "left": "12mm", "right": "12mm"})

            return {
                "pdf_b64": base64.b64encode(pdf).decode("ascii"),
                "bytes": len(pdf),
                "pages_hint": pdf.count(b"/Type /Page") or pdf.count(b"/Type/Page"),
                "images_total": img_state.get("total"),
                "images_loaded": img_state.get("loaded"),
                "error": None, "error_type": None,
            }
    except Exception as e:  # noqa: BLE001
        return {"pdf_b64": None, "bytes": 0, "pages_hint": 0,
                "images_total": None, "images_loaded": None,
                "error": "%s: %s" % (type(e).__name__, e),
                "error_type": _classify_error(e)}
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
