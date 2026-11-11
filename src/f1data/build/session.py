"""Per-session builders: summary (per session type), laps, strategy, race control, quali, overtakes."""

from __future__ import annotations

import bisect
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from f1data.build.classify import classify
from f1data.build.ids import IdMap, parse_utc
from f1data.models.base import SessionCode
from f1data.models.session import (
    ClassifiedResult,
    DriverLaps,
    DriverStrategy,
    FastestLap,
    Laps,
    Overtake,
    Overtakes,
    Penalty,
    Period,
    PitStop,
    PracticeEntry,
    PracticeSummary,
    Quali,
    QualiDriver,
    QualifyingEntry,
    QualifyingSummary,
    QualiStatus,
    RaceControl,
    RaceControlMessage,
    RaceSummary,
    Range,
    Stint,
    Strategy,
    Weather,
)

log = logging.getLogger(__name__)

Rows = list[dict[str, Any]]

SEGMENTS = ("q1", "q2", "q3")
LAPPED = re.compile(r"^\+(\d+) LAPS?$")


@dataclass(frozen=True)
class SessionMeta:
    year: int
    round: int
    session: SessionCode
    provisional: bool
    generated_at: datetime

    def fields(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "round": self.round,
            "session": self.session,
            "provisional": self.provisional,
            "generated_at": self.generated_at,
        }


def ms(seconds: float | None) -> int | None:
    return round(seconds * 1000) if seconds is not None else None


def clock_ms(text: str | None) -> int | None:
    """'1:23:06.801', '1:12.983' or '72.983' -> milliseconds."""
    if not text:
        return None
    seconds = 0.0
    for part in text.split(":"):
        seconds = seconds * 60 + float(part)
    return round(seconds * 1000)


def build_weather(rows: Rows) -> Weather | None:
    if not rows:
        return None
    air = [r["air_temperature"] for r in rows if r.get("air_temperature") is not None]
    track = [r["track_temperature"] for r in rows if r.get("track_temperature") is not None]
    if not air or not track:
        return None
    return Weather(
        air_temp_c=Range(min=min(air), max=max(air)),
        track_temp_c=Range(min=min(track), max=max(track)),
        rain=any(r.get("rainfall") for r in rows),
    )


# ---------------------------------------------------------------- summary.json


def build_race_summary(
    meta: SessionMeta,
    jolpica_rows: Rows,
    openf1_results: Rows,
    weather: Rows,
    idmap: IdMap,
    starting_grid: Rows | None = None,
    race_control: Rows = (),
) -> RaceSummary:
    """Race or sprint classification from Jolpica; OpenF1 flags cross-check DNF/DNS/DSQ."""
    by_driver = {idmap.driver(r["driver_number"]): r for r in openf1_results}
    grid_fallback = {idmap.driver(g["driver_number"]): g["position"] for g in starting_grid or []}
    rows = sorted(jolpica_rows, key=lambda r: int(r.get("positionOrder") or r["position"]))
    winner = rows[0] if rows else None
    winner_ms = int(winner["Time"]["millis"]) if winner and winner.get("Time") else None
    winner_laps = int(winner["laps"]) if winner else 0
    results = []
    fastest = None
    for row in rows:
        driver_id = row["Driver"]["driverId"]
        c = classify(row["positionText"], by_driver.get(driver_id), who=f"{meta.session} {driver_id}")
        laps = int(row["laps"])
        time_ms = int(row["Time"]["millis"]) if row.get("Time", {}).get("millis") else None
        lapped = c == "finished" and laps < winner_laps
        gap = time_ms - winner_ms if c == "finished" and not lapped and time_ms and winner_ms else None
        grid = int(row["grid"]) if row.get("grid") not in (None, "") else grid_fallback.get(driver_id)
        results.append(
            ClassifiedResult(
                driver_id=driver_id,
                team_id=row["Constructor"]["constructorId"],
                position=int(row["positionText"]) if c == "finished" else None,
                position_text=row["positionText"],
                classification=c,
                grid=grid,
                laps=laps,
                time_ms=time_ms,
                gap_ms=gap,
                laps_behind=winner_laps - laps if lapped else None,
                points=float(row.get("points", 0)),
                status_text=row.get("status") or None,
            )
        )
        fl = row.get("FastestLap")
        if fl and fl.get("rank") == "1":
            fastest = FastestLap(driver_id=driver_id, lap=int(fl["lap"]), ms=clock_ms(fl["Time"]["time"]))
    return RaceSummary(
        **meta.fields(),
        results=results,
        fastest_lap=fastest,
        weather=build_weather(weather),
        periods=build_periods(race_control, f"{meta.year}/{meta.round}/{meta.session}"),
    )


