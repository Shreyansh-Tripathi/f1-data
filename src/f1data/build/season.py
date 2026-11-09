from __future__ import annotations

from datetime import datetime

from f1data.build.ids import SESSION_ORDER, Weekend, parse_utc
from f1data.models.season import CancelledEvent, Round, Season, SeasonSession


def build_season(
    year: int,
    weekends: list[Weekend],
    cancelled: list[dict],
    completed_rounds: set[int],
    generated_at: datetime,
) -> Season:
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
                circuit_id=w.circuit_id,
                country=w.country,
                locality=w.locality,
                start=w.race_start,
                status="completed" if w.round in completed_rounds else "upcoming",
                sprint=any(s.code == "sprint" for s in sessions),
                sessions=sessions,
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
