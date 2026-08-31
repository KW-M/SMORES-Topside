"""Integration test for `sampler.py`: `SensorManager` (wired to mocked
sensors) driving real writes into a real (temp-file) SQLite `Database` on
a schedule, per AGENTS.md's "run with a config using a small interval and
confirm the DB contains the expected number of correct rows after a
suitable runtime."
"""

import asyncio

import pytest

from constants import UNREADABLE_VALUE
from db.database import Database
from hardware.manager import SensorManager
from sampler import run_forever, sample_once
from tests.mocks.mock_rdo_blue import MockBlueRDOSensor


async def test_sample_once_writes_one_row_per_sensor(
    sensor_manager: SensorManager,
    database: Database,
    mock_sensors: dict[int, MockBlueRDOSensor],
) -> None:
    readings = await sample_once(sensor_manager, database)

    assert len(readings) == len(mock_sensors)
    assert all(reading.row_id is not None for reading in readings)
    assert await database.count_rows() == len(mock_sensors)


async def test_sample_once_persists_unreachable_sensor_with_unreadable_value(
    sensor_manager: SensorManager,
    database: Database,
    mock_sensors: dict[int, MockBlueRDOSensor],
) -> None:
    mock_sensors[3].set_unreachable(True)

    await sample_once(sensor_manager, database)

    stored = await database.get_readings()
    unreachable_rows = [r for r in stored if r.sensor_address == 3]
    assert len(unreachable_rows) == 1
    assert unreachable_rows[0].temperature_c == UNREADABLE_VALUE
    # Every sensor is still sampled, unreachable or not.
    assert len(stored) == len(mock_sensors)


async def test_run_forever_writes_rows_on_a_drift_corrected_schedule(
    sensor_manager: SensorManager,
    database: Database,
    mock_sensors: dict[int, MockBlueRDOSensor],
) -> None:
    interval_seconds = 0.05
    runtime_seconds = 0.32

    task = asyncio.create_task(run_forever(sensor_manager, database, interval_seconds))
    await asyncio.sleep(runtime_seconds)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    expected_cycles = runtime_seconds / interval_seconds
    row_count = await database.count_rows()

    assert row_count % len(mock_sensors) == 0
    cycles_run = row_count // len(mock_sensors)
    assert expected_cycles - 3 <= cycles_run <= expected_cycles + 2