# ---------------------------------------------------------------- SC / VSC / red-flag periods

SC_MESSAGES = {
    "SAFETY CAR DEPLOYED": ("start", "sc"),
    "SAFETY CAR IN THIS LAP": ("end", "sc"),
    "VSC DEPLOYED": ("start", "vsc"),
    "VSC ENDING": ("end", "vsc"),
}
SC_WORDING = re.compile(r"\bSAFETY CAR\b|\bVSC\b")
# Mention the safety car or VSC without starting or ending a period (seen in 2026 race control).
SC_NOT_A_PERIOD = re.compile(
    r"SAFETY CAR LIGHTS (?:ON|OFF)|BEHIND SAFETY CAR|OVERTAKE THE SAFETY CAR|SAFETY CAR WILL USE|"
    r"(?:SAFETY CAR|VSC) INFRINGEMENT|AT SAFETY CAR LINE"
)


def is_red_flag(r: dict[str, Any]) -> bool:
    return r["message"].startswith("RED FLAG") or (r.get("category") == "Flag" and r.get("flag") == "RED")


def build_periods(race_control: Rows, where: str = "") -> list[Period]:
    """SC, VSC and red-flag periods from race control, in time order.

    - SC: SAFETY CAR DEPLOYED .. SAFETY CAR IN THIS LAP; VSC: VSC DEPLOYED .. VSC ENDING. A deployment of the
      other kind while one is open ends it there (VSC upgraded to SC, or SC to VSC).
    - Red flag: a RED FLAG message .. the next SESSION STARTED (the restart). It ends any open SC/VSC.
    - Anything still open at the chequered flag (or SESSION FINISHED) ends there.
    Other SC/VSC wording that isn't a known non-period message is logged, not guessed at.
    """
    periods: list[Period] = []
    open_: dict[str, dict[str, Any]] = {}  # "track" (sc/vsc) and "red" -> start fields
    lap: int | None = None

    def close(slot: str, t: datetime, end_lap: int | None, message: str) -> None:
        p = open_.pop(slot, None)
        if p:
            periods.append(Period(**p, end_lap=end_lap, end_time=t, end_message=message))

    for r in sorted(race_control, key=lambda r: r["date"]):
        m, t = r["message"].strip(), parse_utc(r["date"])
        lap = r.get("lap_number") or lap
        if (r.get("flag") or "").upper() == "CHEQUERED" or (
            r.get("category") == "SessionStatus" and m == "SESSION FINISHED"
        ):
            close("track", t, lap, m)
            close("red", t, lap, m)
            break
        if is_red_flag(r):
            close("track", t, lap, m)
            open_.setdefault("red", {"kind": "red", "start_lap": lap, "start_time": t, "start_message": m})
            continue
        if r.get("category") == "SessionStatus" and m == "SESSION STARTED":
            close("red", t, lap, m)
            continue
        if r.get("category") != "SafetyCar" and not SC_WORDING.search(m):
            continue
        action = SC_MESSAGES.get(m)
        if action is None:
            if not SC_NOT_A_PERIOD.search(m):
                log.warning("periods: unrecognised SC/VSC message in %s: %r", where, m)
            continue
        what, kind = action
        current = open_.get("track")
        if what == "start":
            if current and current["kind"] == kind:
                continue  # repeated deployment message
            close("track", t, lap, m)
            open_["track"] = {"kind": kind, "start_lap": lap, "start_time": t, "start_message": m}
        elif current and current["kind"] == kind:
            close("track", t, lap, m)
        else:
            log.warning("periods: %r in %s with no open %s period", m, where, kind)
    last = race_control and max(race_control, key=lambda r: r["date"])
    if last and open_:  # no chequered flag in the data: end at the last message
        t = parse_utc(last["date"])
        close("track", t, lap, last["message"])
        close("red", t, lap, last["message"])
    return sorted(periods, key=lambda p: p.start_time)


