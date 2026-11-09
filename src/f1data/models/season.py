from __future__ import annotations

from datetime import date
from typing import Literal

from f1data.models.base import Model, OutputFile, SessionCode, Utc


class SeasonSession(Model):
    code: SessionCode
    start: Utc
    end: Utc | None


class Round(Model):
    round: int
    name: str
    circuit_id: str
    country: str
    locality: str
    start: Utc
    status: Literal["completed", "upcoming"]
    sprint: bool
    sessions: list[SeasonSession]


class CancelledEvent(Model):
    name: str
    date: date


class Season(OutputFile):
    year: int
    rounds: list[Round]
    cancelled: list[CancelledEvent]
