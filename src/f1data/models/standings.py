from __future__ import annotations

from typing import Literal

from f1data.models.base import Model, OutputFile

TitleStatus = Literal["champion", "contender", "out"]


class DriverStanding(Model):
    position: int | None
    driver_id: str
    team_ids: list[str]
    points: float
    wins: int
    active: bool
    title_status: TitleStatus | None  # latest round only
    clinched_at_round: int | None  # on the champion's latest-round entry


class TeamStanding(Model):
    position: int | None
    team_id: str
    points: float
    wins: int
    title_status: TitleStatus | None  # latest round only
    clinched_at_round: int | None  # on the champion's latest-round entry


class DriverStandingsRound(Model):
    round: int
    entries: list[DriverStanding]


class TeamStandingsRound(Model):
    round: int
    entries: list[TeamStanding]


class DriverStandings(OutputFile):
    year: int
    rounds: list[DriverStandingsRound]


class TeamStandings(OutputFile):
    year: int
    rounds: list[TeamStandingsRound]