def build_qualifying_summary(
    meta: SessionMeta,
    openf1_results: Rows,
    weather: Rows,
    idmap: IdMap,
    jolpica_rows: Rows | None = None,
    f1db_codes: dict[int, str] | None = None,
) -> QualifyingSummary:
    reached = _segments_reached(openf1_results, jolpica_rows, idmap)
    last = _last_segment(reached)
    results = []
    for r in sorted(openf1_results, key=_position_key):
        times = _segment_times(r)
        knockout = reached.get(idmap.drivers[r["driver_number"]])
        results.append(
            QualifyingEntry(
                driver_id=idmap.drivers[r["driver_number"]],
                team_id=idmap.teams[r["driver_number"]],
                position=_position(r),
                best_ms=min((t for t in times if t is not None), default=None),
                eliminated_in=knockout if knockout and knockout != last else None,
                **_quali_status_fields(r, f1db_codes, meta),
            )
        )
    return QualifyingSummary(**meta.fields(), results=results, weather=build_weather(weather))


def build_practice_summary(
    meta: SessionMeta, openf1_results: Rows, laps: Rows, stints: Rows, weather: Rows, idmap: IdMap
) -> PracticeSummary:
    best: dict[int, dict[str, Any]] = {}
    count: dict[int, int] = {}
    for lap in laps:
        n = lap["driver_number"]
        count[n] = max(count.get(n, 0), lap["lap_number"])
        if lap.get("lap_duration") and (n not in best or lap["lap_duration"] < best[n]["lap_duration"]):
            best[n] = lap
    results = []
    for r in sorted(openf1_results, key=_position_key):
        n = r["driver_number"]
        b = best.get(n)
        results.append(
            PracticeEntry(
                driver_id=idmap.drivers[n],
                team_id=idmap.teams[n],
                position=_position(r),
                best_lap_ms=ms(b["lap_duration"]) if b else ms(r.get("duration")),
                best_lap_number=b["lap_number"] if b else None,
                laps=r.get("number_of_laps") or count.get(n, 0),
                tyre=_tyre_on_lap(stints, n, b["lap_number"]) if b else None,
            )
        )
    return PracticeSummary(**meta.fields(), results=results, weather=build_weather(weather))


# ---------------------------------------------------------------- laps.json


class _Series:
    """Per-driver time series for 'last value at or before t' lookups."""

    def __init__(self, rows: Rows, value_key: str) -> None:
        self._t: dict[int, list[datetime]] = {}
        self._v: dict[int, list[Any]] = {}
        for r in sorted(rows, key=lambda r: parse_utc(r["date"])):
            self._t.setdefault(r["driver_number"], []).append(parse_utc(r["date"]))
            self._v.setdefault(r["driver_number"], []).append(r.get(value_key))

    def at(self, driver: int, t: datetime | None) -> Any:
        if t is None or driver not in self._t:
            return None
        i = bisect.bisect_right(self._t[driver], t)
        return self._v[driver][i - 1] if i else None


