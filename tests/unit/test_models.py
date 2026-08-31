"""Unit tests for `models.readings`: `SensorReading`/`ScanResult` are the
single source of truth for the hardware/db/api "reading" shape, so their
defaults, validation, and JSON round-trip need to be pinned down precisely.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from constants import UNREADABLE_VALUE
from models.readings import ScanResult, SensorReading


def _reading_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        sensor_address=1,
        serial_converter_id="/dev/mock-bus0",
        timestamp_utc=1_700_000_000.0,
        temperature_c=20.5,
        do_percent_saturation=98.2,
        do_partial_pressure_torr=150.1,
        do_mg_l=8.1,
        status_code=0,
        status_text="OK",
    )
    base.update(overrides)
    return base


def test_sensor_reading_row_id_defaults_to_none() -> None:
    reading = SensorReading(**_reading_kwargs())
    assert reading.row_id is None


def test_sensor_reading_round_trips_through_json() -> None:
    reading = SensorReading(**_reading_kwargs(row_id=42))
    restored = SensorReading.model_validate_json(reading.model_dump_json())
    assert restored == reading


def test_sensor_reading_round_trips_through_dict() -> None:
    reading = SensorReading(**_reading_kwargs())
    restored = SensorReading.model_validate(reading.model_dump())
    assert restored == reading


@pytest.mark.parametrize(
    "missing_field",
    [
        "sensor_address",
        "serial_converter_id",
        "timestamp_utc",
        "temperature_c",
        "do_percent_saturation",
        "do_partial_pressure_torr",
        "do_mg_l",
        "status_code",
        "status_text",
    ],
)
def test_sensor_reading_requires_every_field_except_row_id(missing_field: str) -> None:
    kwargs = _reading_kwargs()
    del kwargs[missing_field]
    with pytest.raises(ValidationError):
        SensorReading(**kwargs)


def test_sensor_reading_accepts_unreadable_sentinel_for_numeric_fields() -> None:
    reading = SensorReading(
        **_reading_kwargs(
            temperature_c=UNREADABLE_VALUE,
            do_percent_saturation=UNREADABLE_VALUE,
            do_partial_pressure_torr=UNREADABLE_VALUE,
            do_mg_l=UNREADABLE_VALUE,
            status_code=-1,
            status_text="Sensor timeout",
        )
    )
    assert reading.temperature_c == UNREADABLE_VALUE
    assert reading.do_mg_l == UNREADABLE_VALUE


def test_scan_result_round_trips_through_json() -> None:
    result = ScanResult(
        converter_id="/dev/mock-bus0", sensor_addresses=[1, 2, 3], scanned_at=1_700_000_000.0
    )
    restored = ScanResult.model_validate_json(result.model_dump_json())
    assert restored == result


def test_scan_result_requires_sensor_addresses() -> None:
    with pytest.raises(ValidationError):
        ScanResult(converter_id="/dev/mock-bus0", scanned_at=1_700_000_000.0)  # type: ignore[call-arg]


def test_scan_result_allows_empty_address_list() -> None:
    result = ScanResult(converter_id="/dev/mock-bus0", sensor_addresses=[], scanned_at=0.0)
    assert result.sensor_addresses == []
