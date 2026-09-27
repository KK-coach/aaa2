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
    description: str = Field(description="What this entity is in this text, in one short "
                                         "sentence.")
    evidence: str = Field(description="A quote copied verbatim from the page text that contains "
                                      "the name.")
    context: str = Field(description="The paragraph or list item that contains the evidence, "
                                     "copied from the page.")


class PageExtraction(BaseModel):
    primary_entity: str = Field(description="The entity the page is mainly about, as written on "
                                            "the page; it may be a common noun.")
    entities: list[ExtractedEntity]
