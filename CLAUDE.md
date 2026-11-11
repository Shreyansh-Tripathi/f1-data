# f1-data — static F1 data pipeline

Python pipeline that turns free F1 data sources into versioned static JSON, published to GitHub Pages and consumed by the `f1-app` Flutter app. Personal project, **zero budget**: never introduce a paid service, API key, or subscription.

## Decisions (do not change without asking)

- Completed sessions only. No live timing.
- This repo is **public** (free Actions minutes + free Pages).
- Runs on GitHub Actions cron; output served from GitHub Pages under `/v1/`.
- Actions runs start cold: restore prior output by checking out `gh-pages` into `site/`, and restore `.cache/` with `actions/cache`.
- The app only reads our JSON. Never expose source-specific field names in output.
- No FastF1: it needs a paid F1 TV subscription (see Findings).

## Sources (priority order)

| Source | Use for | Notes |
| --- | --- | --- |
| OpenF1 `https://api.openf1.org/v1/` | All session data: sessions, meetings, drivers, session_result, starting_grid, laps, stints, pit, intervals, position, race_control, weather, overtakes, team_radio, location | Historical (2023+) free, no auth. Observed limit ~30 req / min per IP (429 with `Retry-After: 60` above it); client spaces requests 2 s apart. championship_* endpoints are beta. Skip meetings with `is_cancelled`. Query `starting_grid` by `meeting_key` (it is keyed to the qualifying session). Fetch location per driver. Empty result = HTTP 404 `{"detail":"No results found."}`, mapped to `[]` and logged. car_data is not used. |
| Jolpica `https://api.jolpi.ca/ergast/f1/` | Round numbers (owner), standings (source of record), results, qualifying, sprint, schedule, grid, fastest lap | ~4 req/s, ~200 req/hour. Cache aggressively. Use season-wide paginated `results` / `qualifying` / `sprint` (`limit` + `offset`). Rounds skip cancelled events. |
| MultiViewer `circuit_info_url` from OpenF1 `meetings` | Circuit outline, corners, rotation | Free, no auth. Only follow URLs under `https://api.multiviewer.app/`. Fetch once per circuit per season, cache permanently. Fallback: outline from OpenF1 `location` (no corners). |
| F1DB (GitHub releases of `f1db/f1db`) | Career totals (titles, wins, podiums, starts); round short names (`grandsPrix.name` via `races.grandPrixId`); fallback for driver code, nationality (country id → demonym via `countries`) and date of birth when Jolpica lacks them | Download release asset once per run. Use `f1db-json-single.zip` (6.8 MB, 85 MB unzipped). Filter season entrants to drivers with race starts. |

Never send auth tokens. Never re-host team radio audio; store only F1's URL.

## Output contract (`site/v1/`)

```
manifest.json                       # {files: {path: {hash, updatedAt}}, schemaVersion, generatedAt}
{year}/season.json                  # rounds (name, shortName, winner), sessions (type, utc start), circuit ids, status
{year}/standings/drivers.json       # WDC after each round; entries carry `active`
{year}/standings/teams.json         # WCC after each round
{year}/drivers.json                 # number, code, name, nationality, team, colour, headshot
{year}/driver-stats.json            # season race stats + separate sprint stats + career totals + per-round results
{year}/teams.json                   # name, shortName, colour, drivers
circuits/{circuitId}.json           # polyline, corners, rotation, length (OpenF1 location frame)
{year}/{round}/{session}/summary.json | laps.json | strategy.json | race-control.json | quali.json | overtakes.json | replay.json | radio.json
```

Session codes: `fp1 fp2 fp3 sq sprint q race`. `summary.json` has a distinct shape per session type (practice, qualifying, race/sprint), discriminated by `type`. `overtakes.json` exists for race and sprint only. Session files carry `provisional`.

## Rules

- **Derive, don't hardcode.** Grid size, knockout counts, points systems, sprint weekends: all from data. Quali knockout segment = last of Q1/Q2/Q3 the driver took part in: Jolpica lists a segment key with an empty time when a driver reached it without setting a lap (e.g. `"Q2": ""`). Without Jolpica (sprint qualifying), fall back to the last segment with a time.
- Times as integer milliseconds; dates as UTC ISO-8601.
- Every file has `schemaVersion` and `generatedAt`. Output models are `pydantic` v2; validate before writing.
- Deterministic output: sorted keys, stable ordering, so unchanged data produces unchanged hashes. Keep the previous `generatedAt` when the rest of a file is unchanged.
- `replay.json`: columnar per driver (`t`, `x`, `y`, `pos`), 1 Hz, integer coords. Budget < 2 MB per session.
- Idempotent and resumable: skip sessions whose output exists and is not provisional, unless `--force`.
- All HTTP through one client with retry, exponential backoff, per-host rate limiting, and an on-disk cache (`.cache/`, gitignored).

