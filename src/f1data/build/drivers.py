"""Season drivers and teams.

Identity (code, name, nationality, date of birth) comes from Jolpica, with F1DB filling fields Jolpica
lacks (e.g. a driver in their first race). OpenF1 provides number, colour and headshot; drivers without
OpenF1 data keep Jolpica's race number and have no colour or headshot.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from f1data.build.ids import IdMap, JolpicaDriver, match_f1db
from f1data.models.drivers import Driver, Drivers, Team, Teams

# Curated by the owner (an exception to "derive, don't hardcode"): sources only carry full or sponsor-ish names.
# Keyed by Jolpica constructorId; unknown ids fall back to the full name.
TEAM_SHORT_NAMES = {
    "alpine": "Alpine",
    "aston_martin": "Aston Martin",
    "audi": "Audi",
    "cadillac": "Cadillac",
    "ferrari": "Ferrari",
    "haas": "Haas",
    "mclaren": "McLaren",
    "mercedes": "Mercedes",
    "rb": "Racing Bulls",
    "red_bull": "Red Bull",
    "williams": "Williams",
}


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
    f1db: dict[str, Any],
    generated_at: datetime,
) -> Drivers:
    teams = latest_teams(results)
    active = active_driver_ids(results)
    openf1_by_id = {idmap.drivers[d["driver_number"]]: d for d in openf1_drivers if d["driver_number"] in idmap.drivers}
    f1db_by_id = match_f1db(year, known, f1db)
    demonyms = {c["id"]: c.get("demonym") for c in f1db.get("countries", [])}
    drivers = []
    for d in sorted(known.values(), key=lambda d: d.id):
        o = openf1_by_id.get(d.id, {})
        f = f1db_by_id.get(d.id, {})
        dob = d.date_of_birth or f.get("dateOfBirth")
        drivers.append(
            Driver(
                id=d.id,
                number=o.get("driver_number") or d.number,
                code=d.code or f.get("abbreviation"),
                first_name=d.first_name,
                last_name=d.last_name,
                nationality=d.nationality or demonyms.get(f.get("nationalityCountryId")),
                date_of_birth=date.fromisoformat(dob) if dob else None,
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
            short_name=TEAM_SHORT_NAMES.get(tid, c["name"]),
            nationality=c.get("nationality"),
            colour=colours.get(tid),
            driver_ids=sorted(by_team.get(tid, [])),
        )
        for tid, c in sorted(constructors.items())
    ]
    return Teams(generated_at=generated_at, year=year, teams=teams)


def _colour(value: str | None) -> str | None:
    return f"#{value.upper()}" if value else None
