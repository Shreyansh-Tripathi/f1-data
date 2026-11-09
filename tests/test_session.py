from datetime import UTC, datetime

import pytest

from f1data.build import ids
from f1data.build.session import (
    SessionMeta,
    build_laps,
    build_overtakes,
    build_practice_summary,
    build_quali,
    build_qualifying_summary,
    build_race_control,
    build_race_summary,
    build_strategy,
    clock_ms,
    parse_penalty,
)
from f1data.sources import openf1

NOW = datetime(2026, 11, 10, tzinfo=UTC)
RACE, Q, FP1 = 11412, 11408, 11405


@pytest.fixture(scope="module")
def brazil(season_data, shared_client):
    c = shared_client
    race_row = next(r for r in season_data["results"] if r["round"] == "20")
    entries = ids.round_entries(race_row)

    def idmap(sk):
        return ids.session_ids(openf1.drivers(c, sk), entries, season_data["known"])

    return {"c": c, "jolpica": race_row["Results"], "idmap": idmap, "qualifying": season_data["qualifying"]}


def meta(session, rnd=20):
    return SessionMeta(year=2026, round=rnd, session=session, provisional=False, generated_at=NOW)


def test_clock_ms():
    assert clock_ms("1:12.983") == 72983
    assert clock_ms("1:23:06.801") == 4986801
    assert clock_ms("58.1") == 58100


# ---------------------------------------------------------------- race summary


@pytest.fixture(scope="module")
def race_summary(brazil):
    c = brazil["c"]
    return build_race_summary(
        meta("race"),
        brazil["jolpica"],
        openf1.session_result(c, RACE),
        openf1.weather(c, RACE),
        brazil["idmap"](RACE),
        openf1.starting_grid(c, 1299),
    )


def test_race_winner_grid_and_fastest_lap(race_summary, brazil):
    s = race_summary
    assert s.type == "race"
    first = s.results[0]
    assert first.position == 1 and first.gap_ms == 0 and first.time_ms > 0
    assert first.grid == int(brazil["jolpica"][0]["grid"])
    rank1 = next(r for r in brazil["jolpica"] if r.get("FastestLap", {}).get("rank") == "1")
    assert s.fastest_lap.driver_id == rank1["Driver"]["driverId"]
    assert s.fastest_lap.driver_id != first.driver_id  # winner had fastest-lap rank 3


def test_lapped_cars_have_null_gap_and_laps_behind(race_summary):
    lapped = [r for r in race_summary.results if r.laps_behind is not None]
    assert len(lapped) == 12
    assert all(r.gap_ms is None for r in lapped)
    assert {r.laps_behind for r in lapped} == {1, 2}
    lead_lap = [r for r in race_summary.results if r.classification == "finished" and r.laps_behind is None]
    assert all(r.gap_ms is not None and r.gap_ms >= 0 for r in lead_lap)


def test_dnf_and_dns_from_position_text(race_summary):
    by_class = {r.position_text: r.classification for r in race_summary.results}
    assert by_class["R"] == "dnf" and by_class["W"] == "dns"
    dnf = next(r for r in race_summary.results if r.classification == "dnf")
    assert dnf.position is None and dnf.gap_ms is None and dnf.laps_behind is None


def test_race_summary_weather(race_summary):
    assert race_summary.weather.air_temp_c.min <= race_summary.weather.air_temp_c.max


# ---------------------------------------------------------------- qualifying / practice


