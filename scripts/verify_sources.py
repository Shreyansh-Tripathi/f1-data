# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "numpy"]
# ///
"""Phase 0: verify every data source against the most recent completed 2026 race.

Records HTTP status, auth requirement, response time, record count and payload
size per request; saves raw responses to tests/fixtures/phase0/ and a summary to
tests/fixtures/phase0/results.json. Never sends auth tokens.

    uv run scripts/verify_sources.py
"""

from __future__ import annotations

import contextlib
import gzip
import io
import json
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "phase0"
CACHE = ROOT / ".cache"
YEAR = 2026

OPENF1 = "https://api.openf1.org/v1"
JOLPICA = "https://api.jolpi.ca/ergast/f1"
F1DB_RELEASE = "https://api.github.com/repos/f1db/f1db/releases/latest"

# Seconds between requests per host (OpenF1 ~30/10s, Jolpica ~4/s).
MIN_INTERVAL = {"api.openf1.org": 0.4, "api.jolpi.ca": 0.5}
# Payloads above this are stored as a sample (first N records) to keep the repo small.
FIXTURE_MAX_BYTES = 1_000_000
SAMPLE_RECORDS = 200

results: list[dict] = []
_last_hit: dict[str, float] = {}
client = httpx.Client(timeout=180, follow_redirects=True, headers={"User-Agent": "f1-data-verify/0.1"})


def get(url: str, params: dict | None = None, *, retries: int = 4) -> tuple[httpx.Response, float]:
    """GET with per-host spacing and backoff on 429/5xx. Returns (response, elapsed_ms)."""
    host = urlparse(url).hostname or ""
    for attempt in range(retries + 1):
        wait = MIN_INTERVAL.get(host, 0) - (time.monotonic() - _last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        start = time.monotonic()
        try:
            resp = client.get(url, params=params)
        except httpx.TransportError:
            if attempt == retries:
                raise
            time.sleep(2**attempt)
            continue
        finally:
            _last_hit[host] = time.monotonic()
        elapsed = (time.monotonic() - start) * 1000
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(float(resp.headers.get("retry-after", 2 ** (attempt + 1))))
            continue
        return resp, elapsed
    raise AssertionError("unreachable")


def count_records(data) -> int | None:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict) and "MRData" in data:  # Jolpica envelope
        table = next(v for k, v in data["MRData"].items() if k.endswith("Table"))
        lists = [v for v in table.values() if isinstance(v, list)]
        if not lists:
            return 0
        inner = lists[0]
        if inner and isinstance(inner[0], dict):
            nested = [v for v in inner[0].values() if isinstance(v, list) and v and isinstance(v[0], dict)]
            if nested:  # e.g. Races[0].Results, StandingsLists[0].DriverStandings
                return sum(len(next(v for v in r.values() if isinstance(v, list))) for r in inner)
        return len(inner)
    return None


def save_fixture(name: str, data) -> str:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True)
    if len(raw) > FIXTURE_MAX_BYTES and isinstance(data, list):
        name = name.replace(".json", ".sample.json")
        raw = json.dumps(data[:SAMPLE_RECORDS], ensure_ascii=False, indent=1, sort_keys=True)
    (FIXTURES / name).write_text(raw + "\n")
    return name


def probe(
    source: str,
    label: str,
    url: str,
    params: dict | None = None,
    *,
    fixture: str | None = None,
):
    """Fetch JSON, record metrics, optionally save fixture. Returns parsed body or None."""
    resp, ms = get(url, params)
    data = None
    with contextlib.suppress(ValueError):
        data = resp.json()
    row = {
        "source": source,
        "label": label,
        "url": str(resp.request.url),
        "status": resp.status_code,
        "auth_needed": resp.status_code in (401, 403),
        "ms": round(ms),
        "bytes": len(resp.content),
        "records": count_records(data) if resp.is_success else None,
    }
    if not resp.is_success:
        row["error"] = resp.text[:300]
    if fixture and data is not None:
        row["fixture"] = save_fixture(fixture, data)
    results.append(row)
    print(
        f"  {row['status']} {row['ms']:>6}ms {row['bytes']:>11,}B rec={row['records']} {label}",
        flush=True,
    )
    return data if resp.is_success else None


