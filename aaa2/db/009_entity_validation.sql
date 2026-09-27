-- Az entitások validálása: Google Knowledge Graph és Wikipedia.
-- entities.kg_type: a KG-találat típusa a tíz entitás-típusra leképezve (config/kg_types.toml);
--   NULL, ha nincs találat, vagy a KG-típus nem képezhető le (csak "Thing").
-- entities.kg_type_mismatch: a KG-típus eltér az entitás típusától, és a KG nem is állította át
--   (nem egyezik a type_suggested-del, vagy a találat nem high / medium).
-- entities.type_changed_from: a korábbi típus, ha a KG átállította (a KG-típus = type_suggested,
--   high / medium találat); ez az egyetlen út a típusváltásra.
-- entities.validated_at: az utolsó validálás ideje.
-- validation_cache: a KG- és Wikipedia-válaszok a kérés kanonikus URL-je szerint (az API-kulcs
--   nélkül). A site-adatbázisban minden sikeres válasz, a shared.duckdb-ben csak a találatok;
--   ismételt futásnál innen jön a válasz, nincs újrahívás.
-- validation_calls: minden kimenő API-kérés (kvóta, rate-limit, hibák): a szolgáltatás, a kérés,
--   a HTTP-státusz, a kísérletek száma, a késleltetés, a hiba.

ALTER TABLE entities ADD COLUMN IF NOT EXISTS kg_type VARCHAR;
ALTER TABLE entities ADD COLUMN IF NOT EXISTS kg_type_mismatch BOOLEAN;
ALTER TABLE entities ADD COLUMN IF NOT EXISTS type_changed_from VARCHAR;
ALTER TABLE entities ADD COLUMN IF NOT EXISTS validated_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS validation_cache (
    service         VARCHAR NOT NULL,              -- kg | wikipedia
    request_key     VARCHAR NOT NULL,              -- kanonikus URL, API-kulcs nélkül
    response        JSON NOT NULL,
    fetched_at      TIMESTAMP NOT NULL,
    PRIMARY KEY (service, request_key)
);

CREATE SEQUENCE IF NOT EXISTS seq_validation_call_id START 1;

CREATE TABLE IF NOT EXISTS validation_calls (
    call_id         INTEGER PRIMARY KEY DEFAULT nextval('seq_validation_call_id'),
    service         VARCHAR NOT NULL,              -- kg | wikipedia
    request_key     VARCHAR NOT NULL,
    status          INTEGER,                       -- HTTP-státusz; NULL kapcsolati hibánál
    attempts        INTEGER NOT NULL DEFAULT 1,
    latency_ms      INTEGER,
    error           VARCHAR,
    called_at       TIMESTAMP NOT NULL
);
