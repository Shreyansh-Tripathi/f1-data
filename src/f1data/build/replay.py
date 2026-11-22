"""replay.json: every car's position at 1 Hz on one race clock (0 = lights out), with laps, positions, intervals, pit
and stopped spells, retirements, periods and key race-control events on the same clock."""

from __future__ import annotations

import bisect
import math
import re
import statistics
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from f1data.build.ids import IdMap, parse_utc
from f1data.build.session import SessionMeta
from f1data.models.replay import (
    Replay,
    ReplayDriver,
    ReplayEnd,
    ReplayEndPlace,
    ReplayEvent,
    ReplayPeriod,
    ReplayStatus,
)

Rows = list[dict[str, Any]]

STEP_MS = 1000
MAX_SPEED = 1200  # dm/s (120 m/s): a faster jump between two samples is a glitch, dropped
REANCHOR = 5  # rejections in a row before trusting the new position
MAX_GAP_MS = 10_000  # longer gaps hold the last position instead of interpolating
FORMATION_LOOKBACK_MS = 15 * 60_000  # formation lap starts within this before lights out
STILL_MS = 60_000  # parked on the grid / in the pit lane this long before the formation lap
MOVING = 50  # dm per second (5 m/s): moving
STOPPED = 10  # dm per second (1 m/s): stationary
STOPPED_MIN_MS = 20_000  # a stationary spell on track at least this long counts as stopped
SUSTAINED_MS = 10_000  # a retired car's last movement: covers SUSTAINED_DM over this span
SUSTAINED_DM = 200
FINISH_MARGIN_MS = 15_000
CHEQUERED_CAP_MS = 180_000  # the window never runs more than this past the chequered flag
PIT_LANE_DM = 300  # a car stopping within 30 m of where cars drove through the pit lane stopped in the pit lane
KEY_EVENTS = {"safetyCar", "penalty", "warning", "investigation", "noFurtherAction", "noted", "lapDeleted"}
LAPS_DOWN = re.compile(r"\+(\d+) LAPS?")
STATUS: dict[str, ReplayStatus] = {"finished": "finished", "dnf": "retired", "dns": "dns", "dsq": "dsq"}


def ms(value: str | datetime) -> int:
    """Epoch milliseconds."""
    dt = parse_utc(value) if isinstance(value, str) else value
    return round(dt.timestamp() * 1000)


def race_start(laps: Rows, race_control: Rows) -> int | None:
    """Lights out: lap 1's start (OpenF1 gives every driver the same), else `SESSION STARTED`."""
    starts = [ms(r["date_start"]) for r in laps if r.get("lap_number") == 1 and r.get("date_start")]
    if starts:
        return min(starts)
    started = [ms(r["date"]) for r in race_control if r.get("message") == "SESSION STARTED"]
    return min(started) if started else None


def chequered(race_control: Rows) -> int | None:
    flags = [ms(r["date"]) for r in race_control if r.get("flag") == "CHEQUERED"]
    finished = [ms(r["date"]) for r in race_control if r.get("message") == "SESSION FINISHED"]
    return max(flags) if flags else (max(finished) if finished else None)


# ---------------------------------------------------------------- samples


def clean(rows: Iterable[dict[str, Any]], origin: int) -> list[tuple[int, int, int]]:
    """(t, x, y) on the race clock, in time order, without (0,0) placeholders or glitches (faster than MAX_SPEED
    from the last kept sample). After REANCHOR rejections in a row the last kept sample was the glitch: keep going
    from the current one."""
    out: list[tuple[int, int, int]] = []
    rejected = 0
    for r in sorted(rows, key=lambda r: r["date"]):
        x, y = r.get("x"), r.get("y")
        if x is None or y is None or (x == 0 and y == 0):
            continue
        t = ms(r["date"]) - origin
        if out:
            pt, px, py = out[-1]
            if t <= pt:
                continue
            if math.hypot(x - px, y - py) / ((t - pt) / 1000) > MAX_SPEED and rejected < REANCHOR:
                rejected += 1
                continue
        rejected = 0
        out.append((t, x, y))
    return out


