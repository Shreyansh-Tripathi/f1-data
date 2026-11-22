"""replay.json: builder units on synthetic tracks, the detectors on recorded windows (Las Vegas start, Brazil car 18's
retirement, Singapore sprint lap 1-2 retirements), and an offline end-to-end build of Brazil."""

import gzip
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import fixture_client

from f1data.build import replay as R
from f1data.cli import main
from f1data.sources import openf1

FIXTURES = Path(__file__).parent / "fixtures" / "replay"
VEGAS_START = R.ms("2026-11-22T04:03:46.118+00:00")
BRAZIL_START = R.ms("2026-11-08T17:03:21.863+00:00")
SPRINT_START = R.ms("2026-10-10T09:36:30.869+00:00")


def iso(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC).isoformat()


def rows(points) -> list[dict]:
    return [{"date": iso(t), "x": x, "y": y} for t, x, y in points]


def recorded(name: str) -> dict[str, list[list[int]]]:
    return json.loads(gzip.decompress((FIXTURES / f"{name}.json.gz").read_bytes()))


def track(points, origin: int, first=-(10**9), last=10**9):
    return R.resample(R.clean(rows(points), origin), first, last)


# ---------------------------------------------------------------- units


def test_clean_drops_placeholders_and_glitches_and_reanchors():
    pts = [(0, 0, 0), (1000, 100, 0), (2000, 200, 0), (2500, 9000, 9000), (3000, 300, 0)]
    assert R.clean(rows(pts), 0) == [(1000, 100, 0), (2000, 200, 0), (3000, 300, 0)]
    # A glitched first sample would reject every real one: after REANCHOR rejections the new position is trusted.
    far = [(0, 90000, 90000)] + [(1000 * i, 100 * i, 0) for i in range(1, 10)]
    kept = R.clean(rows(far), 0)
    assert kept[0] == (0, 90000, 90000)
    assert kept[1:] == [(1000 * i, 100 * i, 0) for i in range(R.REANCHOR + 1, 10)]


def test_resample_interpolates_short_gaps_and_holds_long_ones():
    samples = [(500, 0, 0), (2500, 200, 100), (20_000, 900, 900)]
    t0, xs, ys = R.resample(samples, -5000, 22_000)
    assert t0 == 1000  # the first grid time with data
    assert (xs[0], ys[0]) == (50, 25)  # interpolated between 500 and 2500
    assert (xs[1], ys[1]) == (150, 75)
    assert (xs[5], ys[5]) == (200, 100)  # 17.5 s gap: held
    assert (xs[-1], ys[-1]) == (900, 900)


def test_delta_encoding_round_trips():
    values = [120, 118, 118, -40, 7]
    encoded = R.delta(values)
    assert encoded == [120, -2, 0, -158, 47]
    decoded = [sum(encoded[: i + 1]) for i in range(len(encoded))]
    assert decoded == values


def test_stopped_spells_on_track_only():
    # Moving, 25 s still, moving again; a second still spell inside a pit stop doesn't count.
    xs = list(range(0, 3000, 100)) + [3000] * 25 + list(range(3100, 6000, 100)) + [6000] * 25 + [6100, 6200]
    ys = [0] * len(xs)
    spells = R.stopped_spells(0, xs, ys, until=10**9, exclude=[[85_000, 115_000]])
    assert spells == [[30_000, 54_000]]  # arrived at 30 s, pulled away at 54 s


def test_spells_shared_by_half_the_field_are_dropped():
    # A feed gap holds every car at once (Britain 2026 sprint, 28 s); one car stopping alone stays.
    field = {f"d{i}": [[1_321_000, 1_349_000]] for i in range(4)}
    field["d0"] = [[1_321_000, 1_349_000], [2_000_000, 2_030_000]]
    assert R.own_stops(field, running=4) == {"d0": [[2_000_000, 2_030_000]], "d1": [], "d2": [], "d3": []}


def test_end_place_near_the_pit_lane_path():
    pit_points = [(0, 0), (500, 0), (1000, 0)]
    assert R.end_place(600, 250, pit_points) == "pitLane"  # 25 m from the pit lane path
    assert R.end_place(600, 400, pit_points) == "track"


def test_interval_values():
    assert R.interval_value({"interval": 1.234}) == [1200]
    assert R.interval_value({"interval": 0.05}) == [100]  # rounded to 100 ms
    assert R.interval_value({"interval": "+2 LAPS"}) == [None, 2]
    assert R.interval_value({"interval": None}) == [None]


def test_changes_keep_the_value_set_before_the_window():
    rows_ = [{"date": iso(t), "position": p} for t, p in [(-9000, 5), (-2000, 4), (3000, 4), (8000, 3)]]
    assert R.changes(rows_, lambda r: [r["position"]], 0, -1000, 10_000) == [[-1000, 4], [8000, 3]]


# ---------------------------------------------------------------- recorded windows


