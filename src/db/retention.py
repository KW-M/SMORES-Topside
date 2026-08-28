"""Disk-space-based pruning policy for `sensor_readings`.

Runs as a periodic background task (started/cancelled by `main.py`,
alongside `sampler.run_forever`). Pure policy over `db.database.Database`
and `psutil`; no DB schema/query logic lives here.
"""

from pathlib import Path

from db.database import Database


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
    raise NotImplementedError


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
    raise NotImplementedError
