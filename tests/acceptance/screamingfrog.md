# Screaming Frog az elfogadáshoz (SF 24.3)

A `run_acceptance.py` az SF CLI-t (`ScreamingFrogSEOSpiderCli.exe --crawl … --headless --save-crawl --config …`) a site konfigurációjával indítja. A `.seospiderconfig` Java-szerializált bináris; csak az SF felületén menthető (File → Configuration → Save As…), a CLI csak betölti. A repóban `binary` (`.gitattributes`).

Két fájl kell, a `tests/acceptance/` alatt:

- `aaa2-acceptance.seospiderconfig`: kk.coach és Materia;
- `aaa2-acceptance-ngx.seospiderconfig`: ugyanez, és az include.

Ellenőrzés mentés után, helyi próba-site-on (lásd `verify_sf_config.py`):

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
   - Crawl Outside of Start Folder: be. Az aaa nem szűkít mappára; az ngx-et az include szűkíti.
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
8. **Include**, csak az ngx-fájlban
   - `https://valor-software\.com/ngx-bootstrap/.*`

## A 2026-09-25-én mentett konfiguráció ellenőrzése

A fájl a `C:\Users\donm6\AAA-v2\tests\acceptance\` alá került („SEO Spider Config.seospiderconfig”), onnan másolva `aaa2-acceptance.seospiderconfig` néven. A próba-site-on:

- **rendben:** JS-render, robots.txt, hreflang- és canonical-crawl, a kezdő mappán kívülre is megy;
- **eltér:**
  - a UA `Screaming Frog SEO Spider/24.3`, nem az aaa-é;
  - a nofollow belső linket nem követi;
  - a sitemapet nem olvassa (a `sitemap.xml`-t le sem kéri);
- **hiányzik:** az include-os ngx-változat. E nélkül az ngx-crawl az egész valor-software.com-ot bejárná, mert a kezdő mappán kívülre is megy.

## Tárolási mód

DB módban az SF legalább 4 GB szabad helyet kér a `%USERPROFILE%\.ScreamingFrogSEOSpider` meghajtóján; ennél kevesebbnél el sem indul („You do not have sufficient disk space…”). A három site kicsi, a memória-mód is elég: File → Settings → Storage Mode.

## Futtatás

```
python -m tests.acceptance.run_acceptance all --resume-test 5
```

Kézzel, ha a CLI nem használható: SF-crawl a felületen ugyanezzel a konfigurációval; Internal fül, HTML szűrő, Export → `internal_html.csv`; rögtön utána, egy órán belül: `python -m tests.acceptance.run_acceptance materia --sf-csv <útvonal>`.
