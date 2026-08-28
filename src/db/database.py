"""aiosqlite CRUD for the `sensor_readings` table. Pure storage only — no
polling/sampling or retention policy logic lives here (see `sampler.py` and
`db/retention.py`). Row layout: ARCHITECTURE.md §6.
"""

from pathlib import Path

from models.readings import SensorReading


class Database:
    """One SQLite connection (WAL mode, incremental auto-vacuum) over
    `<data_dir>/smores.db`."""

    def __init__(self, db_path: Path) -> None:
        """Construct the wrapper. Does not open the connection yet — call
        `init_schema()` before any other method."""
        raise NotImplementedError

    async def init_schema(self) -> None:
        """Open the connection, set `PRAGMA journal_mode=WAL` and
        `PRAGMA auto_vacuum=INCREMENTAL`, and create `sensor_readings`
        (and its timestamp index) if not already present."""
        raise NotImplementedError

    async def insert_reading(self, reading: SensorReading) -> int:
        """Insert one row (`reading.row_id` is ignored on input).

        Returns:
            The new row's autoincrement `id`.
        """
        raise NotImplementedError

    async def get_readings(
        self, start: float | None = None, end: float | None = None
    ) -> list[SensorReading]:
        """Return rows with `start <= timestamp_utc <= end`, ordered by
        `timestamp_utc` ascending. Either bound may be omitted for an
        open-ended range; omitting both returns every row."""
        raise NotImplementedError

    async def delete_before(self, cutoff: float) -> int:
        """Delete rows with `timestamp_utc < cutoff`.

        Returns:
            Number of rows deleted.
        """
        raise NotImplementedError

    async def delete_oldest(self, batch_size: int) -> int:
        """Delete the `batch_size` rows with the smallest `timestamp_utc`,
        used by `db.retention`'s disk-space-based pruning.

        Returns:
            Number of rows actually deleted (may be less than `batch_size`
            if fewer rows exist).
        """
        raise NotImplementedError

    async def count_rows(self) -> int:
        """Return the current total row count in `sensor_readings`."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Close the underlying aiosqlite connection."""
        raise NotImplementedError