def test_qualifying_summary_and_quali(brazil):
    c, idmap = brazil["c"], brazil["idmap"](Q)
    results = openf1.session_result(c, Q)
    jolpica_q = next(r for r in brazil["qualifying"] if r["round"] == "20")["QualifyingResults"]
    summary = build_qualifying_summary(meta("q"), results, openf1.weather(c, Q), idmap, jolpica_q)
    assert summary.type == "qualifying"
    assert summary.results[0].position == 1 and summary.results[0].eliminated_in is None
    q = build_quali(meta("q"), results, idmap, jolpica_q)
    assert q.segments == 3
    # Verstappen reached Q2 without setting a time: Jolpica's empty "Q2" puts him out in Q2.
    ver = next(d for d in q.drivers if d.driver_id == "max_verstappen")
    assert ver.q2_ms is None and ver.knockout == "q2"
    # Without Jolpica the "last segment with a time" fallback misplaces him.
    assert (
        next(d for d in build_quali(meta("q"), results, idmap).drivers if d.driver_id == ver.driver_id).knockout == "q1"
    )
    # Knockout counts derived from data: drivers whose last time was in Q1 / Q2.
    assert q.eliminated == {"q1": 6, "q2": 6}
    assert q.drivers[0].knockout == "q3"
    assert sum(e.eliminated_in == "q1" for e in summary.results) == 6


def test_practice_summary(brazil):
    c, idmap = brazil["c"], brazil["idmap"](FP1)
    s = build_practice_summary(
        meta("fp1"),
        openf1.session_result(c, FP1),
        openf1.laps(c, FP1),
        openf1.stints(c, FP1),
        openf1.weather(c, FP1),
        idmap,
    )
    assert s.type == "practice"
    top = s.results[0]
    assert top.position == 1 and top.best_lap_ms == 71717 and top.best_lap_number is not None
    assert top.tyre in {"soft", "medium", "hard", "intermediate", "wet"}


# ---------------------------------------------------------------- laps / strategy / overtakes


def test_laps_have_position_and_gap(brazil, race_summary):
    c, idmap = brazil["c"], brazil["idmap"](RACE)
    laps = build_laps(meta("race"), openf1.laps(c, RACE), openf1.position(c, RACE), openf1.intervals(c, RACE), idmap)
    winner = race_summary.results[0].driver_id
    w = laps.drivers[winner]
    assert w.lap[-1] == race_summary.results[0].laps
    assert w.pos[-1] == 1
    assert w.gap_ms[-1] == 0
    lapped = next(r for r in race_summary.results if r.laps_behind)
    d = laps.drivers[lapped.driver_id]
    assert d.laps_behind[-1] == lapped.laps_behind and d.gap_ms[-1] is None
    # Last-lap running order matches the classification except where post-race penalties
    # reshuffled it (Leclerc passed Lawson on the final lap, then was penalised).
    finishers = [r for r in race_summary.results if r.classification == "finished"]
    mismatched = {r.driver_id for r in finishers if laps.drivers[r.driver_id].pos[-1] != r.position}
    assert mismatched <= {"lawson", "leclerc"}


def test_strategy(brazil):
    c, idmap = brazil["c"], brazil["idmap"](RACE)
    s = build_strategy(meta("race"), openf1.stints(c, RACE), openf1.pit(c, RACE), idmap)
    assert sum(len(d.stints) for d in s.drivers.values()) > 22
    assert all(st.tyre == st.tyre.lower() for d in s.drivers.values() for st in d.stints if st.tyre)
    stops = [p for d in s.drivers.values() for p in d.stops]
    assert stops and all(isinstance(p.lane_ms, int) for p in stops if p.lane_ms is not None)


def test_overtakes(brazil):
    c, idmap = brazil["c"], brazil["idmap"](RACE)
    o = build_overtakes(meta("race"), openf1.overtakes(c, RACE), idmap)
    assert len(o.overtakes) == 297
    assert o.overtakes[0].overtaker_id in set(idmap.drivers.values())


# ---------------------------------------------------------------- race control


def test_race_control_penalties(brazil):
    c, idmap = brazil["c"], brazil["idmap"](RACE)
    rc = build_race_control(meta("race"), openf1.race_control(c, RACE), idmap)
    penalties = [(m.penalty.driver, m.penalty.kind, m.penalty.seconds) for m in rc.messages if m.penalty]
    assert (idmap.driver(23), "time", 5) in penalties
    assert (idmap.driver(27), "time", 10) in penalties
    served = [m for m in rc.messages if "PENALTY SERVED" in m.message]
    assert served and all(m.penalty is None for m in served)
    assert all(m.message for m in rc.messages)  # raw message kept


