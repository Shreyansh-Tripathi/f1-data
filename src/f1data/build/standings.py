"""WDC and WCC after each round, from Jolpica (source of record), with title status for the latest round."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from f1data.models.standings import (
    DriverStanding,
    DriverStandings,
    DriverStandingsRound,
    TeamStanding,
    TeamStandings,
    TeamStandingsRound,
    TitleStatus,
)

# ---------------------------------------------------------------- title maths (pure)


@dataclass(frozen=True)
class EventPoints:
    """Most points one entrant can take from one event, as observed this season."""

    race: float
    sprint: float | None  # None: no sprint run yet, so its value is unknown


@dataclass(frozen=True)
class TitleContext:
    remaining: dict[int, tuple[int, int]]  # round -> (races, sprints) still to run after it
    final_round: int
    drivers: EventPoints  # race / sprint P1 points
    teams: EventPoints  # race / sprint P1 + P2 points


def max_remaining(races: int, sprints: int, points: EventPoints) -> float:
    if sprints and points.sprint is None:
        return math.inf  # can't bound it: never declare a champion on a guess
    return races * points.race + sprints * (points.sprint or 0.0)


def title_status(table: list[tuple[str, float]], max_rem: float, final: bool) -> dict[str, TitleStatus]:
    """`table` is (id, points) in championship order, leader first.

    Champion: the leader's margin over every rival is strictly greater than `max_rem` (or the season is over).
    Contender: points + `max_rem` >= the leader's points. Otherwise out.
    """
    if not table:
        return {}
    leader, lead_points = table[0]
    if final or all(lead_points - points > max_rem for _, points in table[1:]):
        return {i: "champion" if i == leader else "out" for i, _ in table}
    return {i: "contender" if points + max_rem >= lead_points else "out" for i, points in table}


def clinched_at_round(
    tables: dict[int, list[tuple[str, float]]],
    remaining: dict[int, tuple[int, int]],
    points: EventPoints,
    final_round: int,
) -> tuple[str, int] | None:
    """(champion id, first round after which the title was decided), if it has been."""
    for rnd, table in sorted(tables.items()):
        races, sprints = remaining.get(rnd, (0, 0))
        status = title_status(table, max_remaining(races, sprints, points), rnd >= final_round)
        if table and status[table[0][0]] == "champion":
            return table[0][0], rnd
    return None


def title_context(
    schedule: list[dict[str, Any]], results: list[dict[str, Any]], sprints: list[dict[str, Any]]
) -> TitleContext:
    """Remaining events from the Jolpica schedule (cancelled events aren't in it); points from results."""
    rounds = sorted((int(r["round"]), "Sprint" in r) for r in schedule)
    remaining = {
        rnd: (sum(1 for r, _ in rounds if r > rnd), sum(1 for r, sprint in rounds if r > rnd and sprint))
        for rnd, _ in rounds
    }
    race1, race2 = _best(results, "Results", "1"), _best(results, "Results", "2")
    sprint1, sprint2 = _best(sprints, "SprintResults", "1"), _best(sprints, "SprintResults", "2")
    return TitleContext(
        remaining=remaining,
        final_round=max((r for r, _ in rounds), default=0),
        drivers=EventPoints(race=race1 or 0.0, sprint=sprint1),
        teams=EventPoints(
            race=(race1 or 0.0) + (race2 or 0.0), sprint=None if sprint1 is None else sprint1 + (sprint2 or 0.0)
        ),
    )


def _best(races: list[dict[str, Any]], key: str, position_text: str) -> float | None:
    """Most points scored for `position_text` in any event (covers half points), None if never run."""
    points = [float(row["points"]) for race in races for row in race[key] if row["positionText"] == position_text]
    return max(points) if points else None


def _title_fields(
    rnd: int,
    latest: int,
    ids: list[str],
    tables: dict[int, list[tuple[str, float]]],
    ctx: TitleContext,
    points: EventPoints,
) -> dict[str, dict[str, Any]]:
    if rnd != latest:
        return {i: {"title_status": None, "clinched_at_round": None} for i in ids}
    races, sprints = ctx.remaining.get(rnd, (0, 0))
    status = title_status(tables[rnd], max_remaining(races, sprints, points), rnd >= ctx.final_round)
    clinched = clinched_at_round(tables, ctx.remaining, points, ctx.final_round)
    return {
        i: {
            "title_status": status.get(i),
            "clinched_at_round": clinched[1] if clinched and status.get(i) == "champion" else None,
        }
        for i in ids
    }


def _tables(per_round: dict[int, list[dict[str, Any]]], id_of: Any) -> dict[int, list[tuple[str, float]]]:
    return {
        rnd: [
            (id_of(row), float(row["points"]))
            for row in sorted(rows, key=lambda r: (_position(r) is None, _position(r) or 0))
        ]
        for rnd, rows in per_round.items()
    }


# ---------------------------------------------------------------- standings files


def build_driver_standings(
    year: int,
    per_round: dict[int, list[dict[str, Any]]],
    active_ids: set[str],
    ctx: TitleContext,
    generated_at: datetime,
) -> DriverStandings:
    tables = _tables(per_round, lambda row: row["Driver"]["driverId"])
    latest = max(per_round, default=0)
    rounds = []
    for rnd, rows in sorted(per_round.items()):
        ids = [row["Driver"]["driverId"] for row in rows]
        title = _title_fields(rnd, latest, ids, tables, ctx, ctx.drivers)
        rounds.append(
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
                        **title[row["Driver"]["driverId"]],
                    )
                    for row in rows
                ],
            )
        )
    return DriverStandings(generated_at=generated_at, year=year, rounds=rounds)


def build_team_standings(
    year: int, per_round: dict[int, list[dict[str, Any]]], ctx: TitleContext, generated_at: datetime
) -> TeamStandings:
    tables = _tables(per_round, lambda row: row["Constructor"]["constructorId"])
    latest = max(per_round, default=0)
    rounds = []
    for rnd, rows in sorted(per_round.items()):
        ids = [row["Constructor"]["constructorId"] for row in rows]
        title = _title_fields(rnd, latest, ids, tables, ctx, ctx.teams)
        rounds.append(
            TeamStandingsRound(
                round=rnd,
                entries=[
                    TeamStanding(
                        position=_position(row),
                        team_id=row["Constructor"]["constructorId"],
                        points=float(row["points"]),
                        wins=int(row["wins"]),
                        **title[row["Constructor"]["constructorId"]],
                    )
                    for row in rows
                ],
            )
        )
    return TeamStandings(generated_at=generated_at, year=year, rounds=rounds)


def _position(row: dict[str, Any]) -> int | None:
    text = row.get("position") or row.get("positionText")
    return int(text) if text and text.isdigit() else None
