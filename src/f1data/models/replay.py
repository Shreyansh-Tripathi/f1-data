from __future__ import annotations

from typing import Literal

from pydantic import Field

from f1data.models.base import Model, SessionFile, Utc

ReplayStatus = Literal["finished", "retired", "dns", "dsq"]
ReplayEndPlace = Literal["track", "pitLane"]
ReplayPeriodKind = Literal["sc", "vsc", "red"]


class ReplayEnd(Model):
    t: int  # ms on the replay clock; the samples end here for a retired car
    status: ReplayStatus
    where: ReplayEndPlace  # where the car stopped: stays on screen briefly on track, vanishes in the pit lane


class ReplayDriver(Model):
    team_id: str
    t0: int | None  # ms of the first sample on the shared clock; null without samples (dns)
    # Delta-encoded positions in the circuits/{id}.json frame: the first value absolute, then differences, one per
    # stepMs from t0.
    x: list[int]
    y: list[int]
    laps: list[list[int]]  # [lap, t] when each lap starts; a finisher's last entry is [laps + 1, finish t]
    pos: list[list[int]]  # [t, position] at each change
    # [t, gapToAheadMs] at each change (100 ms steps); [t, null] when leading; [t, null, laps] a lap or more behind
    intervals: list[list[int | None]] = Field(alias="int")
    pit: list[list[int]]  # [entry t, exit t] in the pit lane
    stopped: list[list[int]]  # [from, to]: stationary on track 20 s or more, then moving again
    end: ReplayEnd


class ReplayPeriod(Model):
    kind: ReplayPeriodKind
    start_t: int
    end_t: int | None


class ReplayEvent(Model):
    t: int
    kind: str  # race-control kind (safetyCar, penalty, ...)
    text: str
    driver_ids: list[str]


class Replay(SessionFile):
    schema_version: int = 1
    start_utc: Utc  # replay clock 0: lights out (lap 1 start); the window may begin earlier (formation lap)
    step_ms: int
    duration_ms: int  # window end on the replay clock
    start_line: list[int] | None  # [x, y]
    periods: list[ReplayPeriod]
    events: list[ReplayEvent]
    drivers: dict[str, ReplayDriver]
