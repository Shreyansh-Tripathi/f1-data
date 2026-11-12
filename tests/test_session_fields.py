"""Phase 4 session fields: SC/VSC/red periods, statusText, race-control kind/driverIds, qualifying status."""

import logging
import re
from datetime import UTC, datetime

import pytest

from f1data.build import ids
from f1data.build.session import (
    SessionMeta,
    build_periods,
    build_quali,
    build_qualifying_summary,
    build_race_control,
    build_race_summary,
    f1db_quali_codes,
    f1db_retirement_reasons,
    quali_status,
)
from f1data.sources import f1db, openf1

NOW = datetime(2026, 11, 12, tzinfo=UTC)
BRAZIL_RACE, MEXICO_RACE, KL_RACE = 11412, 11404, 11731
CANADA_Q, R4_SPRINT = 11287, 11275
R4_SQ, R4_Q, R17_SQ, R17_Q, R19_Q = 11271, 11276, 11379, 11384, 11400


def meta(session, rnd):
    return SessionMeta(year=2026, round=rnd, session=session, provisional=False, generated_at=NOW)


@pytest.fixture(scope="module")
def idmap_for(season_data, shared_client):
    rows = {int(r["round"]): r for r in season_data["results"]}

    def idmap(sk, rnd):
        return ids.session_ids(openf1.drivers(shared_client, sk), ids.round_entries(rows[rnd]), season_data["known"])

    return idmap


def spans(periods):
    return [(p.kind, p.start_lap, p.end_lap, p.end_message) for p in periods]


# ---------------------------------------------------------------- periods (3 races)


def test_periods_mexico_vsc_sc_red_flag_and_restart(shared_client):
    periods = build_periods(openf1.race_control(shared_client, MEXICO_RACE))
    assert spans(periods) == [
        ("vsc", 7, 8, "VSC ENDING"),
        ("sc", 44, 45, "RED FLAG - RACE SUSPENDED"),  # the red flag ends the safety car
        ("red", 45, 46, "SESSION STARTED"),  # restart
        ("sc", 59, 62, "SAFETY CAR IN THIS LAP"),
    ]
    red = periods[2]
    assert (red.start_time, red.end_time) == (
        datetime(2026, 11, 1, 21, 6, 2, tzinfo=UTC),
        datetime(2026, 11, 1, 21, 45, 0, 156000, tzinfo=UTC),
    )
    assert red.start_message == "RED FLAG - RACE SUSPENDED"


def test_periods_kuala_lumpur_vsc_upgraded_to_sc(shared_client):
    assert spans(build_periods(openf1.race_control(shared_client, KL_RACE))) == [
        ("sc", 9, 12, "SAFETY CAR IN THIS LAP"),
        ("vsc", 43, 45, "SAFETY CAR DEPLOYED"),  # VSC -> SC transition
        ("sc", 45, 51, "SAFETY CAR IN THIS LAP"),
    ]


def test_periods_brazil_none_and_in_race_summary(season_data, shared_client, idmap_for):
    c = shared_client
    jolpica = next(r for r in season_data["results"] if r["round"] == "20")["Results"]
    rc = openf1.race_control(c, BRAZIL_RACE)
    s = build_race_summary(
        meta("race", 20), jolpica, openf1.session_result(c, BRAZIL_RACE), [], idmap_for(BRAZIL_RACE, 20), None, rc
    )
    assert s.periods == []
    out = s.model_dump(mode="json", by_alias=True)
    assert "periods" in out


def rc(t, message, lap, category="Other", flag=None):
    return {"date": f"2026-01-01T{t}+00:00", "message": message, "lap_number": lap, "category": category, "flag": flag}


def test_period_open_at_the_finish_ends_at_the_chequered_flag():
    periods = build_periods(
        [
            rc("15:00:00", "SAFETY CAR DEPLOYED", 55, "SafetyCar"),
            rc("15:09:00", "CHEQUERED FLAG", 58, "Flag", "CHEQUERED"),
            rc("15:20:00", "VSC DEPLOYED", 58, "SafetyCar"),  # after the finish: ignored
        ]
    )
    assert spans(periods) == [("sc", 55, 58, "CHEQUERED FLAG")]


