-- 038: a linkek eredeti alakja, az átirányítási lánc és a linkelt alak mért válasza.
--
-- links.raw_url: a link href-jének abszolút alakja a normalizálás előtt, töredék nélkül (a
--   `to_url` a normalizált cél). NULL a korábbi soroknál, amíg a tárolt DOM-ból vissza nem
--   töltődik.
-- pages.redirect_hops: a végső válasz előtti HTTP-átirányítási lépések száma (NULL: nem mért);
--   pages.redirect_chain: a lépések sorrendben (JSON: `status`, `url`), NULL, ha nincs lépés.
-- link_variants: a linkelt, nem normalizált cím mért válasza (render nélküli kérés): státusz,
--   végső cím, a lépések száma és lánca. Csak élő bejárásnál töltődik; rögzített készleten üres.
-- findings.type új értéke: 'link_not_final_url' (belső link nem a végleges címre mutat). A
--   CHECK megszorítás nem módosítható helyben, ezért a tábla az új megszorítással épül újra, a
--   sorai változatlanok.

ALTER TABLE links ADD COLUMN IF NOT EXISTS raw_url VARCHAR;
ALTER TABLE pages ADD COLUMN IF NOT EXISTS redirect_hops INTEGER;
ALTER TABLE pages ADD COLUMN IF NOT EXISTS redirect_chain JSON;

CREATE TABLE IF NOT EXISTS link_variants (
    raw_url         VARCHAR PRIMARY KEY,
    normalized_url  VARCHAR NOT NULL,
    status          INTEGER,
    final_url       VARCHAR,
    hops            INTEGER,
    chain           JSON,
    error           VARCHAR,
    fetched_at      TIMESTAMP NOT NULL
);

CREATE TABLE findings_038 (
    finding_id  INTEGER PRIMARY KEY,
    type        VARCHAR NOT NULL CHECK (type IN (
                    'h1_title_mismatch', 'cannibalization', 'shared_topic', 'missing_page',
                    'uncovered_topic', 'unclear_topic', 'missing_h1', 'h1_outside_content',
                    'multiple_h1', 'empty_section', 'skipped_level', 'missing_h2',
                    'paragraph_heading', 'canonical_issue', 'legal_page', 'soft_404',
                    'schema_id_names', 'title_without_main_entity',
                    'article_markup_on_other_pages', 'breadcrumb_foreign_home',
                    'menu_home_target', 'menu_broken_target', 'orphan_pages',
                    'breadcrumb_ignores_menu', 'link_not_final_url')),
    severity    VARCHAR NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    page_id     INTEGER,
    entity_id   INTEGER,
    summary     VARCHAR NOT NULL,
    evidence    JSON NOT NULL
);

INSERT INTO findings_038
SELECT finding_id, type, severity, page_id, entity_id, summary, evidence
FROM findings ORDER BY finding_id;

DROP TABLE findings;

ALTER TABLE findings_038 RENAME TO findings;
