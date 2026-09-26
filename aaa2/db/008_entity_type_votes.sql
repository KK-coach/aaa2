-- Az LLM típusjavaslatai. A típus (entities.type) ettől nem változik; az M2/4 a KG alapján dönt.
-- entities.type_votes: JSON, típus → szavazatszám; az LLM-kör minden elfogadott sora egy szavazat
--   a saját típusára, futásról futásra halmozódva.
-- entities.type_suggested: a legtöbb szavazatot kapott típus; holtversenyben a jelenlegi típus,
--   különben az entities.type felsorolásának sorrendje. NULL, ha nincs szavazat.

ALTER TABLE entities ADD COLUMN IF NOT EXISTS type_suggested VARCHAR;
ALTER TABLE entities ADD COLUMN IF NOT EXISTS type_votes JSON;