def test_sc_to_vsc_and_red_flag_without_restart():
    periods = build_periods(
        [
            rc("14:00:00", "SAFETY CAR DEPLOYED", 10, "SafetyCar"),
            rc("14:03:00", "VSC DEPLOYED", 11, "SafetyCar"),
            rc("14:05:00", "VSC ENDING", 12, "SafetyCar"),
            rc("14:30:00", "RED FLAG", 30, "Flag", "RED"),
            rc("15:30:00", "SESSION FINISHED", 30, "SessionStatus"),
        ]
    )
    assert spans(periods) == [
        ("sc", 10, 11, "VSC DEPLOYED"),
        ("vsc", 11, 12, "VSC ENDING"),
        ("red", 30, 30, "SESSION FINISHED"),
    ]


def test_unrecognised_sc_wording_is_logged_not_guessed(caplog):
    with caplog.at_level(logging.WARNING, logger="f1data.build.session"):
        periods = build_periods(
            [
                rc("14:00:00", "SAFETY CAR DEPLOYED", 10, "SafetyCar"),
                rc("14:01:00", "SAFETY CAR LIGHTS ON", 10),  # known, not a period event
                rc("14:02:00", "SAFETY CAR WITHDRAWN AT PIT ENTRY", 11, "SafetyCar"),
                rc("14:03:00", "SAFETY CAR IN THIS LAP", 12, "SafetyCar"),
                rc("14:04:00", "VSC ENDING", 12, "SafetyCar"),  # no open VSC
            ],
            "test",
        )
    assert spans(periods) == [("sc", 10, 12, "SAFETY CAR IN THIS LAP")]
    assert "unrecognised SC/VSC message in test: 'SAFETY CAR WITHDRAWN AT PIT ENTRY'" in caplog.text
    assert "'VSC ENDING' in test with no open vsc period" in caplog.text
    assert "LIGHTS ON" not in caplog.text


# ---------------------------------------------------------------- statusText


def test_status_text_passes_jolpica_status_through(season_data, shared_client, idmap_for):
    c = shared_client
    jolpica = next(r for r in season_data["results"] if r["round"] == "20")["Results"]
    s = build_race_summary(
        meta("race", 20), jolpica, openf1.session_result(c, BRAZIL_RACE), [], idmap_for(BRAZIL_RACE, 20)
    )
    by_id = {r.driver_id: r for r in s.results}
    for row in jolpica:
        assert by_id[row["Driver"]["driverId"]].status_text == row["status"]
    assert {r.status_text for r in s.results if r.classification == "dnf"} == {"Retired"}  # generic, as published
    assert {r.status_text for r in s.results if r.classification == "dns"} == {"Did not start"}


# ---------------------------------------------------------------- race control kind / driverIds


@pytest.fixture(scope="module")
def mexico_rc(shared_client, idmap_for):
    idmap = idmap_for(MEXICO_RACE, 19)
    return build_race_control(meta("race", 19), openf1.race_control(shared_client, MEXICO_RACE), idmap), idmap


def kind_of(rc_file, prefix):
    return next(m for m in rc_file.messages if m.message.startswith(prefix))


def test_race_control_kinds_on_mexico(mexico_rc):
    out, idmap = mexico_rc
    assert kind_of(out, "RED FLAG - RACE SUSPENDED").kind == "flag"
    assert kind_of(out, "CHEQUERED FLAG").kind == "flag"
    assert kind_of(out, "SAFETY CAR DEPLOYED").kind == "safetyCar"
    assert kind_of(out, "VSC DEPLOYED").kind == "safetyCar"
    noted = kind_of(out, "INCIDENT INVOLVING CAR 10 (GAS) NOTED")
    assert (noted.kind, noted.driver_ids) == ("noted", [idmap.driver(10)])
    assert kind_of(out, "FIA STEWARDS: INCIDENT INVOLVING CAR 10 (GAS) REVIEWED NO FURTHER").kind == "noFurtherAction"
    assert kind_of(out, "SESSION STARTED").kind == "other"
    assert all(m.deletion is None for m in out.messages if m.kind != "lapDeleted")
    kinds = {m.kind for m in out.messages}
    assert {"flag", "safetyCar", "noted", "noFurtherAction", "penalty", "investigation"} <= kinds


