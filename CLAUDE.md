# f1-data — static F1 data pipeline

Python pipeline that turns free F1 data sources into versioned static JSON, published to GitHub Pages and consumed by the `f1-app` Flutter app. Personal project, **zero budget**: never introduce a paid service, API key, or subscription.

## Decisions (do not change without asking)

- Completed sessions only. No live timing.
- This repo is **public** (free Actions minutes + free Pages).
- Runs on GitHub Actions (hourly cron as a backup starter, calendar-aware; while a round is in progress a run polls and dispatches its successor: see CI and publishing); output served from GitHub Pages under `/v1/`.
- Actions runs start cold: restore prior output by checking out `gh-pages` into `site/`, and restore `.cache/` with `actions/cache`.
- The app only reads our JSON. Never expose source-specific field names in output.
- No FastF1: it needs a paid F1 TV subscription (see Findings).

## Sources (priority order)

| Source | Use for | Notes |
| --- | --- | --- |
| OpenF1 `https://api.openf1.org/v1/` | All session data: sessions, meetings, drivers, session_result, starting_grid, laps, stints, pit, intervals, position, race_control, weather, overtakes, team_radio, location | Historical (2023+) free, no auth. Observed limit ~30 req / min per IP (429 with `Retry-After: 60` above it); client spaces requests 2 s apart. championship_* endpoints are beta. Skip meetings with `is_cancelled`. Query `starting_grid` by `meeting_key` (it is keyed to the qualifying session). Fetch location per driver. Empty result = HTTP 404 `{"detail":"No results found."}`, mapped to `[]` and logged. While a session is live it answers every unauthenticated request, past sessions included, with 401 `Live F1 session in progress…`. car_data is not used. |
| Jolpica `https://api.jolpi.ca/ergast/f1/` | Round numbers (owner), standings (source of record), results, qualifying, sprint, schedule, grid, fastest lap | ~4 req/s, ~200 req/hour. Cache aggressively. Use season-wide paginated `results` / `qualifying` / `sprint` (`limit` + `offset`). Rounds skip cancelled events. |
| MultiViewer `circuit_info_url` from OpenF1 `meetings` | Circuit outline, corners, rotation | Free, no auth. Only follow URLs under `https://api.multiviewer.app/`. Fetch once per circuit per season, cache permanently. Fallback: outline from OpenF1 `location` (no corners). |
| F1DB (GitHub releases of `f1db/f1db`) | Career totals (titles, wins, podiums, starts, poles, fastest laps); qualifying status (`positionText`); retirement reasons (`reasonRetired`); round short names (`grandsPrix.name` via `races.grandPrixId`); fallback for driver code, nationality (country id → demonym via `countries`) and date of birth when Jolpica lacks them | Download release asset once per run. Use `f1db-json-single.zip` (6.8 MB, 85 MB unzipped). Find the latest tag via the GitHub API with Actions' `GITHUB_TOKEN` (unauthenticated: 60 req/h per IP, exhausted on shared runners); without a token or if that fails, the `github.com/f1db/f1db/releases/latest` redirect; then the most recently cached tag in `.cache/`. An F1DB failure never blocks session files: they are built without it (F1DB-derived fields fall back or stay null) and filled in on a later run. Filter season entrants to drivers with race starts. |

Never send auth tokens. **Only exception:** the workflow's built-in `GITHUB_TOKEN` (free, no secret to manage), sent only to `api.github.com` (F1DB release lookup; `gh workflow run` for the next polling run) and only inside Actions; `HttpClient` refuses to send a token to any other host. Never re-host team radio audio; store only F1's URL.

## Output contract (`site/v1/`)

```
manifest.json                       # {files: {path: {hash, updatedAt}}, lastFullBuildAt, schemaVersion, generatedAt}
{year}/season.json                  # rounds (name, shortName, winner, provisional), sessions (type, utc start), circuit ids, status
{year}/standings/drivers.json       # WDC after each round; entries carry `active`, `titleStatus`, `clinchedAtRound`
{year}/standings/teams.json         # WCC after each round; entries carry `titleStatus`, `clinchedAtRound`
{year}/drivers.json                 # number, code, name, nationality, team, colour, headshot
{year}/driver-stats.json            # season race stats + separate sprint stats + career totals + per-round results
{year}/teams.json                   # name, shortName, colour, drivers
circuits/{circuitId}.json           # polyline, corners, rotation, length (OpenF1 location frame)
{year}/{round}/{session}/summary.json | laps.json | strategy.json | race-control.json | quali.json | overtakes.json | replay.json | radio.json
{year}/{round}/race|sprint/grid.json  # starting grid, from the moment q/sq has results (see Data rules: Grid)
```

