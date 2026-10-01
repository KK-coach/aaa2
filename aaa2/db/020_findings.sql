-- 020: SEO-megállapítások az entitásgráfból (M3 spec, 4. pont; M3/2).
--
-- findings: típus (h1_title_mismatch, cannibalization, missing_page, unclear_topic), súlyosság
--   (high / medium / low), az érintett oldal és entitás (ha egy van), rövid összefoglaló, és a
--   bizonyíték (érintett oldalak, H1 és title, említések). Minden futás újraépíti.

CREATE SEQUENCE IF NOT EXISTS seq_finding_id START 1;

CREATE TABLE IF NOT EXISTS findings (
    finding_id  INTEGER PRIMARY KEY DEFAULT nextval('seq_finding_id'),
    type        VARCHAR NOT NULL CHECK (type IN ('h1_title_mismatch', 'cannibalization',
                                                 'missing_page', 'unclear_topic')),
    severity    VARCHAR NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    page_id     INTEGER,
    entity_id   INTEGER,
    summary     VARCHAR NOT NULL,
    evidence    JSON NOT NULL
);
