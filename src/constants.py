"""Global constants and exception classes shared across all subsystems.

Hardware, db, and api layers translate failures to these exception types
(or, for hardware, to `UNREADABLE_VALUE` + a status string) instead of
using ad hoc try/except per module. See ARCHITECTURE.md and AGENTS.md.
"""

from typing import Final

UNREADABLE_VALUE: Final[int] = -9999
"""Sentinel value stored in place of any sensor numeric field that could not be
read (timeout, Modbus exception response, or a bad Data Quality ID)."""


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
