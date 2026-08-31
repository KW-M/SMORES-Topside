"""Unit tests for `hardware.rdo_blue.BlueRDOSensor`, the real
Modbus-backed `BlueRDOInterface` implementation, in isolation from real
serial hardware.

`FakeModbusBus` below is a minimal `hardware.modbus_bus.ModbusBus`-shaped
test double — not `tests.mocks.mock_rdo_blue.MockBlueRDOSensor`, which
fakes a whole *sensor*; this fakes just the one bus operation
`BlueRDOSensor` calls (`read_holding_registers`), backed by an in-memory
register map built from `hardware.rdo_blue_constants`'s register map so
register-decoding bugs (wrong offset, wrong word order) show up directly.
"""

import struct

import pytest

from constants import UNREADABLE_VALUE, SensorReadError, SensorTimeoutError
from hardware.rdo_blue import BlueRDOSensor
from hardware.rdo_blue_constants import (
    DATA_QUALITY_ERROR_READING_PARAMETER,
    DATA_QUALITY_OK,
    DATA_QUALITY_RDO_CAP_EXPIRED,
    DEVICE_ID_RDO_BLUE,
    DEVICE_ID_REGISTER,
    DO_CONCENTRATION_MG_L_PARAMETER_REGISTER,
    DO_PARTIAL_PRESSURE_TORR_PARAMETER_REGISTER,
    DO_PERCENT_SATURATION_PARAMETER_REGISTER,
    PARAMETER_DATA_QUALITY_OFFSET,
    SERIAL_NUMBER_REGISTER,
    TEMPERATURE_PARAMETER_REGISTER,
)

READ_TIMEOUT_SECONDS = 1.0


def _float_to_registers(value: float) -> tuple[int, int]:
    """Big-endian (MSB-first) word order, per the vendor doc's 'Set the
    byte order to: Big Endian (MSB)' instruction."""
    high, low = struct.unpack(">HH", struct.pack(">f", value))
    return high, low


class FakeModbusBus:
    """In-memory stand-in for `hardware.modbus_bus.ModbusBus`."""

    def __init__(self, converter_id: str = "/dev/fake-bus0") -> None:
        self._converter_id = converter_id
        self.registers: dict[int, int] = {}
        self.calls: list[tuple[int, int, int]] = []
        self.timeout_addresses: set[int] = set()

    @property
    def converter_id(self) -> str:
        return self._converter_id

    def set_uint16(self, address: int, value: int) -> None:
        self.registers[address] = value & 0xFFFF

    def set_uint32(self, address: int, value: int) -> None:
        self.registers[address] = (value >> 16) & 0xFFFF
        self.registers[address + 1] = value & 0xFFFF

    def set_float32(self, address: int, value: float) -> None:
        high, low = _float_to_registers(value)
        self.registers[address] = high
        self.registers[address + 1] = low

    def set_parameter_block(
        self, register: int, value: float, data_quality: int = DATA_QUALITY_OK
    ) -> None:
        self.set_float32(register, value)
        self.set_uint16(register + PARAMETER_DATA_QUALITY_OFFSET, data_quality)

    async def read_holding_registers(self, address: int, count: int, slave: int) -> list[int]:
        self.calls.append((address, count, slave))
        if address in self.timeout_addresses:
            raise SensorTimeoutError(f"simulated timeout at {address}")
        return [self.registers.get(address + i, 0) for i in range(count)]


@pytest.fixture
def bus() -> FakeModbusBus:
    fake = FakeModbusBus()
    fake.set_uint16(DEVICE_ID_REGISTER, DEVICE_ID_RDO_BLUE)
    fake.set_uint32(SERIAL_NUMBER_REGISTER, 123456)
    fake.set_parameter_block(TEMPERATURE_PARAMETER_REGISTER, 20.5)
    fake.set_parameter_block(DO_CONCENTRATION_MG_L_PARAMETER_REGISTER, 8.1)
    fake.set_parameter_block(DO_PERCENT_SATURATION_PARAMETER_REGISTER, 98.2)
    fake.set_parameter_block(DO_PARTIAL_PRESSURE_TORR_PARAMETER_REGISTER, 150.1)
    return fake


@pytest.fixture
def sensor(bus: FakeModbusBus) -> BlueRDOSensor:
    return BlueRDOSensor(bus, address=1, read_timeout_seconds=READ_TIMEOUT_SECONDS)  # type: ignore[arg-type]


