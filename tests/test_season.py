from datetime import UTC, datetime

from f1data.build import ids
from f1data.build.season import build_season

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


def test_build_season(season_data):
    completed = {int(r["round"]) for r in season_data["results"]}
    cancelled = ids.cancelled_meetings(season_data["meetings"])
    season = build_season(2026, season_data["weekends"], cancelled, completed, NOW)
    by_round = {r.round: r for r in season.rounds}
    assert by_round[20].status == "completed"
    assert by_round[21].status == "upcoming"
    assert [s.code for s in by_round[20].sessions] == ["fp1", "fp2", "fp3", "q", "race"]
    assert sum(r.sprint for r in season.rounds) == 6
    assert [c.name for c in season.cancelled] == ["Bahrain Grand Prix", "Saudi Arabian Grand Prix"]
    out = season.model_dump(mode="json", by_alias=True)
    assert out["rounds"][19]["circuitId"] == "interlagos"
    assert out["rounds"][19]["start"] == "2026-11-08T17:00:00Z"


def test_session_ids_resolve_race_numbers(season_data, shared_client):
    from f1data.sources import openf1

    race = next(r for r in season_data["results"] if r["round"] == "20")
    idmap = ids.session_ids(openf1.drivers(shared_client, 11412), ids.round_entries(race), season_data["known"])
    assert idmap.driver(1) == "norris"  # champion's #1, not his permanent number
    assert idmap.teams[1] == "mclaren"
    assert len(idmap.drivers) == 22
    assert set(idmap.drivers.values()) <= set(season_data["known"])
