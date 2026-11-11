"""should_build over the real 2026 calendar (as published in season.json)."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from f1data.build import ids
from f1data.build.season import build_season
from f1data.calendar import should_build
from f1data.cli import main


def utc(*args):
    return datetime(*args, tzinfo=UTC)


@pytest.fixture(scope="module")
def season(season_data):
    completed = {int(r["round"]) for r in season_data["results"]}
    cancelled = ids.cancelled_meetings(season_data["meetings"])
    s = build_season(2026, season_data["weekends"], cancelled, completed, season_data["results"], {}, utc(2026, 11, 10))
    return s.model_dump(mode="json", by_alias=True)


def test_mid_weekend_builds_even_after_a_recent_full_build(season):
    # São Paulo (round 20): FP1 2026-11-06 15:30Z, race ends 2026-11-08 19:00Z.
    now = utc(2026, 11, 7, 12)
    assert should_build(now, season, now - timedelta(minutes=30))[0] is True
    assert should_build(utc(2026, 11, 6, 15, 30), season, utc(2026, 11, 6, 15))[0] is True  # first session starts


def test_72h_tail_after_last_session(season):
    race_end = utc(2026, 11, 8, 19)
    recent = lambda now: now - timedelta(hours=1)  # noqa: E731
    assert should_build(race_end + timedelta(hours=71), season, recent(race_end + timedelta(hours=71)))[0] is True
    assert should_build(race_end + timedelta(hours=72), season, recent(race_end + timedelta(hours=72)))[0] is True
    assert should_build(race_end + timedelta(hours=73), season, recent(race_end + timedelta(hours=73)))[0] is False


def test_quiet_weekday_skips(season):
    now = utc(2026, 11, 15, 12)  # Sunday between São Paulo's tail and Las Vegas FP1 (2026-11-20 00:30Z)
    decision, reason = should_build(now, season, now - timedelta(hours=3))
    assert decision is False and reason.startswith("quiet")
    assert should_build(utc(2026, 11, 20, 0, 29), season, utc(2026, 11, 19, 12))[0] is False
    assert should_build(utc(2026, 11, 20, 0, 30), season, utc(2026, 11, 19, 12))[0] is True  # Las Vegas FP1


def test_24h_fallback(season):
    now = utc(2026, 11, 15, 12)
    assert should_build(now, season, now - timedelta(hours=24))[0] is False
    assert should_build(now, season, now - timedelta(hours=24, minutes=1))[0] is True
    assert should_build(now, season, None)[0] is True  # never recorded (e.g. before this feature)


def test_season_boundary(season):
    # After Abu Dhabi's tail (race ends 2026-12-06 15:00Z): no rounds left, only the daily fallback.
    now = utc(2026, 12, 20, 12)
    assert should_build(now, season, now - timedelta(hours=2))[0] is False
    assert should_build(now, season, now - timedelta(hours=25))[0] is True
    # New year, next season not published yet: build so it gets created.
    decision, reason = should_build(utc(2027, 1, 2, 12), None, utc(2027, 1, 2, 11))
    assert decision is True and reason == "no season.json yet"


def test_cancelled_round_does_not_open_a_window(season):
    # Bahrain (2026-04-10) was cancelled: listed under `cancelled`, not a round.
    assert {"name": "Bahrain Grand Prix", "date": "2026-04-10"} in season["cancelled"]
    now = utc(2026, 4, 11, 12)  # Japan's tail ended 2026-04-01, Miami starts 2026-05-01
    assert should_build(now, season, now - timedelta(hours=2))[0] is False


def test_session_without_end_counts_at_its_start():
    season = {"rounds": [{"round": 1, "start": "2027-03-07T05:00:00Z", "sessions": [
        {"code": "fp1", "start": "2027-03-05T01:30:00Z", "end": None},
        {"code": "race", "start": "2027-03-07T05:00:00Z", "end": None},
    ]}]}  # fmt: skip
    recent = utc(2027, 3, 1)
    assert should_build(utc(2027, 3, 10, 4), season, utc(2027, 3, 10))[0] is True  # race start + 71 h
    assert should_build(utc(2027, 3, 10, 6), season, utc(2027, 3, 10))[0] is False
    assert should_build(utc(2027, 3, 1, 12), season, recent)[0] is False


# ---------------------------------------------------------------- CLI


def should_build_cli(out, now, capsys):
    assert main(["should-build", "--out", str(out)], now=now) == 0
    return capsys.readouterr().out.strip()


def test_cli_reads_published_files(tmp_path, season, capsys):
    (tmp_path / "2026").mkdir()
    (tmp_path / "2026" / "season.json").write_text(json.dumps(season))
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {}, "lastFullBuildAt": "2026-11-15T09:00:00Z"}))
    assert should_build_cli(tmp_path, utc(2026, 11, 15, 12), capsys) == "false"
    assert should_build_cli(tmp_path, utc(2026, 11, 16, 12), capsys) == "true"  # fallback
    assert should_build_cli(tmp_path, utc(2026, 11, 7, 12), capsys) == "true"  # weekend
    assert should_build_cli(tmp_path, utc(2027, 1, 2), capsys) == "true"  # 2027/season.json missing
