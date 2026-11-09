from __future__ import annotations

from f1data.models.base import Model, OutputFile


class DriverStanding(Model):
    position: int | None
    driver_id: str
    team_ids: list[str]
    points: float
    wins: int
    active: bool


class TeamStanding(Model):
    position: int | None
    team_id: str
    points: float
    wins: int


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
