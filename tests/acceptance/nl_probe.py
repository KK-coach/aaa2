"""Google Cloud Natural Language API-próba: `analyzeEntities` a kk.coach mérés-oldal és az ngx
Accordion szövegére, összevetve a referencialistával és a mi kinyerésünkkel.

    python -m tests.acceptance.nl_probe [--model gpt-6-luna] [--tag cp] [--pages-dir DIR]

- Hitelesítés: Application Default Credentials (`google.auth.default`); a kvótaprojekt a
  `--quota-project`, különben az ADC-é. A token nem kerül kimenetre.
- A szöveg: az oldal tartalmi blokkjai (`blocks.block_text`) dokumentum-sorrendben, üres sorral
  elválasztva, `PLAIN_TEXT`, a nyelv felismerésével.
- Két hívás oldalanként: v2 (`languageSupported`, entitások típussal) és v1 (entitások
  salience-szel; ha a nyelvet nem támogatja, a hiba a jelentésbe kerül).
- Nyers válaszok: `<data-dir>/nl/<oldal>.<v1|v2>.json`; ha már megvannak, nincs újrahívás. A
  hibás válasz nem kerül gyorsítótárba, csak a jelentésbe.
- Egyezés a referencialistával: egy kötelező tétel megvan, ha egy NL-entitás neve vagy egy
  említésének szövege a tétel kanonikus nevének vagy egy aliasának kulcsa (`alias_key`).
- Rangsor: a v1 salience szerinti sorrend és a mi fontossági sorrendünk (a tárolt kimenet
  csoportjai: title- vagy heading-helyű előre, aztán az említésszám) a mindkettőben szereplő
  kötelező tételeken, Spearman-féle rangkorrelációval, és a legfontosabb 10 átfedése.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx

import tests.acceptance.synthetic_eval as se
from aaa2.db.connect import DATA_DIR
from aaa2.entities.blocks import block_text
from aaa2.entities.gate import PROMINENT_KINDS, PageContext, SoftItem, structure
from aaa2.entities.rules import alias_key
from tests.acceptance.gate_eval import read_record

PAGES = ("kk_coach_meres_hu", "ngx_accordion_en")
ENDPOINTS = {"v1": "https://language.googleapis.com/v1/documents:analyzeEntities",
             "v2": "https://language.googleapis.com/v2/documents:analyzeEntities"}
SCOPES = ["https://www.googleapis.com/auth/cloud-language"]


def page_text(page: Mapping) -> str:
    return "\n\n".join(block_text(b) for b in page["blocks"])


def _headers(quota_project: str | None = None) -> dict[str, str]:
    import google.auth
    import google.auth.transport.requests
    credentials, project = google.auth.default(scopes=SCOPES)
    credentials.refresh(google.auth.transport.requests.Request())
    headers = {"Authorization": f"Bearer {credentials.token}",
               "Content-Type": "application/json; charset=utf-8"}
    quota = quota_project or getattr(credentials, "quota_project_id", None) or project
    if quota:
        headers["x-goog-user-project"] = quota
    return headers


def analyze(page: Mapping, version: str, out_dir: Path, headers_of) -> dict:
    """A nyers válasz (a hibás is, `error` kulccsal), gyorsítótárból, ha van."""
    path = out_dir / f"{page['page_id']}.{version}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    body = {"document": {"type": "PLAIN_TEXT", "content": page_text(page)},
            "encodingType": "UTF8"}
    response = httpx.post(ENDPOINTS[version], json=body, headers=headers_of(), timeout=60.0)
    data = response.json() if response.content else {}
    if response.status_code != 200:
        return data if "error" in data else {
            "error": {"code": response.status_code, "message": response.text[:500]}}
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def entity_keys(entity: Mapping) -> set[str]:
    keys = {alias_key(entity.get("name") or "")}
    keys |= {alias_key((m.get("text") or {}).get("content") or "")
             for m in entity.get("mentions") or []}
    return keys - {""}


def gold_matches(page: Mapping, entities: Sequence[Mapping]) -> dict[str, int | None]:
    """Kötelező tétel → az első illeszkedő NL-entitás sorszáma (a válasz sorrendjében), vagy
    None."""
    out: dict[str, int | None] = {}
    for item in se._items(page["gold"]["entities"]):
        out[item.canonical] = next((i for i, e in enumerate(entities)
                                    if entity_keys(e) & item.keys), None)
    return out


def our_order(page: Mapping, record: Mapping) -> list[str]:
    """A tárolt kimenet csoportjainak kulcsai fontossági sorrendben (minden típus)."""
    context = PageContext(page["blocks"], lang=page.get("lang") or "en")
    groups: dict[str, SoftItem] = {}
    for raw in record.get("entities") or []:
        key = alias_key(raw["canonical_name"])
        groups.setdefault(key, SoftItem(key, raw["canonical_name"], raw["type"])
                          ).mentions.append(raw)
    items = list(groups.values())
    first = {id(item): index for index, item in enumerate(items)}
    items.sort(key=lambda item: (structure(item, context, PROMINENT_KINDS) is None,
                                 -len(item.mentions), first[id(item)]))
    return [item.key for item in items]


def spearman(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Rangkorreláció két sorrend közös elemein (a közös elemek újrarangsorolva)."""
    common = [x for x in a if x in b]
    n = len(common)
    if n < 3:
        return None
    rank_a = {x: i for i, x in enumerate(common)}
    rank_b = {x: i for i, x in enumerate([x for x in b if x in rank_a])}
    d2 = sum((rank_a[x] - rank_b[x]) ** 2 for x in common)
    return 1 - 6 * d2 / (n * (n * n - 1))


