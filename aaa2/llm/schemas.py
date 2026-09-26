"""Az LLM-sémák közös típusai. Az `EntityType` értékei azonosak az `entities.type` CHECK-jével
(005_entities_llm.sql)."""
from __future__ import annotations

from typing import Literal, get_args

EntityType = Literal[
    "brand", "product", "service", "work", "event", "person", "org", "place", "tech", "concept",
]
ENTITY_TYPES: tuple[str, ...] = get_args(EntityType)
