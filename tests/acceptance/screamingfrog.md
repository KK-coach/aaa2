# Screaming Frog az elfogadáshoz (SF 24.3)

A `run_acceptance.py` az SF CLI-t (`ScreamingFrogSEOSpiderCli.exe --crawl … --headless --save-crawl --config …`) a site konfigurációjával indítja. A `.seospiderconfig` Java-szerializált bináris; csak az SF felületén menthető (File → Configuration → Save As…), a CLI csak betölti. A repóban `binary` (`.gitattributes`).

Két fájl, a `tests/acceptance/` alatt:

- **`aaa2-acceptance.seospiderconfig`:** a GUI-ból mentett; kk.coach és Materia.
- **`aaa2-acceptance-ngx.seospiderconfig`:** a `make_ngx_config.py` generálja a közösből, a futtató indulás előtt is.
  - Az SF CLI-nek nincs include-kapcsolója. A fájlban az include-lista (`SpiderInternalURLConfig.mInternalRegexes`) elemének beírása a szerializáció belső hivatkozásait eltolná.
  - Ezért két logikai mezőt állít hamisra: `SpiderInternalURLConfig.mCrawlOutsideStartFolder` és `SpiderCrawlConfig.mCheckLinksOutsideFolder`.
  - Az ngx seedjének kezdő mappája `/ngx-bootstrap/`, így a crawl ugyanarra szűkül, mint az aaa `--include /ngx-bootstrap/`-ja.
  - A fájl pontosan ebben a két bájtban tér el a közöstől.

Ellenőrzés mentés után, helyi próba-site-on, kérésnaplóval (`verify_sf_config.py`):

```
python -m tests.acceptance.verify_sf_config tests/acceptance/aaa2-acceptance.seospiderconfig
python -m tests.acceptance.verify_sf_config tests/acceptance/aaa2-acceptance-ngx.seospiderconfig --ngx
```

## Beállítások

Az `aaa crawl` alapértelmezéseihez igazítva. A 24-es verzióban a pontok a Configuration menü Crawl Config ablakában vannak.

1. **Spider → Rendering**
   - Rendering: JavaScript.
   - Window Size: egyéni, 1920 × 1080 (az aaa nézetmérete).
   - AJAX Timeout: 5 mp (alapérték).
2. **Spider → Crawl**
   - Crawl Outside of Start Folder: be. Az aaa nem szűkít mappára; az ngx-változat szűkít (lásd fent).
   - Crawl All Subdomains: ki. Az összevető a seed hostjára szűkít.
   - Follow Internal "nofollow": be. Az aaa a nofollow belső linket is követi.
   - Crawl Linked XML Sitemaps: be; Auto Discover XML Sitemaps via robots.txt: be. Az aaa a robots.txt Sitemap-soraiból indul; mindhárom site robots.txt-jében van ilyen sor.
   - Hreflang: Store és Crawl be.
   - Canonicals: Store és Crawl be.
3. **Spider → Limits**
   - Limit Crawl Total: 5000 (az aaa `MAX_PAGES`-e).
4. **Spider → Advanced**
   - Always Follow Redirects: be.
   - Respect Noindex, Respect Canonical, Respect Next/Prev: ki (alapérték).
5. **robots.txt**
   - Respect robots.txt (alapérték; az aaa is tiszteli).
6. **User-Agent**: egyéni (Custom).
   - HTTP Request User-Agent: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36`. Ez az aaa UA-ja a Playwright Chromium 153-mal. Ha a Playwright frissül, a főverzió változik; a `verify_sf_config.py` az aktuálishoz méri.
   - Robots User-Agent: ugyanez. Így csak a `User-agent: *` csoport illeszkedik, mint az aaa-ban.
7. **Speed**
   - Max Threads: 6 (az aaa `CONCURRENCY`-je).

## Ellenőrzések

- **2026-09-25-én mentett változat:**
  - a UA `Screaming Frog SEO Spider/24.3` volt;
  - a nofollow linket nem követte;
  - a sitemapet nem olvasta.
- **2026-09-26-án újramentett változat** (`C:\Users\donm6\AAA-v2\tests\acceptance\SEO Spider Config.seospiderconfig`, 38 422 bájt): a `verify_sf_config.py` mind a 8 pontja rendben, a UA bájtra egyezik.
- **Az ngx-változat** (`--ngx`): a `/a/`-ból indítva csak a `/a/`, `/a/sub/`, `/robots.txt` és `/sitemap.xml` kérés ment ki.
  - Csak az első mező átírásával még lekérte a mappán kívüli linkeket (`/`, `/b/`, `/canon/`).
  - Ezt a második mező zárja.

## Tárolási mód

DB módban az SF legalább 4 GB szabad helyet kér a `%USERPROFILE%\.ScreamingFrogSEOSpider` meghajtóján; ennél kevesebbnél el sem indul („You do not have sufficient disk space…”). A három site kicsi, a memória-mód is elég: File → Settings → Storage Mode.

## Futtatás

```
python -m tests.acceptance.run_acceptance all --resume-test 5
```

Kézzel, ha a CLI nem használható:

1. SF-crawl a felületen ugyanezzel a konfigurációval;
2. Internal fül, HTML szűrő, Export → `internal_html.csv`;
3. rögtön utána: `python -m tests.acceptance.run_acceptance materia --sf-csv <útvonal>`.
