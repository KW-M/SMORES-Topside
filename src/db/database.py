"""aiosqlite CRUD for the `sensor_readings` table. Pure storage only — no
polling/sampling or retention policy logic lives here (see `sampler.py` and
`db/retention.py`). Row layout: ARCHITECTURE.md §6.
"""

import logging
from pathlib import Path

import aiosqlite

from models.readings import SensorReading

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sensor_readings (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc               REAL    NOT NULL,
    sensor_address               INTEGER NOT NULL,
    converter_id                 TEXT    NOT NULL,
    temperature_c                REAL    NOT NULL,
    do_percent_saturation        REAL    NOT NULL,
    do_partial_pressure_torr     REAL    NOT NULL,
    do_mg_l                       REAL    NOT NULL,
    status_code                  INTEGER NOT NULL,
    status_text                  TEXT    NOT NULL
)
"""

_CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_sensor_readings_timestamp "
    "ON sensor_readings(timestamp_utc)"
)

_INSERT_SQL = """
INSERT INTO sensor_readings (
    timestamp_utc, sensor_address, converter_id, temperature_c,
    do_percent_saturation, do_partial_pressure_torr, do_mg_l,
    status_code, status_text
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_COLUMNS = (
    "id, timestamp_utc, sensor_address, converter_id, temperature_c, "
    "do_percent_saturation, do_partial_pressure_torr, do_mg_l, "
    "status_code, status_text"
)


def _row_to_reading(row: aiosqlite.Row) -> SensorReading:
    return SensorReading(
        row_id=row["id"],
        sensor_address=row["sensor_address"],
        serial_converter_id=row["converter_id"],
        timestamp_utc=row["timestamp_utc"],
        temperature_c=row["temperature_c"],
        do_percent_saturation=row["do_percent_saturation"],
        do_partial_pressure_torr=row["do_partial_pressure_torr"],
        do_mg_l=row["do_mg_l"],
        status_code=row["status_code"],
        status_text=row["status_text"],
    )


class Database:
    """One SQLite connection (WAL mode, incremental auto-vacuum) over
    `<data_dir>/smores.db`."""

    def __init__(self, db_path: Path) -> None:
        """Construct the wrapper. Does not open the connection yet — call
        `init_schema()` before any other method."""
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    @property
    def _connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.init_schema() must be called before use")
        return self._conn

    async def init_schema(self) -> None:
        """Open the connection, set `PRAGMA journal_mode=WAL` and
        `PRAGMA auto_vacuum=INCREMENTAL`, and create `sensor_readings`
        (and its timestamp index) if not already present."""
        logger.info("Opening database at %s", self._db_path)
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        await conn.execute(_CREATE_TABLE_SQL)
        await conn.execute(_CREATE_INDEX_SQL)
        await conn.commit()
        self._conn = conn
        logger.info("Database schema ready at %s", self._db_path)

    async def insert_reading(self, reading: SensorReading) -> int:
        """Insert one row (`reading.row_id` is ignored on input).

        Returns:
            The new row's autoincrement `id`.
        """
        cursor = await self._connection.execute(
            _INSERT_SQL,
            (
                reading.timestamp_utc,
                reading.sensor_address,
                reading.serial_converter_id,
                reading.temperature_c,
                reading.do_percent_saturation,
                reading.do_partial_pressure_torr,
                reading.do_mg_l,
                reading.status_code,
                reading.status_text,
            ),
        )
        await self._connection.commit()
        row_id = cursor.lastrowid
        await cursor.close()
        if row_id is None:
            raise RuntimeError("INSERT into sensor_readings did not return a row id")
        return row_id

    async def get_readings(
        self, start: float | None = None, end: float | None = None
    ) -> list[SensorReading]:
        """Return rows with `start <= timestamp_utc <= end`, ordered by
        `timestamp_utc` ascending. Either bound may be omitted for an
        open-ended range; omitting both returns every row."""
        clauses = []
        params: list[float] = []
        if start is not None:
            clauses.append("timestamp_utc >= ?")
            params.append(start)
        if end is not None:
            clauses.append("timestamp_utc <= ?")
            params.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT {_SELECT_COLUMNS} FROM sensor_readings {where} ORDER BY timestamp_utc ASC"
        async with self._connection.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_reading(row) for row in rows]

    async def delete_before(self, cutoff: float) -> int:
        """Delete rows with `timestamp_utc < cutoff`.

        Returns:
            Number of rows deleted.
        """
        cursor = await self._connection.execute(
            "DELETE FROM sensor_readings WHERE timestamp_utc < ?", (cutoff,)
        )
        await self._connection.commit()
        deleted = cursor.rowcount
        await cursor.close()
        return max(deleted, 0)

    async def delete_oldest(self, batch_size: int) -> int:
        """Delete the `batch_size` rows with the smallest `timestamp_utc`,
        used by `db.retention`'s disk-space-based pruning.

        Returns:
            Number of rows actually deleted (may be less than `batch_size`
            if fewer rows exist).
        """
        cursor = await self._connection.execute(
            """
            DELETE FROM sensor_readings WHERE id IN (
                SELECT id FROM sensor_readings ORDER BY timestamp_utc ASC LIMIT ?
            )
            """,
            (batch_size,),
        )
        await self._connection.commit()
        deleted = cursor.rowcount
        await cursor.close()
        return max(deleted, 0)

    async def count_rows(self) -> int:
        """Return the current total row count in `sensor_readings`."""
        async with self._connection.execute("SELECT COUNT(*) FROM sensor_readings") as cursor:
            row = await cursor.fetchone()
        assert row is not None
        return int(row[0])

    async def incremental_vacuum(self, pages: int) -> None:
        """Reclaim up to `pages` freed pages back to the filesystem.

        Only effective because `init_schema()` sets `auto_vacuum=INCREMENTAL`
        — a plain `DELETE` alone leaves the file size unchanged. Called by
        `db.retention` after each `delete_oldest` batch, per ARCHITECTURE.md §6.
        `PRAGMA` statements don't support bound parameters, so `pages` (always
        config-derived, never user input) is formatted directly into the SQL.
        """
        await self._connection.execute(f"PRAGMA incremental_vacuum({int(pages)})")
        await self._connection.commit()

    async def aclose(self) -> None:
        """Close the underlying aiosqlite connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
