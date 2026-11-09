"""Per-session builders: summary (per session type), laps, strategy, race control, quali, overtakes."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
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
    PitStop,
    PracticeEntry,
    PracticeSummary,
    Quali,
    QualiDriver,
    QualifyingEntry,
    QualifyingSummary,
    RaceControl,
    RaceControlMessage,
    RaceSummary,
    Range,
    Stint,
    Strategy,
    Weather,
)

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
            )
        )
        fl = row.get("FastestLap")
        if fl and fl.get("rank") == "1":
            fastest = FastestLap(driver_id=driver_id, lap=int(fl["lap"]), ms=clock_ms(fl["Time"]["time"]))
    return RaceSummary(**meta.fields(), results=results, fastest_lap=fastest, weather=build_weather(weather))


def build_qualifying_summary(
    meta: SessionMeta, openf1_results: Rows, weather: Rows, idmap: IdMap, jolpica_rows: Rows | None = None
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


def build_strategy(meta: SessionMeta, stints: Rows, pit: Rows, idmap: IdMap) -> Strategy:
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
    for p in sorted(pit, key=lambda p: (p["driver_number"], p["date"])):
        out.setdefault(idmap.drivers[p["driver_number"]], {"stints": [], "stops": []})["stops"].append(
            PitStop(
                lap=p["lap_number"],
                utc=parse_utc(p["date"]),
                lane_ms=ms(p.get("lane_duration") if p.get("lane_duration") is not None else p.get("pit_duration")),
                stop_ms=ms(p.get("stop_duration")),
            )
        )
    return Strategy(**meta.fields(), drivers={k: DriverStrategy(**v) for k, v in sorted(out.items())})


# ---------------------------------------------------------------- race-control.json

PENALTY_PATTERNS = [
    (re.compile(r"(\d+) SECOND TIME PENALTY FOR CAR (\d+)"), "time"),
    (re.compile(r"(\d+) SECOND STOP[/ ]?(?:AND )?GO PENALTY FOR CAR (\d+)"), "stopGo"),
    (re.compile(r"()STOP[/ ]?(?:AND )?GO PENALTY FOR CAR (\d+)"), "stopGo"),
    (re.compile(r"()DRIVE[ -]THROUGH PENALTY FOR CAR (\d+)"), "driveThrough"),
    (re.compile(r"(\d+) PLACE GRID PENALTY FOR CAR (\d+)"), "grid"),
]
NOT_ISSUED = ("PENALTY SERVED", "WITHDRAWN", "REVOKED")


def parse_penalty(message: str, idmap: IdMap) -> Penalty | None:
    if any(word in message for word in NOT_ISSUED):
        return None
    for pattern, kind in PENALTY_PATTERNS:
        m = pattern.search(message)
        if m:
            driver = idmap.driver(int(m.group(2)))
            if driver is None:
                return None
            return Penalty(driver=driver, kind=kind, seconds=int(m.group(1)) if m.group(1) else None)
    return None


def build_race_control(meta: SessionMeta, rows: Rows, idmap: IdMap) -> RaceControl:
    messages = [
        RaceControlMessage(
            utc=parse_utc(r["date"]),
            lap=r.get("lap_number"),
            category=_lower_first(r.get("category") or "other"),
            flag=r["flag"].lower() if r.get("flag") else None,
            scope=r["scope"].lower() if r.get("scope") else None,
            sector=r.get("sector"),
            driver_id=idmap.driver(r.get("driver_number")),
            message=r["message"],
            penalty=parse_penalty(r["message"], idmap),
        )
        for r in sorted(rows, key=lambda r: parse_utc(r["date"]))
    ]
    return RaceControl(**meta.fields(), messages=messages)


# ---------------------------------------------------------------- quali.json


def build_quali(meta: SessionMeta, openf1_results: Rows, idmap: IdMap, jolpica_rows: Rows | None = None) -> Quali:
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
            )
        )
    last = _last_segment(reached)
    eliminated: dict[str, int] = {}
    for d in drivers:
        if d.knockout and d.knockout != last:
            eliminated[d.knockout] = eliminated.get(d.knockout, 0) + 1
    segments = SEGMENTS.index(last) + 1 if last else 0
    return Quali(**meta.fields(), segments=segments, eliminated=eliminated, drivers=drivers)


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