def build_laps(meta: SessionMeta, laps: Rows, position: Rows, intervals: Rows, idmap: IdMap) -> Laps:
    pos = _Series(position, "position")
    gap = _Series(intervals, "gap_to_leader")
    per_driver: dict[int, Rows] = {}
    for lap in laps:
        per_driver.setdefault(lap["driver_number"], []).append(lap)
    drivers = {}
    for n, rows in per_driver.items():
        rows.sort(key=lambda r: r["lap_number"])
        cols: dict[str, list[Any]] = {
            k: [] for k in ("lap", "ms", "s1", "s2", "s3", "pit_out", "pos", "gap_ms", "laps_behind")
        }
        for i, lap in enumerate(rows):
            end = _lap_end(lap, rows[i + 1] if i + 1 < len(rows) else None)
            g = gap.at(n, end)
            cols["lap"].append(lap["lap_number"])
            cols["ms"].append(ms(lap.get("lap_duration")))
            cols["s1"].append(ms(lap.get("duration_sector_1")))
            cols["s2"].append(ms(lap.get("duration_sector_2")))
            cols["s3"].append(ms(lap.get("duration_sector_3")))
            cols["pit_out"].append(bool(lap.get("is_pit_out_lap")))
            cols["pos"].append(pos.at(n, end))
            gap_ms, behind = _gap(g)
            cols["gap_ms"].append(gap_ms)
            cols["laps_behind"].append(behind)
        drivers[idmap.drivers[n]] = DriverLaps(**cols)
    return Laps(**meta.fields(), drivers=dict(sorted(drivers.items())))


def _lap_end(lap: dict[str, Any], next_lap: dict[str, Any] | None) -> datetime | None:
    if lap.get("date_start") and lap.get("lap_duration"):
        return parse_utc(lap["date_start"]) + timedelta(seconds=lap["lap_duration"])
    if next_lap and next_lap.get("date_start"):
        return parse_utc(next_lap["date_start"])
    return None


def _gap(value: Any) -> tuple[int | None, int | None]:
    if value is None:
        return None, None
    if isinstance(value, str):
        m = LAPPED.match(value.strip())
        return (None, int(m.group(1))) if m else (None, None)
    return ms(float(value)), None


# ---------------------------------------------------------------- strategy.json


def build_strategy(
    meta: SessionMeta, stints: Rows, pit: Rows, race_control: Rows, idmap: IdMap, session_start: datetime
) -> Strategy:
    out: dict[str, dict[str, list[Any]]] = {}
    for s in sorted(stints, key=lambda s: (s["driver_number"], s["stint_number"])):
        if s.get("lap_start") is None or s.get("lap_end") is None:
            continue
        out.setdefault(idmap.drivers[s["driver_number"]], {"stints": [], "stops": []})["stints"].append(
            Stint(
                stint=s["stint_number"],
                tyre=s["compound"].lower() if s.get("compound") else None,
                lap_start=s["lap_start"],
                lap_end=s["lap_end"],
                age_at_start=s.get("tyre_age_at_start"),
            )
        )
    suspensions = red_flag_suspensions(race_control)
    for p in sorted(pit, key=lambda p: (p["driver_number"], p["date"])):
        lane_ms = ms(p.get("lane_duration") if p.get("lane_duration") is not None else p.get("pit_duration"))
        exit_utc = parse_utc(p["date"])
        who = f"{meta.year}/{meta.round}/{meta.session} car {p['driver_number']} lap {p.get('lap_number')}"
        if exit_utc < session_start:
            log.info("pit: dropped pre-session entry %s at %s", who, p["date"])
            continue
        if lane_ms is None:
            log.warning("pit: dropped entry without lane time %s at %s", who, p["date"])
            continue
        entry_utc = exit_utc - timedelta(milliseconds=lane_ms)
        if any(start <= entry_utc <= end for start, end in suspensions):
            log.info("pit: dropped red-flag pit lane entry %s (%d ms)", who, lane_ms)
            continue
        out.setdefault(idmap.drivers[p["driver_number"]], {"stints": [], "stops": []})["stops"].append(
            PitStop(lap=p["lap_number"], utc=exit_utc, lane_ms=lane_ms, stop_ms=ms(p.get("stop_duration")))
        )
    return Strategy(**meta.fields(), drivers={k: DriverStrategy(**v) for k, v in sorted(out.items())})


