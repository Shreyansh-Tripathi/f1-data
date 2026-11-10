"""Identity resolution across sources.

- Rounds: Jolpica owns round numbers. Each round is matched to the non-cancelled
  OpenF1 meeting whose race starts within ±1 day of Jolpica's race start.
- Drivers: our id is Jolpica `driverId`. OpenF1 `driver_number` maps to it via
  the number/code Jolpica reports for the season (race numbers can differ from
  permanent numbers, e.g. a champion running #1).
- Teams: Jolpica `constructorId`. Circuits: Jolpica `circuitId`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

MATCH_TOLERANCE = timedelta(days=1)

OPENF1_SESSION_CODES = {
    "Practice 1": "fp1",
    "Practice 2": "fp2",
    "Practice 3": "fp3",
    "Sprint Qualifying": "sq",
    "Sprint Shootout": "sq",
    "Sprint": "sprint",
    "Qualifying": "q",
    "Race": "race",
}
JOLPICA_SESSION_CODES = {
    "FirstPractice": "fp1",
    "SecondPractice": "fp2",
    "ThirdPractice": "fp3",
    "SprintQualifying": "sq",
    "SprintShootout": "sq",
    "Sprint": "sprint",
    "Qualifying": "q",
}
SESSION_ORDER = ["fp1", "fp2", "fp3", "sq", "sprint", "q", "race"]


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def jolpica_start(entry: dict[str, Any]) -> datetime:
    """Start of a Jolpica race or session block (`date` + `time`)."""
    return parse_utc(f"{entry['date']}T{entry.get('time', '00:00:00Z')}")


@dataclass(frozen=True)
class Weekend:
    round: int
    name: str
    circuit_id: str
    country: str
    locality: str
    race_start: datetime
    meeting_key: int | None
    meeting_name: str | None
    circuit_info_url: str | None
    # session code -> OpenF1 session row (empty when no meeting matched)
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    # session code -> Jolpica start, used when OpenF1 has no matching meeting
    scheduled: dict[str, datetime] = field(default_factory=dict)


def match_weekends(
    schedule: list[dict[str, Any]], meetings: list[dict[str, Any]], sessions: list[dict[str, Any]]
) -> list[Weekend]:
    live = {m["meeting_key"]: m for m in meetings if not m.get("is_cancelled")}
    by_meeting: dict[int, dict[str, dict[str, Any]]] = {}
    for s in sessions:
        code = OPENF1_SESSION_CODES.get(s["session_name"])
        if code and s["meeting_key"] in live:
            by_meeting.setdefault(s["meeting_key"], {})[code] = s

    weekends = []
    for race in schedule:
        start = jolpica_start(race)
        meeting_key = next(
            (
                mk
                for mk, codes in by_meeting.items()
                if "race" in codes and abs(parse_utc(codes["race"]["date_start"]) - start) <= MATCH_TOLERANCE
            ),
            None,
        )
        scheduled = {code: jolpica_start(race[key]) for key, code in JOLPICA_SESSION_CODES.items() if key in race}
        scheduled["race"] = start
        weekends.append(
            Weekend(
                round=int(race["round"]),
                name=race["raceName"],
                circuit_id=race["Circuit"]["circuitId"],
                country=race["Circuit"]["Location"]["country"],
                locality=race["Circuit"]["Location"]["locality"],
                race_start=start,
                meeting_key=meeting_key,
                meeting_name=live[meeting_key].get("meeting_name") if meeting_key else None,
                circuit_info_url=live[meeting_key].get("circuit_info_url") if meeting_key else None,
                sessions=by_meeting.get(meeting_key, {}) if meeting_key else {},
                scheduled=scheduled,
            )
        )
    return weekends


def cancelled_meetings(meetings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in meetings if m.get("is_cancelled")]


# ---------------------------------------------------------------- drivers and teams


@dataclass(frozen=True)
class JolpicaDriver:
    id: str
    number: int | None
    code: str | None
    first_name: str
    last_name: str
    nationality: str | None
    date_of_birth: str | None


def season_drivers(*race_lists: list[dict[str, Any]]) -> dict[str, JolpicaDriver]:
    """Every driver seen in season-wide Jolpica results/qualifying/sprint, latest number wins."""
    out: dict[str, JolpicaDriver] = {}
    for races in race_lists:
        for race in sorted(races, key=lambda r: int(r["round"])):
            for row in _rows(race):
                d = row["Driver"]
                out[d["driverId"]] = JolpicaDriver(
                    id=d["driverId"],
                    number=int(row["number"]) if row.get("number") else _int(d.get("permanentNumber")),
                    code=d.get("code"),
                    first_name=d["givenName"],
                    last_name=d["familyName"],
                    nationality=d.get("nationality"),
                    date_of_birth=d.get("dateOfBirth"),
                )
    return out


def round_entries(race: dict[str, Any]) -> dict[int, tuple[str, str]]:
    """Race number -> (driverId, constructorId) for one Jolpica round."""
    return {int(r["number"]): (r["Driver"]["driverId"], r["Constructor"]["constructorId"]) for r in _rows(race)}


@dataclass
class IdMap:
    """Maps OpenF1 driver numbers / team names for one session to our ids."""

    drivers: dict[int, str]
    teams: dict[int, str]

    def driver(self, number: int | None) -> str | None:
        return self.drivers.get(number) if number is not None else None


def session_ids(
    openf1_drivers: list[dict[str, Any]],
    round_rows: dict[int, tuple[str, str]],
    known: dict[str, JolpicaDriver],
) -> IdMap:
    """Resolve each OpenF1 driver in a session. Order: round entry by number+code, season driver by code,
    then a name slug (e.g. a practice-only driver Jolpica never lists)."""
    by_code = {d.code: d.id for d in known.values() if d.code}
    team_by_name: dict[str, str] = {}
    drivers: dict[int, str] = {}
    teams: dict[int, str] = {}
    for d in openf1_drivers:
        n, code = d["driver_number"], d.get("name_acronym")
        entry = round_rows.get(n)
        entry_code = known[entry[0]].code if entry and entry[0] in known else None
        if entry and (code is None or entry_code is None or entry_code == code):
            drivers[n] = entry[0]
            if d.get("team_name"):
                team_by_name[d["team_name"]] = entry[1]
        elif code in by_code:
            drivers[n] = by_code[code]
        else:
            drivers[n] = slug(f"{d.get('first_name', '')} {d.get('last_name', '')}") or f"car-{n}"
    for d in openf1_drivers:
        n = d["driver_number"]
        entry = round_rows.get(n)
        if entry and drivers[n] == entry[0]:
            teams[n] = entry[1]
        elif d.get("team_name") in team_by_name:
            teams[n] = team_by_name[d["team_name"]]
        else:
            teams[n] = slug(d.get("team_name") or "") or "unknown"
    return IdMap(drivers=drivers, teams=teams)


def match_f1db(year: int, known: dict[str, JolpicaDriver], f1db: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Jolpica driverId -> F1DB driver, among F1DB entrants of `year` with race starts.

    Matched by code + number, then by name (new drivers can lack a Jolpica code).
    """
    season = next((s for s in f1db.get("seasons", []) if s["year"] == year), None)
    if season is None:
        return {}
    entrant_ids = {d["driverId"] for e in season["entrants"] for c in e["constructors"] for d in c["drivers"]}
    racers = [d for d in f1db["drivers"] if d["id"] in entrant_ids and (d.get("totalRaceStarts") or 0) > 0]
    by_code_number = {(d.get("abbreviation"), str(d.get("permanentNumber"))): d for d in racers}
    by_name = {slug(f"{d['firstName']} {d['lastName']}"): d for d in racers}
    out = {}
    for j in known.values():
        f = by_code_number.get((j.code, str(j.number))) or by_name.get(slug(f"{j.first_name} {j.last_name}"))
        if f:
            out[j.id] = f
    return out


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")


def _rows(race: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("Results", "QualifyingResults", "SprintResults"):
        if key in race:
            return race[key]
    return []


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
