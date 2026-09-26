-- Az entitás-futások naplója és az LLM-újrapróba oka.
-- llm_calls.last_error: az utolsó sikertelen kísérlet hibája ("<HTTP-kód vagy kivétel>: <üzenet>");
--   NULL, ha nem kellett újrapróba.
-- entity_runs: futásonként egy sor. method: rules | llm. pages: a vizsgált (sikeres) oldalak,
--   pages_with_entities: ahonnan legalább egy sor jött, entities: a futás sorainak különböző
--   entitásai, row_count: a futás page_entities sorai, llm_calls: a futás LLM-hívásai.
--   by_position: sorok pozíciónként {"schema": n, "title": n, "h1": n, "anchor": n}.
--   skipped: a kimaradt jelöltek okonként (pl. {"unmapped_schema_types": {"WebPage": 13}}).
-- page_entities.source: rule | llm | schema (a JSON-LD-ből jött sor schema).

ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS last_error VARCHAR;

CREATE SEQUENCE IF NOT EXISTS seq_entity_run_id START 1;

CREATE TABLE IF NOT EXISTS entity_runs (
    run_id              INTEGER PRIMARY KEY DEFAULT nextval('seq_entity_run_id'),
    started_at          TIMESTAMP NOT NULL,
    finished_at         TIMESTAMP,
    method              VARCHAR NOT NULL,          -- rules | llm
    pages               INTEGER,
    pages_with_entities INTEGER,
    entities            INTEGER,
    row_count           INTEGER,
    llm_calls           INTEGER NOT NULL DEFAULT 0,
    by_position         JSON,
    skipped             JSON
);
