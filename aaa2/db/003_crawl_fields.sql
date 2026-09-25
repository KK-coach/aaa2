-- A crawl (összefűzés) mezői.
-- pages.final_url: a ténylegesen kiszolgált URL (átirányítás és JS-navigáció után), nyersen.
--   Ha eltér a sor url-jétől, a normalizált alak és a site valódi alakja nem ugyanaz.
-- pages.main_content_method: a main content kinyerésének stratégiája
--   (semantic_main | semantic_article | role_main | content_selector | readability | fallback_body).
-- site.robots_txt, site.robots_status: a seed hostjának robots.txt-je és HTTP-státusza a crawl
--   elején; a szöveg csak 200-nál, a státusz NULL, ha nem volt elérhető.
ALTER TABLE pages ADD COLUMN IF NOT EXISTS final_url VARCHAR;
ALTER TABLE pages ADD COLUMN IF NOT EXISTS main_content_method VARCHAR;
ALTER TABLE site ADD COLUMN IF NOT EXISTS robots_txt VARCHAR;
ALTER TABLE site ADD COLUMN IF NOT EXISTS robots_status INTEGER;
