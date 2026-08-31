"""Unit tests for `db.database.Database`: pure aiosqlite CRUD against the
`sensor_readings` table (ARCHITECTURE.md §6). Uses a real (temp-file)
SQLite DB — no mocking, since this module has no external dependencies
worth faking.
"""

from pathlib import Path

import pytest

from db.database import Database
from models.readings import SensorReading


def _reading(
    *, sensor_address: int = 1, converter_id: str = "/dev/mock-bus0", timestamp_utc: float = 1000.0
) -> SensorReading:
    return SensorReading(
        sensor_address=sensor_address,
        serial_converter_id=converter_id,
        timestamp_utc=timestamp_utc,
        temperature_c=20.0,
        do_percent_saturation=95.0,
        do_partial_pressure_torr=150.0,
        do_mg_l=8.0,
        status_code=0,
        status_text="OK",
    )


async def test_using_database_before_init_schema_raises(tmp_path: Path) -> None:
    db = Database(tmp_path / "unopened.db")
    with pytest.raises(RuntimeError):
        await db.count_rows()


async def test_init_schema_is_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "smores.db")
    await db.init_schema()
    await db.init_schema()
    assert await db.count_rows() == 0
    await db.aclose()


async def test_insert_reading_returns_increasing_ids(database: Database) -> None:
    first_id = await database.insert_reading(_reading(timestamp_utc=1.0))
    second_id = await database.insert_reading(_reading(timestamp_utc=2.0))
    assert second_id > first_id


async def test_insert_reading_round_trips_all_fields(database: Database) -> None:
    reading = _reading(sensor_address=7, converter_id="/dev/mock-bus1", timestamp_utc=123.5)
    row_id = await database.insert_reading(reading)

    [stored] = await database.get_readings()

    assert stored.row_id == row_id
    assert stored.sensor_address == reading.sensor_address
    assert stored.serial_converter_id == reading.serial_converter_id
    assert stored.timestamp_utc == reading.timestamp_utc
    assert stored.temperature_c == reading.temperature_c
    assert stored.do_percent_saturation == reading.do_percent_saturation
    assert stored.do_partial_pressure_torr == reading.do_partial_pressure_torr
    assert stored.do_mg_l == reading.do_mg_l
    assert stored.status_code == reading.status_code
    assert stored.status_text == reading.status_text


async def test_count_rows(database: Database) -> None:
    assert await database.count_rows() == 0
    await database.insert_reading(_reading())
    await database.insert_reading(_reading())
    assert await database.count_rows() == 2


async def test_get_readings_returns_all_rows_ordered_by_timestamp(database: Database) -> None:
    await database.insert_reading(_reading(timestamp_utc=30.0))
    await database.insert_reading(_reading(timestamp_utc=10.0))
    await database.insert_reading(_reading(timestamp_utc=20.0))

    readings = await database.get_readings()

    assert [r.timestamp_utc for r in readings] == [10.0, 20.0, 30.0]


async def test_get_readings_range_is_inclusive_on_both_ends(database: Database) -> None:
    for ts in (10.0, 20.0, 30.0, 40.0):
        await database.insert_reading(_reading(timestamp_utc=ts))

    readings = await database.get_readings(start=20.0, end=30.0)

    assert [r.timestamp_utc for r in readings] == [20.0, 30.0]


async def test_get_readings_open_ended_start(database: Database) -> None:
    for ts in (10.0, 20.0, 30.0):
        await database.insert_reading(_reading(timestamp_utc=ts))

    readings = await database.get_readings(end=20.0)

    assert [r.timestamp_utc for r in readings] == [10.0, 20.0]


async def test_get_readings_open_ended_end(database: Database) -> None:
    for ts in (10.0, 20.0, 30.0):
        await database.insert_reading(_reading(timestamp_utc=ts))

    readings = await database.get_readings(start=20.0)

    assert [r.timestamp_utc for r in readings] == [20.0, 30.0]


async def test_get_readings_empty_table_returns_empty_list(database: Database) -> None:
    assert await database.get_readings() == []


async def test_delete_before_deletes_only_strictly_older_rows(database: Database) -> None:
    for ts in (10.0, 20.0, 30.0):
        await database.insert_reading(_reading(timestamp_utc=ts))

    deleted = await database.delete_before(20.0)

    assert deleted == 1
    remaining = await database.get_readings()
    assert [r.timestamp_utc for r in remaining] == [20.0, 30.0]


async def test_delete_before_returns_zero_when_nothing_matches(database: Database) -> None:
    await database.insert_reading(_reading(timestamp_utc=100.0))
    assert await database.delete_before(0.0) == 0


async def test_delete_oldest_deletes_smallest_timestamps_first(database: Database) -> None:
    for ts in (10.0, 20.0, 30.0, 40.0):
        await database.insert_reading(_reading(timestamp_utc=ts))

    deleted = await database.delete_oldest(2)

    assert deleted == 2
    remaining = await database.get_readings()
    assert [r.timestamp_utc for r in remaining] == [30.0, 40.0]


async def test_delete_oldest_caps_at_available_row_count(database: Database) -> None:
    await database.insert_reading(_reading(timestamp_utc=1.0))
    deleted = await database.delete_oldest(50)
    assert deleted == 1
    assert await database.count_rows() == 0


async def test_incremental_vacuum_does_not_raise(database: Database) -> None:
    await database.insert_reading(_reading())
    await database.delete_oldest(1)
    await database.incremental_vacuum(50)


async def test_aclose_is_safe_to_call_twice(tmp_path: Path) -> None:
    db = Database(tmp_path / "smores.db")
    await db.init_schema()
    await db.aclose()
    await db.aclose()
