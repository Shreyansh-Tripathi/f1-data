"""grid.json: the starting grid of a race or sprint, from the moment its qualifying session has results."""

from __future__ import annotations

from typing import Any

from f1data.build.ids import IdMap
from f1data.build.session import SessionMeta
from f1data.models.session import Grid, GridBasis, GridEntry, GridNote

Rows = list[dict[str, Any]]

DID_NOT_START = {"W", "F"}  # Jolpica positionText: grid 0 here is not a pit-lane start


def build_grid(
    meta: SessionMeta,
    quali: dict[str, Any],
    idmap: IdMap,
    jolpica_rows: Rows | None = None,
    openf1_grid: Rows = (),
    f1db_rows: Rows | None = None,
) -> Grid | None:
    """Starting order, first available: Jolpica's race/sprint `grid` (official), OpenF1 `starting_grid` for the
    qualifying session (startingGrid), else our published qualifying order (qualifying, provisional).

    `quali` is the published quali.json of the q/sq session. Notes say why a driver starts behind their qualifying
    position: gridPenalty (with places) and pitLane only from F1DB's grid or Jolpica's grid 0, dsq from the
    qualifying status, otherwise moved. None while qualifying has no drivers."""
    drivers = quali.get("drivers") or []
    if not drivers:
        return None
    by_id = {d["driverId"]: d for d in drivers}
    quali_order = [d["driverId"] for d in drivers]  # quali.json lists drivers in classification order

    basis: GridBasis
    pit_lane: set[str] = set()
    if jolpica_rows and any(r.get("grid") not in (None, "") for r in jolpica_rows):
        basis = "official"
        order, pit_lane = _jolpica_order(jolpica_rows, quali_order)
    elif openf1_grid:
        basis = "startingGrid"
        order = [d for g in sorted(openf1_grid, key=lambda g: g["position"]) if (d := idmap.driver(g["driver_number"]))]
    else:
        basis = "qualifying"
        order = list(quali_order)
    # Anyone qualifying lists but the grid source doesn't (e.g. withdrawn) goes last, in qualifying order.
    order = list(dict.fromkeys(order + [d for d in quali_order if d not in order]))

    confirmed = _f1db_notes(f1db_rows or [], idmap)
    entries = []
    for i, driver_id in enumerate(order, start=1):
        q = by_id.get(driver_id)
        quali_position = q.get("position") if q else None
        note = confirmed.get(driver_id)
        if note is None and driver_id in pit_lane:
            note = GridNote(kind="pitLane", places=None)
        if note is None and q and q.get("status") == "dsq":
            note = GridNote(kind="dsq", places=None)
        if note is None and quali_position is not None and i > quali_position:
            note = GridNote(kind="moved", places=None)
        entries.append(
            GridEntry(
                grid_position=i,
                driver_id=driver_id,
                team_id=q["teamId"] if q else _team(jolpica_rows, driver_id),
                quali_position=quali_position,
                note=note,
            )
        )
    # provisional: no published starting grid yet, only the qualifying order
    return Grid(**{**meta.fields(), "provisional": basis == "qualifying"}, basis=basis, entries=entries)


def _jolpica_order(rows: Rows, quali_order: list[str]) -> tuple[list[str], set[str]]:
    """Grid slots in order, then pit-lane starters (grid 0) and non-starters in qualifying order."""
    rank = {d: i for i, d in enumerate(quali_order)}

    def grid(r: dict[str, Any]) -> int:
        return int(r["grid"]) if r.get("grid") not in (None, "") else 0

    slots = sorted((r for r in rows if grid(r) > 0), key=grid)
    rest = sorted((r for r in rows if grid(r) == 0), key=lambda r: rank.get(r["Driver"]["driverId"], len(rank)))
    pit_lane = {r["Driver"]["driverId"] for r in rest if r.get("positionText") not in DID_NOT_START}
    return [r["Driver"]["driverId"] for r in slots + rest], pit_lane


def _f1db_notes(rows: Rows, idmap: IdMap) -> dict[str, GridNote]:
    """Confirmed notes from F1DB's starting grid (published after the race): pit-lane starts and grid penalties."""
    out = {}
    for r in rows:
        driver_id = idmap.driver(int(r["driverNumber"])) if r.get("driverNumber") else None
        if driver_id is None:
            continue
        if r.get("positionText") == "PL":
            out[driver_id] = GridNote(kind="pitLane", places=None)
        elif r.get("gridPenaltyPositions"):
            out[driver_id] = GridNote(kind="gridPenalty", places=int(r["gridPenaltyPositions"]))
    return out


def _team(rows: Rows | None, driver_id: str) -> str:
    row = next(r for r in rows or [] if r["Driver"]["driverId"] == driver_id)
    return row["Constructor"]["constructorId"]