@pytest.mark.parametrize(
    ("message", "category", "kind", "cars"),
    [
        ("FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 1 (NOR) - TRACK LIMITS (15:01:00)", "Other", "penalty", [1]),
        ("FIA STEWARDS: PENALTY SERVED - 5 SECOND TIME PENALTY FOR CAR 1 (NOR)", "Other", "penalty", [1]),
        ("FIA STEWARDS: REPRIMAND (DRIVING) FOR CAR 81 (PIA) - GENERAL SAFETY (16:00:00)", "Other", "penalty", [81]),
        (
            "FIA STEWARDS: TURN 4 INCIDENT INVOLVING CARS 1 (NOR) AND 81 (PIA) UNDER INVESTIGATION - COLLISION",
            "Other",
            "investigation",
            [1, 81],
        ),
        (
            "FIA STEWARDS: Q1 INCIDENT INVOLVING CARS 1 (NOR), 16 (LEC), 44 (HAM) AND 81 (PIA) WILL BE INVESTIGATED "
            "AFTER THE SESSION - IMPEDING",
            "Other",
            "investigation",
            [1, 16, 44, 81],
        ),
        (
            "FIA STEWARDS: INCIDENT INVOLVING CAR 1 (NOR) NO FURTHER ACTION - SAFETY CAR INFRINGEMENT",
            "Other",
            "noFurtherAction",
            [1],
        ),
        ("INCIDENT INVOLVING CAR 1 (NOR) NOTED - VSC INFRINGEMENT", "Other", "noted", [1]),
        ("CAR 1 (NOR) TIME 1:32.490 DELETED - TRACK LIMITS AT TURN 5 LAP 5 16:37:46", "Other", "lapDeleted", [1]),
        ("CAR 81 (PIA) LAP DELETED - TRACK LIMITS AT TURN 11 LAP 3 21:35:19 (PIT)", "Other", "lapDeleted", [81]),
        ("CAR LAW TIME DELETED - TRACK LIMITS AT TURN 4 (NEXT LAP PIT) (Q1)", "Other", "lapDeleted", [30]),
        ("CAR 1 (NOR) TIME 1:32.490 WILL BE REINSTATED", "Other", "lapReinstated", [1]),
        ("CAR 55 (SAI) LAP 1 WILL BE REINSTATED", "Other", "lapReinstated", [55]),
        ("FIA STEWARDS: WARNING FOR CAR 55 (SAI) - MOVING UNDER BRAKING (14:00:00)", "Other", "warning", [55]),
        ("BLACK AND WHITE FLAG FOR CAR 16 (LEC) - TRACK LIMITS", "Flag", "flag", [16]),
        ("BLACK AND WHITE FLAG FOR CAR 5 - CROSSING THE LINE AT PIT ENTRY (14:00:00)", "Flag", "flag", [5]),
        ("WAVED BLUE FLAG FOR CAR 44 (HAM) TIMED AT 16:11:36", "Flag", "flag", [44]),
        ("FIRST CAR TO TAKE THE FLAG - CAR 1 (NOR)", "Other", "flag", [1]),
        ("SAFETY CAR IN THIS LAP", "SafetyCar", "safetyCar", []),
        ("LAPPED CARS MAY NOW OVERTAKE THE SAFETY CAR: 1, 16", "Other", "safetyCar", []),
        ("CAR 1 (NOR) TIME 1:21.003 DELETED - DOUBLE YELLOW AT TURN 7", "Other", "lapDeleted", [1]),
        ("INCIDENT INVOLVING ALL CARS NOTED - RACE CONTROL TEST", "Other", "noted", []),
    ],
)
def test_race_control_kind_and_named_cars(message, category, kind, cars):
    numbers = {1: "norris", 5: "bortoleto", 16: "leclerc", 30: "lawson", 44: "hamilton", 55: "sainz", 81: "piastri"}
    idmap = ids.IdMap(drivers=numbers, teams={}, codes={"LAW": "lawson", "NOR": "norris"})
    row = {"date": "2026-01-01T15:00:00+00:00", "message": message, "category": category, "flag": None}
    m = build_race_control(meta("race", 1), [row], idmap).messages[0]
    assert m.kind == kind
    assert m.driver_ids == [numbers[n] for n in cars]
    assert m.message == message  # raw text kept


# ---------------------------------------------------------------- qualifying status (rounds 4, 17, 19)


@pytest.fixture(scope="module")
def db(shared_client):
    return f1db.download_json_single(shared_client, f1db.latest_release(shared_client))


@pytest.fixture(scope="module")
def weekends(season_data):
    return {w.round: w for w in season_data["weekends"]}