def test_formation_lap_starts_before_lights_out_las_vegas():
    tracks = [track(p, VEGAS_START, -R.FORMATION_LOOKBACK_MS, 0) for p in recorded("vegas_start").values()]
    start = R.formation_start(tracks)
    assert start is not None and -260_000 <= start <= -150_000
    assert start % R.STEP_MS == 0


def test_no_formation_lap_without_movement_falls_back_to_lights_out():
    parked = [(t, 100, 100) for t in range(-600_000, 0, 250)]
    assert R.formation_start([R.resample(parked, -R.FORMATION_LOOKBACK_MS, 0)]) is None


def test_retirement_ends_at_the_last_movement_brazil_car_18():
    t0, xs, ys = track(recorded("brazil_car18_retirement")["18"], BRAZIL_START)
    # Lap 31 starts at 2605 s; the car stops on track 10 s later and OpenF1 keeps sending its position.
    assert 2_600_000 <= R.last_movement(t0, xs, ys) <= 2_630_000


@pytest.mark.parametrize(("car", "low", "high"), [(18, 0, 200_000), (63, 0, 200_000), (6, 250_000, 400_000)])
def test_sprint_lap_one_and_two_retirements_singapore(car, low, high):
    t0, xs, ys = track(recorded("singapore_sprint_retirements")[str(car)], SPRINT_START)
    assert low < R.last_movement(t0, xs, ys) <= high


# ---------------------------------------------------------------- end to end (Brazil, synthetic location)


def synthetic_location(origin: int, stops: dict[int, int]):
    """Every car laps a circle at 50 m/s from the formation lap (-200 s); a car in `stops` parks at that clock time."""

    def fetch(client, session_key, driver_number, start, end, *, final=False, cache=True):
        assert cache is False  # replay location is never cached
        a = R.ms(start + "+00:00") - origin
        b = R.ms(end + "+00:00") - origin
        out = []
        for t in range(a, b, 250):
            moving_from = -200_000
            t_eff = min(t, stops.get(driver_number, 10**12))
            run = max(0, t_eff - moving_from) / 1000 * 500  # dm travelled
            angle = run / 50_000 + driver_number
            out.append(
                {"date": iso(origin + t), "x": round(50_000 * math.cos(angle)), "y": round(50_000 * math.sin(angle))}
            )
        return out

    return fetch


def test_end_to_end_brazil(tmp_path, monkeypatch):
    calls = []
    fetch = synthetic_location(BRAZIL_START, {18: 2_615_000})
    monkeypatch.setattr(openf1, "location", lambda *a, **k: calls.append(a[2]) or fetch(*a, **k))
    out = tmp_path / "site"
    now = datetime(2026, 11, 9, 12, tzinfo=UTC)  # inside the corrections window: not final
    args = ["build", "--year", "2026", "--round", "20", "--session", "race", "--out", str(out)]
    assert main(args, client=fixture_client(tmp_path / "cache"), now=now) == 0
    path = out / "2026/20/race/replay.json"
    raw = path.read_bytes()
    replay = json.loads(raw)
    assert replay["schemaVersion"] == 1 and replay["final"] is False and replay["stepMs"] == 1000
    assert len(gzip.compress(raw)) < 1_000_000
    assert "2026/20/race/replay.json" in json.loads((out / "manifest.json").read_text())["files"]
    assert -210_000 <= min(d["t0"] for d in replay["drivers"].values() if d["t0"] is not None) <= -190_000
    stroll, bottas = replay["drivers"]["stroll"], replay["drivers"]["bottas"]
    # Parked at 2615 s; samples aren't aligned to the 1 Hz clock, so within one step.
    assert abs(stroll["end"]["t"] - 2_615_000) <= R.STEP_MS and stroll["end"]["status"] == "retired"
    assert len(stroll["x"]) == (stroll["end"]["t"] - stroll["t0"]) // 1000 + 1
    assert bottas["end"]["status"] == "dns" and bottas["x"] == []
    norris = replay["drivers"]["norris"]
    assert norris["end"]["status"] == "finished" and norris["laps"][0] == [1, 0]
    assert norris["laps"][-1][0] == 72  # 71 laps plus the finish
    assert norris["pos"] and norris["int"] and norris["pit"]
    assert replay["startLine"] is not None and replay["events"]

    # A later run refreshes the header without refetching location.
    calls.clear()
    later = datetime(2026, 11, 12, tzinfo=UTC)
    assert main(args, client=fixture_client(tmp_path / "cache"), now=later) == 0
    assert calls == []
    refreshed = json.loads(path.read_text())
    assert refreshed["final"] is True and refreshed["drivers"] == replay["drivers"]


def test_a_final_session_without_a_replay_is_not_retried(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(openf1, "location", lambda *a, **k: calls.append(a) or [])
    out = tmp_path / "site"
    args = ["build", "--year", "2026", "--round", "20", "--session", "race", "--out", str(out)]
    assert main(args, client=fixture_client(tmp_path / "cache"), now=datetime(2026, 11, 12, tzinfo=UTC)) == 0
    assert calls == [] and not (out / "2026/20/race/replay.json").exists()
