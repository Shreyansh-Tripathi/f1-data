"""Championship status maths: pure functions on (id, points) tables."""

import math

from f1data.build.standings import EventPoints, clinched_at_round, max_remaining, title_context, title_status

DRIVERS = EventPoints(race=25, sprint=8)
TEAMS = EventPoints(race=25 + 18, sprint=8 + 7)


def test_clinched_mid_season():
    # 3 races left, no sprints: 75 points available; a 76-point lead is decisive.
    table = [("a", 300), ("b", 224), ("c", 200)]
    assert title_status(table, max_remaining(3, 0, DRIVERS), final=False) == {
        "a": "champion",
        "b": "out",
        "c": "out",
    }
    # Exactly 75 ahead is not enough (strictly greater): b can still tie.
    assert title_status([("a", 300), ("b", 225)], 75, final=False) == {"a": "contender", "b": "contender"}


def test_clinched_at_round_is_the_first_decisive_round():
    remaining = {18: (5, 1), 19: (4, 0), 20: (3, 0)}
    tables = {
        18: [("a", 280), ("b", 200)],  # 80 < 5 * 25 + 8 = 133
        19: [("a", 310), ("b", 205)],  # 105 > 100: decided
        20: [("a", 320), ("b", 230)],
    }
    assert clinched_at_round(tables, remaining, DRIVERS, final_round=23) == ("a", 19)
    assert clinched_at_round({18: tables[18]}, remaining, DRIVERS, final_round=23) is None


def test_sprint_keeps_the_title_open():
    # 3 races = 75; the lead of 80 would clinch it, but one sprint adds 8 (83 available).
    table = [("a", 300), ("b", 220), ("c", 217), ("d", 216)]
    assert title_status(table, max_remaining(3, 0, DRIVERS), final=False)["a"] == "champion"
    status = title_status(table, max_remaining(3, 1, DRIVERS), final=False)
    # c: 217 + 83 = 300 >= 300 (contender); d: 216 + 83 = 299 (out)
    assert status == {"a": "contender", "b": "contender", "c": "contender", "d": "out"}


def test_unknown_sprint_value_never_clinches():
    assert max_remaining(1, 1, EventPoints(race=25, sprint=None)) == math.inf
    assert title_status([("a", 500), ("b", 0)], math.inf, final=False) == {"a": "contender", "b": "contender"}


def test_final_round_leader_is_champion_even_on_a_tie():
    # Level on points after the last round: the order (Jolpica countback) decides.
    table = [("a", 400), ("b", 400), ("c", 350)]
    assert title_status(table, max_remaining(0, 0, DRIVERS), final=True) == {
        "a": "champion",
        "b": "out",
        "c": "out",
    }


def test_constructors_use_p1_plus_p2():
    # 2 races + 1 sprint left: 2 * 43 + 15 = 101 for a team (a driver could only take 58).
    assert max_remaining(2, 1, TEAMS) == 101
    table = [("mercedes", 600), ("ferrari", 510)]
    assert title_status(table, max_remaining(2, 1, DRIVERS), final=False)["mercedes"] == "champion"
    assert title_status(table, max_remaining(2, 1, TEAMS), final=False) == {
        "mercedes": "contender",
        "ferrari": "contender",
    }
    assert title_status([("mercedes", 602), ("ferrari", 500)], 101, final=False)["mercedes"] == "champion"


def test_title_context_from_2026(season_data):
    ctx = title_context(season_data["schedule"], season_data["results"], season_data["sprint"])
    assert ctx.drivers == EventPoints(race=25, sprint=8)  # observed P1 points
    assert ctx.teams == EventPoints(race=43, sprint=15)  # observed P1 + P2
    assert ctx.final_round == 23
    assert ctx.remaining[20] == (3, 0)  # Las Vegas, Qatar, Abu Dhabi; no sprints left
    assert ctx.remaining[23] == (0, 0)
    assert sum(1 for r in season_data["schedule"] if "Sprint" in r) == 6
    assert ctx.remaining[1][1] == 6 - ("Sprint" in season_data["schedule"][0])
