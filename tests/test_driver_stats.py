import logging
from datetime import UTC, datetime

import pytest

from f1data.build.classify import classify
from f1data.build.driver_stats import build_driver_stats
from f1data.sources import f1db

NOW = datetime(2026, 11, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1", "finished"), ("20", "finished"), ("R", "dnf"), ("N", "dnf"), ("W", "dns"), ("F", "dns"), ("D", "dsq")],
)
def test_classify_from_position_text(text, expected):
    assert classify(text) == expected


def test_classify_logs_openf1_mismatch(caplog):
    with caplog.at_level(logging.WARNING):
        assert classify("R", {"dnf": False, "dns": True, "dsq": False}, who="x") == "dnf"
    assert "mismatch" in caplog.text


@pytest.fixture(scope="module")
def stats(season_data, shared_client):
    db = f1db.download_json_single(shared_client, f1db.latest_release(shared_client))
    d = season_data
    out = build_driver_stats(2026, d["known"], d["results"], d["qualifying"], d["sprint"], db, NOW)
    return {s.driver_id: s for s in out.drivers}, d


def test_season_totals_match_jolpica(stats):
    by_id, d = stats
    rounds = len(d["results"])
    assert sum(s.season.wins for s in by_id.values()) == rounds
    assert sum(s.season.podiums for s in by_id.values()) == 3 * rounds
    assert sum(s.season.poles for s in by_id.values()) == len(d["qualifying"])
    retired = sum(row["positionText"] in ("R", "N") for r in d["results"] for row in r["Results"])
    assert sum(s.season.dnfs for s in by_id.values()) == retired


def test_sprint_stats_are_separate(stats):
    by_id, d = stats
    assert sum(s.season.sprint_wins for s in by_id.values()) == len(d["sprint"]) == 6
    race_points = sum(float(row["points"]) for r in d["results"] for row in r["Results"])
    assert sum(s.season.points for s in by_id.values()) == pytest.approx(race_points)
    assert any(s.season.sprint_points > 0 for s in by_id.values())


def test_career_totals_from_f1db(stats):
    by_id, _ = stats
    alonso = by_id["alonso"].career
    assert alonso is not None
    assert alonso.championships == 2
    assert alonso.starts > 400
    assert sum(s.career is not None for s in by_id.values()) >= 22


def test_rounds_carry_quali_race_and_team(stats):
    by_id, d = stats
    # Every Jolpica race and quali row appears exactly once.
    entries = {(s.driver_id, r.round) for s in by_id.values() for r in s.rounds}
    rows = {(row["Driver"]["driverId"], int(race["round"])) for race in d["results"] for row in race["Results"]}
    rows |= {(row["Driver"]["driverId"], int(q["round"])) for q in d["qualifying"] for row in q["QualifyingResults"]}
    assert entries == rows
    for s in by_id.values():
        assert [r.round for r in s.rounds] == sorted(r.round for r in s.rounds)

    race20 = next(r for r in d["results"] if r["round"] == "20")
    for row in race20["Results"]:
        r20 = next(r for r in by_id[row["Driver"]["driverId"]].rounds if r.round == 20)
        assert r20.team_id == row["Constructor"]["constructorId"]
        assert r20.race_points == float(row["points"])
        if row["positionText"].isdigit():
            assert (r20.race_position, r20.race_classification) == (int(row["positionText"]), "finished")
        else:
            assert r20.race_position is None and r20.race_classification in ("dnf", "dns", "dsq")
    # Brazil 2026: Verstappen qualified P16 (see Findings).
    assert next(r for r in by_id["max_verstappen"].rounds if r.round == 20).quali_position == 16


def test_rounds_season_points_add_up(stats):
    by_id, _ = stats
    for s in by_id.values():
        assert sum(r.race_points or 0 for r in s.rounds) == pytest.approx(s.season.points)
        assert sum(r.race_position == 1 for r in s.rounds) == s.season.wins
