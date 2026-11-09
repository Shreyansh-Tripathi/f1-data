# f1-data

Static Formula 1 data as versioned JSON, rebuilt by GitHub Actions from free public sources and served from GitHub Pages. It feeds the `f1-app` Flutter app, but any client can read it.

**Data URL:** <https://shreyansh-tripathi.github.io/f1-data/v1/>

Start with [`manifest.json`](https://shreyansh-tripathi.github.io/f1-data/v1/manifest.json). It lists every file with its SHA-256 and last-change time, so a client only re-downloads what changed.

> **Unofficial.** This project is not associated in any way with the Formula 1 companies. F1, FORMULA ONE, FORMULA 1, FIA FORMULA ONE WORLD CHAMPIONSHIP, GRAND PRIX and related marks are trade marks of Formula One Licensing B.V. Data comes from community sources (see [NOTICE](NOTICE)) and may contain errors.

## Contract (`/v1/`)

```
manifest.json                         {files: {path: {hash, updatedAt}}}
{year}/season.json                    rounds, sessions (UTC start/end), circuit ids, status, cancelled events
{year}/drivers.json                   number, code, name, nationality, team, colour, headshot
{year}/teams.json                     name, colour, current drivers
{year}/standings/drivers.json         WDC after each round (inactive drivers kept, `active: false`)
{year}/standings/teams.json           WCC after each round
{year}/driver-stats.json              season race stats, separate sprint stats, career totals
circuits/{circuitId}.json             outline, corners, rotation, approximate length
{year}/{round}/{session}/summary.json         shape depends on `type`: practice | qualifying | race
{year}/{round}/{session}/laps.json            per driver, per lap: time, sectors, position, gap
{year}/{round}/{session}/strategy.json        stints (tyre, laps) and pit stops
{year}/{round}/{session}/race-control.json    messages, with parsed penalties
{year}/{round}/{session}/quali.json           Q1/Q2/Q3 times and knockout segment (q, sq)
{year}/{round}/{session}/overtakes.json       race and sprint only
```

Session codes: `fp1 fp2 fp3 sq sprint q race`. Round numbers are the official championship rounds.

Conventions:
- Every file has `schemaVersion` and `generatedAt`. Keys are camelCase.
- Times are integer milliseconds and dates are UTC ISO-8601 (`…Z`).
- Lapped cars have `gapMs: null` and `lapsBehind: N`.
- `classification` is one of `finished | dnf | dns | dsq`.
- Session files carry `provisional: true` until the session ended at least 72 h ago and the official classification is published. Provisional files may still change.
- Circuit coordinates use the timing-data frame (decimetres). Rotate by `rotation` degrees to draw the usual orientation.

Only completed sessions are published; there is no live timing.

## Updates

GitHub Actions rebuilds the data hourly from Friday to Monday and every 6 hours on other days. A run publishes only when `manifest.json` changes. The `gh-pages` branch holds a single squashed commit of the current data.

## Development

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run ruff check . && uv run ruff format .
uv run pytest                      # offline, replays recorded fixtures
uv run f1data build --year 2026    # writes site/v1/
uv run f1data build --year 2026 --round 20 --session race --force
```

`scripts/record_fixtures.py` re-records the test fixtures from the live sources.

## License

Code: [MIT](LICENSE). Data sources and attribution: [NOTICE](NOTICE). F1DB data is CC BY 4.0.