def resample(samples: list[tuple[int, int, int]], first: int, last: int) -> tuple[int | None, list[int], list[int]]:
    """Positions every STEP_MS on the shared clock from `first` to `last` (both multiples of STEP_MS), starting at the
    first grid time with data. Gaps up to MAX_GAP_MS are interpolated; longer ones hold the last position."""
    if not samples:
        return None, [], []
    times = [s[0] for s in samples]
    start = max(first, math.ceil(samples[0][0] / STEP_MS) * STEP_MS)
    xs: list[int] = []
    ys: list[int] = []
    for g in range(start, last + 1, STEP_MS):
        i = bisect.bisect_left(times, g)
        if i < len(times) and times[i] == g:
            _, x, y = samples[i]
        elif i == 0:
            _, x, y = samples[0]
        elif i == len(times):
            _, x, y = samples[-1]
        else:
            (ta, xa, ya), (tb, xb, yb) = samples[i - 1], samples[i]
            if tb - ta <= MAX_GAP_MS:
                f = (g - ta) / (tb - ta)
                x, y = round(xa + (xb - xa) * f), round(ya + (yb - ya) * f)
            else:
                x, y = xa, ya
        xs.append(x)
        ys.append(y)
    return (start if xs else None), xs, ys


def speeds(xs: list[int], ys: list[int]) -> list[float]:
    """dm per step, the first 0."""
    return [0.0] + [math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]) for i in range(1, len(xs))]


def formation_start(tracks: Iterable[tuple[int | None, list[int], list[int]]]) -> int | None:
    """When the formation lap began: the median over cars of the first movement after standing still for STILL_MS,
    within FORMATION_LOOKBACK_MS before lights out. None when fewer than half the cars show it."""
    starts, total = [], 0
    for t0, xs, ys in tracks:
        if t0 is None:
            continue
        total += 1
        v = speeds(xs, ys)
        still = 0
        pulled_away = None  # when the car last left a long still spell
        found = None
        for i, s in enumerate(v):
            t = t0 + i * STEP_MS
            if t >= -STILL_MS:  # the grid wait just before lights out isn't the formation lap
                break
            if t < -FORMATION_LOOKBACK_MS:
                continue
            if s < STOPPED:
                still += 1
                continue
            if still * STEP_MS >= STILL_MS:
                pulled_away = t
            still = 0
            # A car pulls away through low speeds: confirm the movement within 10 s.
            if s >= MOVING and pulled_away is not None and t - pulled_away <= 10_000:
                found = pulled_away
        if found is not None:
            starts.append(found)
    if total == 0 or len(starts) * 2 < total:
        return None
    return int(statistics.median(starts)) // STEP_MS * STEP_MS


def last_movement(t0: int, xs: list[int], ys: list[int]) -> int:
    """Clock time of a retired car's last sustained movement (SUSTAINED_DM within SUSTAINED_MS)."""
    span = SUSTAINED_MS // STEP_MS
    v = speeds(xs, ys)
    for i in range(len(xs) - 1, span - 1, -1):
        if math.hypot(xs[i] - xs[i - span], ys[i] - ys[i - span]) >= SUSTAINED_DM:
            # The span ends after the car stopped: back up to its last moving sample.
            while i > 0 and v[i] < STOPPED:
                i -= 1
            return t0 + i * STEP_MS
    return t0


def stopped_spells(
    t0: int, xs: list[int], ys: list[int], until: int, exclude: list[tuple[int, int]]
) -> list[list[int]]:
    """Stationary on track for STOPPED_MIN_MS or more, then moving again, from lights out to `until`; spells
    overlapping `exclude` (pit stops, red flags) don't count."""
    out: list[list[int]] = []
    v = speeds(xs, ys)
    run_start = None
    for i, s in enumerate(v):
        t = t0 + i * STEP_MS
        if t < 0 or t > until:
            run_start = None
            continue
        # Speed at t covers the step before it: a still sample means still since t - STEP_MS.
        if s < STOPPED:
            run_start = t - STEP_MS if run_start is None else run_start
            continue
        moved = t - STEP_MS
        if (
            run_start is not None
            and moved - run_start >= STOPPED_MIN_MS
            and not any(a <= moved and run_start <= b for a, b in exclude)
        ):
            out.append([run_start, moved])
        run_start = None
    return out


def own_stops(spells: dict[str, list[list[int]]], running: int) -> dict[str, list[list[int]]]:
    """Spells shared by half the running field or more are a feed gap (positions held) or a field-wide stop (restart
    queue), not a car stopping: dropped."""

    def shared(a: int, b: int) -> int:
        return sum(any(c <= b and a <= d for c, d in other) for other in spells.values())

    return {d: [s for s in v if shared(*s) * 2 < running] for d, v in spells.items()}


def end_place(x: int, y: int, pit_points: list[tuple[int, int]]) -> ReplayEndPlace:
    """pitLane when the car stopped within PIT_LANE_DM of where cars drove through the pit lane, else track."""
    near = any(math.hypot(x - px, y - py) <= PIT_LANE_DM for px, py in pit_points)
    return "pitLane" if near else "track"


