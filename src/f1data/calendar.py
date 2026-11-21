"""Whether a scheduled run should build: calendar-aware, so quiet weeks cost one run a day."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta
from typing import Any, NamedTuple

from f1data.build.ids import parse_utc

TAIL = timedelta(hours=72)  # matches cache finality: data can still change until then
FALLBACK = timedelta(hours=24)
POLL_TAIL = timedelta(hours=6)  # sources publish within an hour or two of a session; Jolpica can take longer
POLL_INTERVAL_S = 15 * 60
SETTLE_CAP = timedelta(hours=48)  # after the race: keep polling a provisional race/sprint until then
SETTLE_INTERVAL_S = 30 * 60


def should_build(now: datetime, season: dict[str, Any] | None, last_full_build: datetime | None) -> tuple[bool, str]:
    """Pure decision over the published season.json and manifest.json's lastFullBuildAt.

    True when no season.json exists yet, when `now` is inside a round's window (first session start to
    72 h after its last session end), or when the last full build is more than 24 h old (or unknown).
    Cancelled events are not rounds, so they never open a window.
    """
    if season is None:
        return True, "no season.json yet"
    for rnd in season.get("rounds", []):
        window = _window(rnd)
        if window and window[0] <= now <= window[1] + TAIL:
            return True, f"round {rnd['round']} window ({window[0]:%Y-%m-%d} to {window[1] + TAIL:%Y-%m-%d %H:%MZ})"
    if last_full_build is None:
        return True, "no full build recorded"
    if now - last_full_build > FALLBACK:
        return True, f"last full build {last_full_build:%Y-%m-%dT%H:%MZ} is over 24 h old"
    return False, f"quiet: last full build {last_full_build:%Y-%m-%dT%H:%MZ}"


def _window(rnd: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """First session start, last session end (a session without an end counts at its start)."""
    times = [(parse_utc(s["start"]), parse_utc(s["end"] or s["start"])) for s in rnd.get("sessions", [])]
    if not times:
        times = [(parse_utc(rnd["start"]), parse_utc(rnd["start"]))]
    return min(t[0] for t in times), max(t[1] for t in times)


class PollDecision(NamedTuple):
    poll: bool
    interval_s: int  # seconds between polls; 0 when not polling
    reason: str


def should_poll(now: datetime, season: dict[str, Any] | None, unsettled: Collection[int] = ()) -> PollDecision:
    """Pure decision: should a run keep polling, and how often?

    Every 15 min from a round's first session start to 6 h after its last session end, when new sessions can
    appear at any time. After that, every 30 min while the round is in `unsettled` (its race or sprint summary is
    missing or provisional), capped at 48 h after the race. The rest of the 72 h tail is left to hourly runs.
    The end of a polling run uses the same decision to dispatch its successor.
    """
    for rnd in (season or {}).get("rounds", []):
        window = _window(rnd)
        if not window:
            continue
        if window[0] <= now <= window[1] + POLL_TAIL:
            until = window[1] + POLL_TAIL
            return PollDecision(
                True,
                POLL_INTERVAL_S,
                f"round {rnd['round']} sessions ({window[0]:%Y-%m-%d %H:%MZ} to {until:%Y-%m-%d %H:%MZ})",
            )
        race_end = _race_end(rnd)
        if rnd["round"] in unsettled and race_end and window[1] + POLL_TAIL < now <= race_end + SETTLE_CAP:
            until = race_end + SETTLE_CAP
            return PollDecision(
                True, SETTLE_INTERVAL_S, f"round {rnd['round']} race/sprint provisional (until {until:%Y-%m-%d %H:%MZ})"
            )
    return PollDecision(False, 0, "no round in progress")


def _race_end(rnd: dict[str, Any]) -> datetime | None:
    race = next((s for s in rnd.get("sessions", []) if s["code"] == "race"), None)
    return parse_utc(race["end"] or race["start"]) if race else None
