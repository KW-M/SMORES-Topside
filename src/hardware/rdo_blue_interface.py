"""Abstract interface for a single Blue RDO sensor, satisfied by both the
real Modbus-backed implementation (`hardware.rdo_blue.BlueRDOSensor`) and
the test mock (`tests.mocks.mock_rdo_blue.MockBlueRDOSensor`).

All read_* methods perform one bounded round trip against the sensor at
`self.address` and must not retry internally: on timeout or a Modbus/Data
Quality error they raise rather than silently returning a stale value, so
`hardware.manager` can uniformly translate failures into
`constants.UNREADABLE_VALUE` + a status string.
"""

import abc

from models.readings import SensorReading


class BlueRDOInterface(abc.ABC):
    """One Blue RDO sensor, addressed by its Modbus unit address."""

    @property
    @abc.abstractmethod
    def address(self) -> int:
        """This sensor's Modbus unit address."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def converter_id(self) -> str:
        """Stable device identifier of the RS485-to-USB converter this
        sensor is reachable through (e.g. `/dev/serial/by-id/...`)."""
        raise NotImplementedError

    @abc.abstractmethod
    async def read_device_id(self) -> int:
        """Read the Device Id register. Expect 35 for an RDO Blue.

        Raises:
            SensorTimeoutError: no response within the configured timeout.
            SensorReadError: a Modbus exception response was returned.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def read_serial_num(self) -> int:
        """Read the instrument's serial number.

        Raises:
            SensorTimeoutError: no response within the configured timeout.
            SensorReadError: a Modbus exception response was returned.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def read_temperature_c(self) -> float:
        """Read the Temperature parameter's measured value, in Celsius.

        Raises:
            SensorTimeoutError: no response within the configured timeout.
            SensorReadError: a Modbus exception response, or a non-zero
                Data Quality ID, was returned for this parameter.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def read_dissolved_o2_percent(self) -> float:
        """Read the DO Percent Saturation parameter's measured value.

        Raises:
            SensorTimeoutError: no response within the configured timeout.
            SensorReadError: a Modbus exception response, or a non-zero
                Data Quality ID, was returned for this parameter.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def read_dissolved_o2_mg_l(self) -> float:
        """Read the DO Concentration parameter's measured value, in mg/L.

        Raises:
            SensorTimeoutError: no response within the configured timeout.
            SensorReadError: a Modbus exception response, or a non-zero
                Data Quality ID, was returned for this parameter.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def read_partial_pressure_torr(self) -> float:
        """Read the Oxygen Partial Pressure parameter's measured value, in torr.

        Raises:
            SensorTimeoutError: no response within the configured timeout.
            SensorReadError: a Modbus exception response, or a non-zero
                Data Quality ID, was returned for this parameter.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def read_status(self) -> tuple[int, str]:
        """Read the Data Quality ID of all 4 parameters and summarize them.

        Returns:
            `(status_code, status_text)` where `status_code` is the
            worst-case (highest-severity) Data Quality ID across the 4
            parameters and `status_text` is a human-readable summary, e.g.
            `"OK"` or `"temperature: Error reading parameter"`.

        Raises:
            SensorTimeoutError: no response within the configured timeout.
            SensorReadError: a Modbus exception response was returned.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def read_all(self) -> SensorReading:
        """Read every parameter plus status in one bounded operation.

        Never raises for an individual parameter's failure: any read_*
        failure for one parameter is caught internally and reflected as
        `constants.UNREADABLE_VALUE` for that field plus a non-OK
        `status_code`/`status_text` on the returned `SensorReading`.
        `row_id` is left `None` (caller decides whether to persist it).

        Raises:
            SensorTimeoutError: the sensor was completely unreachable
                (e.g. every parameter read timed out).
        """
        raise NotImplementedError
