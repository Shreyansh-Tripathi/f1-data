"""Record source responses into tests/fixtures/http/ for offline tests.

Covers the 2026 season-level endpoints, every session of the Brazilian GP
(meeting 1299, round 20) and the Singapore sprint weekend (meeting 1296),
plus MultiViewer circuits and a trimmed F1DB subset. Writes index.json mapping
each full URL to its file; tests replay it through `tests/conftest.py`.

    uv run scripts/record_fixtures.py
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any

from f1data.http import HttpClient, full_url
from f1data.sources import f1db, jolpica, multiviewer, openf1

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "http"
YEAR = 2026
MEETINGS = (1299, 1296)  # Brazil (latest race), Singapore (latest sprint weekend)
LAST_ROUND = 20
RED_FLAG_RACE = 11404  # Mexico 2026
VSC_TO_SC_RACE = 11731  # Kuala Lumpur 2026 (round 16)
QUALI_EDGE_CASES = (11271, 11276, 11400)  # round 4 sq and q, round 19 q (round 17 is recorded with Singapore)
GZIP_OVER = 500_000


class RecordingClient(HttpClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.index: dict[str, dict[str, str]] = {}

    def get_json(self, url: str, params: dict[str, Any] | None = None, *, final: bool = False) -> Any:
        body = super().get_json(url, params, final=final)
        key = full_url(url, params)
        self.index[key] = {"file": save(key, body), "kind": "json"}
        return body


def save(key: str, body: Any) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", key.split("://", 1)[1]).strip("_")[:150]
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    if len(raw) > GZIP_OVER:
        name += ".json.gz"
        (OUT / name).write_bytes(gzip.compress(raw, 9, mtime=0))
    else:
        name += ".json"
        (OUT / name).write_bytes(raw)
    return name


def f1db_subset(db: dict[str, Any], ids: set[str], season: dict[str, Any]) -> dict[str, Any]:
    """The parts of F1DB the builders read: current entrants, this season and its races and grands prix,
    countries (for demonyms)."""
    races = [r for r in db["races"] if r["year"] == season["year"]]
    gp_ids = {r["grandPrixId"] for r in races}
    return {
        "drivers": [d for d in db["drivers"] if d["id"] in ids],
        "seasons": [season],
        "countries": db["countries"],
        "races": races,
        "grandsPrix": [g for g in db["grandsPrix"] if g["id"] in gp_ids],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = RecordingClient(ROOT / ".cache")

    meetings = openf1.meetings(client, YEAR)
    sessions = openf1.sessions(client, YEAR)
    jolpica.schedule(client, YEAR)
    race_results = jolpica.results(client, YEAR)
    jolpica.qualifying(client, YEAR)
    jolpica.sprint(client, YEAR)
    for rnd in range(1, LAST_ROUND + 1):
        jolpica.driver_standings(client, YEAR, rnd)
        jolpica.constructor_standings(client, YEAR, rnd)

    for mk in MEETINGS:
        meeting = next(m for m in meetings if m["meeting_key"] == mk)
        multiviewer.circuit(client, meeting["circuit_info_url"])
        openf1.starting_grid(client, mk)
        for s in (s for s in sessions if s["meeting_key"] == mk):
            sk = s["session_key"]
            for fetch in (openf1.drivers, openf1.session_result, openf1.laps, openf1.stints, openf1.race_control,
                          openf1.weather):  # fmt: skip
                fetch(client, sk)
            if s["session_type"] == "Race":
                for fetch in (openf1.pit, openf1.intervals, openf1.position, openf1.overtakes):
                    fetch(client, sk)

    # Mexico race: red-flag suspension and pre-race pit rows (strategy filtering), plus VSC and SC periods.
    for fetch in (openf1.pit, openf1.stints, openf1.race_control, openf1.drivers):
        fetch(client, RED_FLAG_RACE)
    # Kuala Lumpur race: VSC upgraded to a safety car.
    for fetch in (openf1.race_control, openf1.drivers):
        fetch(client, VSC_TO_SC_RACE)
    # Qualifying edge cases: DSQ (round 4 q, round 19 q), not classified (round 4 sq, round 17 q).
    for sk in QUALI_EDGE_CASES:
        for fetch in (openf1.session_result, openf1.drivers):
            fetch(client, sk)

    # One short location window for the circuit-outline fallback (Brazil winner, 90 s).
    brazil = next(s for s in sessions if s["meeting_key"] == 1299 and s["session_name"] == "Race")
    winner = int(race_results[-1]["Results"][0]["number"])
    openf1.location(client, brazil["session_key"], winner, "2026-11-08T18:00:00", "2026-11-08T18:01:30")

    # F1DB: keep the release metadata and a subset of the DB (current entrants), served as a zip in tests.
    release = f1db.latest_release(client)
    db = f1db.download_json_single(client, release)
    season = next(s for s in db["seasons"] if s["year"] == YEAR)
    ids = {d["driverId"] for e in season["entrants"] for c in e["constructors"] for d in c["drivers"]}
    subset = f1db_subset(db, ids, season)
    asset = next(a for a in release["assets"] if a["name"] == f1db.ASSET)
    client.index[asset["browser_download_url"]] = {"file": save(asset["browser_download_url"], subset), "kind": "zip"}

    (OUT / "index.json").write_text(json.dumps(client.index, indent=1, sort_keys=True) + "\n")
    total = sum(p.stat().st_size for p in OUT.iterdir())
    print(f"recorded {len(client.index)} responses, {total / 1e6:.1f} MB in {OUT}")


if __name__ == "__main__":
    main()
