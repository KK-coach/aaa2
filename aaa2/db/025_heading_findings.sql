-- 025: a heading-fa szerkezeti megállapításai.
--
-- findings.type új értékei: 'missing_h1' (nincs H1), 'h1_outside_content' (H1 a fő tartalmon
--   kívül), 'multiple_h1' (több H1 a fő tartalomban, indokolatlanul), 'empty_section' (üres
--   szakasz), 'skipped_level' (kihagyott heading-szint), 'missing_h2' (hosszú tartalom H2
--   nélkül). A CHECK megszorítás nem módosítható helyben, ezért a tábla az új megszorítással
--   épül újra, a sorai változatlanok. Az azonosítót a `build_findings` adja (1-től), a
--   szekvencia alapértéke nem kell.

CREATE TABLE findings_025 (
    finding_id  INTEGER PRIMARY KEY,
    type        VARCHAR NOT NULL CHECK (type IN (
                    'h1_title_mismatch', 'cannibalization', 'shared_topic', 'missing_page',
                    'uncovered_topic', 'unclear_topic', 'missing_h1', 'h1_outside_content',
                    'multiple_h1', 'empty_section', 'skipped_level', 'missing_h2')),
    severity    VARCHAR NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    page_id     INTEGER,
    entity_id   INTEGER,
    summary     VARCHAR NOT NULL,
    evidence    JSON NOT NULL
);

INSERT INTO findings_025
SELECT finding_id, type, severity, page_id, entity_id, summary, evidence
FROM findings ORDER BY finding_id;

DROP TABLE findings;

ALTER TABLE findings_025 RENAME TO findings;