def delta(values: list[int]) -> list[int]:
    return values[:1] + [values[i] - values[i - 1] for i in range(1, len(values))]


# ---------------------------------------------------------------- series


def changes(rows: Rows, value, origin: int, first: int, last: int) -> list[list[Any]]:
    """[t, value] whenever `value(row)` changes; the value already set before `first` is placed at `first`."""
    out: list[list[Any]] = []
    for r in sorted(rows, key=lambda r: r["date"]):
        t = max(ms(r["date"]) - origin, first)
        if t > last:
            break
        v = value(r)
        if out and out[-1][1:] == v:
            continue
        if out and out[-1][0] == t:  # several before `first`: keep the latest
            out[-1] = [t, *v]
        else:
            out.append([t, *v])
    return out


def interval_value(r: dict[str, Any]) -> list[int | None]:
    v = r.get("interval")
    if isinstance(v, (int, float)):
        return [math.floor(v * 10 + 0.5) * 100]  # half up, not to even
    if isinstance(v, str) and (m := LAPS_DOWN.match(v)):
        return [None, int(m.group(1))]
    return [None]  # leading (or unknown)


# ---------------------------------------------------------------- build


def build_replay(
    meta: SessionMeta,
    idmap: IdMap,
    locations: dict[int, Rows],
    laps: Rows,
    positions: Rows,
    intervals: Rows,
    openf1_race_control: Rows,
    summary: dict[str, Any],
    strategy: dict[str, Any],
    race_control: dict[str, Any],
) -> Replay | None:
    """None when lights out or the chequered flag can't be placed. `summary`, `strategy` and `race_control` are the
    session's published files."""
    origin = race_start(laps, openf1_race_control)
    flag = chequered(openf1_race_control)
    if origin is None or flag is None:
        return None
    flag -= origin
    results = {r["driverId"]: r for r in summary.get("results", [])}
    by_number = {n: d for n, d in idmap.drivers.items() if d in results}

    # Finishers' last lap end bounds the window, capped past the chequered flag.
    lap_rows: dict[int, Rows] = {}
    for r in laps:
        lap_rows.setdefault(r["driver_number"], []).append(r)
    finish: dict[int, int] = {}
    for n, rows in lap_rows.items():
        done = [r for r in rows if r.get("date_start") and r.get("lap_duration")]
        if done and results.get(by_number.get(n, ""), {}).get("classification") in ("finished", "dsq"):
            last = max(done, key=lambda r: r["lap_number"])
            finish[n] = ms(last["date_start"]) - origin + round(last["lap_duration"] * 1000)
    end = min(max(finish.values(), default=flag), flag + CHEQUERED_CAP_MS) + FINISH_MARGIN_MS
    end = end // STEP_MS * STEP_MS
    lookback = -FORMATION_LOOKBACK_MS

    samples = {n: clean(locations.get(n, []), origin) for n in by_number}
    early = {n: resample(s, lookback, 0) for n, s in samples.items()}
    first = formation_start(early.values())
    first = 0 if first is None else first

    stops = {
        d: [[ms(s["utc"]) - origin - s["laneMs"], ms(s["utc"]) - origin] for s in v.get("stops", [])]
        for d, v in strategy.get("drivers", {}).items()
    }
    reds = [
        (ms(p["startTime"]) - origin, ms(p["endTime"]) - origin)
        for p in summary.get("periods", [])
        if p["kind"] == "red"
    ]
    tracks = {n: resample(s, first, end) for n, s in samples.items()}

    # Where cars actually drove through the pit lane, to tell a pit-lane retirement from one on track.
    pit_points: list[tuple[int, int]] = []
    for n, (t0, xs, ys) in tracks.items():
        if t0 is None:
            continue
        for a, b in stops.get(by_number[n], []):
            for t in range(max(a, t0), min(b, t0 + (len(xs) - 1) * STEP_MS) + 1, 5 * STEP_MS):
                i = (t - t0) // STEP_MS
                pit_points.append((xs[i], ys[i]))

    by_driver_rows = _by_driver(positions), _by_driver(intervals)
    drivers: dict[str, ReplayDriver] = {}
    for n, driver_id in sorted(by_number.items(), key=lambda kv: kv[1]):
        result = results[driver_id]
        status = STATUS.get(result["classification"], "finished")
        t0, xs, ys = tracks[n]
        if status == "dns" or t0 is None:
            drivers[driver_id] = _empty(result["teamId"], "dns" if status == "dns" else status)
            continue
        stop_t = end
        if status == "retired":
            stop_t = min(last_movement(t0, xs, ys), end)
            keep = (stop_t - t0) // STEP_MS + 1
            xs, ys = xs[:keep], ys[:keep]
        where = end_place(xs[-1], ys[-1], pit_points) if status == "retired" else "track"
        pit = [p for p in stops.get(driver_id, []) if p[0] <= stop_t]
        lap_starts = sorted(
            [r["lap_number"], ms(r["date_start"]) - origin]
            for r in lap_rows.get(n, [])
            if r.get("date_start") and ms(r["date_start"]) - origin <= stop_t
        )
        if n in finish:
            lap_starts.append([lap_starts[-1][0] + 1 if lap_starts else 1, finish[n]])
        drivers[driver_id] = ReplayDriver(
            team_id=result["teamId"],
            t0=t0,
            x=delta(xs),
            y=delta(ys),
            laps=lap_starts,
            pos=changes(by_driver_rows[0].get(n, []), lambda r: [r["position"]], origin, first, stop_t),
            intervals=changes(by_driver_rows[1].get(n, []), interval_value, origin, first, stop_t),
            pit=pit,
            stopped=stopped_spells(t0, xs, ys, min(stop_t, flag), pit + [list(r) for r in reds]),
            end=ReplayEnd(t=stop_t, status=status, where=where),
        )

    kept = own_stops({d: v.stopped for d, v in drivers.items()}, sum(v.t0 is not None for v in drivers.values()))
    drivers = {d: v.model_copy(update={"stopped": kept[d]}) for d, v in drivers.items()}
    return Replay(
        **meta.fields(),
        start_utc=datetime.fromtimestamp(origin / 1000, tz=UTC),
        step_ms=STEP_MS,
        duration_ms=end,
        start_line=_start_line(tracks, lap_rows, origin),
        periods=[
            ReplayPeriod(kind=p["kind"], start_t=ms(p["startTime"]) - origin, end_t=ms(p["endTime"]) - origin)
            for p in summary.get("periods", [])
            if ms(p["endTime"]) - origin >= first and ms(p["startTime"]) - origin <= end
        ],
        events=[
            ReplayEvent(
                t=ms(m["utc"]) - origin, kind=m["kind"], text=m["shortText"] or m["text"], driver_ids=m["driverIds"]
            )
            for m in race_control.get("messages", [])
            if (m["kind"] in KEY_EVENTS or "RED FLAG" in m["message"]) and first <= ms(m["utc"]) - origin <= end
        ],
        drivers=drivers,
    )


