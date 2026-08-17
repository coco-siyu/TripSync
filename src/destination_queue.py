"""Validated scheduled-destination configuration for catalog discovery."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION_QUEUE_PATH = REPOSITORY_ROOT / "data" / "destination_queue.json"


class DestinationQueueItem(BaseModel):
    """One city/country pair whose candidates may be retrieved on schedule."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    city: str = Field(min_length=1, max_length=120)
    country: str = Field(min_length=1, max_length=80)
    active: bool = True

    @field_validator("city", "country")
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        if not value:
            raise ValueError("must not be blank")
        return value


class DestinationQueue(BaseModel):
    """The versioned source of truth for scheduled city discovery."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1)
    review_note: str | None = Field(default=None, max_length=1_000)
    initial_next_destination: DestinationQueueItem | None = None
    destinations: list[DestinationQueueItem] = Field(min_length=1, max_length=100)


def load_destination_queue(path: Path = DEFAULT_DESTINATION_QUEUE_PATH) -> list[DestinationQueueItem]:
    """Load active destinations and reject duplicate city/country pairs."""

    document = json.loads(path.read_text(encoding="utf-8"))
    queue = DestinationQueue.model_validate(document)
    active = [item for item in queue.destinations if item.active]
    seen: set[tuple[str, str]] = set()
    deduplicated: list[DestinationQueueItem] = []
    for item in active:
        key = (item.city.casefold(), item.country.casefold())
        if key not in seen:
            seen.add(key)
            deduplicated.append(item)
    if not deduplicated:
        raise ValueError("destination queue contains no active destinations")
    return deduplicated


def load_initial_destination(path: Path = DEFAULT_DESTINATION_QUEUE_PATH) -> DestinationQueueItem | None:
    """Return the optional, human-chosen starting point for a new cursor."""

    document = json.loads(path.read_text(encoding="utf-8"))
    return DestinationQueue.model_validate(document).initial_next_destination