def statuses(shared_client, idmap_for, db, weekends, season_data, sk, rnd, code):
    results = openf1.session_result(shared_client, sk)
    idmap = idmap_for(sk, rnd)
    codes = f1db_quali_codes(db, weekends[rnd].race_start.date(), sprint=code == "sq")
    jq = next((r["QualifyingResults"] for r in season_data["qualifying"] if r["round"] == str(rnd)), None)
    summary = build_qualifying_summary(meta(code, rnd), results, [], idmap, jq if code == "q" else None, codes)
    quali = build_quali(meta(code, rnd), results, idmap, jq if code == "q" else None, codes)
    a = {e.driver_id: (e.status, e.status_text) for e in summary.results}
    b = {d.driver_id: (d.status, d.status_text) for d in quali.drivers}
    assert a == b  # both files agree
    return {k: v for k, v in a.items() if v != ("classified", None)}, len(a)


@pytest.mark.parametrize(
    ("rnd", "code", "sk", "expected"),
    [
        (4, "q", R4_Q, {"hadjar": ("dsq", "DSQ")}),  # set Q2/Q3 times, then disqualified
        (4, "sq", R4_SQ, {"alonso": ("noTime", "NC")}),  # not classified (time set, then deleted)
        (17, "q", R17_Q, {"hadjar": ("noTime", "NC")}),  # OpenF1 position "RT", no times
        (17, "sq", R17_SQ, {}),  # OpenF1 dnf in SQ3 but classified 9th/10th
        (19, "q", R19_Q, {"lawson": ("dsq", "DSQ")}),  # Jolpica still lists him 8th
    ],
)
def test_qualifying_status(shared_client, idmap_for, db, weekends, season_data, rnd, code, sk, expected):
    odd, n = statuses(shared_client, idmap_for, db, weekends, season_data, sk, rnd, code)
    assert odd == expected
    assert n >= 21


@pytest.mark.parametrize(
    ("row", "f1db_code", "expected"),
    [
        ({"driver_number": 1, "duration": [80.1, 79.5, 79.0]}, "1", ("classified", None)),
        ({"driver_number": 1, "duration": [80.1, None, None]}, "EX", ("dsq", "EX")),
        ({"driver_number": 1, "duration": [None, None, None]}, "DNS", ("dns", "DNS")),
        # No F1DB data yet (e.g. right after the session): OpenF1 flags, then "no time".
        ({"driver_number": 1, "duration": [80.1, None, None], "dsq": True}, None, ("dsq", None)),
        ({"driver_number": 1, "duration": [None, None, None], "dns": True}, None, ("dns", None)),
        ({"driver_number": 1, "duration": [None, None, None], "position": "RT"}, None, ("noTime", "RT")),
        ({"driver_number": 1, "duration": [80.1, None, None], "position": 18}, None, ("classified", None)),
    ],
)
def test_quali_status_rules(row, f1db_code, expected):
    assert quali_status(row, f1db_code) == expected


# ---------------------------------------------------------------- lap deletions / reinstatements / codes


def deletion_row(t, message):
    return {"date": f"2026-01-01T{t}+00:00", "message": message, "category": "Other", "flag": None}


def test_lap_deletion_reasons_and_reinstatement_matching():
    idmap = ids.IdMap(drivers={41: "arvid_lindblad", 55: "sainz"}, teams={})
    out = build_race_control(
        meta("fp1", 1),
        [
            deletion_row("12:43:44", "CAR 41 (LIN) TIME 2:34.639 DELETED - TRACK LIMITS AT TURN 4 LAP 9 12:43:44"),
            deletion_row("12:47:48", "CAR 41 (LIN) LAP DELETED - DOUBLE YELLOW AT TURN 5 LAP 11 12:47:48 (PIT)"),
            deletion_row("13:02:30", "CAR 41 (LIN) TIME 2:12.515 DELETED - TRACK LIMITS AT TURN 1 LAP 15 13:02:30"),
            deletion_row("13:03:05", "CAR 41 (LIN) TIME 2:12.515 DELETED - TRACK LIMITS AT TURN 4 LAP 15 13:03:05"),
            deletion_row("13:04:00", "CAR 55 (SAI) TIME 2:12.515 DELETED - UNSAFE REJOIN"),  # same time, other car
            deletion_row("13:05:00", "CAR 55 (SAI) LAP DELETED - TRACK LIMITS AT TURN 5 LAP 1 15:04:11"),
            deletion_row("13:20:00", "CAR 41 (LIN) TIME 2:12.515 WILL BE REINSTATED"),
            deletion_row("13:21:00", "CAR 55 (SAI) LAP 1 WILL BE REINSTATED"),
        ],
        idmap,
    )
    got = [
        (m.kind, m.deletion and (m.deletion.reason, m.deletion.lap_time_ms, m.deletion.reinstated))
        for m in out.messages
    ]
    assert got == [
        ("lapDeleted", ("trackLimits", 154639, False)),
        ("lapDeleted", ("doubleYellow", None, False)),
        ("lapDeleted", ("trackLimits", 132515, True)),  # both deletions of the same time are reinstated
        ("lapDeleted", ("trackLimits", 132515, True)),
        ("lapDeleted", ("other", 132515, False)),
        ("lapDeleted", ("trackLimits", None, True)),  # matched by lap number
        ("lapReinstated", None),
        ("lapReinstated", None),
    ]


