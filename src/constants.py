"""Global constants and exception classes shared across all subsystems.

Hardware, db, and api layers translate failures to these exception types
(or, for hardware, to `UNREADABLE_VALUE` + a status string) instead of
using ad hoc try/except per module. See ARCHITECTURE.md and AGENTS.md.
"""

from typing import Final

UNREADABLE_VALUE: Final[int] = -9999
"""Sentinel value stored in place of any sensor numeric field that could not be
read (timeout, Modbus exception response, or a bad Data Quality ID)."""

SENSOR_UNREACHABLE_STATUS_CODE: Final[int] = -1
"""`SensorReading.status_code` used by `hardware.manager.SensorManager` for a
sensor whose `BlueRDOInterface.read_all()` raised `SensorTimeoutError`
(every parameter read timed out) — distinct from any (non-negative) Blue RDO
Data Quality ID, since the instrument didn't just report a bad measurement,
it didn't respond at all."""

SENSOR_UNREACHABLE_STATUS_TEXT: Final[str] = "Sensor timeout"
"""`SensorReading.status_text` paired with `SENSOR_UNREACHABLE_STATUS_CODE`."""

PARAMETER_TIMEOUT_STATUS_CODE: Final[int] = -2
"""`SensorReading.status_code` used when *some* (not all) of a sensor's
parameter reads timed out while the instrument kept answering others — the
instrument is reachable, so this isn't `SENSOR_UNREACHABLE_STATUS_CODE`, but
it also never reported a Data Quality ID for those parameters. Ranked as the
most severe outcome for a single parameter (see
`hardware.rdo_blue._severity_rank`)."""

SENSOR_READ_ERROR_STATUS_CODE: Final[int] = -3
"""`SensorReading.status_code` used by `hardware.manager.SensorManager` when a
sensor's `read_all()` raised `SensorReadError` — i.e. the transport itself
failed (Modbus exception response, short/malformed reply) rather than the
instrument reporting a Data Quality problem it could describe."""

MODBUS_MIN_UNIT_ADDRESS: Final[int] = 1
"""Lowest legal Modbus RTU unit/slave address (0 is the broadcast address)."""

MODBUS_MAX_UNIT_ADDRESS: Final[int] = 247
"""Highest legal Modbus RTU unit/slave address; 248-255 are reserved. Bounds
`config.scan_min_address`/`scan_max_address` and validates any sensor address
handed to `hardware.manager.SensorManager.save_sensor_mapping`."""


class SensorTimeoutError(Exception):
    """A sensor read or scan probe exceeded its configured timeout.

    Raised by `hardware.modbus_bus.ModbusBus` / `hardware.rdo_blue.BlueRDOSensor`
    on a per-request or per-sensor timeout; caught by `hardware.manager` and
    translated into `constants.UNREADABLE_VALUE` fields + a status string.
    """


class SensorReadError(Exception):
    """A sensor responded, but the response indicates an error.

    Covers a non-zero Data Quality ID, a Modbus exception response, or a
    malformed/partial register read. Raised by `hardware.rdo_blue.BlueRDOSensor`;
    caught by `hardware.manager` and translated into `constants.UNREADABLE_VALUE`
    fields + a status string.
    """


class BusScanError(Exception):
    """A Modbus bus scan could not be completed, or a sensor query was made
    while the mapping it depends on is not yet established.

    Covers: a scan probe failing unexpectedly, and API/sampler calls to
    `hardware.manager.SensorManager.query_all_sensors`/`query_sensor` made
    while a startup or on-demand scan is still in progress.
    """


class ConfigValidationError(Exception):
    """`config.json` could not be parsed or failed schema validation.

    Raised by `config.loader.load_config`/`save_config`, wrapping the
    underlying `json.JSONDecodeError`/pydantic `ValidationError`.
    """
