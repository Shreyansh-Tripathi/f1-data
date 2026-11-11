"""The output contract as data: every published file's model and schemaVersion, flattened to `path -> type`.

`tests/contract/snapshot.json` holds the committed contract; `scripts/update_contract_snapshot.py` rewrites it.
Within a schemaVersion only additions are allowed, including new enum values (see "Contract change policy" in
CLAUDE.md).
"""

from __future__ import annotations

from typing import Any

from f1data.models.base import OutputFile
from f1data.models.circuit import Circuit
from f1data.models.drivers import Drivers, DriverStatsFile, Teams
from f1data.models.manifest import Manifest
from f1data.models.meta import PublishLag
from f1data.models.season import Season
from f1data.models.session import (
    Laps,
    Overtakes,
    PracticeSummary,
    Quali,
    QualifyingSummary,
    RaceControl,
    RaceSummary,
    Strategy,
)
from f1data.models.standings import DriverStandings, TeamStandings

# Published path -> model. summary.json has one model per `type`.
OUTPUT_FILES: dict[str, type[OutputFile]] = {
    "manifest.json": Manifest,
    "../meta/lag.json": PublishLag,
    "{year}/season.json": Season,
    "{year}/standings/drivers.json": DriverStandings,
    "{year}/standings/teams.json": TeamStandings,
    "{year}/drivers.json": Drivers,
    "{year}/driver-stats.json": DriverStatsFile,
    "{year}/teams.json": Teams,
    "circuits/{circuitId}.json": Circuit,
    "{year}/{round}/{session}/summary.json#race": RaceSummary,
    "{year}/{round}/{session}/summary.json#qualifying": QualifyingSummary,
    "{year}/{round}/{session}/summary.json#practice": PracticeSummary,
    "{year}/{round}/{session}/laps.json": Laps,
    "{year}/{round}/{session}/strategy.json": Strategy,
    "{year}/{round}/{session}/race-control.json": RaceControl,
    "{year}/{round}/{session}/quali.json": Quali,
    "{year}/{round}/{session}/overtakes.json": Overtakes,
}


def schema_version(model: type[OutputFile]) -> int:
    return model.model_fields["schema_version"].default


def contract() -> dict[str, dict[str, Any]]:
    return {
        path: {"schemaVersion": schema_version(model), "fields": shape(model)}
        for path, model in sorted(OUTPUT_FILES.items())
    }


def shape(model: type[OutputFile]) -> dict[str, str]:
    """`path -> type` for every field, e.g. "messages[].deletion.lapTimeMs": "integer|null"."""
    schema = model.model_json_schema(by_alias=True, mode="serialization")
    out: dict[str, str] = {}
    _walk(schema, schema.get("$defs", {}), "", out)
    return dict(sorted(out.items()))


def _resolve(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        node = defs[node["$ref"].rsplit("/", 1)[-1]]
    return node


def _type(node: dict[str, Any], defs: dict[str, Any]) -> str:
    node = _resolve(node, defs)
    for key in ("anyOf", "oneOf"):
        if key in node:
            return "|".join(sorted({_type(o, defs) for o in node[key]}))
    if "const" in node:
        return f"const({node['const']})"
    if "enum" in node:
        return "enum(" + ",".join(sorted(map(str, node["enum"]))) + ")"
    t = node.get("type", "any")
    if t == "string" and node.get("format"):
        return f"string({node['format']})"
    if t == "object" and "additionalProperties" in node and "properties" not in node:
        return "map"
    return t


def _walk(node: dict[str, Any], defs: dict[str, Any], path: str, out: dict[str, str]) -> None:
    node = _resolve(node, defs)
    for key in ("anyOf", "oneOf"):
        for option in node.get(key, []):
            _walk(option, defs, path, out)
    for name, prop in node.get("properties", {}).items():
        child = f"{path}.{name}" if path else name
        out[child] = _type(prop, defs)
        _walk(prop, defs, child, out)
    if node.get("type") == "array" and "items" in node:
        out[f"{path}[]"] = _type(node["items"], defs)
        _walk(node["items"], defs, f"{path}[]", out)
    extra = node.get("additionalProperties")
    if isinstance(extra, dict):
        out[f"{path}{{}}"] = _type(extra, defs)
        _walk(extra, defs, f"{path}{{}}", out)


def breaking_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Non-additive differences between two contracts that are not covered by a schemaVersion bump."""
    problems = []
    for path, before in sorted(old.items()):
        after = new.get(path)
        if after is None:
            problems.append(f"{path}: file removed from the contract")
            continue
        if after["schemaVersion"] < before["schemaVersion"]:
            problems.append(f"{path}: schemaVersion went down ({before['schemaVersion']} -> {after['schemaVersion']})")
            continue
        if after["schemaVersion"] > before["schemaVersion"]:
            continue  # bumped: anything goes
        for field, t in before["fields"].items():
            if field not in after["fields"]:
                problems.append(f"{path}: {field} removed or renamed (schemaVersion {before['schemaVersion']})")
            elif not compatible(t, after["fields"][field]):
                problems.append(f"{path}: {field} changed {t} -> {after['fields'][field]}")
    return problems


def compatible(before: str, after: str) -> bool:
    """Same type, except that an enum may gain values (additive); losing one is breaking."""
    if before == after:
        return True
    old, new = before.split("|"), after.split("|")
    old_enums = [t for t in old if t.startswith("enum(")]
    new_enums = [t for t in new if t.startswith("enum(")]
    if len(old_enums) != 1 or len(new_enums) != 1:
        return False
    if sorted(t for t in old if t not in old_enums) != sorted(t for t in new if t not in new_enums):
        return False  # nullability or another member changed
    return _enum_values(old_enums[0]) <= _enum_values(new_enums[0])


def _enum_values(t: str) -> set[str]:
    return set(t[len("enum(") : -1].split(","))
