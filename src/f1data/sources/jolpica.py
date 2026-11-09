"""Jolpica (Ergast-compatible) raw fetchers. Return the Races/Standings lists unchanged."""

from __future__ import annotations

from typing import Any

from f1data.http import HttpClient

BASE = "https://api.jolpi.ca/ergast/f1"
PAGE = 100  # Jolpica's maximum limit

Races = list[dict[str, Any]]


def schedule(client: HttpClient, year: int) -> Races:
    data = client.get_json(f"{BASE}/{year}.json", {"limit": PAGE})
    return data["MRData"]["RaceTable"]["Races"]


def results(client: HttpClient, year: int) -> Races:
    return _season_races(client, f"{BASE}/{year}/results.json", "Results")


def qualifying(client: HttpClient, year: int) -> Races:
    return _season_races(client, f"{BASE}/{year}/qualifying.json", "QualifyingResults")


def sprint(client: HttpClient, year: int) -> Races:
    return _season_races(client, f"{BASE}/{year}/sprint.json", "SprintResults")


def driver_standings(client: HttpClient, year: int, rnd: int, *, final: bool = False) -> list[dict[str, Any]]:
    data = client.get_json(f"{BASE}/{year}/{rnd}/driverStandings.json", {"limit": PAGE}, final=final)
    lists = data["MRData"]["StandingsTable"]["StandingsLists"]
    return lists[0]["DriverStandings"] if lists else []


def constructor_standings(client: HttpClient, year: int, rnd: int, *, final: bool = False) -> list[dict[str, Any]]:
    data = client.get_json(f"{BASE}/{year}/{rnd}/constructorStandings.json", {"limit": PAGE}, final=final)
    lists = data["MRData"]["StandingsTable"]["StandingsLists"]
    return lists[0]["ConstructorStandings"] if lists else []


def _season_races(client: HttpClient, url: str, rows_key: str) -> Races:
    """Fetch every page of a season-wide endpoint and merge races split across pages.

    Jolpica paginates by result row, so one race's rows can span two pages.
    Season-wide data changes every round, so it is never cached as final.
    """
    by_round: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        data = client.get_json(url, {"limit": PAGE, "offset": offset})
        mr = data["MRData"]
        for race in mr["RaceTable"]["Races"]:
            if race["round"] in by_round:
                by_round[race["round"]][rows_key].extend(race.get(rows_key, []))
            else:
                by_round[race["round"]] = {**race, rows_key: list(race.get(rows_key, []))}
        offset += PAGE
        if offset >= int(mr["total"]):
            break
    return sorted(by_round.values(), key=lambda r: int(r["round"]))
