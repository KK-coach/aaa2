# AAA v2

Sitewide SEO/GEO elemzőmotor. A mag: oldalak × entitások × belső linkek gráfja egy DuckDB-fájlban site-onként. Lokálban fut, CLI-ből.

A terv és a döntések a projektindítóban vannak (Claude-dokumentum, AAA v2 projekt): cél és keret, mit hozunk át a v1-ből, architektúra, adatmodell, modulterv stop-feltételekkel, stack. Az M1 részletes specje ugyanott külön tab.

## Rétegek

- `aaa2/engine/` — crawl motor: Playwright-render mindenre, parser, site-profil. Csak a DuckDB-be ír.
- `aaa2/entities/` — entitás-réteg: szabály-kör, LLM-kör, KG- és Wikipedia-validálás. A motor tábláiból olvas, az entitás-táblákat tölti; a motorból csak a `parse` két segédfüggvényét importálja, a motor semmit innen.
- `aaa2/resolver/` — szabály-vagy-LLM osztályozó, szabály-életciklussal (M4).
- `aaa2/functions/` — gráf, site-profil, entity gap. Csak SQL a DuckDB-n.
- `aaa2/llm/` — modellfüggetlen LLM-kliens, sémák, költségkönyvelés (`llm_calls` tábla).
- `aaa2/db/` — séma (`001_init.sql`), kapcsolat, migrációk.
- `aaa2/cli/` — Typer parancsok: `aaa crawl`, `aaa entities`, `aaa validate`, `aaa status`, `aaa models`, `aaa export`.
- `legacy/` — a v1 (`aaa-dev`) átvehető részei, csak referenciának. Nem importáljuk; darabokat emelünk át, amikor az adott modul odaér.
- `tests/fixtures/` — rögzített renderelt válaszok a három referencia-site-ról (gitignore-olva, lokálisan felvéve).
- `data/` — `<domain>.duckdb` és `shared.duckdb` (gitignore-olva).

## Indulás

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]" -c constraints.txt
playwright install chromium
pytest -m "not slow" -n auto
aaa --help
```

A `constraints.txt` a tesztelt függőségverziókat rögzíti (tiszta környezetben, Windows,
Python 3.14: a gyors tesztsor ezekkel fut le); nélküle a `pyproject.toml` tartományai szerint a
legfrissebb verziók települnek. A `selectolax` felső korlátja (`<1`) a `pyproject.toml`-ban is
áll: az 1.0 megszünteti a kód által használt `selectolax.parser.HTMLParser` osztályt. A
`httpx[socks]` extra a SOCKS-proxy mögötti futáshoz kell. A CI (`.github/workflows/ci.yml`)
ugyanezt a telepítést és a gyors tesztsort futtatja tiszta gépen; a felvételt visszajátszó
és az élő tesztek kimaradnak.

## Konvenciók

- Egy modul csak a `db/` sémán keresztül beszél a másikkal.
- Minden LLM-hívás az `aaa2/llm/` csomagon megy át és sort ír az `llm_calls` táblába.
- Kommentben nincs ticket-hivatkozás.
- Mért tény és LLM-ítélet külön van címkézve; az LLM sosem talál ki számot.

## Modulok és stop-feltételek

| # | Modul | Kész, ha | Stop |
| --- | --- | --- | --- |
| M1 | Crawl motor | 3 referencia-site végig, linkgráf teljes a Screaming Frog baseline ellen, `--resume` működik | kezdés + 2 hét |
| M2 | Entity-kinyerés (buta) | fabrikált entitás = 0, LLM-hívás/oldal mérve | fabrikáció > 2% |
| M3 | Gráf-funkciók | egy valódi auditban hasznosítva | nem mond újat a kézi elemzéshez képest |
| M4 | Resolver | LLM-hívás/oldal < 20%-ra esik egy 500+ oldalas site-on | az arány nem esik |
| M5 | Konkurencia | entity gap egyezik a kézi elemzéssel | 3-ból 2-nél kézi javítás kell |

Referencia-site-ok (mind 100 oldal alatt): kk.coach (Astro, kétnyelvű, Zaraz-consent), Materia Trattoria (WP, consent, részleges JS-linkek), ngx-bootstrap a `/ngx-bootstrap/` include-dal (tiszta CSR, Angular). Mérések és seedek: CLAUDE.md.
