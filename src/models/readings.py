"""Shared data models for the sensor "reading" and "scan result" shapes.

Single source of truth used by the hardware, db, and api layers — see
ARCHITECTURE.md §5. `SensorReading` is produced by
`hardware.manager.query_all_sensors()`, stored 1:1 as a `db.database` row,
and serialized as the JSON/CSV body of `/api/sensors/current` and
`/api/data`(`/csv`).
"""

from pydantic import BaseModel, Field


class SensorReading(BaseModel):
    """One sensor's readings at a point in time.

    Unreadable numeric fields use `constants.UNREADABLE_VALUE` (-9999)
    rather than `None`, so every field stays a plain float/int for DB
    storage and CSV export.
    """

    row_id: int | None = Field(
        default=None,
        description="DB autoincrement id; None for a fresh, unsaved poll.",
    )
    sensor_address: int = Field(description="Globally unique Modbus address.")
    serial_converter_id: str = Field(
        description=(
            "Stable device identifier path of the RS485-to-USB converter this "
            "sensor was read from."
        )
    )
    timestamp_utc: float = Field(description="Unix epoch seconds, UTC.")
    temperature_c: float = Field(
        description="Degrees Celsius; constants.UNREADABLE_VALUE if unreadable."
    )
    do_percent_saturation: float = Field(
        description="Dissolved O2, % saturation; constants.UNREADABLE_VALUE if unreadable."
    )
    do_partial_pressure_torr: float = Field(
        description="Dissolved O2 partial pressure, torr; constants.UNREADABLE_VALUE if unreadable."
    )
    do_mg_l: float = Field(
        description="Dissolved O2, mg/L; constants.UNREADABLE_VALUE if unreadable."
    )
    status_code: int = Field(
        description=(
            "Worst-case Data Quality ID across the 4 Blue RDO provided parameters, or a "
            "negative internal code for timeout/unreachable."
        )
    )
    status_text: str = Field(
        description=(
            "Human-readable status, e.g. 'OK', 'Sensor timeout', "
            "'temperature: Error reading parameter'."
        )
    )


class ScanResult(BaseModel):
    """Modbus addresses found present on one RS485-to-USB converter."""

    converter_id: str = Field(description="Stable device identifier path of the converter scanned.")
    sensor_addresses: list[int] = Field(
        description="Modbus addresses found present on this converter."
    )
    scanned_at: float = Field(description="Unix epoch seconds when the scan completed.")
