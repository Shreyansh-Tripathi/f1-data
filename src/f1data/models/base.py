"""Base types for every output file: strict, frozen, camelCase, UTC dates, int milliseconds."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel

SCHEMA_VERSION = 1  # default; each file's model overrides `schema_version` when its contract breaks

SessionCode = Literal["fp1", "fp2", "fp3", "sq", "sprint", "q", "race"]


def _utc_iso(dt: datetime) -> str:
    dt = dt.astimezone(UTC)
    spec = "milliseconds" if dt.microsecond else "seconds"
    return dt.isoformat(timespec=spec).replace("+00:00", "Z")


Utc = Annotated[AwareDatetime, PlainSerializer(_utc_iso, return_type=str)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, alias_generator=to_camel, populate_by_name=True)


class OutputFile(Model):
    schema_version: int = SCHEMA_VERSION
    generated_at: Utc


class SessionFile(OutputFile):
    year: int
    round: int
    session: SessionCode
    provisional: bool