### Data rules

- **Cache finality.** A session's data and its round's Jolpica data are cached permanently only once the session ended at least 72 h ago **and** Jolpica has the round. Before that: 1 h TTL, and outputs are written with `provisional: true`.
- **Matching.** Jolpica owns round numbers. Match each round to the OpenF1 meeting whose race date is within ±1 day; cancelled OpenF1 meetings are skipped.
- **Classification.** DNF/DNS/DSQ come from Jolpica `positionText` (`R`/`N` = DNF, `W`/`F` = DNS, `D`/`E` = DSQ; `N` is "not classified", i.e. ran but did not finish), cross-checked with OpenF1 `dnf`/`dns`/`dsq`. Never parse `status` strings.
- **Grid and fastest lap** come from Jolpica results (`grid`, `FastestLap.rank == "1"`). OpenF1 `starting_grid` is only a fallback.
- **Gaps.** `gapMs` is an integer on the lead lap. For lapped cars `gapMs` is null and `lapsBehind` is an integer (OpenF1 sends `"+1 LAP"` / `"+2 LAPS"`).
- **Laps.** `laps.json` carries per-lap `pos` and `gapMs`/`lapsBehind`, from `position` and `intervals` reduced to the last value at or before each lap's end.
- **Pit stops.** `strategy.json` stops are OpenF1 `pit` rows minus non-stops: rows dated before the session start (pre-race, all durations null), rows without a lane time (logged), and rows whose pit lane entry (`date` − lane duration; `date` is the lane exit) falls inside a red-flag suspension, from the `RED FLAG` message to the next `SESSION STARTED`. `laneMs` is always an int; `stopMs` is null when OpenF1 lacks `stop_duration`.
- **Penalties.** Race-control entries keep the raw `message` and add `penalty: {driver, kind, seconds, reason} | null`, with `kind` one of `time | driveThrough | stopGo | grid`. `seconds` holds seconds for time and stop-go penalties, places for grid penalties, null otherwise. `reason` is the stewards' text after `FOR CAR n (XXX) - `, in sentence case (acronyms `DRS VSC SC DSQ DNF FIA` and driver codes like `(NOR)` stay upper case), without the trailing clock time; null when the message has none. "PENALTY SERVED" and investigation messages have no penalty.
- **Stats.** Season stats are race-only (`wins, podiums, poles, dnfs, points`), with separate `sprintWins, sprintPodiums, sprintPoles, sprintPoints`. Each driver also has `rounds`: per round they qualified or raced in, `teamId` (that round's entry), `qualiPosition`, `grid`, `racePosition` (null unless classified), `raceClassification`, `racePoints`, all from Jolpica.
- **Names.** Round `shortName` is F1DB's grand prix name for the race within ±1 day ("Great Britain", "São Paulo"); fallback is the OpenF1 meeting name, else Jolpica's, without "Grand Prix". Round `winner` `{driverId, teamId}` is the Jolpica `positionText == "1"` row, null until completed. Team `shortName` comes from the curated `TEAM_SHORT_NAMES` map in `build/drivers.py`, keyed by team id (owner decision, an exception to "derive, don't hardcode"); unknown ids fall back to the full name. Add new constructors to the map (a test fails otherwise).
- **Standings** keep drivers no longer racing, with `active: false`.
- **Driver identity.** `drivers.json` takes code, nationality and date of birth from Jolpica, falling back to F1DB per field. Number, colour and headshot come from OpenF1 (latest race session); drivers without OpenF1 data keep Jolpica's race number and have null colour/headshot.
- **Circuits.** Coordinates stay in the OpenF1 location frame as integers; `rotation` is passed through and applied in the app.
- **404s.** Every OpenF1 404 mapped to `[]` is logged at WARNING with its URL.

## Stack and layout

Python 3.12, `uv`, `httpx`, `pydantic`, `numpy`, `pytest`, `ruff`.

```
src/f1data/
  http.py            # shared client: retry, rate limit, cache
  sources/           # openf1.py, jolpica.py, f1db.py, multiviewer.py  (raw fetch only)
  models/            # pydantic output models = the contract
  build/             # finality.py, ids.py, season.py, standings.py, drivers.py, circuits.py, session.py, replay.py
  io.py              # validated, deterministic writes
  manifest.py
  cli.py             # `f1data build --year 2026 [--round N] [--session race] [--force] [--offline]`
tests/               # unit tests on fixtures in tests/fixtures/ (recorded JSON, no network)
site/v1/             # build output (published to gh-pages, not committed to main)
```

## Commands

```
uv sync
uv run ruff check . && uv run ruff format .
uv run pytest
uv run f1data build --year 2026
```

## CI and publishing

- `.github/workflows/ci.yml`: on push to `main` and on PRs, runs `uv sync --locked`, `ruff check`, `ruff format --check` and `pytest`. Tests are offline: they replay `tests/fixtures/http/` through `httpx.MockTransport`. No fixture may exceed 5 MB (enforced by `tests/test_fixture_sizes.py`).
- `.github/workflows/build.yml`:
  - **Schedule (UTC):** hourly Fri–Mon (`0 * * * 5,6,0,1`), every 6 h Tue–Thu (`0 */6 * * 2-4`).
  - **Manual runs:** `workflow_dispatch` with inputs `year`, `round`, `session`, `force`.
  - **Settings:** `permissions: contents: write`; concurrency group `build-data` without cancel-in-progress (runs queue); 60 min timeout.
  - **Steps:**
    1. Check out `gh-pages` into `site/`.
    2. Restore `.cache/` with `actions/cache`.
    3. `uv sync --locked`.
    4. `f1data build`. If it fails, the steps continue and the job fails at the end.
    5. Publish, but only if `site/v1/manifest.json` changed: write `site/.nojekyll`, build an orphan commit from `site/` with a temporary index, and force-push it to `gh-pages`. The branch always holds one squashed commit.
  - All actions are pinned to commit SHAs, with the exact version in a comment.
- `.github/dependabot.yml`: weekly updates for GitHub Actions and Python. Python uses the `uv` ecosystem so `uv.lock` is updated together with `pyproject.toml`.
- Pages must be configured to serve the `gh-pages` branch root. The data URL is `https://shreyansh-tripathi.github.io/f1-data/v1/`.

## Working style

- Plan before multi-file changes; keep diffs small; one concern per commit.
- Don't add Co-Authored-By trailers to commits.
- Add a fixture-based test for every builder.
- When a source behaves differently than documented here, update the Sources table and the Findings log below.

## Status

- [x] Phase 0 — verify sources (see Findings)
- [x] Phase 1 — pipeline + Actions + Pages
- [ ] Phase 5 — replay builder
- [ ] Phase 6 — radio + local whisper.cpp transcription

## Findings log

### 2026-11-10 — Phase 0 source verification

Run: `uv run scripts/verify_sources.py`. Target is the latest completed 2026 race, the **Brazilian GP**: OpenF1 session_key 11412 / meeting 1299, Jolpica round 20. Raw responses are in `tests/fixtures/phase0/`. Responses over 1 MB are saved as `*.sample.json` (first 200 records). The full metrics are in `results.json`. No request needed auth. Every request returned 200 unless noted.

| Source / endpoint | Status | Time | Records | Payload |
| --- | --- | --- | --- | --- |
| OpenF1 meetings (year) / sessions (year, Race) | 200 / 200 | 0.9 s / 0.4 s | 27 / 25 | 21 KB / 9 KB |
| OpenF1 drivers / session_result | 200 | 0.2 s | 22 / 22 | 9 KB / 4 KB |
| OpenF1 starting_grid (`meeting_key`) | 200 (404 with race `session_key`) | 0.5 s | 22 | 2 KB |
| OpenF1 laps / stints / pit | 200 | 0.9 / 0.2 / 0.2 s | 1437 / 80 / 58 | 676 / 12 / 11 KB |
| OpenF1 intervals / position | 200 | 2.8 / 0.3 s | 29,368 / 576 | 4.2 MB / 67 KB |
| OpenF1 race_control / weather / team_radio | 200 | 0.2–1.0 s | 212 / 160 / 30 | 57 / 34 / 7 KB |
| OpenF1 overtakes (not in table, exists) | 200 | 0.2 s | 297 | 46 KB |
| OpenF1 championship_drivers / _teams (beta) | 200 | 0.3 s | 25 / 11 | 4 / 2 KB |
| OpenF1 location (22 per-driver requests) | 200 | 104 s total | 794,970 | 103 MB |
| OpenF1 car_data (22 per-driver requests) | 200 | 89 s total | 795,406 | 135 MB |
| MultiViewer `meetings.circuit_info_url` | 200 | 0.6 s | 15 corners, 283 pts | 11 KB |
| Jolpica schedule / results / qualifying | 200 | 0.1 s | 23 rounds / 22 / 22 | 14 / 13 / 9 KB |
| Jolpica sprint (not a sprint weekend) | 200 | <0.1 s | 0 | 188 B |
| Jolpica driverStandings / constructorStandings | 200 | <0.1 s | 25 / 11 | 11 / 3 KB |
| F1DB GitHub releases/latest | 200 | 0.5 s | `v2026.20.0` | 37 KB |
| F1DB `f1db-json-single.zip` | 200 | 0.8 s | 921 drivers | 6.8 MB (85 MB unzipped) |
| FastF1 3.9.2 `session.load()` + `get_circuit_info()` | see below | 7.3 s | — | — |

Findings:

- **FastF1 fails without an account, for every year.** `load()` returns but logs `No F1 account credentials available. Set the environment variables F1_USERNAME and F1_PASSWORD.` After that, timing, laps, session_info, car/pos data, weather and race control are all unavailable. `get_circuit_info()` raises `DataNotLoadedError`. Round 1 of 2026 and 2024 R21 fail the same way. Only `session.results` loads (22 rows, from the Ergast fallback). `livetiming.formula1.com/static/.../*.json(Stream)` returns 401.
- **A free F1 account doesn't fix FastF1; it needs a paid F1 TV subscription.** FastF1 3.9.2 (`fastf1/internals/f1account/login.py`) signs in, then reads the `subscriptionToken` claim from the access token. If the claim is missing it raises `An active F1TV subscription is required.` The free-account sign-in test (2026-11-10) was rejected at the password step (`There was a problem with your password or email address`), so this conclusion comes from reading the code, not from a successful login.
- **Circuit data replacement candidate.** OpenF1 `meetings` has a `circuit_info_url` field pointing to `https://api.multiviewer.app/api/v1/circuits/{circuit_key}/{year}`. It needs no auth and returns `corners`, `rotation`, `x`/`y` outline, marshal sectors and `pitLoss`. This is FastF1's upstream for circuit info. **This is a new source, so it needs owner sign-off before use.**
- **Cancelled meetings.** OpenF1 keeps sessions for cancelled meetings (2026: Bahrain 1282, Saudi Arabia 1283, `is_cancelled=true`), plus Kuala Lumpur (1308) was added. OpenF1 lists 25 race sessions; Jolpica has 23 rounds. Round numbers come from Jolpica, matched on race date.
- **OpenF1 `starting_grid`** comes back on the qualifying session_key (11408). Querying it with the race session_key returns 404. Query it by `meeting_key`.
- **OpenF1 location/car_data** are about 100–135 MB per race. They need per-driver requests (≈4.5 s each, still inside the rate limit). Fetch only location; car_data is not in the output contract.
- **Replay size** for location, columnar `{t, x, y, pos}` per driver with integer coords, cut to the race window (17:00–19:00Z):
  - Raw (whole session): 103 MB
  - 2 Hz: 286,330 samples, 6.18 MB JSON, **1.86 MB gzip**
  - 1 Hz: 144,364 samples, 3.12 MB JSON, **0.98 MB gzip**

  The < 2 MB budget only holds for 2 Hz if gzip is used. Pages serves gzip, but this race has little headroom; 1 Hz is the safe default.
- **Team radio, last 5 rounds (by meeting):** KL 78, Singapore 88, Austin 93, Mexico 81, Brazil 102 clips. The race session alone has 30. `recording_url` is `livetiming.formula1.com/static/.../TeamRadio/*.mp3`. Fetched with no auth it gives 200, `audio/mpeg`, 290 KB, while the JSON on the same host gives 401.
- **Freshness:** the race ended 2026-11-08 19:00Z. By about 2026-11-10, OpenF1, Jolpica (round 20) and F1DB (`v2026.20.0`, published 2026-11-09 07:04Z) all had it.
- **F1DB** season drivers are nested under `seasons[].entrants[].constructors[].drivers[]`. 2026 has 36 entries because it counts FP1/test drivers, so filter by race starts. Driver objects carry `totalRaceWins`, `totalPodiums`, `totalRaceStarts`, `totalChampionshipWins` and similar fields.
- **Jolpica** `driverStandings` has 25 rows and OpenF1 `championship_drivers` has 25, but only 22 drivers are active. Mid-season driver changes are kept in standings.

### 2026-11-10 — Decisions applied

- FastF1 removed. MultiViewer approved as the circuit source, with an OpenF1-location outline as fallback.
- Replay at 1 Hz. car_data dropped. Overtakes added to the contract.
- Jolpica owns round numbers; cancelled OpenF1 meetings skipped; rounds matched by race date ±1 day.
- Data rules added above (cache finality, classification, gaps, penalties, stats, circuit frame, 404 logging).
- Fixture facts behind these rules (Brazil 2026):
  - Jolpica `status` values are `Finished`, `Lapped`, `Retired`, `Did not start`. They are not usable for classification; `positionText` is (`R`, `W`).
  - OpenF1 `session_result.gap_to_leader` and `intervals.gap_to_leader` are strings such as `"+1 LAP"` for lapped cars.
  - Jolpica `FastestLap.rank` is per driver. The fastest lap is `rank == "1"`; the winner had rank 3.
  - Stewards messages have `driver_number: null`. The car number appears only in the text, e.g. `5 SECOND TIME PENALTY FOR CAR 23 (ALB) - REJOINING UNSAFELY`.
- Quali (Brazil 2026): Verstappen was P16 with `Q2: ""` in Jolpica and no Q2 time in OpenF1. "Last segment with a time" puts him out in Q1 (counts 7/5); Jolpica participation gives the real 6/6.
- `laps.json` `pos` is the on-track running order at each lap end, so it can differ from the final classification after post-race penalties (Brazil 2026: Leclerc/Lawson).
- MultiViewer outline units are decimetres. Its reference lap is old (Interlagos uses a 2021 FP1 lap), so check circuits that changed layout. `lengthM` is the outline perimeter: 4,232 m vs the official 4,309 m (−1.8%).
- OpenF1 returned one 429 with `Retry-After: 60` during fixture recording at 0.4 s spacing.
- Full 2026 season cold build (2026-11-10): 447 files, 7.4 MB, 26 min, 714 OpenF1 + 21 MultiViewer requests. At 0.4 s spacing OpenF1 returned 21 × 429 (`Retry-After: 60`), so its real limit is ~30 req/min; spacing is now 2 s.
- OpenF1 `session_result.position` can be a string code for unclassified entries (Singapore 2026 Q: `"RT"` for car 6, no times). Treated as `position: null`, sorted last.
- Jolpica round 16 of 2026 is named "Bahrain Grand Prix in Malaysia" (Kuala Lumpur replacement event); matching by date handles it.
- OpenF1 `dnf: true` can disagree with Jolpica for classified finishers who stopped in the last laps (Jolpica `positionText` numeric). This is logged as a mismatch; Jolpica wins.
- Jolpica can lack `code`, `nationality` and `dateOfBirth` for a driver's first race (Rafael Camara, round 20 of 2026, in results, qualifying and `/2026/drivers.json`). F1DB had all three, so they are filled from it.
- OpenF1 stewards also write `STOP-AND-GO PENALTY` (hyphenated, 2026: COL, HAD); the parser previously missed it. Reasons can contain " - " (`FALSE START - MOVING BEFORE SIGNAL`) and an offence count (`TRACK LIMITS (5TH OFFENCE)`); some messages have no reason (`... FOR CAR 23 (ALB) (15:04:34)`).
- Short names: OpenF1 meeting names are adjectival for most rounds ("British", "Hungarian") and OpenF1 calls the Kuala Lumpur round "Bahrain Grand Prix"; F1DB `grandsPrix.name` gives F1's style ("Great Britain", "Mexico", "Barcelona-Catalunya").
- OpenF1 `pit` records red-flag pit-lane waits as pit entries (2026 rounds 6, 12, 13, 19: lane times of 26–38 min, plus ~55 s exits at the restart), and Mexico 2026 has 9 pre-race rows at 19:08:59Z with null durations. `date` is the pit lane exit. `stop_duration` is missing for about a third of rows. Long non-red-flag entries are real (garage repairs, e.g. 21 min in the Dutch sprint).
