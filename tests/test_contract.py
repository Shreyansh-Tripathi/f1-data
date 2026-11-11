"""Contract change policy: within a schemaVersion, output changes are additive only."""

import copy
import json
from pathlib import Path

import pytest

import f1data.models as models_pkg
from f1data.models import base
from f1data.models.contract import OUTPUT_FILES, breaking_changes, contract, shape

SNAPSHOT = Path(__file__).parent / "contract" / "snapshot.json"
UPDATE = "uv run python scripts/update_contract_snapshot.py"


@pytest.fixture(scope="module")
def snapshot():
    return json.loads(SNAPSHOT.read_text())


def test_no_breaking_change_without_a_schema_version_bump(snapshot):
    problems = breaking_changes(snapshot, contract())
    assert problems == [], (
        "Non-additive contract change. Bump `schema_version` on the affected model(s), then run "
        f"`{UPDATE}`:\n  " + "\n  ".join(problems)
    )


def test_snapshot_matches_the_models(snapshot):
    # Additive changes are allowed but must be recorded, so a later removal is caught.
    assert contract() == snapshot, f"Contract changed; review the diff and run `{UPDATE}`."


def test_every_output_model_is_in_the_contract():
    import importlib
    import pkgutil

    for info in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"f1data.models.{info.name}")
    models = {
        cls
        for cls in _subclasses(base.OutputFile)
        if cls.__module__.startswith("f1data.models") and cls is not base.SessionFile
    }
    assert models == set(OUTPUT_FILES.values())


def _subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


# ---------------------------------------------------------------- the rules themselves


def file(version, **fields):
    return {"x.json": {"schemaVersion": version, "fields": fields}}


@pytest.mark.parametrize(
    ("old", "new", "problem"),
    [
        (file(1, a="integer"), file(1, a="integer", b="string"), None),  # field added
        (file(1, a="integer", b="string"), file(1, a="integer"), "b removed or renamed"),
        (file(1, timeMs="integer|null"), file(1, lapTimeMs="integer|null"), "timeMs removed or renamed"),
        (file(1, timeMs="integer|null"), file(2, lapTimeMs="integer|null"), None),  # renamed with a bump
        (file(1, a="integer"), file(1, a="integer|null"), "a changed integer -> integer|null"),  # now nullable
        (file(1, k="enum(a,b)"), file(1, k="enum(a,b,c)"), "k changed enum(a,b) -> enum(a,b,c)"),  # new value
        (file(2, a="integer"), file(1, a="integer"), "schemaVersion went down"),
        (file(1, a="integer"), {}, "file removed"),
    ],
)
def test_breaking_change_rules(old, new, problem):
    problems = breaking_changes(old, new)
    if problem is None:
        assert problems == []
    else:
        assert len(problems) == 1 and problem in problems[0]


def test_the_lap_time_rename_needed_the_bump(snapshot):
    # Rebuild the race-control contract as it was before the rename (schemaVersion 1, deletion.timeMs).
    key = "{year}/{round}/{session}/race-control.json"
    old = copy.deepcopy({key: snapshot[key]})
    fields = old[key]["fields"]
    fields["messages[].deletion.timeMs"] = fields.pop("messages[].deletion.lapTimeMs")
    old[key]["schemaVersion"] = 1
    current = {key: contract()[key]}
    assert current[key]["schemaVersion"] == 2
    assert breaking_changes(old, current) == []
    unbumped = copy.deepcopy(current)
    unbumped[key]["schemaVersion"] = 1
    assert breaking_changes(old, unbumped) == [
        f"{key}: messages[].deletion.timeMs removed or renamed (schemaVersion 1)"
    ]


def test_shape_records_nested_types():
    from f1data.models.session import RaceControl

    s = shape(RaceControl)
    assert s["messages[].deletion.lapTimeMs"] == "integer|null"
    assert s["messages[].driverIds[]"] == "string"
    assert s["messages[].kind"].startswith("enum(") and "lapDeleted" in s["messages[].kind"]
