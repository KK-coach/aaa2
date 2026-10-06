-- 028: a sitemap tárolása és a crawl módja.
--
-- sitemap_files: a lekért sitemap-fájlok (a gyökerek és a sitemap index fájljai), fájlonként:
--   megvan-e (200-as válasz, amelyben van cím vagy sitemap index), index-e, hány címet adott.
-- sitemap_urls: a sitemap címei soronként: a nyers cím, a normalizált cím (a crawl címeivel
--   azonos normalizálással; NULL, ha nem normalizálható), a site címe-e, lastmod, melyik
--   fájlból jött.
-- snapshot: 'crawl' (a crawl idején, a crawl részeként tárolva) vagy 'refetch' (utólagos
--   lekérés a rögzített készlethez; a `fetched_at` a lekérés ideje).
-- source: honnan került elő a sitemap: 'given' (megadott cím), 'robots' (a robots.txt
--   Sitemap-sora), 'default' (alapútvonal), 'queue' (a crawl-sorból visszaállítva: a korábbi
--   crawl a sitemapet nem tárolta, csak a sorban maradt nyoma; nyers cím, fájl és lastmod nincs).
-- crawl_runs.mode: 'links' (link alapú bejárás) vagy 'sitemap' (sitemap-mód: a sorba csak a
--   seed és a sitemap címei kerülnek); a korábbi futásoknál NULL.

CREATE TABLE IF NOT EXISTS sitemap_files (
    snapshot        VARCHAR NOT NULL CHECK (snapshot IN ('crawl', 'refetch')),
    ordinal         INTEGER NOT NULL,
    url             VARCHAR NOT NULL,
    source          VARCHAR NOT NULL CHECK (source IN ('given', 'robots', 'default', 'queue')),
    found           BOOLEAN NOT NULL,
    is_index        BOOLEAN NOT NULL DEFAULT false,
    urls            INTEGER NOT NULL DEFAULT 0,
    fetched_at      TIMESTAMP,
    PRIMARY KEY (snapshot, ordinal)
);

CREATE TABLE IF NOT EXISTS sitemap_urls (
    snapshot        VARCHAR NOT NULL CHECK (snapshot IN ('crawl', 'refetch')),
    ordinal         INTEGER NOT NULL,
    raw_url         VARCHAR,
    url             VARCHAR,
    internal        BOOLEAN NOT NULL DEFAULT true,
    lastmod         VARCHAR,
    sitemap_file    VARCHAR,
    source          VARCHAR NOT NULL CHECK (source IN ('given', 'robots', 'default', 'queue')),
    fetched_at      TIMESTAMP,
    PRIMARY KEY (snapshot, ordinal)
);

ALTER TABLE crawl_runs ADD COLUMN IF NOT EXISTS mode VARCHAR;