def red_flag_suspensions(race_control: Rows) -> list[tuple[datetime, datetime]]:
    """(red flag, restart) windows: from a "RED FLAG" message to the next SESSION STARTED, or open-ended.

    Cars wait in the pit lane while the session is suspended, and OpenF1 records that as a pit entry.
    """
    out: list[tuple[datetime, datetime]] = []
    red: datetime | None = None
    for r in sorted(race_control, key=lambda r: r["date"]):
        t = parse_utc(r["date"])
        if red is None and (r["message"].startswith("RED FLAG") or (r.get("flag") or "").upper() == "RED"):
            red = t
        elif red is not None and r.get("category") == "SessionStatus" and r["message"] == "SESSION STARTED":
            out.append((red, t))
            red = None
    if red is not None:
        out.append((red, datetime.max.replace(tzinfo=red.tzinfo)))
    return out


# ---------------------------------------------------------------- race-control.json

PENALTY_PATTERNS = [
    (re.compile(r"(\d+) SECOND TIME PENALTY FOR CAR (\d+)"), "time"),
    (re.compile(r"(\d+) SECOND STOP[-/ ]?(?:AND[- ])?GO PENALTY FOR CAR (\d+)"), "stopGo"),
    (re.compile(r"()STOP[-/ ]?(?:AND[- ])?GO PENALTY FOR CAR (\d+)"), "stopGo"),
    (re.compile(r"()DRIVE[ -]THROUGH PENALTY FOR CAR (\d+)"), "driveThrough"),
    (re.compile(r"(\d+) PLACE GRID PENALTY FOR CAR (\d+)"), "grid"),
]
NOT_ISSUED = ("PENALTY SERVED", "WITHDRAWN", "REVOKED")
# "... FOR CAR 23 (ALB) - REJOINING UNSAFELY (14:50:05)": the reason, without the trailing clock time.
PENALTY_REASON = re.compile(r"FOR CAR \d+(?: \([A-Z]+\))? - (.+?)(?: \(\d{1,2}:\d{2}:\d{2}\))?$")
# Kept upper case when sentence-casing reasons, along with driver codes ("(NOR)").
ACRONYMS = frozenset({"DRS", "VSC", "SC", "DSQ", "DNF", "FIA"})
_REASON_TOKEN = re.compile(r"\(([A-Z]{3})\)|[A-Za-z0-9']+")


def sentence_case(text: str) -> str:
    """ "TRACK LIMITS (5TH OFFENCE)" -> "Track limits (5th offence)"; acronyms and "(NOR)" stay upper case."""

    def token(m: re.Match[str]) -> str:
        word = m.group(0)
        if m.group(1) or word.upper() in ACRONYMS:
            return word.upper()
        return word.lower()

    out = _REASON_TOKEN.sub(token, text)
    return out[:1].upper() + out[1:]


def parse_penalty(message: str, idmap: IdMap) -> Penalty | None:
    if any(word in message for word in NOT_ISSUED):
        return None
    for pattern, kind in PENALTY_PATTERNS:
        m = pattern.search(message)
        if m:
            driver = idmap.driver(int(m.group(2)))
            if driver is None:
                return None
            reason = PENALTY_REASON.search(message)
            return Penalty(
                driver=driver,
                kind=kind,
                seconds=int(m.group(1)) if m.group(1) else None,
                reason=sentence_case(reason.group(1).strip()) if reason else None,
            )
    return None


# "CAR 23 (ALB)", "CARS 4 (NOR) AND 81 (PIA)", "CARS 1 (A), 2 (B) AND 3 (C)", "CAR 5 - ..."
NAMED_CARS = re.compile(r"\bCARS? (\d+(?: \([A-Z]{3}\))?(?:(?:, | AND )\d+(?: \([A-Z]{3}\))?)*)")
LAP_DELETED_TRACK_LIMITS = re.compile(r"\b(?:TIME(?: \S+)?|LAP) DELETED - TRACK LIMITS\b")


def named_drivers(r: dict[str, Any], idmap: IdMap) -> list[str]:
    numbers = [int(n) for m in NAMED_CARS.finditer(r["message"]) for n in re.findall(r"\d+", m.group(1))]
    if r.get("driver_number") is not None:
        numbers.insert(0, r["driver_number"])
    out: list[str] = []
    for n in numbers:
        driver = idmap.driver(n)
        if driver and driver not in out:
            out.append(driver)
    return out


