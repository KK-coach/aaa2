-- 037: három új megállapítás-típus.
--
-- findings.type új értékei: 'menu_broken_target' (a menü hibás oldalra mutat), 'orphan_pages'
--   (árva oldalak: csak a sitemapből ismertek, illetve csak egymásra linkelők),
--   'breadcrumb_ignores_menu' (a morzsa nem követi a menü hierarchiáját).
-- A CHECK megszorítás nem módosítható helyben, ezért a tábla az új megszorítással épül újra, a
--   sorai változatlanok.

CREATE TABLE findings_037 (
    finding_id  INTEGER PRIMARY KEY,
    type        VARCHAR NOT NULL CHECK (type IN (
                    'h1_title_mismatch', 'cannibalization', 'shared_topic', 'missing_page',
                    'uncovered_topic', 'unclear_topic', 'missing_h1', 'h1_outside_content',
                    'multiple_h1', 'empty_section', 'skipped_level', 'missing_h2',
                    'paragraph_heading', 'canonical_issue', 'legal_page', 'soft_404',
                    'schema_id_names', 'title_without_main_entity',
                    'article_markup_on_other_pages', 'breadcrumb_foreign_home',
                    'menu_home_target', 'menu_broken_target', 'orphan_pages',
                    'breadcrumb_ignores_menu')),
    severity    VARCHAR NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    page_id     INTEGER,
    entity_id   INTEGER,
    summary     VARCHAR NOT NULL,
    evidence    JSON NOT NULL
);

INSERT INTO findings_037
SELECT finding_id, type, severity, page_id, entity_id, summary, evidence
FROM findings ORDER BY finding_id;

DROP TABLE findings;

ALTER TABLE findings_037 RENAME TO findings;
