"""Phase 4 session fields: SC/VSC/red periods, statusText, race-control kind/driverIds, qualifying status."""

import logging
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
    quali_status,
)
from f1data.sources import f1db, openf1

NOW = datetime(2026, 11, 12, tzinfo=UTC)
BRAZIL_RACE, MEXICO_RACE, KL_RACE = 11412, 11404, 11731
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
        ("CAR 1 (NOR) TIME 1:32.490 DELETED - TRACK LIMITS AT TURN 5 LAP 5 16:37:46", "Other", "trackLimits", [1]),
        ("CAR 81 (PIA) LAP DELETED - TRACK LIMITS AT TURN 11 LAP 3 21:35:19 (PIT)", "Other", "trackLimits", [81]),
        ("CAR LAW TIME DELETED - TRACK LIMITS AT TURN 4 (NEXT LAP PIT) (Q1)", "Other", "trackLimits", []),
        ("BLACK AND WHITE FLAG FOR CAR 16 (LEC) - TRACK LIMITS", "Flag", "flag", [16]),
        ("BLACK AND WHITE FLAG FOR CAR 5 - CROSSING THE LINE AT PIT ENTRY (14:00:00)", "Flag", "flag", [5]),
        ("WAVED BLUE FLAG FOR CAR 44 (HAM) TIMED AT 16:11:36", "Flag", "flag", [44]),
        ("FIRST CAR TO TAKE THE FLAG - CAR 1 (NOR)", "Other", "flag", [1]),
        ("SAFETY CAR IN THIS LAP", "SafetyCar", "safetyCar", []),
        ("LAPPED CARS MAY NOW OVERTAKE THE SAFETY CAR: 1, 16", "Other", "safetyCar", []),
        ("CAR 1 (NOR) TIME 1:21.003 DELETED - DOUBLE YELLOW AT TURN 7", "Other", "other", [1]),
        ("INCIDENT INVOLVING ALL CARS NOTED - RACE CONTROL TEST", "Other", "noted", []),
    ],
)
def test_race_control_kind_and_named_cars(message, category, kind, cars):
    numbers = {1: "norris", 5: "bortoleto", 16: "leclerc", 44: "hamilton", 81: "piastri"}
    idmap = ids.IdMap(drivers=numbers, teams={})
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
