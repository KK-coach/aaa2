-- 019: a súlytábla bejövő anchorai külön: content_anchors (tartalmi, a links.position = body,
-- darabra) és nav_anchors (menü, lábléc, oldalsáv; forrás-oldalcsoportonként egyszer).

ALTER TABLE entity_weights RENAME COLUMN inbound_anchors TO content_anchors;
ALTER TABLE entity_weights ADD COLUMN nav_anchors INTEGER DEFAULT 0;