def race_control_kind(r: dict[str, Any], penalty: Penalty | None) -> str:
    """First match wins: stewards' outcome, then lap deletions, then safety car, then flags."""
    m = r["message"]
    if penalty or "PENALTY SERVED" in m or "REPRIMAND" in m:
        return "penalty"
    if "NO FURTHER ACTION" in m or "NO FURTHER INVESTIGATION" in m:
        return "noFurtherAction"
    if "UNDER INVESTIGATION" in m or "WILL BE INVESTIGATED" in m:
        return "investigation"
    if re.search(r"\bNOTED\b", m):
        return "noted"
    if LAP_DELETED_TRACK_LIMITS.search(m):
        return "trackLimits"
    if r.get("category") == "SafetyCar" or SC_WORDING.search(m):
        return "safetyCar"
    if r.get("category") == "Flag" or is_red_flag(r) or " FLAG" in m:
        return "flag"
    return "other"


def build_race_control(meta: SessionMeta, rows: Rows, idmap: IdMap) -> RaceControl:
    messages = []
    for r in sorted(rows, key=lambda r: parse_utc(r["date"])):
        penalty = parse_penalty(r["message"], idmap)
        messages.append(
            RaceControlMessage(
                utc=parse_utc(r["date"]),
                lap=r.get("lap_number"),
                category=_lower_first(r.get("category") or "other"),
                flag=r["flag"].lower() if r.get("flag") else None,
                scope=r["scope"].lower() if r.get("scope") else None,
                sector=r.get("sector"),
                driver_id=idmap.driver(r.get("driver_number")),
                driver_ids=named_drivers(r, idmap),
                message=r["message"],
                kind=race_control_kind(r, penalty),
                penalty=penalty,
            )
        )
    return RaceControl(**meta.fields(), messages=messages)


# ---------------------------------------------------------------- quali.json


def build_quali(
    meta: SessionMeta,
    openf1_results: Rows,
    idmap: IdMap,
    jolpica_rows: Rows | None = None,
    f1db_codes: dict[int, str] | None = None,
) -> Quali:
    reached = _segments_reached(openf1_results, jolpica_rows, idmap)
    drivers = []
    for r in sorted(openf1_results, key=_position_key):
        q1, q2, q3 = _segment_times(r)
        drivers.append(
            QualiDriver(
                driver_id=idmap.drivers[r["driver_number"]],
                team_id=idmap.teams[r["driver_number"]],
                position=_position(r),
                q1_ms=q1,
                q2_ms=q2,
                q3_ms=q3,
                knockout=reached.get(idmap.drivers[r["driver_number"]]),
                **_quali_status_fields(r, f1db_codes, meta),
            )
        )
    last = _last_segment(reached)
    eliminated: dict[str, int] = {}
    for d in drivers:
        if d.knockout and d.knockout != last:
            eliminated[d.knockout] = eliminated.get(d.knockout, 0) + 1
    segments = SEGMENTS.index(last) + 1 if last else 0
    return Quali(**meta.fields(), segments=segments, eliminated=eliminated, drivers=drivers)


F1DB_QUALI_CODES: dict[str, QualiStatus] = {"DSQ": "dsq", "EX": "dsq", "DNS": "dns", "NC": "noTime", "DNQ": "noTime"}


def quali_status(r: dict[str, Any], f1db_code: str | None) -> tuple[QualiStatus, str | None]:
    """(status, source code). F1DB's qualifying positionText when it has the session ("DSQ", "NC", "7"), else
    OpenF1: its dsq/dns flags, then no segment time at all = noTime. The code is null for a normal classification."""
    if f1db_code is not None:
        if f1db_code.isdigit():
            return "classified", None
        if f1db_code in F1DB_QUALI_CODES:
            return F1DB_QUALI_CODES[f1db_code], f1db_code
        log.warning("quali: unknown F1DB positionText %r for car %s; using OpenF1", f1db_code, r["driver_number"])
    code = r["position"] if isinstance(r.get("position"), str) else None
    if r.get("dsq"):
        return "dsq", code
    if r.get("dns"):
        return "dns", code
    if all(t is None for t in _segment_times(r)):
        return "noTime", code
    return "classified", code


