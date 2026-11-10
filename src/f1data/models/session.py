from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from f1data.models.base import Model, SessionFile, Utc

Classification = Literal["finished", "dnf", "dns", "dsq"]
Segment = Literal["q1", "q2", "q3"]


class Range(Model):
    min: float
    max: float


class Weather(Model):
    air_temp_c: Range
    track_temp_c: Range
    rain: bool


# ---------------------------------------------------------------- summary.json


class ClassifiedResult(Model):
    driver_id: str
    team_id: str
    position: int | None
    position_text: str
    classification: Classification
    grid: int | None
    laps: int
    time_ms: int | None
    gap_ms: int | None
    laps_behind: int | None
    points: float


class FastestLap(Model):
    driver_id: str
    lap: int
    ms: int


class RaceSummary(SessionFile):
    type: Literal["race"] = "race"
    results: list[ClassifiedResult]
    fastest_lap: FastestLap | None
    weather: Weather | None


class QualifyingEntry(Model):
    driver_id: str
    team_id: str
    position: int | None
    best_ms: int | None
    eliminated_in: Literal["q1", "q2"] | None


class QualifyingSummary(SessionFile):
    type: Literal["qualifying"] = "qualifying"
    results: list[QualifyingEntry]
    weather: Weather | None


class PracticeEntry(Model):
    driver_id: str
    team_id: str
    position: int | None
    best_lap_ms: int | None
    best_lap_number: int | None
    laps: int
    tyre: str | None


class PracticeSummary(SessionFile):
    type: Literal["practice"] = "practice"
    results: list[PracticeEntry]
    weather: Weather | None


Summary = Annotated[RaceSummary | QualifyingSummary | PracticeSummary, Field(discriminator="type")]


# ---------------------------------------------------------------- laps.json


class DriverLaps(Model):
    """Columnar: index i of every list describes the same lap."""

    lap: list[int]
    ms: list[int | None]
    s1: list[int | None]
    s2: list[int | None]
    s3: list[int | None]
    pit_out: list[bool]
    pos: list[int | None]
    gap_ms: list[int | None]
    laps_behind: list[int | None]

    @model_validator(mode="after")
    def _same_length(self) -> DriverLaps:
        n = len(self.lap)
        cols = (self.ms, self.s1, self.s2, self.s3, self.pit_out, self.pos, self.gap_ms, self.laps_behind)
        if any(len(c) != n for c in cols):
            raise ValueError("laps columns differ in length")
        return self


class Laps(SessionFile):
    drivers: dict[str, DriverLaps]


# ---------------------------------------------------------------- strategy.json


class Stint(Model):
    stint: int
    tyre: str | None
    lap_start: int
    lap_end: int
    age_at_start: int | None


class PitStop(Model):
    lap: int
    utc: Utc
    lane_ms: int | None
    stop_ms: int | None


class DriverStrategy(Model):
    stints: list[Stint]
    stops: list[PitStop]


class Strategy(SessionFile):
    drivers: dict[str, DriverStrategy]


# ---------------------------------------------------------------- race-control.json


class Penalty(Model):
    driver: str
    kind: Literal["time", "driveThrough", "stopGo", "grid"]
    seconds: int | None  # seconds for time penalties, places for grid penalties
    reason: str | None  # stewards' wording, e.g. "REJOINING UNSAFELY"


class RaceControlMessage(Model):
    utc: Utc
    lap: int | None
    category: str
    flag: str | None
    scope: str | None
    sector: int | None
    driver_id: str | None
    message: str
    penalty: Penalty | None


class RaceControl(SessionFile):
    messages: list[RaceControlMessage]


# ---------------------------------------------------------------- quali.json


class QualiDriver(Model):
    driver_id: str
    team_id: str
    position: int | None
    q1_ms: int | None
    q2_ms: int | None
    q3_ms: int | None
    knockout: Segment | None  # last segment with a time


class Quali(SessionFile):
    segments: int
    eliminated: dict[str, int]  # segment -> number of drivers knocked out there
    drivers: list[QualiDriver]


# ---------------------------------------------------------------- overtakes.json


class Overtake(Model):
    utc: Utc
    overtaker_id: str
    overtaken_id: str
    position: int


class Overtakes(SessionFile):
    overtakes: list[Overtake]
