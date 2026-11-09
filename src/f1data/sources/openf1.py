"""OpenF1 raw fetchers. Return source JSON unchanged; shaping happens in build/."""

from __future__ import annotations

from typing import Any

from f1data.http import HttpClient

BASE = "https://api.openf1.org/v1"

Rows = list[dict[str, Any]]


def _get(client: HttpClient, endpoint: str, final: bool, **params: Any) -> Rows:
    return client.get_json(f"{BASE}/{endpoint}", params, final=final)


def meetings(client: HttpClient, year: int, *, final: bool = False) -> Rows:
    return _get(client, "meetings", final, year=year)


def sessions(client: HttpClient, year: int, *, final: bool = False) -> Rows:
    return _get(client, "sessions", final, year=year)


def drivers(client: HttpClient, session_key: int, *, final: bool = False) -> Rows:
    return _get(client, "drivers", final, session_key=session_key)


def session_result(client: HttpClient, session_key: int, *, final: bool = False) -> Rows:
    return _get(client, "session_result", final, session_key=session_key)


def starting_grid(client: HttpClient, meeting_key: int, *, final: bool = False) -> Rows:
    # Keyed to the qualifying session, so query by meeting.
    return _get(client, "starting_grid", final, meeting_key=meeting_key)


def laps(client: HttpClient, session_key: int, *, final: bool = False) -> Rows:
    return _get(client, "laps", final, session_key=session_key)


def stints(client: HttpClient, session_key: int, *, final: bool = False) -> Rows:
    return _get(client, "stints", final, session_key=session_key)


def pit(client: HttpClient, session_key: int, *, final: bool = False) -> Rows:
    return _get(client, "pit", final, session_key=session_key)


def intervals(client: HttpClient, session_key: int, *, final: bool = False) -> Rows:
    return _get(client, "intervals", final, session_key=session_key)


def position(client: HttpClient, session_key: int, *, final: bool = False) -> Rows:
    return _get(client, "position", final, session_key=session_key)


def race_control(client: HttpClient, session_key: int, *, final: bool = False) -> Rows:
    return _get(client, "race_control", final, session_key=session_key)


def weather(client: HttpClient, session_key: int, *, final: bool = False) -> Rows:
    return _get(client, "weather", final, session_key=session_key)


def overtakes(client: HttpClient, session_key: int, *, final: bool = False) -> Rows:
    return _get(client, "overtakes", final, session_key=session_key)


def location(
    client: HttpClient, session_key: int, driver_number: int, start: str, end: str, *, final: bool = False
) -> Rows:
    """Location samples for one driver between two ISO timestamps (circuit outline fallback)."""
    params = {"session_key": session_key, "driver_number": driver_number, "date>": start, "date<": end}
    return client.get_json(f"{BASE}/location", params, final=final)