def report(pages: Sequence[Mapping], responses: Mapping, records: Mapping) -> str:
    lines = ["# Google Natural Language API-próba (`analyzeEntities`)", ""]
    for page in pages:
        pid = page["page_id"]
        lines += [f"## {pid}", ""]
        v2, v1 = responses[pid]["v2"], responses[pid]["v1"]
        if "error" in v2:
            lines.append(f"- v2 hiba: {v2['error'].get('code')} {v2['error'].get('message')}")
        else:
            lines.append(f"- v2: languageCode `{v2.get('languageCode')}`, languageSupported "
                         f"`{v2.get('languageSupported')}`, entitás {len(v2.get('entities') or [])}")
        if "error" in v1:
            lines.append(f"- v1 hiba: {v1['error'].get('code')} {v1['error'].get('message')}")
        else:
            lines.append(f"- v1: language `{v1.get('language')}`, entitás "
                         f"{len(v1.get('entities') or [])}")
        salient = sorted(v1.get("entities") or [], key=lambda e: -(e.get("salience") or 0))
        typed = salient or (v2.get("entities") or [])
        lines += ["", "Entitások (v1 salience szerint, ha van; különben a v2 sorrendje):", ""]
        lines += [f"{i + 1}. {e.get('name')} — {e.get('type')}"
                  + (f" — {e['salience']:.4f}" if "salience" in e else "")
                  + (f" — {e['metadata']['wikipedia_url']}"
                     if (e.get("metadata") or {}).get("wikipedia_url") else "")
                  for i, e in enumerate(typed)]
        matched = gold_matches(page, typed)
        found = [name for name, index in matched.items() if index is not None]
        missing = [name for name, index in matched.items() if index is None]
        record = records.get(pid) or {}
        ours = se.score_page(page, record) if record else None
        our_missed = {name for name, *_ in ours.missed} if ours else set()
        lines += ["", f"- kötelező tételek az NL-ben: {len(found)}/{len(matched)}",
                  f"  - megvan: {', '.join(found) or '—'}",
                  f"  - hiányzik: {', '.join(missing) or '—'}",
                  f"- a mi kinyerésünk (`{record.get('model', '?')}`): "
                  + (f"{ours.found}/{ours.required}" if ours else "—"),
                  "  - NL megvan, mi nem: " + (", ".join(n for n in found if n in our_missed)
                                               or "—"),
                  "  - mi megvan, NL nem: " + (", ".join(n for n in missing
                                                         if n not in our_missed) or "—")]
        if salient and record:
            nl_rank = []
            for item in se._items(page["gold"]["entities"]):
                index = matched.get(item.canonical)
                if index is not None:
                    nl_rank.append((index, alias_key(item.canonical), item.keys))
            nl_order = [key for _, key, _ in sorted(nl_rank)]
            order = our_order(page, record)
            ours_keys = []
            for key in order:
                hit = next((k for _, k, keys in nl_rank if key in keys), None)
                if hit and hit not in ours_keys:
                    ours_keys.append(hit)
            rho = spearman(nl_order, ours_keys)
            top_nl, top_ours = set(nl_order[:10]), set(ours_keys[:10])
            common = len(set(nl_order) & set(ours_keys))
            shown = "—" if rho is None else f"{rho:.2f}"
            lines += [(f"- rangsor a közös kötelező tételeken ({common} db): Spearman {shown}; "
                       f"a legfontosabb 10 átfedése {len(top_nl & top_ours)}/10"),
                      f"  - NL salience: {', '.join(nl_order[:10])}",
                      f"  - mi: {', '.join(ours_keys[:10])}"]
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--tag", default="cp")
    parser.add_argument("--pages-dir", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--out", type=Path, default=se.OUT_DIR.parent / "nl")
    parser.add_argument("--quota-project", default=None,
                        help="a Google Cloud-projekt, amelyben a Natural Language API be van "
                             "kapcsolva (alapból az ADC kvótaprojektje)")
    args = parser.parse_args(argv)
    pages = [p for p in se.load_pages(args.pages_dir, se.REAL) if p["page_id"] in PAGES]
    cached: dict[str, str] = {}

    def headers_of():
        if not cached:
            cached.update(_headers(args.quota_project))
        return cached

    responses = {p["page_id"]: {v: analyze(p, v, args.data_dir / "nl", headers_of)
                                for v in ("v2", "v1")} for p in pages}
    records = {p["page_id"]: read_record(args.data_dir, p["page_id"], args.model, args.tag)
               for p in pages}
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / "nl-probe.md"
    out.write_text(report(pages, responses, records), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
