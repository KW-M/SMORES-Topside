"""Real, Modbus-backed implementation of `BlueRDOInterface`."""

from hardware.modbus_bus import ModbusBus
from hardware.rdo_blue_interface import BlueRDOInterface
from models.readings import SensorReading


class BlueRDOSensor(BlueRDOInterface):
    """One physical Blue RDO sensor, reached via a `ModbusBus` at a fixed
    Modbus unit address."""

    def __init__(self, bus: ModbusBus, address: int, read_timeout_seconds: float) -> None:
        """
        Args:
            bus: the `ModbusBus` this sensor is wired to.
            address: this sensor's Modbus unit address.
            read_timeout_seconds: timeout for one full `read_all()` (the
                "sensor timeout", `config.sensor_read_timeout_seconds`),
                distinct from `bus`'s per-request timeout.
        """
        raise NotImplementedError

    @property
    def address(self) -> int:
        raise NotImplementedError

    @property
    def converter_id(self) -> str:
        raise NotImplementedError

    async def read_device_id(self) -> int:
        raise NotImplementedError

    async def read_serial_num(self) -> int:
        raise NotImplementedError

    async def read_temperature_c(self) -> float:
        raise NotImplementedError

    async def read_dissolved_o2_percent(self) -> float:
        raise NotImplementedError

    async def read_dissolved_o2_mg_l(self) -> float:
        raise NotImplementedError

    async def read_partial_pressure_torr(self) -> float:
        raise NotImplementedError

    async def read_status(self) -> tuple[int, str]:
        raise NotImplementedError

    async def read_all(self) -> SensorReading:
        raise NotImplementedError
