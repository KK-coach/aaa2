-- 034: új megállapítás-típus.
--
-- findings.type új értéke: 'article_markup_on_other_pages' (a site cikk-jelölést tesz olyan
--   oldalakra is, amelyek nem cikkek; egy megállapítás a site-ra).
-- A CHECK megszorítás nem módosítható helyben, ezért a tábla az új megszorítással épül újra, a
--   sorai változatlanok.

CREATE TABLE findings_034 (
    finding_id  INTEGER PRIMARY KEY,
    type        VARCHAR NOT NULL CHECK (type IN (
                    'h1_title_mismatch', 'cannibalization', 'shared_topic', 'missing_page',
                    'uncovered_topic', 'unclear_topic', 'missing_h1', 'h1_outside_content',
                    'multiple_h1', 'empty_section', 'skipped_level', 'missing_h2',
                    'paragraph_heading', 'canonical_issue', 'legal_page', 'soft_404',
                    'schema_id_names', 'title_without_main_entity',
                    'article_markup_on_other_pages')),
    severity    VARCHAR NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    page_id     INTEGER,
    entity_id   INTEGER,
    summary     VARCHAR NOT NULL,
    evidence    JSON NOT NULL
);

INSERT INTO findings_034
SELECT finding_id, type, severity, page_id, entity_id, summary, evidence
FROM findings ORDER BY finding_id;

DROP TABLE findings;

ALTER TABLE findings_034 RENAME TO findings;
