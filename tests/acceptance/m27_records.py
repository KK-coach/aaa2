"""A pipeline-futás rekordjai a mérőkörnyezetbe (M2/7 B): a zárolt oldalak kinyerése és v3
utáni rekordja a pipeline adatbázisából a `v3_eval` formájában, hogy a `v3_eval report`
ugyanazokkal a mérőszámokkal pontozza.

    python -m tests.acceptance.m27_records --out data/m27/eval \\
        --site marketinglens-crawl=data/m27/marketinglens.duckdb \\
        --site duex-crawl=data/m27/duex2.duckdb
    python -m tests.acceptance.v3_eval report --data-dir data/m27/eval --from m27 --tag m27v3

Oldalanként (a `locked_pages.json` URL-je szerint) a kinyerő modell legutóbbi `done` sora az
`entity_run_pages`-ből: a kinyerés (`--from` tag, alapból m27) és a v3 utáni rekord (`--tag`,
alapból m27v3). A hívásazonosítók site-onként eltolva (`OFFSET` × a site sorszáma), a hozzájuk
tartozó `llm_calls` sorok a kimeneti mappa `synthetic.duckdb`-jébe kerülnek (a költség és a token
innen). Ellenőrzés: a rekord blokk-azonosítóinak szövege a pipeline adatbázisában és a zárolt
oldal JSON-jában azonos-e (az eltérő azonosítók a konzolra).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

import tests.acceptance.gate_eval as ge
import tests.acceptance.synthetic_eval as se
from aaa2.db.connect import connect
from aaa2.llm.config import load_config
from tests.acceptance.annotation import LOCKED_DIR, locked_sources

OFFSET = 1_000_000


def shift(value, offset: int):
    """A rekordban minden `call_id` / `call_ids` / `llm_call_id` érték eltolva."""
    if isinstance(value, dict):
        return {k: ([i + offset if isinstance(i, int) else i for i in v]
                    if k == "call_ids" and isinstance(v, list)
                    else v + offset if k in ("call_id", "llm_call_id") and isinstance(v, int)
                    else shift(v, offset)) for k, v in value.items()}
    if isinstance(value, list):
        return [shift(v, offset) for v in value]
    return value


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=Path("data/m27/eval"))
    parser.add_argument("--site", action="append", required=True,
                        help="név=adatbázis (a locked_pages.json site-neve)")
    parser.add_argument("--from", dest="source", default="m27")
    parser.add_argument("--tag", default="m27v3")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)
    model = args.model or load_config().pipeline["extraction"]
    sites = dict(pair.split("=", 1) for pair in args.site)
    (args.out / "synthetic").mkdir(parents=True, exist_ok=True)
    calls = connect(args.out / "synthetic.duckdb")
    for index, (name, db) in enumerate(sites.items(), start=1):
        offset = OFFSET * index
        con = duckdb.connect(db, read_only=True)
        pages = [(pid, url) for pid, site, url, _ in locked_sources() if site == name]
        for page_id, url in pages:
            row = con.execute(
                "SELECT p.page_id, rp.extraction, rp.refined, rp.call_ids "
                "FROM entity_run_pages rp JOIN entity_runs r USING (run_id) "
                "JOIN pages p ON p.page_id = rp.page_id WHERE r.method = 'llm' "
                "AND r.model = ? AND rp.status = 'done' AND p.url = ? "
                "ORDER BY rp.run_id DESC LIMIT 1", [model, url]).fetchone()
            if row is None:
                print(f"{page_id}: nincs kész kinyerés ({url})")
                continue
            db_page, extraction, refined, call_ids = row
            extraction = json.loads(extraction)
            refined = json.loads(refined) if refined else extraction
            # a rekordot létrehozó hívások: újrahasznált kinyerésnél a futás sora üres, a
            # visszajátszott ellenőrzés hívása a korábbi
            ids = list(dict.fromkeys(
                i for i in [*(extraction.get("call_ids") or []), refined.get("verify_call_id"),
                            *(call_ids or [])] if i is not None))
            ge.write_record(args.out, page_id, model, args.source, shift(extraction, offset))
            ge.write_record(args.out, page_id, model, args.tag,
                            {**shift(refined, offset), "call_ids": [i + offset for i in ids]})
            for call in con.execute("SELECT * FROM llm_calls WHERE list_contains(?, call_id)",
                                    [ids]).fetchall():
                columns = [d[0] for d in con.description]
                values = dict(zip(columns, call, strict=True))
                values["call_id"] += offset
                calls.execute(
                    f"INSERT OR REPLACE INTO llm_calls ({', '.join(values)}) VALUES "
                    f"({', '.join('?' * len(values))})", list(values.values()))
            stored = {f"b{ordinal}": text for ordinal, text in con.execute(
                "SELECT ordinal, text FROM blocks WHERE page_id = ?", [db_page]).fetchall()}
            locked = json.loads((LOCKED_DIR / f"{page_id}.json").read_text(encoding="utf-8"))
            mine = {b["id"]: b["text"] for b in locked["blocks"]}
            used = {m.get("block_id") for e in refined.get("entities") or []
                    for m in e.get("mentions") or [e] if m.get("block_id")}
            differ = sorted(b for b in used if stored.get(b) != mine.get(b))
            print(f"{page_id}: {len(ids)} hívás; a rekord {len(used)} blokkja közül a zárolt "
                  f"oldaléval eltérő szövegű: {len(differ)}" + (f" {differ[:8]}" if differ else ""))
        con.close()
    calls.close()
    print(f"kimenet: {args.out} (rekordok: {args.source}, {args.tag}); a pontozás: "
          f"python -m tests.acceptance.v3_eval report --data-dir {args.out} "
          f"--from {args.source} --tag {args.tag}")


if __name__ == "__main__":
    main()


__all__ = ["main", "se"]
