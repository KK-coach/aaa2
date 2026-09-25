-- A site-profil célország- és piacihatókör-mezői, a crawl végén számolva.
-- site.target_country_confidence: high / medium / low, a célországot támogató különböző erős
--   jelekből (ccTLD, hreflang, telefon, schema-cím, megerősített og:locale) és az ütközésükből;
--   NULL, ha nincs célország.
-- site.target_country_candidates: JSON-lista, legfeljebb 3 elem, pontszám szerint csökkenő
--   sorrendben: {"country": "HU", "score": 0.6, "signals": ["path_prefix", "phone"]}.
--   A score normalizált (az összes ország pontszámának összege 1), a signals a hozzájáruló jelek.
-- site.market_scope: local / country_specific / international_global / mixed /
--   not_country_specific. mixed: város mellett ország- vagy nemzetközi jel is van; leírja,
--   nem oldja fel.
-- site.market_scope_city: a helyi jel városa (local és mixed esetén), különben NULL.
ALTER TABLE site ADD COLUMN IF NOT EXISTS target_country_confidence VARCHAR;
ALTER TABLE site ADD COLUMN IF NOT EXISTS target_country_candidates JSON;
ALTER TABLE site ADD COLUMN IF NOT EXISTS market_scope VARCHAR;
ALTER TABLE site ADD COLUMN IF NOT EXISTS market_scope_city VARCHAR;
