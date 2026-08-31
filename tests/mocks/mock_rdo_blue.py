"""Mock implementation of `BlueRDOInterface` for unit/integration tests.

Returns configurable canned values for every register-backed read, and can
simulate the two failure modes `BlueRDOInterface.read_all()` documents:

- A full timeout (`set_unreachable`) simulates the whole instrument going
  silent: every `read_*` call raises `SensorTimeoutError`, matching
  `read_all()`'s "sensor was completely unreachable" case.
- A per-parameter fault (`set_field_error` / `set_field_timeout`) simulates
  the instrument responding but reporting a problem with one parameter
  only (a non-zero Data Quality ID, or that one parameter's value read
  timing out while the rest of the instrument keeps responding) — caught
  internally by `read_all()` and reflected as `constants.UNREADABLE_VALUE`
  for that field plus a non-OK `status_code`/`status_text`, per
  `read_all()`'s "never raises for an individual parameter's failure"
  contract. `read_all()` only propagates `SensorTimeoutError` when *every*
  parameter read timed out, matching the literal "e.g. every parameter
  read timed out" wording of that contract.
"""

import time
from typing import Final

from constants import UNREADABLE_VALUE, SensorReadError, SensorTimeoutError
from hardware.rdo_blue_constants import (
    DATA_QUALITY_ERROR_READING_PARAMETER,
    DATA_QUALITY_OK,
    DATA_QUALITY_TEXT,
    DEVICE_ID_RDO_BLUE,
    PARAMETER_NAMES,
)
from hardware.rdo_blue_interface import BlueRDOInterface
from models.readings import SensorReading

STATUS_CODE_PARAMETER_TIMEOUT: Final[int] = -2
"""Mock-only status_code contribution for a field whose value read timed
out while the rest of the (simulated) instrument kept responding — distinct
from a device-reported Data Quality ID. Only meaningful within this mock;
the real `hardware.rdo_blue.BlueRDOSensor` is free to define its own
equivalent, since a value-register timeout on real hardware typically means
the whole instrument stopped responding (see module docstring)."""

TEMPERATURE_FIELD: Final[str] = "temperature_c"
DO_PERCENT_FIELD: Final[str] = "do_percent_saturation"
DO_MG_L_FIELD: Final[str] = "do_mg_l"
DO_PRESSURE_FIELD: Final[str] = "do_partial_pressure_torr"

_PARAM_FIELDS: Final[tuple[str, ...]] = (
    TEMPERATURE_FIELD,
    DO_MG_L_FIELD,
    DO_PERCENT_FIELD,
    DO_PRESSURE_FIELD,
)

_FIELD_LABELS: Final[dict[str, str]] = {
    TEMPERATURE_FIELD: PARAMETER_NAMES[1],
    DO_MG_L_FIELD: PARAMETER_NAMES[20],
    DO_PERCENT_FIELD: PARAMETER_NAMES[21],
    DO_PRESSURE_FIELD: PARAMETER_NAMES[30],
}


def _severity_rank(code: int) -> int:
    """Order per-field outcomes from least to most severe: OK, then a
    device-reported Data Quality ID, then a transport-level timeout for
    that one field (ranked worst: an instrument that stops answering is a
    worse sign than one reporting its own Data Quality problem)."""
    if code == DATA_QUALITY_OK:
        return 0
    if code == STATUS_CODE_PARAMETER_TIMEOUT:
        return 1_000
    return 100 + code


