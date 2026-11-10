from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from f1data.build.ids import MATCH_TOLERANCE, SESSION_ORDER, Weekend, parse_utc
from f1data.models.season import CancelledEvent, Round, RoundWinner, Season, SeasonSession

GRAND_PRIX = re.compile(r"\s+Grand Prix\b.*$")


def build_season(
    year: int,
    weekends: list[Weekend],
    cancelled: list[dict],
    completed_rounds: set[int],
    results: list[dict[str, Any]],
    f1db: dict[str, Any],
    generated_at: datetime,
) -> Season:
    winners = _winners(results)
    gp_names = _f1db_gp_names(year, f1db)
    rounds = []
    for w in weekends:
        sessions = []
        for code in SESSION_ORDER:
            if code in w.sessions:
                s = w.sessions[code]
                sessions.append(
                    SeasonSession(code=code, start=parse_utc(s["date_start"]), end=parse_utc(s["date_end"]))
                )
            elif code in w.scheduled:
                sessions.append(SeasonSession(code=code, start=w.scheduled[code], end=None))
        rounds.append(
            Round(
                round=w.round,
                name=w.name,
                short_name=short_name(w, gp_names),
                circuit_id=w.circuit_id,
                country=w.country,
                locality=w.locality,
                start=w.race_start,
                status="completed" if w.round in completed_rounds else "upcoming",
                sprint=any(s.code == "sprint" for s in sessions),
                sessions=sessions,
                winner=winners.get(w.round) if w.round in completed_rounds else None,
            )
        )
    return Season(
        generated_at=generated_at,
        year=year,
        rounds=rounds,
        cancelled=sorted(
            (CancelledEvent(name=m["meeting_name"], date=parse_utc(m["date_start"]).date()) for m in cancelled),
            key=lambda c: c.date,
        ),
    )


def short_name(w: Weekend, gp_names: dict[date, str]) -> str:
    """F1DB's grand prix name ("Great Britain", "São Paulo") for the race within ±1 day. Fallback: OpenF1's
    meeting name, else Jolpica's, without "Grand Prix"."""
    for day, name in gp_names.items():
        if abs(day - w.race_start.date()) <= MATCH_TOLERANCE:
            return name
    return GRAND_PRIX.sub("", w.meeting_name or w.name) or w.name


def _f1db_gp_names(year: int, f1db: dict[str, Any]) -> dict[date, str]:
    gps = {g["id"]: g["name"] for g in f1db.get("grandsPrix", [])}
    return {
        date.fromisoformat(r["date"]): gps[r["grandPrixId"]]
        for r in f1db.get("races", [])
        if r["year"] == year and r["grandPrixId"] in gps
    }


def _winners(results: list[dict[str, Any]]) -> dict[int, RoundWinner]:
    out = {}
    for race in results:
        for row in race["Results"]:
            if row["positionText"] == "1":
                out[int(race["round"])] = RoundWinner(
                    driver_id=row["Driver"]["driverId"], team_id=row["Constructor"]["constructorId"]
                )
    return out
