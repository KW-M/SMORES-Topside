"""Disk-space-based pruning policy for `sensor_readings`.

Runs as a periodic background task (started/cancelled by `main.py`,
alongside `sampler.run_forever`). Pure policy over `db.database.Database`
and `psutil`; no DB schema/query logic lives here.
"""

import asyncio
import logging
from pathlib import Path

import psutil

from db.database import Database

logger = logging.getLogger(__name__)

_BYTES_PER_MB = 1024 * 1024


async def prune_if_needed(
    database: Database,
    data_dir: Path,
    min_free_disk_space_mb: int,
    delete_batch_size: int,
) -> int:
    """Check free space on the filesystem containing `data_dir`; if below
    `min_free_disk_space_mb`, repeatedly call
    `database.delete_oldest(delete_batch_size)` followed by
    `PRAGMA incremental_vacuum` until free space recovers or the table is
    empty. One check-and-prune pass; called on each tick of `run_forever`.

    Returns:
        Total number of rows deleted during this pass (0 if free space was
        already sufficient).
    """
    min_free_bytes = min_free_disk_space_mb * _BYTES_PER_MB
    total_deleted = 0
    while psutil.disk_usage(str(data_dir)).free < min_free_bytes:
        deleted = await database.delete_oldest(delete_batch_size)
        if deleted == 0:
            remaining = await database.count_rows()
            logger.warning(
                "Free disk space below %d MB but sensor_readings has %d rows "
                "left to delete; cannot free more space by pruning",
                min_free_disk_space_mb,
                remaining,
            )
            break

        await database.incremental_vacuum(delete_batch_size)
        total_deleted += deleted
        logger.info(
            "Retention: deleted %d rows (%d total this pass) to recover disk space",
            deleted,
            total_deleted,
        )

    return total_deleted


async def run_forever(
    database: Database,
    data_dir: Path,
    min_free_disk_space_mb: int,
    check_interval_seconds: float,
    delete_batch_size: int,
) -> None:
    """Drift-corrected loop calling `prune_if_needed` every
    `check_interval_seconds`. Runs until cancelled; intended to be wrapped
    in an `asyncio.Task` by `main.py` and cancelled on shutdown.
    """
    loop = asyncio.get_running_loop()
    next_run = loop.time()
    while True:
        next_run += check_interval_seconds
        await asyncio.sleep(max(0.0, next_run - loop.time()))
        try:
            deleted = await prune_if_needed(
                database, data_dir, min_free_disk_space_mb, delete_batch_size
            )
            if deleted:
                logger.info("Retention pass complete: %d rows deleted total", deleted)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Retention pass failed")
