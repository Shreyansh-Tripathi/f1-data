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