def test_reinstatement_without_deletion_is_logged(caplog):
    idmap = ids.IdMap(drivers={1: "norris"}, teams={})
    with caplog.at_level(logging.WARNING, logger="f1data.build.session"):
        build_race_control(
            meta("q", 1), [deletion_row("13:00:00", "CAR 1 (NOR) TIME 1:30.000 WILL BE REINSTATED")], idmap
        )
    assert "reinstatement with no matching deletion" in caplog.text


def test_real_reinstatement_round_4_sprint(shared_client, idmap_for):
    out = build_race_control(meta("sprint", 4), openf1.race_control(shared_client, R4_SPRINT), idmap_for(R4_SPRINT, 4))
    deleted = next(m for m in out.messages if m.message.startswith("CAR 30 (LAW) TIME 1:37.515 DELETED"))
    assert (deleted.driver_ids, deleted.deletion.lap_time_ms, deleted.deletion.reinstated) == (["lawson"], 97515, True)
    assert (deleted.deletion.lap, deleted.deletion.turn) == (2, 6)  # "... AT TURN 6 LAP 2 12:05:58"
    assert kind_of(out, "CAR 30 (LAW) TIME 1:37.515 WILL BE REINSTATED").kind == "lapReinstated"


def test_code_only_car_resolved_through_session_acronyms(shared_client, idmap_for):
    # Canada 2026 qualifying (round 5): race control named three cars by code only.
    idmap = idmap_for(CANADA_Q, 5)
    out = build_race_control(meta("q", 5), openf1.race_control(shared_client, CANADA_Q), idmap)
    law = kind_of(out, "CAR LAW TIME DELETED - TRACK LIMITS")
    assert (law.kind, law.driver_ids) == ("lapDeleted", ["lawson"])
    assert (law.deletion.reason, law.deletion.lap_time_ms, law.deletion.lap, law.deletion.turn) == (
        "trackLimits",
        None,
        None,  # "(NEXT LAP PIT)": no lap number
        14,
    )
    assert law.text == "Car LAW time deleted - track limits at turn 14 (next lap pit) (Q2)"
    assert kind_of(out, "CAR LEC TIME 1:36.518 DELETED").driver_ids == ["leclerc"]
    assert kind_of(out, "CAR BEA TIME 1:39.643 DELETED").driver_ids == ["bearman"]
    # Words that look like codes ("LAPPED CARS MAY ...", "ALL CARS USE ...") resolve to nothing.
    assert all(not m.driver_ids for m in out.messages if "ALL CARS" in m.message)


# ---------------------------------------------------------------- retirementReason


def test_retirement_reason_from_f1db(season_data, shared_client, idmap_for, db, weekends):
    c = shared_client
    jolpica = next(r for r in season_data["results"] if r["round"] == "20")["Results"]
    reasons = f1db_retirement_reasons(db, weekends[20].race_start.date(), sprint=False)
    s = build_race_summary(
        meta("race", 20),
        jolpica,
        openf1.session_result(c, BRAZIL_RACE),
        [],
        idmap_for(BRAZIL_RACE, 20),
        None,
        [],
        reasons,
    )
    race = next(r for r in db["races"] if r["year"] == 2026 and r["round"] == 20)
    expected = {int(x["driverNumber"]): x["reasonRetired"] for x in race["raceResults"] if x.get("reasonRetired")}
    assert expected  # Brazil 2026 had retirements with reasons
    by_number = {int(row["number"]): row["Driver"]["driverId"] for row in jolpica}
    by_id = {r.driver_id: r for r in s.results}
    assert {n: by_id[by_number[n]].retirement_reason for n in expected} == expected
    retired = [r for r in s.results if r.classification == "dnf"]
    assert all(r.status_text == "Retired" and r.retirement_reason for r in retired)  # statusText kept, as published
    assert all(
        r.retirement_reason is None for r in s.results if r.classification == "finished" and r.status_text == "Finished"
    )


