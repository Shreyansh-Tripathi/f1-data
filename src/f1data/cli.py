"""`f1data build --year 2026 [--round N] [--session race] [--force] [--offline] [--out site/v1]`."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from f1data.build import circuits, ids
from f1data.build.driver_stats import build_driver_stats
from f1data.build.drivers import active_driver_ids, build_drivers, build_teams
from f1data.build.finality import is_final
from f1data.build.season import build_season
from f1data.build.session import (
    SessionMeta,
    build_laps,
    build_overtakes,
    build_practice_summary,
    build_quali,
    build_qualifying_summary,
    build_race_control,
    build_race_summary,
    build_strategy,
    f1db_quali_codes,
    f1db_retirement_reasons,
)
from f1data.build.standings import build_driver_standings, build_team_standings, title_context
from f1data.calendar import should_build
from f1data.http import HttpClient, HttpError
from f1data.io import is_complete, read_json, write_model
from f1data.manifest import write_manifest
from f1data.models.meta import PublishLag, SessionLag
from f1data.sources import f1db, jolpica, multiviewer, openf1

log = logging.getLogger("f1data")

ROOT = Path.cwd()
SESSION_CODES = ["fp1", "fp2", "fp3", "sq", "sprint", "q", "race"]


class Build:
    def __init__(
        self,
        client: HttpClient,
        out: Path,
        year: int,
        now: datetime,
        force: bool,
        lag_file: Path,
        clock: Callable[[], datetime],
    ) -> None:
        self.c = client
        self.out = out
        self.year = year
        self.now = now
        self.force = force
        self.lag_file = lag_file
        self.clock = clock
        previous = read_json(lag_file)
        self.lag = PublishLag.model_validate(previous).sessions if previous else {}
        self.lag_changed = False
        self.failures = 0
        self.written = 0
        self._f1db: dict[str, Any] | None = None

    def write(self, rel: str, model: Any) -> None:
        if write_model(self.out / rel, model):
            self.written += 1
            log.info("wrote %s", rel)

    def guarded(self, what: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:  # one failure must not stop the run
            self.failures += 1
            log.exception("failed: %s", what)

    # ------------------------------------------------------------------ run

    def run(self, round_filter: int | None, session_filter: str | None) -> None:
        c, y = self.c, self.year
        self.meetings = openf1.meetings(c, y)
        self.sessions = openf1.sessions(c, y)
        self.schedule = jolpica.schedule(c, y)
        self.results = jolpica.results(c, y)
        self.qualifying = jolpica.qualifying(c, y)
        self.sprint = jolpica.sprint(c, y)
        self.weekends = ids.match_weekends(self.schedule, self.meetings, self.sessions)
        self.known = ids.season_drivers(self.results, self.qualifying, self.sprint)
        self.completed = {int(r["round"]) for r in self.results}

        self.guarded("season", self.build_season)
        selected = [w for w in self.weekends if round_filter in (None, w.round)]
        for w in selected:
            self.guarded(f"circuit {w.circuit_id}", lambda w=w: self.build_circuit(w))
        self.guarded("drivers/teams", self.build_drivers_teams)
        self.guarded("standings", self.build_standings)
        self.guarded("driver-stats", self.build_driver_stats)
        for w in selected:
            for code in SESSION_CODES:
                if session_filter in (None, code) and code in w.sessions:
                    self.guarded(f"round {w.round} {code}", lambda w=w, code=code: self.build_session(w, code))
        if self.lag_changed:
            write_model(self.lag_file, PublishLag(generated_at=self.now, sessions=self.lag))
        full_build = round_filter is None and session_filter is None and not self.failures
        write_manifest(self.out, self.now, full_build)
        log.info("done: %d files written, %d failures, requests %s", self.written, self.failures, self.c.request_counts)

    # ------------------------------------------------------------------ season-level

    def build_season(self) -> None:
        cancelled = ids.cancelled_meetings(self.meetings)
        self.write(
            f"{self.year}/season.json",
            build_season(self.year, self.weekends, cancelled, self.completed, self.results, self.f1db(), self.now),
        )

    def build_circuit(self, w: ids.Weekend) -> None:
        rel = f"circuits/{w.circuit_id}.json"
        existing = read_json(self.out / rel)
        if not self.force and existing and existing.get("season") == self.year and existing.get("source") == "official":
            return
        name = next(r["Circuit"]["circuitName"] for r in self.schedule if int(r["round"]) == w.round)
        if w.circuit_info_url:
            try:
                mv = multiviewer.circuit(self.c, w.circuit_info_url)
                self.write(rel, circuits.from_multiviewer(w.circuit_id, name, self.year, mv, self.now))
                return
            except (HttpError, ValueError, KeyError) as exc:
                log.warning("MultiViewer failed for %s (%s); trying location fallback", w.circuit_id, exc)
        if existing:
            return  # keep whatever we have rather than downgrading
        samples = self.fastest_lap_location(w)
        if samples:
            self.write(rel, circuits.from_location(w.circuit_id, name, self.year, samples, self.now))
        else:
            log.warning("no circuit data for %s yet", w.circuit_id)

    def fastest_lap_location(self, w: ids.Weekend) -> list[dict[str, Any]]:
        race = w.sessions.get("race")
        if not race or ids.parse_utc(race["date_end"]) > self.now:
            return []
        laps = [lap for lap in openf1.laps(self.c, race["session_key"]) if lap.get("lap_duration")]
        if not laps:
            return []
        best = min(laps, key=lambda lap: lap["lap_duration"])
        start = ids.parse_utc(best["date_start"])
        end = start + timedelta(seconds=best["lap_duration"])
        return openf1.location(
            self.c,
            race["session_key"],
            best["driver_number"],
            start.strftime("%Y-%m-%dT%H:%M:%S"),
            end.strftime("%Y-%m-%dT%H:%M:%S"),
            final=True,
        )

    def latest_race(self) -> tuple[ids.Weekend, ids.IdMap, list[dict[str, Any]]] | None:
        done = [w for w in self.weekends if w.round in self.completed and "race" in w.sessions]
        if not done:
            return None
        w = max(done, key=lambda w: w.round)
        sk = w.sessions["race"]["session_key"]
        drivers = openf1.drivers(self.c, sk, final=self.final(w, "race"))
        return w, self.idmap(w, drivers), drivers

    def build_drivers_teams(self) -> None:
        latest = self.latest_race()
        drivers, idmap = (latest[2], latest[1]) if latest else ([], ids.IdMap({}, {}))
        y = self.year
        out = build_drivers(y, self.known, self.results, drivers, idmap, self.f1db(), self.now)
        self.write(f"{y}/drivers.json", out)
        self.write(f"{y}/teams.json", build_teams(y, self.results, drivers, idmap, self.now))

    def build_standings(self) -> None:
        y = self.year
        weekends = {w.round: w for w in self.weekends}
        drivers, teams = {}, {}
        for rnd in sorted(self.completed):
            final = rnd in weekends and self.final(weekends[rnd], "race")
            drivers[rnd] = jolpica.driver_standings(self.c, y, rnd, final=final)
            teams[rnd] = jolpica.constructor_standings(self.c, y, rnd, final=final)
        active = active_driver_ids(self.results)
        ctx = title_context(self.schedule, self.results, self.sprint)
        self.write(f"{y}/standings/drivers.json", build_driver_standings(y, drivers, active, ctx, self.now))
        self.write(f"{y}/standings/teams.json", build_team_standings(y, teams, ctx, self.now))

    def f1db(self) -> dict[str, Any]:
        if self._f1db is None:
            self._f1db = f1db.download_json_single(self.c, f1db.latest_release(self.c))
        return self._f1db

    def build_driver_stats(self) -> None:
        db = self.f1db()
        out = build_driver_stats(self.year, self.known, self.results, self.qualifying, self.sprint, db, self.now)
        self.write(f"{self.year}/driver-stats.json", out)

    # ------------------------------------------------------------------ sessions

    def final(self, w: ids.Weekend, code: str) -> bool:
        s = w.sessions.get(code)
        return bool(s) and is_final(ids.parse_utc(s["date_end"]), w.round in self.completed, self.now)

    def idmap(self, w: ids.Weekend, openf1_drivers: list[dict[str, Any]]) -> ids.IdMap:
        rows = next((r for r in self.results if int(r["round"]) == w.round), None) or next(
            (r for r in self.qualifying if int(r["round"]) == w.round), {}
        )
        return ids.session_ids(openf1_drivers, ids.round_entries(rows) if rows else {}, self.known)

    def jolpica_rows(self, races: list[dict[str, Any]], rnd: int, key: str) -> list[dict[str, Any]] | None:
        race = next((r for r in races if int(r["round"]) == rnd), None)
        return race[key] if race else None

    def build_session(self, w: ids.Weekend, code: str) -> None:
        s = w.sessions[code]
        if ids.parse_utc(s["date_end"]) > self.now:
            return
        rel = f"{self.year}/{w.round}/{code}"
        if not self.force and is_complete(self.out / rel):
            log.debug("skip %s (complete)", rel)
            return
        jolpica_rows = None
        if code == "race":
            jolpica_rows = self.jolpica_rows(self.results, w.round, "Results")
        elif code == "sprint":
            jolpica_rows = self.jolpica_rows(self.sprint, w.round, "SprintResults")
        if code in ("race", "sprint") and not jolpica_rows:
            log.info("skip %s: Jolpica has no classification yet", rel)
            return

        final = self.final(w, code)
        meta = SessionMeta(year=self.year, round=w.round, session=code, provisional=not final, generated_at=self.now)
        c, sk = self.c, s["session_key"]
        drivers = openf1.drivers(c, sk, final=final)
        idmap = self.idmap(w, drivers)
        results = openf1.session_result(c, sk, final=final)
        weather = openf1.weather(c, sk, final=final)
        laps = openf1.laps(c, sk, final=final)
        stints = openf1.stints(c, sk, final=final)
        rc = openf1.race_control(c, sk, final=final)
        is_race = code in ("race", "sprint")
        position = openf1.position(c, sk, final=final) if is_race else []
        intervals = openf1.intervals(c, sk, final=final) if is_race else []
        pit = openf1.pit(c, sk, final=final) if is_race else []

        if is_race:
            grid = openf1.starting_grid(c, w.meeting_key, final=final) if code == "race" else None
            reasons = f1db_retirement_reasons(self.f1db(), w.race_start.date(), sprint=code == "sprint")
            summary = build_race_summary(meta, jolpica_rows, results, weather, idmap, grid, rc, reasons, drivers)
            self.write(f"{rel}/overtakes.json", build_overtakes(meta, openf1.overtakes(c, sk, final=final), idmap))
        elif code in ("q", "sq"):
            jq = self.jolpica_rows(self.qualifying, w.round, "QualifyingResults") if code == "q" else None
            codes = f1db_quali_codes(self.f1db(), w.race_start.date(), sprint=code == "sq")
            summary = build_qualifying_summary(meta, results, weather, idmap, jq, codes, drivers)
            self.write(f"{rel}/quali.json", build_quali(meta, results, idmap, jq, codes))
        else:
            summary = build_practice_summary(meta, results, laps, stints, weather, idmap, drivers)
        self.write(f"{rel}/laps.json", build_laps(meta, laps, position, intervals, idmap))
        self.write(f"{rel}/strategy.json", build_strategy(meta, stints, pit, rc, idmap, ids.parse_utc(s["date_start"])))
        self.write(f"{rel}/race-control.json", build_race_control(meta, rc, idmap))
        # summary last: its presence (non-provisional) marks the session complete.
        first_publish = not (self.out / rel / "summary.json").exists()
        self.write(f"{rel}/summary.json", summary)
        if first_publish and rel not in self.lag:
            self.lag[rel] = SessionLag(session_end=session_end(s, rc), first_published_at=self.clock())
            self.lag_changed = True


def session_end(session: dict[str, Any], race_control: list[dict[str, Any]]) -> datetime:
    """Last chequered flag (races overrun their slot, e.g. after a red flag), else the scheduled end."""
    flags = [ids.parse_utc(r["date"]) for r in race_control if (r.get("flag") or "").upper() == "CHEQUERED"]
    return max(flags) if flags else ids.parse_utc(session["date_end"])


def should_build_command(args: argparse.Namespace, now: datetime) -> int:
    season = read_json(args.out / str(args.year or now.year) / "season.json")
    manifest = read_json(args.out / "manifest.json") or {}
    last = manifest.get("lastFullBuildAt")
    decision, reason = should_build(now, season, ids.parse_utc(last) if last else None)
    log.info("should-build: %s (%s)", str(decision).lower(), reason)
    print(str(decision).lower())
    return 0


def main(argv: list[str] | None = None, *, client: HttpClient | None = None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(prog="f1data")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="build static JSON for a season")
    b.add_argument("--year", type=int, required=True)
    b.add_argument("--round", type=int)
    b.add_argument("--session", choices=SESSION_CODES)
    b.add_argument("--force", action="store_true", help="rebuild sessions even if complete")
    b.add_argument("--offline", action="store_true", help="serve only from .cache/")
    b.add_argument("--out", type=Path, default=ROOT / "site" / "v1")
    b.add_argument("--cache", type=Path, default=ROOT / ".cache")
    b.add_argument("--lag-file", type=Path, help="publish-lag record (default: <out>/../meta/lag.json)")
    b.add_argument("-v", "--verbose", action="store_true")
    sb = sub.add_parser("should-build", help="print true/false: should a scheduled run build now?")
    sb.add_argument("--year", type=int, help="season to check (default: current UTC year)")
    sb.add_argument("--out", type=Path, default=ROOT / "site" / "v1")
    sb.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.command == "should-build":
        return should_build_command(args, now or datetime.now(UTC))
    http = client or HttpClient(args.cache, offline=args.offline)
    lag_file = args.lag_file or args.out.parent / "meta" / "lag.json"
    clock = (lambda: now) if now else (lambda: datetime.now(UTC))
    build = Build(http, args.out, args.year, now or datetime.now(UTC), args.force, lag_file, clock)
    build.run(args.round, args.session)
    return 1 if build.failures else 0


if __name__ == "__main__":
    sys.exit(main())
