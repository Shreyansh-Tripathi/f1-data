"""grid.json: offline builds of Brazil (round 20, grid penalties) and Singapore (round 17, pit-lane starts, sprint)."""

import json
from datetime import UTC, datetime

import httpx
from conftest import _respond

from f1data.build.grid import build_grid
from f1data.build.ids import IdMap
from f1data.build.session import SessionMeta
from f1data.cli import main
from f1data.http import HttpClient

NOW = datetime(2026, 11, 12, tzinfo=UTC)  # after Brazil, before Las Vegas

BRAZIL_PENALTIES = {  # F1DB v2026.20.0 startingGridPositions
    "russell": (7, 2, 5),
    "leclerc": (14, 4, 10),
    "arvid_lindblad": (18, 14, 10),
    "bortoleto": (19, 22, 10),
    "hamilton": (20, 11, 75),
    "max_verstappen": (21, 16, 30),
    "perez": (22, 21, 30),
}


def build(tmp_path, rnd: int, *, jolpica=True, starting_grid=True, f1db=True) -> dict[str, dict]:
    """Build round `rnd` with sources switched off, as before they publish; returns {session: grid.json}."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        if not f1db and url.host in ("github.com", "api.github.com", "objects.githubusercontent.com"):
            return httpx.Response(503)
        if not starting_grid and url.path.endswith("/starting_grid"):
            return httpx.Response(404, json={"detail": "No results found."})
        resp = _respond(request)
        if not jolpica and url.host == "api.jolpi.ca" and "/2026/" in url.path and resp.status_code == 200:
            body = json.loads(resp.content)
            table = body["MRData"].get("RaceTable")
            if table and "Races" in table and url.path.split("/")[-1].split(".")[0] in ("results", "sprint"):
                table["Races"] = [r for r in table["Races"] if r.get("round") != str(rnd)]
                return httpx.Response(200, json=body)
        return resp

    out = tmp_path / "site"
    client = HttpClient(tmp_path / "cache", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    main(["build", "--year", "2026", "--round", str(rnd), "--out", str(out)], client=client, now=NOW)
    return {p.parent.name: json.loads(p.read_text()) for p in (out / "2026" / str(rnd)).glob("*/grid.json")}


def by_driver(grid: dict) -> dict[str, dict]:
    return {e["driverId"]: e for e in grid["entries"]}


def test_official_grid_with_confirmed_penalties(tmp_path):
    grid = build(tmp_path, 20)["race"]
    assert (grid["basis"], grid["provisional"], grid["final"], grid["schemaVersion"]) == ("official", False, True, 1)
    assert [e["gridPosition"] for e in grid["entries"]] == list(range(1, 23))
    assert [e["driverId"] for e in grid["entries"][:2]] == ["norris", "antonelli"]
    noted = {e["driverId"]: e for e in grid["entries"] if e["note"]}
    assert {
        d: (e["gridPosition"], e["qualiPosition"], e["note"]["places"]) for d, e in noted.items()
    } == BRAZIL_PENALTIES
    assert {e["note"]["kind"] for e in noted.values()} == {"gridPenalty"}
    assert grid["entries"][0]["teamId"] == "mclaren"


def test_before_confirmation_drops_are_moved_without_places(tmp_path):
    # Jolpica and F1DB don't have the race yet: OpenF1's grid, and every drop is "moved".
    grid = build(tmp_path, 20, jolpica=False, f1db=False)["race"]
    assert (grid["basis"], grid["provisional"], grid["final"]) == ("startingGrid", False, False)
    entries = by_driver(grid)
    for driver, (position, quali, _) in BRAZIL_PENALTIES.items():
        assert (entries[driver]["gridPosition"], entries[driver]["qualiPosition"]) == (position, quali)
    # Every driver starting behind their qualifying position is "moved"; Bortoleto (penalised, but up from 22nd
    # to 19th by others' penalties) and everyone else get no note until a source confirms it.
    moved = {d for d, e in entries.items() if e["note"] == {"kind": "moved", "places": None}}
    assert moved == set(BRAZIL_PENALTIES) - {"bortoleto"}
    assert all(e["note"] is None for d, e in entries.items() if d not in moved)


def test_qualifying_order_is_provisional(tmp_path):
    grid = build(tmp_path, 20, jolpica=False, starting_grid=False, f1db=False)["race"]
    assert (grid["basis"], grid["provisional"]) == ("qualifying", True)
    assert [e["qualiPosition"] for e in grid["entries"]] == list(range(1, 23))
    assert all(e["note"] is None for e in grid["entries"])


def test_pit_lane_starts_and_sprint_grid(tmp_path):
    grids = build(tmp_path, 17)
    race, sprint = by_driver(grids["race"]), grids["sprint"]
    assert race["russell"]["gridPosition"] == 19 and race["russell"]["note"] == {"kind": "gridPenalty", "places": 60}
    pit_lane = [e["driverId"] for e in grids["race"]["entries"] if e["note"] and e["note"]["kind"] == "pitLane"]
    assert pit_lane == ["alonso", "stroll", "hadjar"]  # after every grid slot, in qualifying order
    assert [race[d]["gridPosition"] for d in pit_lane] == [20, 21, 22]
    assert race["hadjar"]["qualiPosition"] is None  # no qualifying time
    # The sprint grid is set by sprint qualifying, not by qualifying.
    assert sprint["session"] == "sprint" and sprint["basis"] == "official"
    assert [e["driverId"] for e in sprint["entries"][:2]] == ["max_verstappen", "russell"]


# ---------------------------------------------------------------- note rules


META = SessionMeta(year=2026, round=1, session="race", provisional=False, final=False, generated_at=NOW)
IDMAP = IdMap(drivers={1: "a", 2: "b", 3: "c", 4: "d"}, teams={})


def quali(*rows: tuple[str, int | None, str]) -> dict:
    return {"drivers": [{"driverId": d, "teamId": "t", "position": p, "status": s} for d, p, s in rows]}


def jolpica(*rows: tuple[str, str, str]) -> list[dict]:
    return [
        {"Driver": {"driverId": d}, "Constructor": {"constructorId": "t"}, "grid": g, "positionText": t}
        for d, g, t in rows
    ]


def notes(grid) -> dict[str, tuple[int, str | None]]:
    return {e.driver_id: (e.grid_position, e.note.kind if e.note else None) for e in grid.entries}


def test_grid_zero_is_pit_lane_unless_the_driver_did_not_start():
    q = quali(("a", 1, "classified"), ("b", 2, "classified"), ("c", 3, "classified"), ("d", 4, "classified"))
    grid = build_grid(META, q, IDMAP, jolpica(("a", "0", "5"), ("b", "1", "1"), ("c", "2", "2"), ("d", "0", "W")))
    # d did not start (W): grid 0 but no pit-lane start; it qualified 4th and is listed 4th, so no note.
    assert notes(grid) == {"b": (1, None), "c": (2, None), "a": (3, "pitLane"), "d": (4, None)}


def test_qualifying_dsq_is_noted_even_without_a_grid_source():
    q = quali(("a", 1, "classified"), ("b", 2, "classified"), ("c", None, "dsq"))
    grid = build_grid(META, q, IDMAP)
    assert grid.basis == "qualifying" and grid.provisional is True
    assert notes(grid) == {"a": (1, None), "b": (2, None), "c": (3, "dsq")}


def test_no_grid_without_qualifying_results():
    assert build_grid(META, {"drivers": []}, IDMAP) is None