# --------------------------------------------------------------------------- target


def find_target() -> dict:
    """Most recent 2026 Race session that has ended and has published results."""
    meetings = probe(
        "openf1",
        "meetings (2026)",
        f"{OPENF1}/meetings",
        {"year": YEAR},
        fixture="openf1_meetings_2026.json",
    )
    cancelled = {m["meeting_key"] for m in meetings if m.get("is_cancelled")}
    sessions = probe(
        "openf1",
        "sessions (2026 races)",
        f"{OPENF1}/sessions",
        {"year": YEAR, "session_name": "Race"},
        fixture="openf1_sessions_2026_race.json",
    )
    now = datetime.now(UTC)
    # OpenF1 keeps sessions of cancelled meetings (2026: Bahrain, Saudi Arabia).
    done = [s for s in sessions if datetime.fromisoformat(s["date_end"]) < now and s["meeting_key"] not in cancelled]
    for s in sorted(done, key=lambda s: s["date_start"], reverse=True):
        resp, _ = get(f"{OPENF1}/session_result", {"session_key": s["session_key"]})
        if resp.is_success and resp.json():
            return {
                "race": s,
                "completed_races": sorted(done, key=lambda s: s["date_start"]),
            }
    sys.exit("no completed 2026 race with results")


# --------------------------------------------------------------------------- OpenF1

OPENF1_SESSION_ENDPOINTS = [
    "drivers",
    "session_result",
    "laps",
    "stints",
    "pit",
    "intervals",
    "position",
    "race_control",
    "weather",
    "team_radio",
    "overtakes",
    "championship_drivers",
    "championship_teams",
]


def verify_openf1(race: dict) -> dict:
    sk, mk = race["session_key"], race["meeting_key"]
    print("OpenF1")
    meeting = probe(
        "openf1",
        "meetings",
        f"{OPENF1}/meetings",
        {"meeting_key": mk},
        fixture="openf1_meetings.json",
    )
    # Circuit outline/corners/rotation (MultiViewer), linked from OpenF1 meetings.
    if meeting and meeting[0].get("circuit_info_url"):
        probe(
            "multiviewer",
            "circuit_info_url",
            meeting[0]["circuit_info_url"],
            fixture=f"multiviewer_circuit_{meeting[0]['circuit_key']}_{YEAR}.json",
        )
    probe(
        "openf1",
        "sessions (weekend)",
        f"{OPENF1}/sessions",
        {"meeting_key": mk},
        fixture="openf1_sessions_weekend.json",
    )
    out = {}
    for ep in OPENF1_SESSION_ENDPOINTS:
        out[ep] = probe(
            "openf1",
            ep,
            f"{OPENF1}/{ep}",
            {"session_key": sk},
            fixture=f"openf1_{ep}.json",
        )
    # starting_grid is keyed to the qualifying session, not the race (race session_key gives 404).
    probe(
        "openf1",
        "starting_grid",
        f"{OPENF1}/starting_grid",
        {"meeting_key": mk},
        fixture="openf1_starting_grid.json",
    )

    # location and car_data are too big for one request; fetch per driver.
    drivers = sorted({d["driver_number"] for d in out["drivers"] or []})
    for ep in ("location", "car_data"):
        rows, total_bytes, total_ms, statuses = [], 0, 0, set()
        for n in drivers:
            resp, ms = get(f"{OPENF1}/{ep}", {"session_key": sk, "driver_number": n})
            statuses.add(resp.status_code)
            total_bytes += len(resp.content)
            total_ms += ms
            if resp.is_success:
                rows.extend(resp.json())
        out[ep] = rows
        results.append(
            {
                "source": "openf1",
                "label": f"{ep} (all drivers, {len(drivers)} requests)",
                "url": f"{OPENF1}/{ep}?session_key={sk}&driver_number=<n>",
                "status": sorted(statuses),
                "auth_needed": bool(statuses & {401, 403}),
                "ms": round(total_ms),
                "bytes": total_bytes,
                "records": len(rows),
                "fixture": save_fixture(f"openf1_{ep}.json", rows),
            }
        )
        print(f"  {sorted(statuses)} {round(total_ms):>6}ms {total_bytes:>11,}B rec={len(rows)} {ep} (per driver)")
    out["_raw_location_bytes"] = next(r["bytes"] for r in results if r["label"].startswith("location"))
    return out


