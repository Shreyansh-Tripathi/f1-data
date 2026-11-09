from datetime import UTC, datetime, timedelta

from f1data.build.finality import is_final

END = datetime(2026, 11, 8, 19, 0, tzinfo=UTC)


def test_not_final_before_72h():
    assert not is_final(END, True, END + timedelta(hours=71))


def test_final_after_72h_with_jolpica_round():
    assert is_final(END, True, END + timedelta(hours=73))


def test_not_final_without_jolpica_round():
    assert not is_final(END, False, END + timedelta(days=30))
