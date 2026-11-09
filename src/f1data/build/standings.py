"""WDC and WCC after each round, from Jolpica (source of record)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from f1data.models.standings import (
    DriverStanding,
    DriverStandings,
    DriverStandingsRound,
    TeamStanding,
    TeamStandings,
    TeamStandingsRound,
)


def build_driver_standings(
    year: int, per_round: dict[int, list[dict[str, Any]]], active_ids: set[str], generated_at: datetime
) -> DriverStandings:
    rounds = [
        DriverStandingsRound(
            round=rnd,
            entries=[
                DriverStanding(
                    position=_position(row),
                    driver_id=row["Driver"]["driverId"],
                    team_ids=[c["constructorId"] for c in row.get("Constructors", [])],
                    points=float(row["points"]),
                    wins=int(row["wins"]),
                    active=row["Driver"]["driverId"] in active_ids,
                )
                for row in rows
            ],
        )
        for rnd, rows in sorted(per_round.items())
    ]
    return DriverStandings(generated_at=generated_at, year=year, rounds=rounds)


def build_team_standings(
    year: int, per_round: dict[int, list[dict[str, Any]]], generated_at: datetime
) -> TeamStandings:
    rounds = [
        TeamStandingsRound(
            round=rnd,
            entries=[
                TeamStanding(
                    position=_position(row),
                    team_id=row["Constructor"]["constructorId"],
                    points=float(row["points"]),
                    wins=int(row["wins"]),
                )
                for row in rows
            ],
        )
        for rnd, rows in sorted(per_round.items())
    ]
    return TeamStandings(generated_at=generated_at, year=year, rounds=rounds)


def _position(row: dict[str, Any]) -> int | None:
    text = row.get("position") or row.get("positionText")
    return int(text) if text and text.isdigit() else None
