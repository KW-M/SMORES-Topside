"""Periodic sampling task: polls every configured sensor and writes one DB
row per sensor on a fixed interval. Sits above both `hardware.manager` and
`db.database` — neither subsystem knows about the other.
"""

import asyncio
import logging

from constants import BusScanError
from db.database import Database
from hardware.manager import SensorManager
from models.readings import SensorReading

logger = logging.getLogger(__name__)


async def sample_once(manager: SensorManager, database: Database) -> list[SensorReading]:
    """Query every sensor via `manager.query_all_sensors()` and insert each
    resulting reading as its own row via `database.insert_reading()`.

    One row is written per sensor per call (not one row per call), per
    ARCHITECTURE.md §6. An unreachable sensor's reading (carrying
    `constants.UNREADABLE_VALUE` fields) is still inserted as a row.

    Returns:
        The readings written, each with `row_id` set to its new DB id.
    """
    readings = await manager.query_all_sensors()

    # Sequential inserts: `Database` owns a single aiosqlite connection, so
    # gathering these would only interleave statements on one cursor.
    saved: list[SensorReading] = []
    for reading in readings:
        row_id = await database.insert_reading(reading)
        saved.append(reading.model_copy(update={"row_id": row_id}))

    unreadable = sum(1 for reading in saved if reading.status_code != 0)
    logger.debug(
        "Sampled %d sensor(s) into rows %s (%d not OK)",
        len(saved),
        [reading.row_id for reading in saved],
        unreadable,
    )
    return saved


async def run_forever(manager: SensorManager, database: Database, interval_seconds: float) -> None:
    """Drift-corrected loop calling `sample_once` every `interval_seconds`
    (`next_run += interval; sleep(max(0, next_run - now))`). Runs until
    cancelled; intended to be wrapped in an `asyncio.Task` by `main.py` and
    cancelled on shutdown.
    """
    loop = asyncio.get_running_loop()
    next_run = loop.time()
    logger.info("Sampling every %.3fs", interval_seconds)

    while True:
        next_run += interval_seconds

        # A poll that overruns its interval (many sensors, all timing out)
        # would otherwise leave `next_run` in the past and fire a burst of
        # back-to-back catch-up samples. Skip the missed ticks instead: the
        # point of the schedule is one sample per interval, not a fixed
        # total count.
        now = loop.time()
        if next_run < now:
            missed = int((now - next_run) // interval_seconds) + 1
            next_run += missed * interval_seconds
            logger.warning(
                "Sampling is running behind its %.3fs interval; skipping %d tick(s)",
                interval_seconds,
                missed,
            )

        await asyncio.sleep(max(0.0, next_run - loop.time()))

        try:
            await sample_once(manager, database)
        except asyncio.CancelledError:
            raise
        except BusScanError as exc:
            # Expected while the startup (or an on-demand) bus scan is still
            # building the sensor mapping — `main.py` starts this task before
            # that scan finishes so the API is up in the meantime.
            logger.info("Skipping this sample: %s", exc)
        except Exception:
            logger.exception("Sampling pass failed")