def refresh(existing: dict[str, Any], meta: SessionMeta, summary: dict[str, Any]) -> Replay:
    """The published replay with a new header and statuses from the latest classification; positions are unchanged
    (no refetch). A car newly classified as retired keeps its samples until the next full rebuild."""
    results = {r["driverId"]: r for r in summary.get("results", [])}
    replay = Replay.model_validate({**existing, **{k: v for k, v in _header(meta).items()}})
    drivers = {}
    for driver_id, d in replay.drivers.items():
        status = STATUS.get(results.get(driver_id, {}).get("classification", ""), d.end.status)
        drivers[driver_id] = d.model_copy(update={"end": d.end.model_copy(update={"status": status})})
    return replay.model_copy(update={"drivers": drivers})


def _header(meta: SessionMeta) -> dict[str, Any]:
    f = meta.fields()
    return {"provisional": f["provisional"], "final": f["final"], "generatedAt": f["generated_at"]}


def _empty(team_id: str, status: ReplayStatus) -> ReplayDriver:
    return ReplayDriver(
        team_id=team_id,
        t0=None,
        x=[],
        y=[],
        laps=[],
        pos=[],
        intervals=[],
        pit=[],
        stopped=[],
        end=ReplayEnd(t=0, status=status, where="pitLane"),
    )


def _by_driver(rows: Rows) -> dict[int, Rows]:
    out: dict[int, Rows] = {}
    for r in rows:
        out.setdefault(r["driver_number"], []).append(r)
    return out


def _start_line(tracks: dict[int, tuple[int | None, list[int], list[int]]], lap_rows: dict[int, Rows], origin: int):
    """Median car position at lap starts from lap 2 on (lap 1 starts on the grid)."""
    xs, ys = [], []
    for n, (t0, px, py) in tracks.items():
        if t0 is None:
            continue
        for r in lap_rows.get(n, []):
            if (r.get("lap_number") or 0) < 2 or not r.get("date_start"):
                continue
            i = round((ms(r["date_start"]) - origin - t0) / STEP_MS)
            if 0 <= i < len(px):
                xs.append(px[i])
                ys.append(py[i])
    if not xs:
        return None
    return [int(statistics.median(xs)), int(statistics.median(ys))]
