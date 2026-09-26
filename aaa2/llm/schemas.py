"""Az LLM-sémák. Az `EntityType` értékei azonosak az `entities.type` CHECK-jével
(005_entities_llm.sql)."""
from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field

EntityType = Literal[
    "brand", "product", "service", "work", "event", "person", "org", "place", "tech", "concept",
]
ENTITY_TYPES: tuple[str, ...] = get_args(EntityType)


class ExtractedEntity(BaseModel):
    name: str = Field(description="Canonical name, written as on the page, not translated.")
    type: EntityType
    evidence: str = Field(description="Verbatim quote of 3 to 15 words from the page text that "
                                      "mentions the entity.")
    context: str = Field(description="The paragraph or list item that contains the evidence, "
                                     "copied from the page.")


class PageExtraction(BaseModel):
    entities: list[ExtractedEntity]