def _quali_status_fields(r: dict[str, Any], f1db_codes: dict[int, str] | None, meta: SessionMeta) -> dict[str, Any]:
    code = f1db_codes.get(r["driver_number"]) if f1db_codes else None
    if f1db_codes and code is None:
        log.warning("quali: car %s missing from F1DB %s/%s/%s", r["driver_number"], meta.year, meta.round, meta.session)
    status, text = quali_status(r, code)
    return {"status": status, "status_text": text}


def f1db_quali_codes(f1db: dict[str, Any], race_day: date, sprint: bool) -> dict[int, str] | None:
    """Car number -> F1DB qualifying positionText for the race within ±1 day, or None if F1DB lacks it yet."""
    key = "sprintQualifyingResults" if sprint else "qualifyingResults"
    for race in f1db.get("races", []):
        if abs(date.fromisoformat(race["date"]) - race_day) <= timedelta(days=1) and race.get(key):
            return {int(x["driverNumber"]): x["positionText"] for x in race[key] if x.get("driverNumber")}
    return None


def _segment_times(r: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    d = r.get("duration")
    values = d if isinstance(d, list) else [d]
    padded = [*values, None, None, None][:3]
    return ms(padded[0]), ms(padded[1]), ms(padded[2])


def _knockout(times: tuple[int | None, ...]) -> str | None:
    """Last segment with a time."""
    last = None
    for seg, t in zip(SEGMENTS, times, strict=False):
        if t is not None:
            last = seg
    return last


def _segments_reached(openf1_results: Rows, jolpica_rows: Rows | None, idmap: IdMap) -> dict[str, str | None]:
    """driverId -> last segment the driver took part in.

    Jolpica lists a segment key even with an empty time when a driver reached it but set no lap
    (e.g. `"Q2": ""`), so it is preferred. Without Jolpica (sprint qualifying) fall back to the last
    segment with a time.
    """
    out: dict[str, str | None] = {
        idmap.drivers[r["driver_number"]]: _knockout(_segment_times(r)) for r in openf1_results
    }
    for row in jolpica_rows or []:
        keys = [seg for seg in SEGMENTS if seg.upper() in row]
        if keys:
            out[row["Driver"]["driverId"]] = keys[-1]
    return out


def _last_segment(reached: dict[str, str | None]) -> str | None:
    """Final segment actually run in the session (derived, not assumed to be Q3)."""
    return max((k for k in reached.values() if k), key=SEGMENTS.index, default=None)


# ---------------------------------------------------------------- overtakes.json


def build_overtakes(meta: SessionMeta, rows: Rows, idmap: IdMap) -> Overtakes:
    return Overtakes(
        **meta.fields(),
        overtakes=[
            Overtake(
                utc=parse_utc(r["date"]),
                overtaker_id=idmap.drivers[r["overtaking_driver_number"]],
                overtaken_id=idmap.drivers[r["overtaken_driver_number"]],
                position=r["position"],
            )
            for r in sorted(rows, key=lambda r: (parse_utc(r["date"]), r["position"]))
            if r["overtaking_driver_number"] in idmap.drivers and r["overtaken_driver_number"] in idmap.drivers
        ],
    )


# ---------------------------------------------------------------- helpers


def _position(r: dict[str, Any]) -> int | None:
    """OpenF1 positions are ints, but non-classified entries can carry codes such as "RT"."""
    p = r.get("position")
    return p if isinstance(p, int) else None


def _position_key(r: dict[str, Any]) -> tuple[int, int]:
    p = _position(r)
    return (0, p) if p is not None else (1, r["driver_number"])


def _tyre_on_lap(stints: Rows, driver: int, lap: int) -> str | None:
    for s in stints:
        if s["driver_number"] == driver and (s.get("lap_start") or 0) <= lap <= (s.get("lap_end") or 0):
            return s["compound"].lower() if s.get("compound") else None
    return None


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]