def test_retirement_reason_missing_until_f1db_has_the_race(db):
    from datetime import date

    assert f1db_retirement_reasons(db, date(2026, 11, 22), sprint=False) == {}  # Las Vegas: not run yet
    assert f1db_retirement_reasons(db, date(2026, 11, 8), sprint=True) == {}  # no sprint in Brazil


# ---------------------------------------------------------------- display text


@pytest.mark.parametrize(
    ("message", "text"),
    [
        (
            "CAR 14 (ALO) TIME 1:32.490 DELETED - TRACK LIMITS AT TURN 5 LAP 5 16:37:46",
            "Car 14 (ALO) time 1:32.490 deleted - track limits at turn 5 lap 5 16:37:46",
        ),
        (
            "FIA STEWARDS: TURN 4 INCIDENT INVOLVING CARS 4 (NOR) AND 81 (PIA) UNDER INVESTIGATION - COLLISION",
            "FIA stewards: turn 4 incident involving cars 4 (NOR) and 81 (PIA) under investigation - collision",
        ),
        ("VSC DEPLOYED", "VSC deployed"),
        ("SAFETY CAR IN THIS LAP", "Safety car in this lap"),
        ("DRS ENABLED", "DRS enabled"),
        ("DOUBLE YELLOW IN TRACK SECTOR 13", "Double yellow in track sector 13"),
        ("RISK OF RAIN FOR THE F1 RACE IS 80%", "Risk of rain for the F1 race is 80%"),
        (
            "FIA STEWARDS: SQ1 INCIDENT INVOLVING CAR 1 (NOR) NOTED",
            "FIA stewards: SQ1 incident involving car 1 (NOR) noted",
        ),
        ("CAR 5 (BOR) DSQ - SC INFRINGEMENT", "Car 5 (BOR) DSQ - SC infringement"),
        ("WAVED BLUE FLAG FOR CAR 43 (COL) TIMED AT 16:11:36", "Waved blue flag for car 43 (COL) timed at 16:11:36"),
    ],
)
def test_race_control_text_is_sentence_case(message, text):
    row = {"date": "2026-01-01T15:00:00+00:00", "message": message, "category": "Other", "flag": None}
    m = build_race_control(meta("race", 1), [row], ids.IdMap(drivers={}, teams={})).messages[0]
    assert (m.text, m.message) == (text, message)


def test_every_real_message_has_text(mexico_rc):
    out, _ = mexico_rc
    assert all(m.text and m.text.lower() == m.message.lower() for m in out.messages)  # only case changes


# ---------------------------------------------------------------- session drivers


def test_session_drivers_include_practice_only_rookies(season_data, shared_client, idmap_for):
    from f1data.build.session import build_practice_summary

    c, fp1 = shared_client, 11405  # Brazil FP1
    drivers = openf1.drivers(c, fp1)
    idmap = idmap_for(fp1, 20)
    s = build_practice_summary(meta("fp1", 20), openf1.session_result(c, fp1), [], [], [], idmap, drivers)
    by_id = {d.id: d for d in s.drivers}
    assert len(s.drivers) == len(drivers)
    assert {r.driver_id for r in s.results} <= set(by_id)  # every result row can be named
    mini = by_id["gabriele_mini"]  # FP1 rookie: not in Jolpica, so not in drivers.json
    assert (mini.number, mini.code, mini.first_name, mini.last_name, mini.team_id, mini.colour) == (
        62,
        "MIN",
        "Gabriele",
        "Mini",
        "alpine",
        "#00A1E8",
    )
    assert "gabriele_mini" not in season_data["known"]
    norris = by_id["norris"]
    assert (norris.code, norris.team_id, norris.colour) == ("NOR", "mclaren", "#F47600")


# ---------------------------------------------------------------- periods outside races


def test_qualifying_periods_brazil_red_flags_resumed_and_not(shared_client):
    periods = build_periods(openf1.race_control(shared_client, 11408), race=False)  # Brazil Q
    got = [(p.kind, p.start_time.strftime("%H:%M:%S"), p.end_time.strftime("%H:%M:%S"), p.end_message) for p in periods]
    assert got == [
        ("red", "18:02:23", "19:26:00", "SESSION STARTED"),  # Q1, rain: resumed after 84 min
        ("red", "20:03:18", "20:05:47", "SESSION FINISHED"),  # Q2 WILL NOT BE RESUMED
    ]
    assert all(p.start_lap is None and p.end_lap is None for p in periods)  # no laps outside races


