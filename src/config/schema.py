"""Typed config schema for SMORES-Topside.

Single source of truth for `<data_dir>/config.json`'s shape, defaults, and
validation. See ARCHITECTURE.md §3 for the field table and rationale.
`config.loader` (a later step) loads/saves this file and wraps pydantic's
`ValidationError` in `constants.ConfigValidationError`.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class Config(BaseModel):
    """Full contents of `<data_dir>/config.json`."""

    model_config = ConfigDict(extra="forbid")

    serial_port_devices: list[str] = Field(
        default_factory=list,
        description=(
            "Stable device identifiers for RS485-to-USB serial adapters, "
            "e.g. '/dev/serial/by-id/usb-FTDI_...-port0'."
        ),
    )
    sensor_mapping: dict[str, list[int]] = Field(
        default_factory=dict,
        description=(
            "Converter device path -> list of Modbus addresses present on "
            "it. Populated by the startup scan or GET /api/scan; used "
            "directly (no scan) when scan_on_startup is false."
        ),
    )
    scan_on_startup: bool = Field(
        default=True,
        description=(
            "If false, trust sensor_mapping as-is and skip the startup "
            "bus scan."
        ),
    )
    sample_interval_seconds: float = Field(
        default=60.0,
        gt=0,
        description=(
            "Interval between DB-writing polls (drift-corrected loop in "
            "sampler.py)."
        ),
    )

    modbus_baudrate: int = Field(
        default=19200,
        gt=0,
        description="Serial baud rate; matches RDO Blue factory default.",
    )
    modbus_parity: Literal["N", "E", "O"] = Field(
        default="E",
        description="Serial parity; E matches RDO Blue factory default.",
    )
    modbus_stopbits: int = Field(
        default=1,
        ge=1,
        le=2,
        description="Serial stop bits; 1 matches RDO Blue factory default.",
    )
    modbus_bytesize: Literal[5, 6, 7, 8] = Field(
        default=8,
        description="Serial byte size; 8 matches RDO Blue factory default.",
    )

    modbus_request_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        description=(
            "Per-Modbus-request (single register read) timeout — the "
            "'serial timeout'."
        ),
    )
    sensor_read_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        description=(
            "Timeout for one sensor's full multi-register read "
            "(read_all()) — the 'sensor timeout'."
        ),
    )
    scan_probe_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        description=(
            "Timeout probing the Modbus address space of one "
            "RS485-to-USB serial converter."
        ),
    )

    api_host: str = Field(default="0.0.0.0", description="aiohttp bind address.")
    api_port: int = Field(
        default=8080, ge=1, le=65535, description="aiohttp bind port."
    )
    api_request_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Default per-request timeout for most endpoints (504 on expiry).",
    )
    poll_timeout_seconds: float = Field(
        default=8.0,
        gt=0,
        description="Timeout specific to GET /api/sensors/current.",
    )
    api_max_concurrent_clients: int = Field(
        default=5,
        ge=1,
        description="asyncio.Semaphore size in api/middleware.py.",
    )

    min_free_disk_space_mb: int = Field(
        default=500,
        ge=0,
        description=(
            "Free space floor in MB; below this, retention starts "
            "deleting oldest rows."
        ),
    )
    disk_check_interval_seconds: float = Field(
        default=300.0,
        gt=0,
        description="How often db/retention.py checks free space.",
    )
    retention_delete_batch_size: int = Field(
        default=50,
        ge=1,
        description="Rows deleted per batch until free space recovers.",
    )

    log_level: str = Field(
        default="INFO",
        description="Passed to logging.basicConfig. One of: " + f"{sorted(_VALID_LOG_LEVELS)}",
    )

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        upper = v.upper()
        if upper not in _VALID_LOG_LEVELS:
            raise ValueError(
                f"log_level must be one of {sorted(_VALID_LOG_LEVELS)}, got {v!r}"
            )
        return upper
