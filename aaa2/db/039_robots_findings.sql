-- 039: két új megállapítás-típus a robots.txt-hez.
--
-- findings.type új értékei: 'robots_blocked_link' (belső link a robots.txt által tiltott címre
--   mutat), 'robots_bot_blocked' (a robots.txt egy vizsgált botot az egész site-ról vagy egy fő
--   szekcióról kitilt). A CHECK megszorítás nem módosítható helyben, ezért a tábla az új
--   megszorítással épül újra, a sorai változatlanok.
-- A crawl_queue.status új értéke 'robots_blocked' (a robots.txt miatt kihagyott, linkelt belső
--   cím; az `error` mezőben a csoport és a tiltó szabály): a státusz oszlopon nincs megszorítás,
--   ehhez séma-változás nem kell.

CREATE TABLE findings_039 (
    finding_id  INTEGER PRIMARY KEY,
    type        VARCHAR NOT NULL CHECK (type IN (
                    'h1_title_mismatch', 'cannibalization', 'shared_topic', 'missing_page',
                    'uncovered_topic', 'unclear_topic', 'missing_h1', 'h1_outside_content',
                    'multiple_h1', 'empty_section', 'skipped_level', 'missing_h2',
                    'paragraph_heading', 'canonical_issue', 'legal_page', 'soft_404',
                    'schema_id_names', 'title_without_main_entity',
                    'article_markup_on_other_pages', 'breadcrumb_foreign_home',
                    'menu_home_target', 'menu_broken_target', 'orphan_pages',
                    'breadcrumb_ignores_menu', 'link_not_final_url', 'robots_blocked_link',
                    'robots_bot_blocked')),
    severity    VARCHAR NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    page_id     INTEGER,
    entity_id   INTEGER,
    summary     VARCHAR NOT NULL,
    evidence    JSON NOT NULL
);

INSERT INTO findings_039
SELECT finding_id, type, severity, page_id, entity_id, summary, evidence
FROM findings ORDER BY finding_id;

DROP TABLE findings;

ALTER TABLE findings_039 RENAME TO findings;
