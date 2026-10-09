"""A `links` tábla újraolvasása a tárolt renderelt DOM-ból, újrabejárás nélkül.

A parser változása (az eredeti cím, `raw_url`; a nem navigációs href kihagyása) a már bejárt
készleteken így érvényesül: oldalanként a tárolt DOM-ból a mai parserrel kiolvasott linkek
kerülnek a régi sorok helyére. A csere csak akkor történik meg, ha az új linksor a régi
részsorozata (cél, horgony, pozíció és nofollow szerint, a sorrendet tartva): ami kimarad, azt a
parser ma már nem veszi linknek. A céloldal (`to_page_id`) a régi sorból öröklődik. Ahol az új
sor nem részsorozata a réginek, az oldal linkjei változatlanok maradnak, és az oldal címe a
jelentésbe kerül (`mismatched`)."""
from __future__ import annotations

from dataclasses import dataclass, field

import duckdb
import zstandard

from aaa2.engine.frontier import stored_policy
from aaa2.engine.parse import parse_page


@dataclass
class RelinkRun:
    pages: int = 0                      # oldalak tárolt DOM-mal
    before: int = 0                     # linksorok a csere előtt
    after: int = 0                      # linksorok a csere után
    dropped: int = 0                    # kieső (nem navigációs) linksorok
    with_raw_url: int = 0               # linksorok eredeti címmel a csere után
    mismatched: list[str] = field(default_factory=list)


def relink(con: duckdb.DuckDBPyConnection) -> RelinkRun:
    """A linkek újraolvasása minden tárolt DOM-ú oldalra (lásd a modul leírását)."""
    run = RelinkRun()
    policy = stored_policy(con)
    if policy is None:
        return run
    decompressor = zstandard.ZstdDecompressor()
    pages = con.execute("SELECT page_id, url, final_url, rendered_html FROM pages "
                        "WHERE rendered_html IS NOT NULL ORDER BY page_id").fetchall()
    for page_id, url, final_url, blob in pages:
        run.pages += 1
        old = con.execute(
            "SELECT to_url, anchor, position, nofollow, to_page_id FROM links "
            "WHERE from_page_id = ? ORDER BY ordinal", [page_id]).fetchall()
        html = decompressor.decompress(blob).decode("utf-8", "replace")
        new = parse_page(html, final_url or url, policy).links
        run.before += len(old)
        targets = []
        place = 0
        for link in new:
            key = (link.to_url, link.anchor, link.position, link.nofollow)
            while place < len(old) and (old[place][0], old[place][1], old[place][2],
                                        bool(old[place][3])) != key:
                place += 1
            if place == len(old):
                break
            targets.append(old[place][4])
            place += 1
        if len(targets) != len(new):
            run.mismatched.append(url)
            run.after += len(old)
            continue
        con.execute("DELETE FROM links WHERE from_page_id = ?", [page_id])
        if new:
            con.executemany(
                "INSERT INTO links (from_page_id, to_url, to_page_id, anchor, position, nofollow, "
                "ordinal, raw_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(page_id, link.to_url, target, link.anchor, link.position, link.nofollow,
                  link.ordinal, link.raw_url) for link, target in zip(new, targets, strict=True)])
        run.after += len(new)
        run.dropped += len(old) - len(new)
        run.with_raw_url += sum(1 for link in new if link.raw_url)
    return run
