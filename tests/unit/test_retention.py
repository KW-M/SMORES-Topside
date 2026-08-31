"""Unit tests for `db.retention`: the disk-space-based pruning policy.
`psutil.disk_usage` is monkeypatched with a scripted sequence of `.free`
values so tests never depend on the real filesystem's free space; the
real `db.database.Database` is used since retention exercises its public
CRUD contract directly (ARCHITECTURE.md §6).
"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

import db.retention as retention
from db.database import Database
from models.readings import SensorReading

_MB = 1024 * 1024


def _reading(timestamp_utc: float) -> SensorReading:
    return SensorReading(
        sensor_address=1,
        serial_converter_id="/dev/mock-bus0",
        timestamp_utc=timestamp_utc,
        temperature_c=20.0,
        do_percent_saturation=95.0,
        do_partial_pressure_torr=150.0,
        do_mg_l=8.0,
        status_code=0,
        status_text="OK",
    )


def _fake_disk_usage(free_bytes_sequence: list[int]) -> Callable[[str], SimpleNamespace]:
    """Stand-in for `psutil.disk_usage` yielding a scripted sequence of
    `.free` values (one per call), holding the last value for any calls
    beyond the scripted sequence."""
    state = {"calls": 0}

    def fake(path: str) -> SimpleNamespace:
        index = min(state["calls"], len(free_bytes_sequence) - 1)
        state["calls"] += 1
        return SimpleNamespace(free=free_bytes_sequence[index])

    return fake


async def test_prune_if_needed_does_nothing_when_space_is_sufficient(
    database: Database, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await database.insert_reading(_reading(1.0))
    monkeypatch.setattr(psutil, "disk_usage", _fake_disk_usage([1000 * _MB]))

    deleted = await retention.prune_if_needed(
        database, data_dir, min_free_disk_space_mb=500, delete_batch_size=50
    )

    assert deleted == 0
    assert await database.count_rows() == 1


async def test_prune_if_needed_deletes_batches_until_space_recovers(
    database: Database, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for ts in range(10):
        await database.insert_reading(_reading(float(ts)))
    monkeypatch.setattr(
        psutil, "disk_usage", _fake_disk_usage([100 * _MB, 100 * _MB, 600 * _MB])
    )

    deleted = await retention.prune_if_needed(
        database, data_dir, min_free_disk_space_mb=500, delete_batch_size=3
    )

    assert deleted == 6
    assert await database.count_rows() == 4


async def test_prune_if_needed_stops_when_table_is_empty(
    database: Database, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(psutil, "disk_usage", _fake_disk_usage([100 * _MB]))

    deleted = await retention.prune_if_needed(
        database, data_dir, min_free_disk_space_mb=500, delete_batch_size=50
    )

    assert deleted == 0


async def test_run_forever_calls_prune_if_needed_periodically(
    database: Database, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    async def fake_prune_if_needed(*args: object, **kwargs: object) -> int:
        nonlocal call_count
        call_count += 1
        return 0

    monkeypatch.setattr(retention, "prune_if_needed", fake_prune_if_needed)

    task = asyncio.create_task(
        retention.run_forever(
            database,
            data_dir,
            min_free_disk_space_mb=500,
            check_interval_seconds=0.05,
            delete_batch_size=50,
        )
    )
    await asyncio.sleep(0.22)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert call_count >= 3


async def test_run_forever_survives_a_failed_pass(
    database: Database, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    async def flaky_prune_if_needed(*args: object, **kwargs: object) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated disk error")
        return 0

    monkeypatch.setattr(retention, "prune_if_needed", flaky_prune_if_needed)

    task = asyncio.create_task(
        retention.run_forever(
            database,
            data_dir,
            min_free_disk_space_mb=500,
            check_interval_seconds=0.05,
            delete_batch_size=50,
        )
    )
    await asyncio.sleep(0.17)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert call_count >= 2
