"""End-to-end, offline: build round 20 of 2026 from recorded fixtures."""

import json
from datetime import UTC, datetime

from conftest import fixture_client

from f1data.cli import main

FINAL_NOW = datetime(2026, 11, 12, tzinfo=UTC)  # > 72 h after the Brazil race
EARLY_NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)  # < 72 h: provisional


def run(tmp_path, now, *extra):
    out = tmp_path / "site"
    client = fixture_client(tmp_path / "cache")
    code = main(["build", "--year", "2026", "--round", "20", "--out", str(out), *extra], client=client, now=now)
    return code, out


def files(out):
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*.json")}


def test_build_round_writes_contract(tmp_path):
    code, out = run(tmp_path, FINAL_NOW)
    assert code == 0
    got = set(files(out))
    for rel in [
        "manifest.json",
        "2026/season.json",
        "2026/drivers.json",
        "2026/teams.json",
        "2026/driver-stats.json",
        "2026/standings/drivers.json",
        "2026/standings/teams.json",
        "circuits/interlagos.json",
        "2026/20/race/summary.json",
        "2026/20/race/laps.json",
        "2026/20/race/strategy.json",
        "2026/20/race/race-control.json",
        "2026/20/race/overtakes.json",
        "2026/20/q/summary.json",
        "2026/20/q/quali.json",
        "2026/20/fp1/summary.json",
    ]:
        assert rel in got, rel
    summary = json.loads((out / "2026/20/race/summary.json").read_text())
    assert summary["type"] == "race" and summary["provisional"] is False
    assert summary["schemaVersion"] == 1
    manifest = json.loads((out / "manifest.json").read_text())
    assert set(manifest["files"]) == got - {"manifest.json"}


def test_no_source_field_names_leak(tmp_path):
    _, out = run(tmp_path, FINAL_NOW)
    leaked = {"driver_number", "session_key", "meeting_key", "lap_duration", "constructorId", "circuit_key"}
    for rel, raw in files(out).items():
        text = raw.decode()
        assert not any(f'"{k}"' in text for k in leaked), rel


def test_rerun_is_idempotent(tmp_path):
    _, out = run(tmp_path, FINAL_NOW)
    before = files(out)
    code, _ = run(tmp_path, FINAL_NOW)
    assert code == 0
    assert files(out) == before


def test_provisional_sessions_are_rebuilt(tmp_path):
    _, out = run(tmp_path, EARLY_NOW)
    summary = json.loads((out / "2026/20/race/summary.json").read_text())
    assert summary["provisional"] is True
    run(tmp_path, FINAL_NOW)
    summary = json.loads((out / "2026/20/race/summary.json").read_text())
    assert summary["provisional"] is False