class MockBlueRDOSensor(BlueRDOInterface):
    """Configurable stand-in for a real Blue RDO sensor. Construct with
    canned values, then use `set_field_error`/`set_field_timeout`/
    `set_unreachable` to simulate the negative paths `hardware.manager`
    and the API layer must handle."""

    def __init__(
        self,
        address: int,
        converter_id: str = "/dev/mock-bus0",
        *,
        serial_num: int | None = None,
        device_id: int = DEVICE_ID_RDO_BLUE,
        temperature_c: float = 20.0,
        do_percent_saturation: float = 98.5,
        do_mg_l: float = 8.2,
        do_partial_pressure_torr: float = 152.0,
    ) -> None:
        self._address = address
        self._converter_id = converter_id
        self._device_id = device_id
        self._serial_num = serial_num if serial_num is not None else 100_000 + address
        self._values: dict[str, float] = {
            TEMPERATURE_FIELD: temperature_c,
            DO_PERCENT_FIELD: do_percent_saturation,
            DO_MG_L_FIELD: do_mg_l,
            DO_PRESSURE_FIELD: do_partial_pressure_torr,
        }
        self._field_quality: dict[str, int] = dict.fromkeys(_PARAM_FIELDS, DATA_QUALITY_OK)
        self._field_timeout: set[str] = set()
        self._unreachable = False

    @property
    def address(self) -> int:
        return self._address

    @property
    def converter_id(self) -> str:
        return self._converter_id

    # --- test configuration -------------------------------------------------

    def set_value(self, field: str, value: float) -> None:
        """Change a parameter's canned measured value."""
        self._check_field(field)
        self._values[field] = value

    def set_field_error(
        self, field: str, quality_code: int = DATA_QUALITY_ERROR_READING_PARAMETER
    ) -> None:
        """Simulate the instrument reporting a non-zero Data Quality ID for
        one parameter (e.g. `DATA_QUALITY_RDO_CAP_EXPIRED`)."""
        self._check_field(field)
        self._field_quality[field] = quality_code
        self._field_timeout.discard(field)

    def set_field_timeout(self, field: str) -> None:
        """Simulate a Modbus-level timeout reading just this one
        parameter's value, while the rest of the instrument keeps
        responding."""
        self._check_field(field)
        self._field_timeout.add(field)

    def clear_faults(self) -> None:
        """Reset to fully healthy: no field errors/timeouts, reachable."""
        self._field_quality = dict.fromkeys(_PARAM_FIELDS, DATA_QUALITY_OK)
        self._field_timeout.clear()
        self._unreachable = False

    def set_unreachable(self, unreachable: bool = True) -> None:
        """Simulate the whole instrument going silent: every `read_*`
        method raises `SensorTimeoutError`."""
        self._unreachable = unreachable

    @staticmethod
    def _check_field(field: str) -> None:
        if field not in _PARAM_FIELDS:
            raise ValueError(f"unknown parameter field {field!r}")

    # --- BlueRDOInterface -----------------------------------------------------

    async def read_device_id(self) -> int:
        self._raise_if_unreachable()
        return self._device_id

    async def read_serial_num(self) -> int:
        self._raise_if_unreachable()
        return self._serial_num

    async def read_temperature_c(self) -> float:
        return await self._read_field(TEMPERATURE_FIELD)

    async def read_dissolved_o2_percent(self) -> float:
        return await self._read_field(DO_PERCENT_FIELD)

    async def read_dissolved_o2_mg_l(self) -> float:
        return await self._read_field(DO_MG_L_FIELD)

    async def read_partial_pressure_torr(self) -> float:
        return await self._read_field(DO_PRESSURE_FIELD)

    async def read_status(self) -> tuple[int, str]:
        self._raise_if_unreachable()
        return self._aggregate_quality_status()

    async def read_all(self) -> SensorReading:
        self._raise_if_unreachable()

        values: dict[str, float] = {}
        messages: list[str] = []
        worst_code = DATA_QUALITY_OK
        timed_out_fields = 0

        for field in _PARAM_FIELDS:
            if field in self._field_timeout:
                timed_out_fields += 1
                values[field] = UNREADABLE_VALUE
                messages.append(f"{_FIELD_LABELS[field]}: Timed out reading parameter")
                if _severity_rank(STATUS_CODE_PARAMETER_TIMEOUT) > _severity_rank(worst_code):
                    worst_code = STATUS_CODE_PARAMETER_TIMEOUT
                continue
            quality = self._field_quality[field]
            if quality != DATA_QUALITY_OK:
                values[field] = UNREADABLE_VALUE
                text = DATA_QUALITY_TEXT.get(quality, f"Unknown data quality id {quality}")
                messages.append(f"{_FIELD_LABELS[field]}: {text}")
                if _severity_rank(quality) > _severity_rank(worst_code):
                    worst_code = quality
            else:
                values[field] = self._values[field]

        if timed_out_fields == len(_PARAM_FIELDS):
            raise SensorTimeoutError(
                f"sensor {self._address} unreachable: every parameter read timed out"
            )

        return SensorReading(
            sensor_address=self._address,
            serial_converter_id=self._converter_id,
            timestamp_utc=time.time(),
            temperature_c=values[TEMPERATURE_FIELD],
            do_percent_saturation=values[DO_PERCENT_FIELD],
            do_partial_pressure_torr=values[DO_PRESSURE_FIELD],
            do_mg_l=values[DO_MG_L_FIELD],
            status_code=worst_code,
            status_text="; ".join(messages) if messages else "OK",
        )

    # --- internals --------------------------------------------------------

    def _raise_if_unreachable(self) -> None:
        if self._unreachable:
            raise SensorTimeoutError(f"sensor {self._address} unreachable (simulated)")

    async def _read_field(self, field: str) -> float:
        self._raise_if_unreachable()
        if field in self._field_timeout:
            raise SensorTimeoutError(f"timed out reading {field} (simulated)")
        quality = self._field_quality[field]
        if quality != DATA_QUALITY_OK:
            text = DATA_QUALITY_TEXT.get(quality, f"Unknown data quality id {quality}")
            raise SensorReadError(text)
        return self._values[field]

    def _aggregate_quality_status(self) -> tuple[int, str]:
        worst_code = DATA_QUALITY_OK
        messages: list[str] = []
        for field in _PARAM_FIELDS:
            quality = self._field_quality[field]
            if quality != DATA_QUALITY_OK:
                text = DATA_QUALITY_TEXT.get(quality, f"Unknown data quality id {quality}")
                messages.append(f"{_FIELD_LABELS[field]}: {text}")
                if quality > worst_code:
                    worst_code = quality
        return worst_code, "; ".join(messages) if messages else "OK"
