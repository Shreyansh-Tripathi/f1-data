"""Season stats (race-only, with separate sprint fields) plus F1DB career totals."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from f1data.build.classify import classify
from f1data.build.ids import JolpicaDriver, match_f1db
from f1data.models.drivers import CareerStats, DriverRound, DriverStats, DriverStatsFile, SeasonStats


def build_driver_stats(
    year: int,
    known: dict[str, JolpicaDriver],
    results: list[dict[str, Any]],
    qualifying: list[dict[str, Any]],
    sprint: list[dict[str, Any]],
    f1db: dict[str, Any],
    generated_at: datetime,
) -> DriverStatsFile:
    race = _tally(results, "Results")
    spr = _tally(sprint, "SprintResults")
    poles = _count(qualifying, "QualifyingResults", lambda row: row.get("position") == "1")
    # Jolpica has no sprint-qualifying classification; sprint pole = sprint grid 1.
    sprint_poles = _count(sprint, "SprintResults", lambda row: row.get("grid") == "1")
    careers = _careers(year, known, f1db)
    rounds = _rounds(results, qualifying)
    drivers = []
    for d in sorted(known.values(), key=lambda d: d.id):
        r, s = race.get(d.id, {}), spr.get(d.id, {})
        drivers.append(
            DriverStats(
                driver_id=d.id,
                season=SeasonStats(
                    starts=r.get("starts", 0),
                    wins=r.get("wins", 0),
                    podiums=r.get("podiums", 0),
                    poles=poles.get(d.id, 0),
                    dnfs=r.get("dnfs", 0),
                    points=r.get("points", 0.0),
                    sprint_starts=s.get("starts", 0),
                    sprint_wins=s.get("wins", 0),
                    sprint_podiums=s.get("podiums", 0),
                    sprint_poles=sprint_poles.get(d.id, 0),
                    sprint_points=s.get("points", 0.0),
                ),
                career=careers.get(d.id),
                rounds=[r for _, r in sorted(rounds.get(d.id, {}).items())],
            )
        )
    return DriverStatsFile(generated_at=generated_at, year=year, drivers=drivers)


def _tally(races: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for race in races:
        for row in race[key]:
            t = out.setdefault(
                row["Driver"]["driverId"], {"starts": 0, "wins": 0, "podiums": 0, "dnfs": 0, "points": 0.0}
            )
            c = classify(row["positionText"])
            t["points"] += float(row.get("points", 0))
            if c == "dns":
                continue
            t["starts"] += 1
            t["dnfs"] += c == "dnf"
            if c == "finished":
                pos = int(row["positionText"])
                t["wins"] += pos == 1
                t["podiums"] += pos <= 3
    return out


def _rounds(results: list[dict[str, Any]], qualifying: list[dict[str, Any]]) -> dict[str, dict[int, DriverRound]]:
    """driverId -> round -> that round's quali and race result. Team is the race entry's, else the quali entry's."""
    quali = {
        (int(race["round"]), row["Driver"]["driverId"]): row for race in qualifying for row in race["QualifyingResults"]
    }
    races = {(int(race["round"]), row["Driver"]["driverId"]): row for race in results for row in race["Results"]}
    out: dict[str, dict[int, DriverRound]] = {}
    for rnd, driver_id in sorted(quali.keys() | races.keys()):
        q, r = quali.get((rnd, driver_id)), races.get((rnd, driver_id))
        c = classify(r["positionText"]) if r else None
        out.setdefault(driver_id, {})[rnd] = DriverRound(
            round=rnd,
            team_id=(r or q)["Constructor"]["constructorId"],
            quali_position=_int(q.get("position")) if q else None,
            grid=_int(r.get("grid")) if r else None,
            race_position=int(r["positionText"]) if c == "finished" else None,
            race_classification=c,
            race_points=float(r.get("points", 0)) if r else None,
        )
    return out


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _count(races: list[dict[str, Any]], key: str, pred: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for race in races:
        for row in race[key]:
            if pred(row):
                out[row["Driver"]["driverId"]] = out.get(row["Driver"]["driverId"], 0) + 1
    return out


def _careers(year: int, known: dict[str, JolpicaDriver], f1db: dict[str, Any]) -> dict[str, CareerStats]:
    out = {}
    for driver_id, f in match_f1db(year, known, f1db).items():
        out[driver_id] = CareerStats(
            championships=f.get("totalChampionshipWins") or 0,
            starts=f.get("totalRaceStarts") or 0,
            wins=f.get("totalRaceWins") or 0,
            podiums=f.get("totalPodiums") or 0,
            poles=f.get("totalPolePositions") or 0,
            fastest_laps=f.get("totalFastestLaps") or 0,
            points=float(f.get("totalPoints") or 0),
        )
    return out
