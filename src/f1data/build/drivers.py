"""Season drivers and teams: Jolpica identity plus OpenF1 colour and headshot."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from f1data.build.ids import IdMap, JolpicaDriver
from f1data.models.drivers import Driver, Drivers, Team, Teams


def latest_teams(results: list[dict[str, Any]]) -> dict[str, str]:
    """driverId -> constructorId of the latest round the driver was entered in."""
    out: dict[str, str] = {}
    for race in sorted(results, key=lambda r: int(r["round"])):
        for row in race["Results"]:
            out[row["Driver"]["driverId"]] = row["Constructor"]["constructorId"]
    return out


def active_driver_ids(results: list[dict[str, Any]]) -> set[str]:
    """Drivers entered in the latest round with results."""
    if not results:
        return set()
    latest = max(results, key=lambda r: int(r["round"]))
    return {row["Driver"]["driverId"] for row in latest["Results"]}


def build_drivers(
    year: int,
    known: dict[str, JolpicaDriver],
    results: list[dict[str, Any]],
    openf1_drivers: list[dict[str, Any]],
    idmap: IdMap,
    generated_at: datetime,
) -> Drivers:
    teams = latest_teams(results)
    active = active_driver_ids(results)
    openf1_by_id = {idmap.drivers[d["driver_number"]]: d for d in openf1_drivers if d["driver_number"] in idmap.drivers}
    drivers = []
    for d in sorted(known.values(), key=lambda d: d.id):
        o = openf1_by_id.get(d.id, {})
        drivers.append(
            Driver(
                id=d.id,
                number=d.number,
                code=d.code,
                first_name=d.first_name,
                last_name=d.last_name,
                nationality=d.nationality,
                date_of_birth=date.fromisoformat(d.date_of_birth) if d.date_of_birth else None,
                team_id=teams.get(d.id),
                colour=_colour(o.get("team_colour")),
                headshot_url=o.get("headshot_url"),
                active=d.id in active,
            )
        )
    return Drivers(generated_at=generated_at, year=year, drivers=drivers)


def build_teams(
    year: int,
    results: list[dict[str, Any]],
    openf1_drivers: list[dict[str, Any]],
    idmap: IdMap,
    generated_at: datetime,
) -> Teams:
    constructors: dict[str, dict[str, Any]] = {}
    for race in results:
        for row in race["Results"]:
            constructors[row["Constructor"]["constructorId"]] = row["Constructor"]
    colours = {
        idmap.teams[d["driver_number"]]: _colour(d.get("team_colour"))
        for d in openf1_drivers
        if d["driver_number"] in idmap.teams
    }
    by_team: dict[str, list[str]] = {}
    active = active_driver_ids(results)
    for driver_id, team_id in latest_teams(results).items():
        if driver_id in active:
            by_team.setdefault(team_id, []).append(driver_id)
    teams = [
        Team(
            id=tid,
            name=c["name"],
            nationality=c.get("nationality"),
            colour=colours.get(tid),
            driver_ids=sorted(by_team.get(tid, [])),
        )
        for tid, c in sorted(constructors.items())
    ]
    return Teams(generated_at=generated_at, year=year, teams=teams)


def _colour(value: str | None) -> str | None:
    return f"#{value.upper()}" if value else None
