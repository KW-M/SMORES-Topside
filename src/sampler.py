"""Periodic sampling task: polls every configured sensor and writes one DB
row per sensor on a fixed interval. Sits above both `hardware.manager` and
`db.database` — neither subsystem knows about the other.
"""

from db.database import Database
from hardware.manager import SensorManager
from models.readings import SensorReading


async def sample_once(manager: SensorManager, database: Database) -> list[SensorReading]:
    """Query every sensor via `manager.query_all_sensors()` and insert each
    resulting reading as its own row via `database.insert_reading()`.

    One row is written per sensor per call (not one row per call), per
    ARCHITECTURE.md §6. An unreachable sensor's reading (carrying
    `constants.UNREADABLE_VALUE` fields) is still inserted as a row.

    Returns:
        The readings written, each with `row_id` set to its new DB id.
    """
    raise NotImplementedError


async def run_forever(manager: SensorManager, database: Database, interval_seconds: float) -> None:
    """Drift-corrected loop calling `sample_once` every `interval_seconds`
    (`next_run += interval; sleep(max(0, next_run - now))`). Runs until
    cancelled; intended to be wrapped in an `asyncio.Task` by `main.py` and
    cancelled on shutdown.
    """
    raise NotImplementedError