def verify_team_radio(completed_races: list[dict]) -> dict:
    print("OpenF1 team_radio, last 5 rounds")
    rounds = []
    for race in completed_races[-5:]:
        data = probe(
            "openf1",
            f"team_radio meeting {race['meeting_key']} ({race['circuit_short_name']})",
            f"{OPENF1}/team_radio",
            {"meeting_key": race["meeting_key"]},
        )
        rounds.append(
            {
                "meeting_key": race["meeting_key"],
                "circuit": race["circuit_short_name"],
                "clips": len(data or []),
                "sample_url": (data or [{}])[-1].get("recording_url"),
            }
        )
    sample = next((r["sample_url"] for r in reversed(rounds) if r["sample_url"]), None)
    audio = None
    if sample:
        resp, ms = get(sample)
        audio = {
            "url": sample,
            "status": resp.status_code,
            "auth_needed": resp.status_code in (401, 403),
            "ms": round(ms),
            "bytes": len(resp.content),
            "content_type": resp.headers.get("content-type"),
        }
        print(f"  audio {audio['status']} {audio['content_type']} {audio['bytes']:,}B")
    return {"rounds": rounds, "audio": audio}


# --------------------------------------------------------------------------- replay size


def replay_size(race: dict, location: list[dict], position: list[dict], raw_bytes: int) -> dict:
    """Columnar per driver {t, x, y, pos}, t = ms since race start, integer coords, at 1 and 2 Hz."""
    t0 = datetime.fromisoformat(race["date_start"])
    t1 = datetime.fromisoformat(race["date_end"])

    def ms(iso: str) -> int:
        return int((datetime.fromisoformat(iso) - t0).total_seconds() * 1000)

    end_ms = int((t1 - t0).total_seconds() * 1000)
    pos_by_driver: dict[int, list[tuple[int, int]]] = {}
    for p in sorted(position, key=lambda p: p["date"]):
        pos_by_driver.setdefault(p["driver_number"], []).append((ms(p["date"]), p["position"]))

    by_driver: dict[int, list[dict]] = {}
    for r in location:
        by_driver.setdefault(r["driver_number"], []).append(r)

    out = {
        "raw_bytes_all_session": raw_bytes,
        "raw_records": len(location),
        "race_window": [race["date_start"], race["date_end"]],
    }
    for hz in (2, 1):
        step = 1000 // hz
        cols = {}
        for n, rows in sorted(by_driver.items()):
            t = np.array([ms(r["date"]) for r in rows])
            keep = (t >= 0) & (t <= end_ms)
            t, rows = t[keep], [r for r, k in zip(rows, keep, strict=True) if k]
            _, first = np.unique(t // step, return_index=True)
            pts = pos_by_driver.get(n, [])
            pt = np.array([p[0] for p in pts]) if pts else np.array([0])
            pv = [p[1] for p in pts] or [None]
            idx = np.clip(np.searchsorted(pt, t[first], side="right") - 1, 0, None)
            cols[str(n)] = {
                "t": [int(t[i]) for i in first],
                "x": [round(rows[i]["x"]) for i in first],
                "y": [round(rows[i]["y"]) for i in first],
                "pos": [pv[i] for i in idx],
            }
        body = json.dumps(cols, separators=(",", ":"), sort_keys=True).encode()
        out[f"{hz}hz"] = {
            "samples": sum(len(c["t"]) for c in cols.values()),
            "json_bytes": len(body),
            "gzip_bytes": len(gzip.compress(body, 9)),
        }
    return out


# --------------------------------------------------------------------------- Jolpica


def verify_jolpica(race: dict) -> dict:
    print("Jolpica")
    sched = probe("jolpica", "schedule", f"{JOLPICA}/{YEAR}.json", fixture="jolpica_schedule.json")
    races = sched["MRData"]["RaceTable"]["Races"] if sched else []
    match = next((r for r in races if r["date"] == race["date_start"][:10]), None)
    rnd = match["round"] if match else "last"
    base = f"{JOLPICA}/{YEAR}/{rnd}"
    probe("jolpica", "results", f"{base}/results.json", fixture="jolpica_results.json")
    probe(
        "jolpica",
        "qualifying",
        f"{base}/qualifying.json",
        fixture="jolpica_qualifying.json",
    )
    probe("jolpica", "sprint", f"{base}/sprint.json", fixture="jolpica_sprint.json")
    probe(
        "jolpica",
        "driverstandings",
        f"{base}/driverStandings.json",
        fixture="jolpica_driver_standings.json",
    )
    probe(
        "jolpica",
        "constructorstandings",
        f"{base}/constructorStandings.json",
        fixture="jolpica_constructor_standings.json",
    )
    return {
        "round": rnd,
        "race_name": match and match["raceName"],
        "rounds_in_schedule": len(races),
    }


# --------------------------------------------------------------------------- F1DB


def verify_f1db() -> dict:
    print("F1DB")
    rel = probe("f1db", "releases/latest", F1DB_RELEASE)
    if not rel:
        return {}
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "f1db_release.json").write_text(
        json.dumps(
            {k: rel[k] for k in ("tag_name", "published_at")}
            | {"assets": [{"name": a["name"], "size": a["size"]} for a in rel["assets"]]},
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )
    asset = next(a for a in rel["assets"] if a["name"] == "f1db-json-single.zip")
    resp, ms = get(asset["browser_download_url"])
    row = {
        "source": "f1db",
        "label": asset["name"],
        "url": asset["browser_download_url"],
        "status": resp.status_code,
        "auth_needed": resp.status_code in (401, 403),
        "ms": round(ms),
        "bytes": len(resp.content),
        "records": None,
    }
    info = {"tag": rel["tag_name"], "published_at": rel["published_at"]}
    if resp.is_success:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            db = json.loads(zf.read("f1db.json"))
            info["uncompressed_bytes"] = sum(i.file_size for i in zf.infolist())
        row["records"] = len(db["drivers"])
        info["top_level_keys"] = sorted(db)
        info["latest_race_with_results"] = max((r["year"], r["round"]) for r in db["races"] if r.get("raceResults"))
        # Keep only drivers entered in YEAR (seasons[].entrants[].constructors[].drivers[]) as the fixture.
        season = next(s for s in db["seasons"] if s["year"] == YEAR)
        current = {d["driverId"] for e in season["entrants"] for c in e["constructors"] for d in c["drivers"]}
        sample = [d for d in db["drivers"] if d["id"] in current]
        info["drivers_in_season"] = len(sample)
        info["driver_fields"] = sorted(db["drivers"][0])
        row["fixture"] = save_fixture("f1db_drivers_2026.json", sample)
    results.append(row)
    print(f"  {row['status']} {row['ms']:>6}ms {row['bytes']:>11,}B rec={row['records']} {asset['name']}")
    return info


# --------------------------------------------------------------------------- main


def main() -> None:
    target = find_target()
    race = target["race"]
    print(f"Target: {race['circuit_short_name']} {race['date_start']} session_key={race['session_key']}")
    openf1 = verify_openf1(race)
    radio = verify_team_radio(target["completed_races"])
    replay = replay_size(
        race,
        openf1["location"],
        openf1["position"] or [],
        openf1["_raw_location_bytes"],
    )
    print(f"Replay: {json.dumps(replay)}")
    jolpica = verify_jolpica(race)
    f1db = verify_f1db()
    summary = {
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "target": {
            k: race[k]
            for k in (
                "session_key",
                "meeting_key",
                "circuit_short_name",
                "date_start",
                "date_end",
            )
        },
        "openf1_race_sessions_completed": len(target["completed_races"]),
        "requests": results,
        "team_radio": radio,
        "replay": replay,
        "jolpica": jolpica,
        "f1db": f1db,
    }
    (FIXTURES / "results.json").write_text(json.dumps(summary, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"Wrote {FIXTURES / 'results.json'}")


if __name__ == "__main__":
    main()
