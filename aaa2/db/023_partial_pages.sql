-- 023: részleges kinyerés az oldalnaplóban.
--
-- entity_run_pages.status új értéke: 'partial' (az oldal darabjai közül legalább egy minden
--   újrapróbálkozás után hibás, de van sikeres darab; a rekord `failed_chunks` mezője a hibás
--   darabok sorszáma). A `partial` oldal nem végleges: a folytatás a hibás darabokat újra
--   kéri, az újrahasználat nem veszi át. A CHECK megszorítás nem módosítható helyben, ezért a
--   tábla az új megszorítással épül újra, a sorai változatlanok.

CREATE TABLE entity_run_pages_023 (
    run_id          INTEGER NOT NULL,
    page_id         INTEGER NOT NULL,
    status          VARCHAR NOT NULL CHECK (status IN (
                        'done', 'extracted', 'partial', 'failed', 'verify_error', 'skipped',
                        'stopped')),
    reasons         JSON,                          -- ok → darab
    call_ids        INTEGER[],
    chunks          INTEGER,
    fabricated      INTEGER,
    seconds         DOUBLE,
    error           VARCHAR,
    extraction      JSON,
    refined         JSON,
    finished_at     TIMESTAMP,
    raw_html_hash   VARCHAR,
    input_hash      VARCHAR,
    PRIMARY KEY (run_id, page_id)
);

INSERT INTO entity_run_pages_023
SELECT run_id, page_id, status, reasons, call_ids, chunks, fabricated, seconds, error,
       extraction, refined, finished_at, raw_html_hash, input_hash
FROM entity_run_pages ORDER BY run_id, page_id;

DROP TABLE entity_run_pages;

ALTER TABLE entity_run_pages_023 RENAME TO entity_run_pages;
