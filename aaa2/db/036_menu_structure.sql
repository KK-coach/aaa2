-- 036: a site-szintű menüfa és két új megállapítás-típus.
--
-- menu_items: a site menüfája nyelvenként és területenként (fejléc / lábléc / oldalsáv), a
--   tárolt DOM menüpontjaiból összesítve. `url`: a menüpont címe (NULL: link nélküli
--   szülő-címke); `page_id`: a készletbeli oldal, ha van; `parent_id`: a szülő menüpont;
--   `level`: 0 = felső szint; `marker`: 'logo' / 'home_icon'; `pages`: hány oldalon áll;
--   `area_pages`: a nyelv hány oldalán áll a terület.
-- menu_page_differences: az oldalfüggő eltérések a site-szintű fától: 'extra' (az oldalon áll,
--   a fában nem) és 'missing' (a fában áll, az oldalon nem). `url`: a menüpont címe vagy a
--   link nélküli címke; `parent`: a szülő címe vagy címkéje.
-- findings.type új értékei: 'breadcrumb_foreign_home' (a morzsa kezdőpontja más nyelvű
--   kezdőoldalra mutat), 'menu_home_target' (a menü Home pontja nem a kezdőoldalra mutat).
--   A CHECK megszorítás nem módosítható helyben, ezért a tábla az új megszorítással épül újra,
--   a sorai változatlanok.

CREATE TABLE IF NOT EXISTS menu_items (
    item_id     INTEGER PRIMARY KEY,
    lang        VARCHAR NOT NULL,
    area        VARCHAR NOT NULL CHECK (area IN ('header', 'footer', 'sidebar')),
    ordinal     INTEGER NOT NULL,
    url         VARCHAR,
    page_id     INTEGER,
    anchor      VARCHAR NOT NULL,
    parent_id   INTEGER,
    level       INTEGER NOT NULL,
    marker      VARCHAR CHECK (marker IN ('logo', 'home_icon')),
    pages       INTEGER NOT NULL,
    area_pages  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS menu_page_differences (
    page_id     INTEGER NOT NULL,
    lang        VARCHAR NOT NULL,
    area        VARCHAR NOT NULL CHECK (area IN ('header', 'footer', 'sidebar')),
    kind        VARCHAR NOT NULL CHECK (kind IN ('extra', 'missing')),
    url         VARCHAR NOT NULL,
    anchor      VARCHAR NOT NULL,
    parent      VARCHAR
);

CREATE TABLE findings_036 (
    finding_id  INTEGER PRIMARY KEY,
    type        VARCHAR NOT NULL CHECK (type IN (
                    'h1_title_mismatch', 'cannibalization', 'shared_topic', 'missing_page',
                    'uncovered_topic', 'unclear_topic', 'missing_h1', 'h1_outside_content',
                    'multiple_h1', 'empty_section', 'skipped_level', 'missing_h2',
                    'paragraph_heading', 'canonical_issue', 'legal_page', 'soft_404',
                    'schema_id_names', 'title_without_main_entity',
                    'article_markup_on_other_pages', 'breadcrumb_foreign_home',
                    'menu_home_target')),
    severity    VARCHAR NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    page_id     INTEGER,
    entity_id   INTEGER,
    summary     VARCHAR NOT NULL,
    evidence    JSON NOT NULL
);

INSERT INTO findings_036
SELECT finding_id, type, severity, page_id, entity_id, summary, evidence
FROM findings ORDER BY finding_id;

DROP TABLE findings;

ALTER TABLE findings_036 RENAME TO findings;
