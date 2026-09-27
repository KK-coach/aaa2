"""Az LLM-sémák. Az `EntityType` értékei azonosak az `entities.type` CHECK-jével
(005_entities_llm.sql)."""
from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field

EntityType = Literal[
    "brand", "product", "service", "work", "event", "person", "org", "place", "tech", "concept",
]
ENTITY_TYPES: tuple[str, ...] = get_args(EntityType)

# Típusonként a zárt altípus-lista (személynél nincs altípus).
SUBTYPE_VOCABULARY: dict[str, tuple[str, ...]] = {
    "service": ("audit", "implementation", "consulting", "reporting", "training", "other"),
    "org": ("company", "restaurant", "institution", "team", "other"),
    "tech": ("software", "platform", "library", "framework", "language", "package", "component",
             "api_symbol", "api", "standard", "feature", "other"),
    "product": ("dish", "ingredient", "wine", "drink", "physical_good", "other"),
    "concept": ("method", "discipline", "metric", "ui_pattern", "license", "designation", "other"),
    "place": ("city", "region", "country", "venue", "other"),
    "work": ("article", "case_study", "book", "course", "other"),
    "event": ("conference", "webinar", "festival", "other"),
    "brand": ("other",),
    "person": (),
}
Subtype = Literal[
    "audit", "implementation", "consulting", "reporting", "training", "company", "restaurant",
    "institution", "team", "software", "platform", "library", "framework", "language", "package",
    "component", "api_symbol", "api", "standard", "feature", "dish", "ingredient", "wine", "drink",
    "physical_good", "method", "discipline", "metric", "ui_pattern", "license", "designation",
    "city", "region", "country", "venue", "article", "case_study", "book", "course",
    "conference", "webinar", "festival", "other",
]


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


class BlockEntity(BaseModel):
    """Egy entitás egy említése egy blokkban (blokkformátumú bemenetnél)."""

    block_id: str = Field(description="The id of the block that contains the mention.")
    surface_form: str = Field(description="The mention exactly as written in that block, "
                                          "inflected form included.")
    canonical_name: str = Field(description="The entity's base name: uninflected, in full form, "
                                            "not translated.")
    type: EntityType
    subtype: Subtype | None = Field(description="One value from the subtype list of the "
                                                "entity's type; null for a person.")
    description: str = Field(description="What the entity is in this text, at most 10 words.")


class BlockExtraction(BaseModel):
    primary_entities: list[str] = Field(description="The canonical names of the entities the "
                                                    "page is mainly about; may be empty.")
    entities: list[BlockEntity]