def test_qualifying_periods_continue_past_each_segment_chequered_flag():
    periods = build_periods(
        [
            rc("05:00:00", "SESSION STARTED", None, "SessionStatus"),
            rc("05:26:29", "CHEQUERED FLAG", None, "Flag", "CHEQUERED"),  # end of Q1
            rc("05:34:00", "SESSION STARTED", None, "SessionStatus"),
            rc("06:02:14", "RED FLAG", None, "Flag", "RED"),  # Q2
            rc("06:10:00", "SESSION STARTED", None, "SessionStatus"),
            rc("06:19:47", "CHEQUERED FLAG", None, "Flag", "CHEQUERED"),
        ],
        race=False,
    )
    assert spans(periods) == [("red", None, None, "SESSION STARTED")]
    # The same messages as a race would stop at the first chequered flag.
    assert (
        build_periods(
            [
                rc("05:26:29", "CHEQUERED FLAG", None, "Flag", "CHEQUERED"),
                rc("06:02:14", "RED FLAG", None, "Flag", "RED"),
            ]
        )
        == []
    )


def test_practice_periods_vsc_ended_by_red_flag(shared_client):
    periods = build_periods(openf1.race_control(shared_client, 11229), race=False)  # round 1 FP3
    got = [(p.kind, p.start_time.strftime("%H:%M:%S"), p.end_time.strftime("%H:%M:%S"), p.end_message) for p in periods]
    assert got == [
        ("vsc", "02:00:23", "02:03:42", "RED FLAG"),
        ("red", "02:03:42", "02:11:00", "SESSION STARTED"),
        ("red", "02:37:13", "02:46:00", "SESSION STARTED"),
    ]


def test_qualifying_and_practice_summaries_carry_periods(season_data, shared_client, idmap_for):
    from f1data.build.session import build_practice_summary

    c = shared_client
    q = build_qualifying_summary(
        meta("q", 20),
        openf1.session_result(c, 11408),
        [],
        idmap_for(11408, 20),
        None,
        None,
        [],
        openf1.race_control(c, 11408),
    )
    assert [p.kind for p in q.periods] == ["red", "red"]
    fp1 = build_practice_summary(
        meta("fp1", 20),
        openf1.session_result(c, 11405),
        [],
        [],
        [],
        idmap_for(11405, 20),
        [],
        openf1.race_control(c, 11405),
    )
    assert isinstance(fp1.periods, list)
    assert "periods" in fp1.model_dump(mode="json", by_alias=True)


# ---------------------------------------------------------------- classification vs the 90% rule


def test_stroll_round_1_is_correctly_not_classified(season_data, shared_client, idmap_for):
    # Jolpica: positionText "R", status "Lapped", 43 of 58 laps. 90% of 58 rounds down to 52, so he is not
    # classified (F1DB: NC; OpenF1: "+15 LAPS"): no position, even though he was running at the end.
    from f1data.build.session import classified_distance

    jolpica = next(r for r in season_data["results"] if r["round"] == "1")["Results"]
    row = next(r for r in jolpica if r["Driver"]["driverId"] == "stroll")
    assert (row["positionText"], row["status"], row["laps"]) == ("R", "Lapped", "43")
    assert not classified_distance(43, 58) and classified_distance(52, 58) and not classified_distance(51, 58)
    s = build_race_summary(meta("race", 1), jolpica, [], [], ids.IdMap(drivers={}, teams={}))
    stroll = next(r for r in s.results if r.driver_id == "stroll")
    assert (stroll.position, stroll.classification, stroll.status_text, stroll.laps) == (None, "dnf", "Lapped", 43)


def test_every_2026_classification_follows_the_90_percent_rule(season_data, caplog):
    from f1data.build.session import classified_distance

    for races, key in ((season_data["results"], "Results"), (season_data["sprint"], "SprintResults")):
        for race in races:
            rows = race[key]
            winner = int(rows[0]["laps"])
            for row in rows:
                if row["positionText"] in ("W", "F", "D", "E"):
                    continue
                assert row["positionText"].isdigit() == classified_distance(int(row["laps"]), winner), (
                    race["round"],
                    row["Driver"]["driverId"],
                )