@pytest.mark.parametrize(
    ("message", "kind", "seconds"),
    [
        ("FIA STEWARDS: DRIVE THROUGH PENALTY FOR CAR 1 (NOR) - SPEEDING IN THE PIT LANE", "driveThrough", None),
        ("FIA STEWARDS: 10 SECOND STOP/GO PENALTY FOR CAR 1 (NOR) - X", "stopGo", 10),
        ("FIA STEWARDS: 3 PLACE GRID PENALTY FOR CAR 1 (NOR) - X", "grid", 3),
        ("FIA STEWARDS: INCIDENT INVOLVING CAR 1 (NOR) UNDER INVESTIGATION", None, None),
    ],
)
def test_parse_penalty_kinds(message, kind, seconds):
    idmap = ids.IdMap(drivers={1: "norris"}, teams={1: "mclaren"})
    p = parse_penalty(message, idmap)
    assert (p.kind, p.seconds) == (kind, seconds) if kind else p is None


# ---------------------------------------------------------------- sprint weekend (Singapore)


def test_sprint_weekend(season_data, shared_client):
    c = shared_client
    weekend = next(w for w in season_data["weekends"] if w.meeting_key == 1296)
    rnd = str(weekend.round)
    sprint_rows = next(r for r in season_data["sprint"] if r["round"] == rnd)["SprintResults"]
    entries = ids.round_entries(next(r for r in season_data["results"] if r["round"] == rnd))
    sk_sprint, sk_sq = weekend.sessions["sprint"]["session_key"], weekend.sessions["sq"]["session_key"]
    idmap = ids.session_ids(openf1.drivers(c, sk_sprint), entries, season_data["known"])

    summary = build_race_summary(
        meta("sprint", weekend.round),
        sprint_rows,
        openf1.session_result(c, sk_sprint),
        openf1.weather(c, sk_sprint),
        idmap,
    )
    assert summary.type == "race" and summary.session == "sprint"
    assert summary.results[0].position == 1
    assert summary.results[0].points == float(sprint_rows[0]["points"])

    sq_idmap = ids.session_ids(openf1.drivers(c, sk_sq), entries, season_data["known"])
    q = build_quali(meta("sq", weekend.round), openf1.session_result(c, sk_sq), sq_idmap)
    assert q.segments == 3
    assert sum(q.eliminated.values()) + sum(d.knockout == "q3" for d in q.drivers) <= len(q.drivers)


def test_quali_tolerates_non_numeric_openf1_position(season_data, shared_client):
    # Singapore 2026 qualifying: OpenF1 reports Hadjar (car 6) with position "RT" and no times.
    c = shared_client
    weekend = next(w for w in season_data["weekends"] if w.meeting_key == 1296)
    sk = weekend.sessions["q"]["session_key"]
    rnd = str(weekend.round)
    entries = ids.round_entries(next(r for r in season_data["results"] if r["round"] == rnd))
    idmap = ids.session_ids(openf1.drivers(c, sk), entries, season_data["known"])
    results = openf1.session_result(c, sk)
    assert any(r["position"] == "RT" for r in results)
    jq = next(r for r in season_data["qualifying"] if r["round"] == rnd)["QualifyingResults"]
    summary = build_qualifying_summary(meta("q", weekend.round), results, openf1.weather(c, sk), idmap, jq)
    hadjar = next(e for e in summary.results if e.driver_id == idmap.driver(6))
    assert hadjar.position is None and hadjar.best_ms is None
    assert summary.results[-1].driver_id == hadjar.driver_id  # unclassified entries sort last
    q = build_quali(meta("q", weekend.round), results, idmap, jq)
    assert next(d for d in q.drivers if d.driver_id == hadjar.driver_id).position is None
