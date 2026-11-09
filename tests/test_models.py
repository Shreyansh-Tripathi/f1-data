from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from f1data.models.base import OutputFile
from f1data.models.session import DriverLaps, Summary

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def test_output_file_has_schema_version_and_generated_at_camel_case():
    out = OutputFile(generated_at=NOW).model_dump(mode="json", by_alias=True)
    assert out == {"schemaVersion": 1, "generatedAt": "2026-11-10T12:00:00Z"}


def test_dates_serialize_as_utc_z():
    local = datetime(2026, 11, 8, 14, 0, 0, 250_000, tzinfo=timezone(timedelta(hours=-3)))
    out = OutputFile(generated_at=local).model_dump(mode="json", by_alias=True)
    assert out["generatedAt"] == "2026-11-08T17:00:00.250Z"


def test_extra_keys_rejected():
    with pytest.raises(ValidationError):
        OutputFile(generated_at=NOW, sessionKey=1)


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        OutputFile(generated_at=datetime(2026, 1, 1))


def test_lap_columns_must_align():
    with pytest.raises(ValidationError):
        DriverLaps(lap=[1, 2], ms=[1], s1=[], s2=[], s3=[], pit_out=[], pos=[], gap_ms=[], laps_behind=[])


def test_summary_discriminates_on_type():
    base = {"generatedAt": "2026-11-10T00:00:00Z", "year": 2026, "round": 20, "provisional": False, "weather": None}
    practice = {**base, "type": "practice", "session": "fp1", "results": []}
    assert type(TypeAdapter(Summary).validate_python(practice)).__name__ == "PracticeSummary"
    with pytest.raises(ValidationError):
        TypeAdapter(Summary).validate_python({**practice, "type": "race"})
