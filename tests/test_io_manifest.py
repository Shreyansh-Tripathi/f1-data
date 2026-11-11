import json
from datetime import UTC, datetime, timedelta

from f1data.io import is_complete, write_model
from f1data.manifest import build_manifest, write_manifest
from f1data.models.season import Season

T0 = datetime(2026, 11, 10, tzinfo=UTC)
T1 = T0 + timedelta(days=1)


def season(at):
    return Season(generated_at=at, year=2026, rounds=[], cancelled=[])


def test_write_is_sorted_compact_and_skips_generated_at_only_changes(tmp_path):
    path = tmp_path / "2026" / "season.json"
    assert write_model(path, season(T0))
    raw = path.read_bytes()
    assert raw == b'{"cancelled":[],"generatedAt":"2026-11-10T00:00:00Z","rounds":[],"schemaVersion":1,"year":2026}\n'
    assert not write_model(path, season(T1))  # only generatedAt differs
    assert path.read_bytes() == raw


def test_manifest_hash_stable_and_updated_at_moves_only_on_change(tmp_path):
    write_model(tmp_path / "2026" / "season.json", season(T0))
    write_manifest(tmp_path, T0)
    first = (tmp_path / "manifest.json").read_bytes()
    write_model(tmp_path / "2026" / "season.json", season(T1))
    write_manifest(tmp_path, T1)
    assert (tmp_path / "manifest.json").read_bytes() == first

    (tmp_path / "2026" / "season.json").unlink()
    write_model(tmp_path / "2026" / "season.json", Season(generated_at=T1, year=2026, rounds=[], cancelled=[]))
    write_model(tmp_path / "2026" / "teams-extra.json", season(T1))
    m = build_manifest(tmp_path, T1)
    assert m.files["2026/teams-extra.json"].updated_at == T1
    assert "manifest.json" not in m.files


def test_is_complete(tmp_path):
    d = tmp_path / "2026" / "20" / "race"
    d.mkdir(parents=True)
    assert not is_complete(d)
    (d / "summary.json").write_text(json.dumps({"provisional": True}))
    assert not is_complete(d)
    (d / "summary.json").write_text(json.dumps({"provisional": False}))
    assert is_complete(d)


def test_last_full_build_recorded_only_by_full_builds(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    write_manifest(tmp_path, T0, full_build=True)
    assert json.loads((tmp_path / "manifest.json").read_text())["lastFullBuildAt"] == "2026-11-10T00:00:00Z"
    write_manifest(tmp_path, T1)  # partial build keeps the previous time
    assert build_manifest(tmp_path, T1).last_full_build_at == T0
    write_manifest(tmp_path, T1, full_build=True)
    assert build_manifest(tmp_path, T1).last_full_build_at == T1
