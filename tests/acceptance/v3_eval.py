"""A megközelítés v3 a mérőkörnyezetben (M2 spec, „Megközelítés v3”): a kinyerés utáni szabály
és a pontozás, a zárolt és a fejlesztési oldalakon.

    python -m tests.acceptance.v3_eval apply --set locked --from v3 --tag v3p
    python -m tests.acceptance.v3_eval apply --set development-real --page kk_coach_meres_hu \\
        --page ngx_accordion_en --from v3 --tag v3p

A szabály (`apply_v3`), oldalanként, a `--from` kör rekordján (Luna-kinyerés, 3a prompt,
elnevezés nélkül):

- megnevezett entitás (a concept és a service kivételével minden típus): változatlan;
- saját ajánlat (`service`): marad, ha szerkezeti helyen áll (title, heading, card vagy
  table_row blokk, navigáció; anchor-szöveg nem elég), és a Sol-ellenőrzés nem vétózza. Csak a
  szerkezeti helyű szolgáltatások mennek a Sol-hívásba (`verify.verify_record`, `select`); ha
  nincs ilyen, nincs hívás;
- fogalom (`concept`): kapu és ellenőrzés nélkül marad. A bizonyíték tételenként a rekord `v3`
  mezőjében: szerkezet (`gate.structure`), ismétlődés (blokkszám), tudásbázis-egyezés
  (Wikidata / Wikipedia, `gate.KnowledgeBase`, a `validate` gyorsítótárán át), title- vagy
  heading-hely, említésszám és fontossági sorszám (title- vagy heading-helyű előre, aztán az
  említésszám, aztán az első említés sorrendje).

A navigáció és az anchor-szövegek a rögzített készlet renderelt DOM-jából jönnek
(`gate_eval.page_context`). A kimenet a `--tag` alá kerül; a konzolra csak darabszám.
"""
from __future__ import annotations

import argparse
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import httpx

import tests.acceptance.gate_eval as ge
import tests.acceptance.synthetic_eval as se
from aaa2.db.connect import DATA_DIR, connect
from aaa2.entities.gate import (
    PROMINENT_KINDS,
    KnowledgeBase,
    PageContext,
    SoftItem,
    repetition,
    soft_items,
    structure,
)
from aaa2.entities.rules import alias_key
from aaa2.entities.validate import _Api
from aaa2.entities.verify import verify_record
from aaa2.llm.client import Retry
from aaa2.llm.config import load_config

SOL = "gpt-6-sol"


def service_place(item: SoftItem, page: PageContext) -> str | None:
    """A szolgáltatás szerkezeti helye; az anchor-szöveg nem számít."""
    where = structure(item, page)
    return None if where == "anchor" else where


def apply_v3(record: Mapping, page: PageContext, verifier, knowledge,
             page_id: int | None = None) -> dict:
    """A rekord a v3 szabállyal (lásd a modul leírását), a `v3` bizonyíték-mezővel."""
    blocks = page.by_id()
    items = soft_items(record.get("entities") or [], blocks)
    places = {item.key: service_place(item, page) for item in items if item.type == "service"}
    out = verify_record(verifier, record, blocks, page_id,
                        select=lambda item: item.type == "service" and places.get(item.key)
                        is not None) if verifier is not None else dict(record)
    vetoed = {alias_key(name) for name, kind, keep in out.get("verify_decisions") or []
              if kind == "service" and keep is False}
    dropped = {key for key, place in places.items() if place is None} | vetoed
    out["entities"] = [raw for raw in record.get("entities") or []
                       if alias_key(raw["canonical_name"]) not in dropped]
    decided = {alias_key(name): keep for name, _, keep in out.get("verify_decisions") or []}
    concepts = [item for item in items if item.type == "concept"]
    order = {id(item): index for index, item in enumerate(concepts)}
    prominent = {item.key: structure(item, page, PROMINENT_KINDS) is not None
                 for item in concepts}
    ranked = sorted(concepts, key=lambda item: (not prominent[item.key], -len(item.mentions),
                                                order[id(item)]))
    out["v3"] = {
        "services": [{"canonical": item.canonical, "structure": places[item.key],
                      "sol": decided.get(item.key), "kept": item.key not in dropped}
                     for item in items if item.type == "service"],
        "concepts": [{"canonical": item.canonical, "rank": rank + 1,
                      "mentions": len(item.mentions), "prominent": prominent[item.key],
                      "structure": structure(item, page), "blocks": repetition(item, page),
                      "knowledge": knowledge(item.names(), page.lang)}
                     for rank, item in enumerate(ranked)]}
    return out


def run_apply(model: str, data_dir: Path, pages: list[dict], source: str, tag: str) -> None:
    con = connect(data_dir / "synthetic.duckdb")
    cache = connect(data_dir / "gate.duckdb")
    try:
        verifier = se._client(con, SOL)
        api = _Api(cache, None, httpx.Client(timeout=20.0), Retry(),
                   lambda: datetime.now(UTC).replace(tzinfo=None), time.monotonic)
        knowledge = KnowledgeBase(api.get)
        for page in pages:
            record = ge.read_record(data_dir, page["page_id"], model, source)
            if record is None or record.get("entities") is None:
                print(f"{page['page_id']}: nincs kinyerés ({source})")
                continue
            out = apply_v3(record, ge.page_context(page, data_dir), verifier, knowledge)
            out["v3_source"] = source
            ge.write_record(data_dir, page["page_id"], model, tag, out)
            services = out["v3"]["services"]
            print(f"{page['page_id']}: {source} → {tag}: szolgáltatás {len(services)} "
                  f"(marad {sum(s['kept'] for s in services)}), fogalom "
                  f"{len(out['v3']['concepts'])}; Sol-hívás "
                  f"{'van' if out.get('verify_call_id') else 'nincs'}"
                  + (f"; hiba: {out['verify_error']}" if out.get("verify_error") else ""))
    finally:
        con.close()
        cache.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["apply"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--from", dest="source", default="v3")
    parser.add_argument("--tag", default="v3p")
    parser.add_argument("--set", dest="page_set", choices=(se.LOCKED, se.REAL), default=se.LOCKED)
    parser.add_argument("--page", action="append", default=[])
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    args = parser.parse_args(argv)
    model = args.model or load_config().pipeline["extraction"]
    pages = [p for p in se.load_pages(page_set=args.page_set)
             if not args.page or p["page_id"] in args.page]
    run_apply(model, args.data_dir, pages, args.source, args.tag)


if __name__ == "__main__":
    main()
