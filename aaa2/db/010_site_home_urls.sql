-- A kezdőoldalak a site-profilból, hogy az entitás-réteg a sémából olvassa, ne a motorból.
-- site.home_urls: a seed oldal (az átirányításait követve) és a sikeres hreflang-alternatívái,
--   normalizált URL-ként; a crawl végén a site-profil írja. NULL, ha a crawl ennél régebbi.

ALTER TABLE site ADD COLUMN IF NOT EXISTS home_urls VARCHAR[];
