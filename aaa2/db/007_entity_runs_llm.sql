-- Az LLM-es entitás-kör mérőszámai.
-- llm_calls.fabricated_count: a hívás eldobott sorai, mert a bizonyíték nincs szó szerint a
--   main contentben, a headingekben vagy a title-ben; NULL, ha a hívás nem entitás-kinyerés.
-- entity_runs.model: az LLM-futás modellje (rules futásnál NULL).
-- entity_runs.cost_usd: a futás LLM-hívásainak költsége.
-- entity_runs.fabricated: a futás eldobott, fabrikált sorai.
-- Oldalankénti arány (hívás, USD, sor, fabrikált / oldal): a pages oszlophoz viszonyítva.

ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS fabricated_count INTEGER;
ALTER TABLE entity_runs ADD COLUMN IF NOT EXISTS model VARCHAR;
ALTER TABLE entity_runs ADD COLUMN IF NOT EXISTS cost_usd DOUBLE;
ALTER TABLE entity_runs ADD COLUMN IF NOT EXISTS fabricated INTEGER;