async def test_address_and_converter_id(sensor: BlueRDOSensor, bus: FakeModbusBus) -> None:
    assert sensor.address == 1
    assert sensor.converter_id == bus.converter_id


async def test_read_device_id(sensor: BlueRDOSensor) -> None:
    assert await sensor.read_device_id() == DEVICE_ID_RDO_BLUE


async def test_read_serial_num(sensor: BlueRDOSensor) -> None:
    assert await sensor.read_serial_num() == 123456


async def test_read_temperature_c(sensor: BlueRDOSensor) -> None:
    assert await sensor.read_temperature_c() == pytest.approx(20.5)


async def test_read_dissolved_o2_mg_l(sensor: BlueRDOSensor) -> None:
    assert await sensor.read_dissolved_o2_mg_l() == pytest.approx(8.1)


async def test_read_dissolved_o2_percent(sensor: BlueRDOSensor) -> None:
    assert await sensor.read_dissolved_o2_percent() == pytest.approx(98.2)


async def test_read_partial_pressure_torr(sensor: BlueRDOSensor) -> None:
    assert await sensor.read_partial_pressure_torr() == pytest.approx(150.1)


async def test_read_raises_sensor_read_error_on_nonzero_data_quality(
    sensor: BlueRDOSensor, bus: FakeModbusBus
) -> None:
    bus.set_parameter_block(
        TEMPERATURE_PARAMETER_REGISTER, 20.5, data_quality=DATA_QUALITY_RDO_CAP_EXPIRED
    )
    with pytest.raises(SensorReadError):
        await sensor.read_temperature_c()


async def test_read_raises_sensor_timeout_error_on_bus_timeout(
    sensor: BlueRDOSensor, bus: FakeModbusBus
) -> None:
    bus.timeout_addresses.add(TEMPERATURE_PARAMETER_REGISTER)
    with pytest.raises(SensorTimeoutError):
        await sensor.read_temperature_c()


async def test_read_status_all_ok(sensor: BlueRDOSensor) -> None:
    status_code, status_text = await sensor.read_status()
    assert status_code == DATA_QUALITY_OK
    assert status_text == "OK"


async def test_read_status_reports_worst_case_and_names_the_bad_parameter(
    sensor: BlueRDOSensor, bus: FakeModbusBus
) -> None:
    bus.set_parameter_block(
        TEMPERATURE_PARAMETER_REGISTER, 20.5, data_quality=DATA_QUALITY_ERROR_READING_PARAMETER
    )
    status_code, status_text = await sensor.read_status()
    assert status_code == DATA_QUALITY_ERROR_READING_PARAMETER
    assert "Temperature" in status_text


async def test_read_all_healthy(sensor: BlueRDOSensor) -> None:
    reading = await sensor.read_all()

    assert reading.row_id is None
    assert reading.sensor_address == 1
    assert reading.temperature_c == pytest.approx(20.5)
    assert reading.do_mg_l == pytest.approx(8.1)
    assert reading.do_percent_saturation == pytest.approx(98.2)
    assert reading.do_partial_pressure_torr == pytest.approx(150.1)
    assert reading.status_code == DATA_QUALITY_OK
    assert reading.status_text == "OK"


async def test_read_all_reflects_a_single_parameter_error_without_raising(
    sensor: BlueRDOSensor, bus: FakeModbusBus
) -> None:
    bus.set_parameter_block(
        DO_PERCENT_SATURATION_PARAMETER_REGISTER, 98.2, data_quality=DATA_QUALITY_RDO_CAP_EXPIRED
    )

    reading = await sensor.read_all()

    assert reading.do_percent_saturation == UNREADABLE_VALUE
    assert reading.temperature_c == pytest.approx(20.5)
    assert reading.status_code == DATA_QUALITY_RDO_CAP_EXPIRED
    assert "DO Percent Saturation" in reading.status_text


async def test_read_all_raises_when_every_parameter_read_times_out(
    sensor: BlueRDOSensor, bus: FakeModbusBus
) -> None:
    bus.timeout_addresses.update(
        {
            TEMPERATURE_PARAMETER_REGISTER,
            DO_CONCENTRATION_MG_L_PARAMETER_REGISTER,
            DO_PERCENT_SATURATION_PARAMETER_REGISTER,
            DO_PARTIAL_PRESSURE_TORR_PARAMETER_REGISTER,
        }
    )
    with pytest.raises(SensorTimeoutError):
        await sensor.read_all()
