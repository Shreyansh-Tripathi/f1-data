from __future__ import annotations

from datetime import date

from f1data.models.base import Model, OutputFile


class Driver(Model):
    id: str
    number: int | None
    code: str | None
    first_name: str
    last_name: str
    nationality: str | None
    date_of_birth: date | None
    team_id: str | None
    colour: str | None
    headshot_url: str | None
    active: bool


class Drivers(OutputFile):
    year: int
    drivers: list[Driver]


class Team(Model):
    id: str
    name: str
    nationality: str | None
    colour: str | None
    driver_ids: list[str]


class Teams(OutputFile):
    year: int
    teams: list[Team]


class SeasonStats(Model):
    starts: int
    wins: int
    podiums: int
    poles: int
    dnfs: int
    points: float
    sprint_starts: int
    sprint_wins: int
    sprint_podiums: int
    sprint_poles: int
    sprint_points: float


class CareerStats(Model):
    championships: int
    starts: int
    wins: int
    podiums: int
    poles: int
    fastest_laps: int
    points: float


class DriverStats(Model):
    driver_id: str
    season: SeasonStats
    career: CareerStats | None


class DriverStatsFile(OutputFile):
    year: int
    drivers: list[DriverStats]
