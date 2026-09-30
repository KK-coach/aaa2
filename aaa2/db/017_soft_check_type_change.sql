-- 017: a service-jelölt típusváltása a jelöltnaplóban (M2/7).
--
-- soft_checks.type_changed_from / type_change_reason: a concept-sor az elvetett service-jelöltből
--   jött (`service`); az ok `no_structure` (nincs szerkezeti helye) vagy `sol_veto` (az ellenőrző
--   hívás vétózta). A service-sor ilyenkor a concept-entitásra mutat.

ALTER TABLE soft_checks ADD COLUMN IF NOT EXISTS type_changed_from VARCHAR;
ALTER TABLE soft_checks ADD COLUMN IF NOT EXISTS type_change_reason VARCHAR;