Outside `v1/`, not in the manifest and not part of the app contract: `meta/lag.json` (`{sessions: {"{year}/{round}/{session}": {sessionEnd, firstPublishedAt}}}`) records publish lag.

Session codes: `fp1 fp2 fp3 sq sprint q race`. `summary.json` has a distinct shape per session type (practice, qualifying, race/sprint), discriminated by `type`; every type lists the session's `drivers` (`{id, number, code, firstName, lastName, teamId, colour}` from that session's OpenF1 drivers, so practice-only drivers missing from `drivers.json` can be named). `overtakes.json` exists for race and sprint only. Session files carry two separate flags:
- `provisional`: the race/sprint is classified from OpenF1 only because Jolpica lacks the round yet (set on every file of that session; always false for practice and qualifying). Drives the app's "provisional" tag and all polling.
- `final`: past the 72 h corrections window with Jolpica's round, so the session won't be rebuilt. Drives permanent caching only.

## Contract change policy

- Each published file has its own `schemaVersion` (the model's `schema_version`; default 1).
- Within a schemaVersion, changes are **additive only**: new fields, new files.
- **Enums:** adding a value is additive (no bump), so the app must tolerate unknown enum values. Removing or renaming a value, or changing what a value means, bumps the version.
- Renaming or removing a field, changing its type or nullability, or changing a field's meaning bumps **that file's** `schemaVersion`. A change in meaning (of a field or an enum value, e.g. a different unit or source) can't be detected by the test, so bump it by hand.
- `tests/contract/snapshot.json` is the committed contract: every file's schemaVersion and its fields flattened to `path -> type` (`f1data/models/contract.py`). `tests/test_contract.py` fails on a non-additive change without a bump, and on any difference from the snapshot. After a reviewed change run `uv run python scripts/update_contract_snapshot.py` and commit the snapshot.
- New output models must be registered in `OUTPUT_FILES` (a test checks every `OutputFile` subclass is listed).
- Versions so far:
  - Session files (`summary.json`, `laps.json`, `strategy.json`, `quali.json`, `overtakes.json`) are 2, and `race-control.json` is 3. In version 1 (2 for race-control), `provisional` meant "inside the 72 h corrections window"; it now means "race/sprint classified from OpenF1 only", and the window became the new field `final`. `race-control.json` version 2 was the `deletion.timeMs` → `lapTimeMs` rename.
  - `grid.json` (new 2026-11-22) is 1; its `provisional` means "qualifying order, no published starting grid yet".
  - `replay.json` (new 2026-11-22) is 1.
  - Everything else is 1. `season.json` round `provisional` (provisional race winner) was additive, no bump.

## Rules

- **Derive, don't hardcode.** Grid size, knockout counts, points systems, sprint weekends: all from data. **Only exception:** `TEAM_SHORT_NAMES` in `build/drivers.py`, a curated map of team id → display short name (`teams.json` `shortName`). It holds display names only and is never used in logic; unknown ids fall back to the full name; a test fails if any team in the current season's `teams.json` lacks an entry, so a new team gets noticed instead of silently falling back. Quali knockout segment = last of Q1/Q2/Q3 the driver took part in: Jolpica lists a segment key with an empty time when a driver reached it without setting a lap (e.g. `"Q2": ""`). Without Jolpica (sprint qualifying), fall back to the last segment with a time.
- Times as integer milliseconds; dates as UTC ISO-8601.
- Every file has `schemaVersion` (per file, see Contract change policy) and `generatedAt`. Output models are `pydantic` v2; validate before writing.
- Deterministic output: sorted keys, stable ordering, so unchanged data produces unchanged hashes. Keep the previous `generatedAt` when the rest of a file is unchanged.
- `replay.json` (race and sprint, see Data rules: Replay): one 1 Hz clock in ms with 0 = lights out; per driver delta-encoded integer `x`/`y` from `t0`, plus laps, positions, intervals, pit and stopped spells and an `end` marker. Target < 1 MB gzip per session.
- Idempotent and resumable: skip sessions whose `summary.json` exists with `final: true`, unless `--force`.
- All HTTP through one client with retry, exponential backoff, per-host rate limiting, and an on-disk cache (`.cache/`, gitignored).

### Data rules

- **Cache finality.** A session's data and its round's Jolpica data are cached permanently only once the session ended at least 72 h ago **and** Jolpica has the round. Before that: 1 h TTL (10 min while polling), and session files are written with `final: false` and rebuilt on every run. `final` has nothing to do with `provisional` (see Output contract).
- **Matching.** Jolpica owns round numbers. Match each round to the OpenF1 meeting whose race date is within ±1 day; cancelled OpenF1 meetings are skipped.
- **Classification.** DNF/DNS/DSQ come from Jolpica `positionText` (`R`/`N` = DNF, `W`/`F` = DNS, `D`/`E` = DSQ; `N` is "not classified", i.e. ran but did not finish), cross-checked with OpenF1 `dnf`/`dns`/`dsq`. Never parse `status` strings.
  - Until Jolpica has the round, a race or sprint is published from OpenF1 `session_result` alone with `provisional: true`: a numeric `position` is classified, otherwise `dsq`, `dns`, else DNF; `positionText` uses Jolpica's codes (`R`, `W`, `D`); points are OpenF1's; `statusText` is null; grid comes from OpenF1 `starting_grid`; fastest lap is the fastest OpenF1 lap. Later runs rebuild it from Jolpica. Nothing is published while neither source has a classification.
- **90% rule.** A race/sprint finisher is classified when they completed at least 90% of the winner's laps, rounded down. Jolpica's `positionText` already follows it (checked for every 2026 row); the builder logs a WARNING if a numeric/`R` row ever disagrees.
- **Grid and fastest lap** come from Jolpica results (`grid`, `FastestLap.rank == "1"`). OpenF1 `starting_grid` is only a fallback.
- **Gaps.** `gapMs` is an integer on the lead lap. For lapped cars `gapMs` is null and `lapsBehind` is an integer (OpenF1 sends `"+1 LAP"` / `"+2 LAPS"`).
- **Laps.** `laps.json` carries per-lap `pos` and `gapMs`/`lapsBehind`, from `position` and `intervals` reduced to the last value at or before each lap's end.
- **Pit stops.** `strategy.json` stops are OpenF1 `pit` rows minus non-stops: rows dated before the session start (pre-race, all durations null), rows without a lane time (logged), and rows whose pit lane entry (`date` − lane duration; `date` is the lane exit) falls inside a red-flag suspension, from the `RED FLAG` message to the next `SESSION STARTED`. `laneMs` is always an int; `stopMs` is null when OpenF1 lacks `stop_duration`.
- **Periods.** Every `summary.json` (race, sprint, qualifying, sprint qualifying, practice) has `periods: [{kind: sc|vsc|red, startLap, endLap, startTime, endTime, startMessage, endMessage}]` from race control:
  - SC: `SAFETY CAR DEPLOYED` .. `SAFETY CAR IN THIS LAP`; VSC: `VSC DEPLOYED` .. `VSC ENDING` (category `SafetyCar`). Deploying the other kind while one is open ends it there (VSC→SC, SC→VSC); a repeated deployment is ignored.
  - Red: a message starting `RED FLAG` (category `Other` in races) or a `Flag`/`RED` row .. the next `SESSION STARTED` (restart). A red flag ends any open SC/VSC.
  - Anything open at `CHEQUERED FLAG` / `SESSION FINISHED` ends there. In races and sprints later messages are ignored. In qualifying and practice each segment ends this way and processing continues, so an unresumed red flag (`Q2 WILL NOT BE RESUMED`) ends at `SESSION FINISHED`. Laps are null outside races (race control has no lap numbers there).
  - Other wording mentioning SAFETY CAR/VSC is logged at WARNING unless it is a known non-period message (lights on/off, behind/overtake the safety car, will use straight, SC/VSC infringement, "at safety car line").
- **Race result status.** `statusText` is Jolpica's `status` as published (2026 only has generic values: Finished, Lapped, Retired, Did not start, Disqualified). `retirementReason` is F1DB `raceResults` / `sprintRaceResults` `reasonRetired` ("Gearbox", "Collision", ...) for the race within ±1 day, matched by car number; null when F1DB has none (or doesn't have the race yet). Neither is used for classification.
- **Race-control kind** (first match wins): `penalty` (parsed penalty, `PENALTY SERVED`, `REPRIMAND`) → `warning` (`WARNING FOR CAR`) → `noFurtherAction` → `investigation` (`UNDER INVESTIGATION`, `WILL BE INVESTIGATED`) → `noted` → `lapDeleted` (`CAR … TIME [t] DELETED - …` / `LAP DELETED - …`) → `lapReinstated` (`… TIME t WILL BE REINSTATED` / `… LAP n WILL BE REINSTATED`) → `safetyCar` (category `SafetyCar` or SC/VSC wording) → `flag` (category `Flag`, `RED FLAG`, any `… FLAG`) → `other`.
  - Every entry keeps the raw `message` and adds `text`: sentence case, keeping `DRS VSC SC DSQ DNF FIA`, driver codes (`(NOR)`, and code-only `CAR LAW` when the code is a session driver's), session codes (`Q1`, `SQ2`, `F1`) and all numbers.
  - `shortText` is `text` without the car references whose drivers are in `driverIds`, together with the word introducing them (`FOR CAR 23 (ALB)`, `INVOLVING CARS 4 (NOR) AND 81 (PIA)`), so the app can show driver chips instead: "FIA stewards: 5 second time penalty - rejoining unsafely". References that don't resolve stay in.
  - `lapDeleted` entries carry `deletion: {reason: trackLimits|doubleYellow|other, lap, lapTimeMs, turn, reinstated}`; `lap` and `turn` are null when the message has none (e.g. `(NEXT LAP)`). A reinstatement sets `reinstated: true` on every earlier deletion of the same driver with the same time, or, when it names a lap, whose text names that lap. A reinstatement with no match is logged.
  - `driverIds` are the cars named as `CAR n` / `CARS n (X), m (Y) AND …` (plus OpenF1 `driver_number`), and code-only `CAR XXX`, resolved through the session's OpenF1 `name_acronym`.
- **Qualifying status** (`quali.json` and q/sq `summary.json`): `status: classified|dsq|dns|noTime` with raw `statusText`. Source: F1DB `qualifyingResults` / `sprintQualifyingResults` `positionText` for the race within ±1 day (digits = classified, `DSQ`/`EX` = dsq, `DNS` = dns, `NC`/`DNQ` = noTime). Before F1DB has the weekend: OpenF1 `dsq`, `dns`, then no segment time = noTime; `statusText` is then OpenF1's non-numeric position (e.g. `RT`). A `dsq` driver has `knockout` (and the summary's `eliminatedIn`) null, so they count in no segment's `eliminated`; their OpenF1 segment times stay as published (2026-11-22; nullability unchanged, no bump).
- **Penalties.** Race-control entries keep the raw `message` and add `penalty: {driver, kind, seconds, reason} | null`, with `kind` one of `time | driveThrough | stopGo | grid`. `seconds` holds seconds for time and stop-go penalties, places for grid penalties, null otherwise. `reason` is the stewards' text after `FOR CAR n (XXX) - `, in sentence case (acronyms `DRS VSC SC DSQ DNF FIA` and driver codes like `(NOR)` stay upper case), without the trailing clock time; null when the message has none. "PENALTY SERVED" and investigation messages have no penalty.
- **Stats.** Season stats are race-only (`wins, podiums, poles, dnfs, points`), with separate `sprintWins, sprintPodiums, sprintPoles, sprintPoints`. Each driver also has `rounds`: per round they qualified or raced in, `teamId` (that round's entry), `qualiPosition`, `grid`, `racePosition` (null unless classified), `raceClassification`, `racePoints`, all from Jolpica.
- **Names.** Round `shortName` is F1DB's grand prix name for the race within ±1 day ("Great Britain", "São Paulo"); fallback is the OpenF1 meeting name, else Jolpica's, without "Grand Prix". Round `winner` `{driverId, teamId}` is the Jolpica `positionText == "1"` row once the round is `completed`. Before that, while the race `summary.json` is provisional, it is that summary's P1 and the round has `provisional: true` (status stays `upcoming`; `provisional` is false on every other round); otherwise null. The season file is built again after the session files, so a race published from OpenF1 reaches the calendar in the same run. Team `shortName` comes from the curated `TEAM_SHORT_NAMES` map (see the exception under "Derive, don't hardcode").
- **Standings** keep drivers no longer racing, with `active: false`.
- **Title status** (latest round's entries only; null on earlier rounds), pure functions in `build/standings.py`:
  - Max remaining = remaining races × race points + remaining sprints × sprint points. Remaining events are the Jolpica schedule rounds after this one (cancelled events aren't in it). Points are the season's observed maximum for P1 (drivers) or P1 + P2 (constructors), from Jolpica results; if a sprint remains but none has run yet, max remaining is unbounded.
  - `champion`: the leader's margin over every rival is strictly greater than max remaining, or the final round is done (then the leader by Jolpica order, even on a points tie). Everyone else is `out`.
  - Otherwise `contender` when points + max remaining ≥ the leader's points, else `out`. Every entrant counts as a rival, including inactive drivers.
  - `clinchedAtRound` is on the champion's entry: the first round after which this test was decisive.
- **Driver identity.** `drivers.json` takes code, nationality and date of birth from Jolpica, falling back to F1DB per field. Number, colour and headshot come from OpenF1 (latest race session); drivers without OpenF1 data keep Jolpica's race number and have null colour/headshot.
- **Circuits.** Coordinates stay in the OpenF1 location frame as integers; `rotation` is passed through and applied in the app.
- **Grid.** `race/grid.json` and `sprint/grid.json` are built once the q/sq session has ended and its `quali.json` exists (a separate step after the q/sq build, skipped once `grid.json` is final). Order, first available: Jolpica race/sprint `grid` (`basis: official`; grid 0 = pit lane, after every slot in qualifying order), OpenF1 `starting_grid` rows for the q/sq `session_key` (`startingGrid`; already post-penalty), else our qualifying order (`qualifying`, `provisional: true`). Notes explain a driver starting behind their qualifying position: `gridPenalty` (with `places`) and `pitLane` only from a confirmed source (F1DB `startingGridPositions` / `sprintStartingGridPositions`, Jolpica grid 0 unless the driver did not start), `dsq` from the qualifying status, otherwise `moved`. Drivers moved up by others' penalties get no note. `final` follows the race's finality.
- **Replay.** `race/replay.json` and `sprint/replay.json`, built by a separate step after the session once its summary, strategy and race control are published and OpenF1 location reaches the chequered flag (checked on the classification's first driver; otherwise skipped until a later run).
  - Clock: ms, 0 = lights out (lap 1 `date_start`, which equals `SESSION STARTED`), one sample per `stepMs` (1000). The window starts at the formation lap (median over cars of the first movement after 60 s still, within 15 min before lights out; else lights out) and ends 15 s after the last finisher's final lap, capped at chequered + 3 min.
  - Samples: OpenF1 location without (0,0) placeholders or glitches (a jump faster than 120 m/s from the last kept sample; after 5 rejections in a row the new position is trusted), linearly interpolated onto the clock across gaps up to 10 s, held across longer ones. `x`/`y` are delta-encoded from `t0`. A car without data at the start (Las Vegas: Lawson until 151 s) starts at its first sample.
  - Per driver: `laps` [lap, start t] (a finisher's last entry is the finish), `pos` and `int` changes from OpenF1 position / intervals (interval in 100 ms steps; `[t, null]` leading, `[t, null, laps]` a lap or more behind), `pit` [entry, exit] from strategy stops, `stopped` spells (still on track 20 s or more, then moving, outside pit stops and red flags), `end {t, status, where}`: a retired car's samples end at its last sustained movement, `where` is `pitLane` when it stopped within 30 m of where cars drove through the pit lane, else `track`; `dns` has no samples.
  - Plus `periods` (summary SC/VSC/red), `events` (key race-control kinds and red flags) and `startLine` (median position at lap starts), all on the same clock.
  - A replay is only attempted while its session isn't final; a final session without `replay.json` had no complete location (see Findings, round 6) and is never asked again (`--force` still tries).
  - Location (~3 MB per driver, ~65 MB a race) is fetched once with `cache=False` (never in `.cache/`). Until the file is final, later runs only refresh its header and `end.status` from the summary without refetching; `--force` refetches everything, so keep forced full rebuilds rare.
- **Publish lag.** The first time a build writes a session's `summary.json` (provisional or not), it adds `{sessionEnd, firstPublishedAt}` to `meta/lag.json`. `sessionEnd` is the last OpenF1 `CHEQUERED` flag, else the scheduled `date_end`. `firstPublishedAt` is when the build wrote the file; the push and Pages deploy follow about 1–2 min later. Entries are never updated; sessions published before tracking started (summary already present) get no entry.
- **404s.** Every OpenF1 404 mapped to `[]` is logged at WARNING with its URL.
- **OpenF1 live lockout.** A 401 `Live F1 session in progress` stops the build at that point without counting as a failure: files already written are kept and published, `f1data build` exits 75, and it doesn't count as a full build (`lastFullBuildAt` unchanged). Inside the polling loop that poll is logged and skipped and the loop retries at the next interval; a single run ends as "skipped, live session" (job succeeds) and the next run retries.

## Stack and layout

Python 3.12, `uv`, `httpx`, `pydantic`, `numpy`, `pytest`, `ruff`.

```
src/f1data/
  http.py            # shared client: retry, rate limit, cache, redirect_target, token only to api.github.com
  sources/           # openf1.py, jolpica.py, f1db.py, multiviewer.py  (raw fetch only)
  models/            # pydantic output models = the contract; contract.py = OUTPUT_FILES registry + snapshot shape
  build/             # finality.py, ids.py, classify.py, season.py, standings.py, drivers.py, driver_stats.py,
                     # circuits.py, session.py, grid.py, replay.py
  calendar.py        # should_build / should_poll: pure scheduling decisions
  io.py              # validated, deterministic writes
  manifest.py
  cli.py             # `f1data build --year 2026 [--round N] [--session race] [--force] [--offline] [--ttl-minutes M]`
                     # `f1data should-build` / `f1data should-poll` (used by build.yml)
scripts/             # record_fixtures.py (re-record tests/fixtures/http), update_contract_snapshot.py,
                     # verify_sources.py (Phase 0, still uses the GitHub API; not part of the build)
.github/scripts/     # build-and-publish.sh: the build + publish + polling loop run by build.yml
tests/               # unit tests on fixtures in tests/fixtures/ (recorded JSON, no network); contract/snapshot.json
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
  - **Schedule (UTC):** hourly at minute 17 (`17 * * * *`; minute 0 is when GitHub delays or skips scheduled runs most), calendar-aware. After checking out `gh-pages` and `uv sync`, `f1data should-build --year <current UTC year>` prints `true`/`false` (pure function `f1data/calendar.py:should_build`), and every later step runs only on `true`. It is `true` when:
    - `site/v1/<year>/season.json` doesn't exist yet (new season not published);
    - now is inside a round's window: its first session start to 72 h after its last session end (a session with no end counts at its start; cancelled events are not rounds);
    - `manifest.json` `lastFullBuildAt` is missing or more than 24 h old.

    `lastFullBuildAt` is set by a build with no `--round`/`--session` filter and no failures, so a failing build is retried hourly. Every full build changes it, so builds inside a window publish every hour.
  - **Polling:** GitHub starts the hourly schedule only 3–4 times a day (see Findings), so a run started while polling is needed keeps going: build and publish, sleep, repeat. `f1data should-poll` (pure function `calendar.py:should_poll`) prints `true <interval_s>` or `false 0`:
    - every 15 min from a round's first session start to 6 h after its last session end;
    - then every 30 min while the round's race or sprint `summary.json` is missing or provisional, capped at 48 h after the race (so polling stops once Jolpica has the round);
    - otherwise no polling; the rest of the 72 h tail is left to hourly runs.

    Each poll uses `--ttl-minutes 10` so provisional responses are re-fetched. A run polls for up to `poll_budget_minutes` (default 330, i.e. 5.5 h). When the budget runs out and `should-poll` is still true, it dispatches its successor (`gh workflow run build.yml -f poll=true` with `GITHUB_TOKEN`, hence `permissions: actions: write`); the concurrency group queues it. Scheduled runs are only a backup starter. The loop stops without a successor when `should-poll` turns false. A failed poll doesn't stop the loop but fails the job at the end. The loop is `.github/scripts/build-and-publish.sh`, tested with stubs in `tests/test_build_script.py`.
    `workflow_dispatch` always builds. If `should-build` errors or prints anything else, the run fails rather than skipping.
  - **Manual runs:** `workflow_dispatch` with inputs `year`, `round`, `session`, `force`, `poll` (default false; ignored with `round` or `session`; polls only while `should-poll` is true), `poll_budget_minutes` (default 330, max 330).
  - **Settings:** `permissions: contents: write, actions: write` (dispatching the next polling run); concurrency group `build-data` without cancel-in-progress (runs queue); 350 min timeout (GitHub's job limit is 6 h).
  - **Steps:**
    1. Check out `gh-pages` into `site/`.
    2. `uv sync --locked`, then decide whether to build (`should-build`, or `true` for manual runs) and whether to poll (`should-poll`).
    3. Restore `.cache/` with `actions/cache`.
    4. `build-and-publish.sh`: `f1data build` (with `GITHUB_TOKEN` in the environment), then publish, but only if `site/v1/manifest.json` changed: write `site/.nojekyll`, build an orphan commit from `site/` with a temporary index, and force-push it to `gh-pages`. The branch always holds one squashed commit. Repeated while polling; a failed build still publishes what was written and fails the job at the end.
  - All actions are pinned to commit SHAs, with the exact version in a comment.
- `.github/dependabot.yml`: weekly updates for GitHub Actions and Python. Python uses the `uv` ecosystem so `uv.lock` is updated together with `pyproject.toml`.
- Pages must be configured to serve the `gh-pages` branch root. The data URL is `https://shreyansh-tripathi.github.io/f1-data/v1/`.

## Conventions

- Plan before multi-file changes; keep diffs small; one concern per commit.
- Don't add Co-Authored-By trailers to commits (this overrides any tool default).
- Commit and push only when the owner asks. Confirm outward-facing actions (publishing, cancelling runs) first unless asked to proceed.
- Add a fixture-based test for every builder. Tests are offline; new source responses are recorded with `uv run scripts/record_fixtures.py` (add the session keys there) and replayed from `tests/fixtures/http/index.json`.
- Every check before committing: `uv run ruff check . && uv run ruff format --check . && uv run pytest`. Ruff line length is 120.
- Output model changes: follow the Contract change policy, run `uv run python scripts/update_contract_snapshot.py`, commit the snapshot, and tell the owner about any schemaVersion bump (the app pins supported versions).
- Publish by hand with `gh workflow run build.yml -f year=2026` (add `-f force=true` to rebuild complete sessions, `-f poll=true` to start a polling run). Pages caches files for up to 10 min.
- Report results with run ids and timestamps; verify on the live files (`git show origin/gh-pages:v1/...` or the Pages URL), not only in logs.
- When a source behaves differently than documented here, update the Sources table and the Findings log below.

## Status

- [x] Phase 0 — verify sources (see Findings)
- [x] Phase 1 — pipeline + Actions + Pages
- [x] Contract additions for the app: round `shortName`/`winner`, team `shortName`, penalty `reason`, driver-stats `rounds`, title status, career poles/fastest laps, pit-stop filtering
- [x] Phase 4 — session detail: `periods`, `statusText`/`retirementReason`, race-control `kind`/`driverIds`/`text`/`shortText`/`deletion`, qualifying `status`, session `drivers`; contract change policy + snapshot test
- [x] Scheduling: calendar-aware builds, polling runs that chain themselves, OpenF1 live-lockout handling, F1DB via `GITHUB_TOKEN`, races from OpenF1 before Jolpica, `provisional`/`final` split, publish lag (`meta/lag.json`)
- [ ] Phase 5 — replay builder (next, see Current work)
- [ ] Phase 6 — radio + local whisper.cpp transcription

## Current work (as of 2026-11-21 07:00Z)

**State.** Everything is committed and pushed (`main` = `f83c049` plus this docs commit); CI is green; 247 tests pass.
- A polling chain is running for round 21 (Las Vegas): run 44876181267 started 06:34Z on `f83c049`, polls every 15 min, and at about 12:05Z dispatches its successor. One handover has been verified (44874815396 → 44876181267, `gh run list`).
- Live files are on the new versions: session files 2, `race-control.json` 3 (`provisional`/`final` split). The owner was told; the app must support these versions.
- Round 21 published so far: FP1, FP2, FP3, qualifying (lag 55 min). Race: 2026-11-22 04:00–06:00Z.

**Check after the Las Vegas race** (first live test of several paths):
- Polls during the session hit the OpenF1 lockout (401) and are skipped, not failed.
- The race summary appears from OpenF1 first with `provisional: true`, then flips to `false` once Jolpica has round 21, and 30-min polling stops then (or at the 48 h cap, 2026-11-24 06:00Z).
- Every handover dispatches a successor while `should-poll` is true; `meta/lag.json` gets the race entry.
- F1DB was still `v2026.20.0` on 2026-11-21; round 21 retirement reasons and qualifying codes fill in when F1DB publishes it.

**Open decisions (owner):**
- Optional additive classification value `notClassified` for cars running at the end but under 90% (Stroll round 1, Albon round 7, Hülkenberg round 12 sprint); today they are `dnf`.
- MultiViewer returns 404 for `madring` and `sepang` (2026); those circuits use the OpenF1-location outline fallback (no corners). Not raised with the owner yet.

**Phase 5, Race Replay:** pipeline side built (see Data rules: Replay); the app's Replay tab follows once `replay.json` is live.

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
- Qualifying status disagreements (2026): round 4 Q Hadjar and round 19 Q Lawson are `DSQ` in F1DB; OpenF1 has times but no position and no `dsq` flag, and Jolpica lists Lawson 8th (Hadjar 22nd with blank times). Round 4 SQ Alonso and round 17 Q Hadjar are `NC` (OpenF1 `RT` for Hadjar). Round 17 SQ: OpenF1 `dnf` for Hadjar and Gasly, but both classified 9th/10th.
- Race-control wording (2026, 100 sessions): SC/VSC events use exactly `SAFETY CAR DEPLOYED`, `SAFETY CAR IN THIS LAP`, `VSC DEPLOYED`, `VSC ENDING`; race red flags are `RED FLAG - RACE SUSPENDED` with category `Other` (practice/qualifying use `Flag`/`RED`). Code-only car references occur once (Canada qualifying: `CAR LEC/LAW/BEA TIME … DELETED`). All 12 reinstatements match a deletion; two name a lap (`CAR 55 (SAI) LAP 1 WILL BE REINSTATED`), not a time.
- F1DB `reasonRetired` is null for some retirements (round 17 sprint: all 8 DNFs; `NC` rows). Jolpica says `R` where F1DB says `NC` for Stroll (round 1) and Albon (round 7).
- Brazil 2026 race (round 20, wet): no SC, VSC or red flag. Race control has no `SafetyCar`-category messages; only local double yellows (sectors 2, 13, 14, 15), 107 blue flags, "RISK OF RAIN FOR THE F1 RACE IS 80%", and rainfall in 7 of 160 weather samples.
- Stroll, round 1 2026: Jolpica `R` with status `Lapped`, 43 of 58 laps (74%, below the 52 needed). Not classified (F1DB `NC`, OpenF1 `+15 LAPS`), so `classification: dnf` with no position is correct even though `statusText` says Lapped. Albon (round 7, 55/66) and Hülkenberg (round 12 sprint, 7/24) are the same case.

### 2026-11-22 — Race Replay sources and sizes

- Lights out is one instant everywhere: every driver's lap 1 `date_start` = `SESSION STARTED` = `RACE START`. Race control never marks the formation lap (only free text on delayed starts), but location shows it: cars stand still, move about 3–4 min before lights out, then wait on the grid for the last ~30 s.
- Location runs at ~3.8 Hz; a race window is ~3 MB and ~4.9 s per driver. It contains glitches (one Las Vegas sample implies 425 m/s), stretches of (0,0) (Las Vegas: Lawson until 151 s), and feed gaps where every car's position freezes (Britain sprint, 28 s), which look like the whole field stopping.
- A retired car stops dead and OpenF1 keeps sending its position (drifting ~1 dm) until the session ends (Brazil car 18 at 2615 s, 10 s into lap 31).
- Round 6 race: location is mostly missing (1802 samples per car, nothing between 391 s and 3402 s, nothing after 3423 s; flag at 8611 s after a 37-min red flag), so it has no replay. **Recorded as unavailable:** once the session is final, no run asks for its location again (only `--force` would).
- OpenF1 intervals as sparse changes (100 ms) are 22–88 KB gzip per session.
- Local backfill 2026-11-22: 26 replays (21 races, 5 sprints with location), 167–546 KB gzip (mean ~407 KB), built in 43–65 s each, 22 location requests each; full-season build 21.5 min.

### 2026-11-22 — Starting grid sources

- OpenF1 `starting_grid` (by `meeting_key`) is the grid after penalties: it matches Jolpica `grid` in every recorded grid (Brazil, Singapore race and sprint). Rows are keyed to the qualifying session that set them (`sq` for the sprint, `q` for the race); the race-summary fallback used to mix both. Las Vegas had it (22 rows, session 11416) right after the race; when it first appears before a race is still unobserved.
- Race control never announces grid penalties or pit-lane starts: 0 matches for `GRID`, `PIT LANE START`, `START FROM THE PIT`, `BACK OF THE` in 618 messages of R10, R13 and R21 (q and race) plus the 15 recorded sessions.
- F1DB `startingGridPositions` has `gridPenaltyPositions` (places, e.g. Brazil HAM 75), `positionText: "PL"` for pit-lane starts and `qualificationPositionNumber`; it arrives with the release after the race. A penalised driver can still move up (Brazil BOR, 22nd → 19th with a 10-place penalty).

### 2026-11-21 — Las Vegas weekend (round 21) build failures

- OpenF1 locks out unauthenticated clients while a session is live, including past sessions: run 44668258214 (2026-11-20 03:31Z) got 401 `Live F1 session in progress. Global API access (including past sessions) is restricted to authenticated users until the session ends.` on `meetings?year=2026`. That was 29 min before FP2 started (04:00Z), so the lockout starts before the session, not only during it. A key is paid (Stripe link), so the build now treats the 401 as a skip.
- `api.github.com/repos/f1db/f1db/releases/latest` returned 403 `API rate limit exceeded for 3.42.157.122` (run 44856340066, 2026-11-21 02:31Z): the unauthenticated limit is 60 req/h per IP and Actions runners share IPs. Season, drivers/teams and driver-stats failed. The tag now comes from the `github.com` redirect (302 to `/releases/tag/<tag>`), with the last known tag as fallback.
- GitHub ran the hourly `17 * * * *` schedule only 3–4 times a day from 2026-11-12 to 2026-11-21 (start times drifting by 4–31 min, gaps up to 10 h), so publish lag on race weekends was up to 11.5 h (FP1). Polling runs now chain themselves (each dispatches its successor) while a round is in progress or its race is provisional; the schedule is only a backup starter. Verified 2026-11-21: run 44874815396 (20 min budget) polled at 06:17Z and 06:33Z, then dispatched 44876181267 at 06:34:24Z, which published at 06:34:48Z.
- Race and sprint publishing waited on Jolpica ("Jolpica has no classification yet"); they now publish from OpenF1 first. Jolpica had no 2026 round 21 qualifying at 05:57Z, 55 min after the session, while OpenF1 had it.
- `workflow_dispatch` triggered with `GITHUB_TOKEN` does start a new run (unlike most events triggered by that token); it needs `permissions: actions: write`. A queued run waits in the `build-data` concurrency group; GitHub keeps only one pending run per group, so a newer pending run replaces an older one.
