-- 027: új megállapítás-típusok és a más típusú oldalra mutató canonical.
--
-- findings.type új értékei: 'paragraph_heading' (bekezdés headingként jelölve: 12 szónál
--   hosszabb, üres szakaszú heading), 'canonical_issue' (hibás canonical), 'legal_page' (webshopon
--   hiányzó vagy a láblécből nem elérhető jogi oldal), 'soft_404' (200-as státusszal
--   kiszolgált „nem található” oldal).
-- page_nodes.canonical_issue új értéke: 'other_type' (a canonical célja más szerepű oldal, az
--   oldal nem duplikátum, külön marad).
-- A CHECK megszorítás nem módosítható helyben, ezért mindkét tábla az új megszorítással épül
--   újra, a soraik változatlanok.

CREATE TABLE findings_027 (
    finding_id  INTEGER PRIMARY KEY,
    type        VARCHAR NOT NULL CHECK (type IN (
                    'h1_title_mismatch', 'cannibalization', 'shared_topic', 'missing_page',
                    'uncovered_topic', 'unclear_topic', 'missing_h1', 'h1_outside_content',
                    'multiple_h1', 'empty_section', 'skipped_level', 'missing_h2',
                    'paragraph_heading', 'canonical_issue', 'legal_page', 'soft_404')),
    severity    VARCHAR NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    page_id     INTEGER,
    entity_id   INTEGER,
    summary     VARCHAR NOT NULL,
    evidence    JSON NOT NULL
);

INSERT INTO findings_027
SELECT finding_id, type, severity, page_id, entity_id, summary, evidence
FROM findings ORDER BY finding_id;

DROP TABLE findings;

ALTER TABLE findings_027 RENAME TO findings;

CREATE TABLE page_nodes_027 (
    page_id         INTEGER PRIMARY KEY,
    url             VARCHAR NOT NULL,
    role            VARCHAR NOT NULL,
    support_kind    VARCHAR,
    title           VARCHAR,
    h1              VARCHAR,
    lang            VARCHAR,
    group_key       VARCHAR NOT NULL,
    hreflang_pages  VARCHAR[],
    main_status     VARCHAR NOT NULL CHECK (main_status IN ('main', 'support', 'none')),
    canonical_page  INTEGER,
    canonical_issue VARCHAR CHECK (canonical_issue IN ('not_crawled', 'error_status',
                                                       'not_a_node', 'loop', 'other_type')),
    decision        JSON
);

-- BY NAME: a közös adatbázis (`shared.duckdb`) page_nodes táblája régebbi alakú (a canonical
-- oszlopok nélkül, üresen); a hiányzó oszlop NULL marad.
INSERT INTO page_nodes_027 BY NAME SELECT * FROM page_nodes ORDER BY page_id;

DROP TABLE page_nodes;

ALTER TABLE page_nodes_027 RENAME TO page_nodes;
