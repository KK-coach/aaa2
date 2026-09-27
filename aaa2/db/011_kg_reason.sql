-- A KG-státusz oka, ha nem a KG-válasz adta.
-- entities.kg_reason: navigational — concept, amelynek minden page_entities sora anchor vagy
--   title; a validálás előtt stub lesz, KG- és Wikipedia-hívás nélkül. NULL, ha a státuszt a
--   KG-válasz adta (a következő rendes validálás törli).

ALTER TABLE entities ADD COLUMN IF NOT EXISTS kg_reason VARCHAR;
