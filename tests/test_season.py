from datetime import UTC, datetime

import pytest

from f1data.build import ids
from f1data.build.season import build_season
from f1data.sources import f1db

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)


def test_rounds_match_meetings_skipping_cancelled(season_data):
    weekends = {w.round: w for w in season_data["weekends"]}
    assert len(weekends) == 23
    assert weekends[20].meeting_key == 1299
    assert weekends[20].circuit_id == "interlagos"
    assert weekends[20].circuit_info_url == "https://api.multiviewer.app/api/v1/circuits/14/2026"
    cancelled = {m["meeting_key"] for m in ids.cancelled_meetings(season_data["meetings"])}
    assert cancelled == {1282, 1283}
    assert not {w.meeting_key for w in weekends.values()} & cancelled


def test_match_tolerates_one_day_offset(season_data):
    schedule = [dict(r) for r in season_data["schedule"] if r["round"] == "20"]
    schedule[0]["date"] = "2026-11-09"  # exactly one day later than OpenF1 says
    assert ids.match_weekends(schedule, season_data["meetings"], season_data["sessions"])[0].meeting_key == 1299
    schedule[0]["date"] = "2026-11-10"  # 2 days off: no match
    assert ids.match_weekends(schedule, season_data["meetings"], season_data["sessions"])[0].meeting_key is None


@pytest.fixture(scope="module")
def db(shared_client):
    return f1db.download_json_single(shared_client, f1db.latest_release(shared_client))


def test_build_season(season_data):
    completed = {int(r["round"]) for r in season_data["results"]}
    cancelled = ids.cancelled_meetings(season_data["meetings"])
    season = build_season(2026, season_data["weekends"], cancelled, completed, season_data["results"], {}, NOW)
    by_round = {r.round: r for r in season.rounds}
    assert by_round[20].status == "completed"
    assert by_round[21].status == "upcoming"
    assert [s.code for s in by_round[20].sessions] == ["fp1", "fp2", "fp3", "q", "race"]
    assert sum(r.sprint for r in season.rounds) == 6
    assert [c.name for c in season.cancelled] == ["Bahrain Grand Prix", "Saudi Arabian Grand Prix"]
    out = season.model_dump(mode="json", by_alias=True)
    assert out["rounds"][19]["circuitId"] == "interlagos"
    assert out["rounds"][19]["start"] == "2026-11-08T17:00:00Z"


def test_short_names_and_winners(season_data, db):
    completed = {int(r["round"]) for r in season_data["results"]}
    season = build_season(2026, season_data["weekends"], [], completed, season_data["results"], db, NOW)
    by_round = {r.round: r for r in season.rounds}
    # F1DB grand prix names. Jolpica: "Brazilian Grand Prix" (20), "Bahrain Grand Prix in Malaysia" (16).
    assert by_round[1].short_name == "Australia"
    assert by_round[9].short_name == "Great Britain"
    assert by_round[16].short_name == "Bahrain"
    assert by_round[20].short_name == "São Paulo"
    assert by_round[23].short_name == "Abu Dhabi"
    for race in season_data["results"]:
        p1 = next(row for row in race["Results"] if row["position"] == "1")
        w = by_round[int(race["round"])].winner
        assert (w.driver_id, w.team_id) == (p1["Driver"]["driverId"], p1["Constructor"]["constructorId"])
    assert all(r.winner is None for r in season.rounds if r.status == "upcoming")
    out = season.model_dump(mode="json", by_alias=True)["rounds"][19]
    assert out["shortName"] == "São Paulo" and set(out["winner"]) == {"driverId", "teamId"}


def test_short_name_falls_back_to_openf1_then_jolpica(season_data):
    by_round = {w.round: w for w in season_data["weekends"]}
    season = build_season(2026, [by_round[9], by_round[20]], [], set(), [], {}, NOW)
    assert [r.short_name for r in season.rounds] == ["British", "São Paulo"]  # OpenF1 meeting names
    schedule = [dict(r) for r in season_data["schedule"] if r["round"] == "16"]
    schedule[0]["date"] = "2030-01-01"  # no OpenF1 meeting
    w = ids.match_weekends(schedule, season_data["meetings"], season_data["sessions"])[0]
    assert build_season(2026, [w], [], set(), [], {}, NOW).rounds[0].short_name == "Bahrain"


def test_session_ids_resolve_race_numbers(season_data, shared_client):
    from f1data.sources import openf1

    race = next(r for r in season_data["results"] if r["round"] == "20")
    idmap = ids.session_ids(openf1.drivers(shared_client, 11412), ids.round_entries(race), season_data["known"])
    assert idmap.driver(1) == "norris"  # champion's #1, not his permanent number
    assert idmap.teams[1] == "mclaren"
    assert len(idmap.drivers) == 22
    assert set(idmap.drivers.values()) <= set(season_data["known"])