def test_classification_disagreeing_with_90_percent_rule_is_logged(caplog):
    winner = {"Driver": {"driverId": "a"}, "Constructor": {"constructorId": "t"}, "number": "1", "position": "1",
              "positionText": "1", "laps": "50", "points": "25", "status": "Finished"}  # fmt: skip
    odd = {**winner, "Driver": {"driverId": "b"}, "number": "2", "position": "2", "positionText": "R", "laps": "48"}
    with caplog.at_level(logging.WARNING, logger="f1data.build.session"):
        build_race_summary(meta("race", 1), [winner, odd], [], [], ids.IdMap(drivers={}, teams={}))
    assert "classification vs 90% rule for 1/race b: Jolpica 'R', 48 of 50 laps" in caplog.text


# ---------------------------------------------------------------- shortText (driver chips)


@pytest.mark.parametrize(
    ("message", "short"),
    [
        (
            "FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 23 (ALB) - REJOINING UNSAFELY (14:50:05)",
            "FIA stewards: 5 second time penalty - rejoining unsafely (14:50:05)",
        ),
        (
            "INCIDENT INVOLVING CAR 27 (HUL) NOTED - LEAVING THE TRACK AND GAINING AN ADVANTAGE (15:09:09)",
            "Incident noted - leaving the track and gaining an advantage (15:09:09)",
        ),
        (
            "FIA STEWARDS: TURN 4 INCIDENT INVOLVING CARS 1 (NOR) AND 81 (PIA) UNDER INVESTIGATION - COLLISION",
            "FIA stewards: turn 4 incident under investigation - collision",
        ),
        (
            "CAR 14 (ALO) TIME 1:32.490 DELETED - TRACK LIMITS AT TURN 5 LAP 5 16:37:46",
            "Time 1:32.490 deleted - track limits at turn 5 lap 5 16:37:46",
        ),
        ("WAVED BLUE FLAG FOR CAR 43 (COL) TIMED AT 16:11:36", "Waved blue flag timed at 16:11:36"),
        ("FIRST CAR TO TAKE THE FLAG - CAR 1 (NOR)", "First car to take the flag"),
        (
            "FIA STEWARDS: WARNING FOR CAR 55 (SAI) - MOVING UNDER BRAKING",
            "FIA stewards: warning - moving under braking",
        ),
        ("CAR LAW TIME DELETED - TRACK LIMITS AT TURN 14 (Q2)", "Time deleted - track limits at turn 14 (Q2)"),
        ("CAR 55 (SAI) LAP 1 WILL BE REINSTATED", "Lap 1 will be reinstated"),
        # Unresolved cars stay in the text (no chip to show them).
        ("CAR 99 (ZZZ) LAP DELETED - TRACK LIMITS", "Car 99 (ZZZ) lap deleted - track limits"),
        ("INCIDENT INVOLVING CARS 1 (NOR) AND 99 (ZZZ) NOTED", "Incident involving cars 1 (NOR) and 99 (ZZZ) noted"),
        # Not car references.
        ("LAPPED CARS MAY NOW OVERTAKE THE SAFETY CAR: 1, 16", "Lapped cars may now overtake the safety car: 1, 16"),
        ("VSC DEPLOYED", "VSC deployed"),
    ],
)
def test_short_text_drops_car_references_shown_as_chips(message, short):
    numbers = {1: "norris", 14: "alonso", 16: "leclerc", 23: "albon", 27: "hulkenberg", 43: "colapinto",
               55: "sainz", 81: "piastri"}  # fmt: skip
    idmap = ids.IdMap(drivers=numbers, teams={}, codes={"LAW": "lawson"})
    row = {"date": "2026-01-01T15:00:00+00:00", "message": message, "category": "Other", "flag": None}
    m = build_race_control(meta("race", 1), [row], idmap).messages[0]
    assert m.short_text == short
    assert m.message == message  # raw unchanged


def test_text_keeps_only_real_codes_upper_case():
    row = {"date": "2026-01-01T15:00:00+00:00", "message": "LAPPED CARS MAY NOW OVERTAKE THE SAFETY CAR: 1, 16",
           "category": "Other", "flag": None}  # fmt: skip
    m = build_race_control(meta("race", 1), [row], ids.IdMap(drivers={}, teams={}, codes={"NOR": "norris"})).messages[0]
    assert m.text == "Lapped cars may now overtake the safety car: 1, 16"


def test_short_text_on_real_messages(mexico_rc):
    out, _ = mexico_rc
    for m in out.messages:
        assert m.short_text
        if m.driver_ids and "(" in m.message:
            assert not re.search(r"\bcars? \d+ \([A-Z]{3}\)", m.short_text, re.IGNORECASE), m.short_text
