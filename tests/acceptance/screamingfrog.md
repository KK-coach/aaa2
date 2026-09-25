# Screaming Frog az elfogadáshoz (SF 24.3)

A `run_acceptance.py` az SF CLI-t (`ScreamingFrogSEOSpiderCli.exe --crawl … --headless --config …`) a `tests/acceptance/sf/` alatti konfigurációval indítja. A `.seospiderconfig` fájlt az SF felületén kell elmenteni (File → Configuration → Save As…); a CLI csak betölti.

Két fájl kell, mindkettő a repóba:

- `tests/acceptance/sf/aaa2-acceptance.seospiderconfig`: kk.coach és Materia;
- `tests/acceptance/sf/aaa2-acceptance-ngx.seospiderconfig`: ugyanez, és az include.

## Beállítások

Az `aaa crawl` alapértelmezéseihez igazítva. Kiindulás: az alapértelmezett konfiguráció (File → Configuration → Clear Default Configuration). A 24-es verzióban a pontok a Configuration menü Crawl Config ablakában vannak.

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
   - HTTP Request User-Agent: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36`. Ez az aaa UA-ja a Playwright Chromium 153-mal. Ha a Playwright frissül, a főverzió változik; a futtató a ténylegeset a `run.json`-ba és az `acceptance.md`-be írja, a konfigurációnak ezzel kell egyeznie.
   - Robots User-Agent: ugyanez. Így csak a `User-agent: *` csoport illeszkedik, mint az aaa-ban.
7. **Speed**
   - Max Threads: 6 (az aaa `CONCURRENCY`-je).
8. **Include**, csak az ngx-fájlban
   - `https://valor-software\.com/ngx-bootstrap/.*`

## Tárolási mód (rendszerbeállítás, nem a konfigurációs fájl része)

DB módban az SF legalább 4 GB szabad helyet kér a `%USERPROFILE%\.ScreamingFrogSEOSpider` meghajtóján; ennél kevesebbnél el sem indul („You do not have sufficient disk space to run the SEO Spider”). A három site kicsi (legfeljebb 100 URL), ehhez a memória-mód is elég: File → Settings → Storage Mode → Memory Storage.

## Futtatás

A CLI-vel, site-onként:

```
python -m tests.acceptance.run_acceptance materia --resume-check 5
python -m tests.acceptance.run_acceptance kk-coach --resume-check 10
python -m tests.acceptance.run_acceptance ngx --resume-check 20
```

Kézzel, ha a CLI nem használható:

1. SF-crawl a felületen ugyanezzel a konfigurációval;
2. Internal fül, HTML szűrő, Export → `internal_html.csv`;
3. rögtön utána, egy órán belül: `python -m tests.acceptance.run_acceptance materia --sf-csv <útvonal>`.
