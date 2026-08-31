"""Unit tests for `hardware.modbus_bus.ModbusBus`, in isolation from real
serial hardware: `hardware.modbus_bus.AsyncModbusSerialClient` is
monkeypatched with `FakeAsyncModbusSerialClient` below (see the
implementation note in `hardware/modbus_bus.py`'s module docstring for the
import-shape this assumes).

The bus under test is built with millisecond-scale session/settle timings
(`_make_bus`) instead of the vendor doc's real 5 s / 1 s values, so the
wake-up tests below exercise the real code path without adding seconds of
sleeping to the suite.
"""

import asyncio

import pytest
from pymodbus.exceptions import ModbusIOException

import hardware.modbus_bus as modbus_bus_module
from constants import BusScanError, SensorReadError, SensorTimeoutError
from hardware.modbus_bus import ModbusBus
from hardware.rdo_blue_constants import DEVICE_ID_REGISTER

SESSION_TIMEOUT_SECONDS = 0.3
SESSION_KEEPALIVE_MARGIN_SECONDS = 0.1
"""Leaves a 0.2 s window in which an instrument counts as still awake."""

WAKEUP_SETTLE_SECONDS = 0.01

DEVICE_PATH = "/dev/serial/by-id/usb-FTDI_USB-RS485_Cable-if00-port0"


class _FakeResponse:
    def __init__(self, registers: list[int] | None = None, error: bool = False) -> None:
        self.registers = registers or []
        self._error = error

    def isError(self) -> bool:
        return self._error


class FakeAsyncModbusSerialClient:
    """Stand-in for `pymodbus.client.AsyncModbusSerialClient`."""

    connect_result = True

    def __init__(self, port: str, **kwargs: object) -> None:
        self.port = port
        self.kwargs = kwargs
        self.connected = False
        self.closed = False
        self.registers: dict[tuple[int, int], list[int]] = {}
        self.timeout_keys: set[tuple[int, int]] = set()
        self.timeout_device_ids: set[int] = set()
        self.error_keys: set[tuple[int, int]] = set()
        self.calls: list[tuple[int, int, int]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.delay_seconds = 0.0
        self.cancel_becomes_io_exception = False
        """Mirror pymodbus, which turns a cancellation of its own in-flight
        request into ModbusIOException("Request cancelled outside library")
        rather than letting the CancelledError through."""

    async def connect(self) -> bool:
        self.connected = self.connect_result
        return self.connected

    def close(self) -> None:
        self.connected = False
        self.closed = True

    async def read_holding_registers(
        self, address: int, *, count: int = 1, device_id: int = 1, **kwargs: object
    ) -> _FakeResponse:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay_seconds:
                try:
                    await asyncio.sleep(self.delay_seconds)
                except asyncio.CancelledError:
                    if not self.cancel_becomes_io_exception:
                        raise
                    raise ModbusIOException("Request cancelled outside library") from None
            self.calls.append((address, count, device_id))
            key = (device_id, address)
            if key in self.timeout_keys or device_id in self.timeout_device_ids:
                raise ModbusIOException(f"simulated timeout at {address}")
            if key in self.error_keys:
                return _FakeResponse(error=True)
            return _FakeResponse(registers=self.registers.get(key, [0] * count))
        finally:
            self.in_flight -= 1


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> list[FakeAsyncModbusSerialClient]:
    """A list `ModbusBus.connect()` appends its constructed fake client to
    (constructed lazily by `ModbusBus`, so the fixture can't hand back the
    instance directly — tests read it out of this list after connecting)."""
    created: list[FakeAsyncModbusSerialClient] = []

    def factory(port: str, **kwargs: object) -> FakeAsyncModbusSerialClient:
        client = FakeAsyncModbusSerialClient(port, **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(modbus_bus_module, "AsyncModbusSerialClient", factory, raising=False)
    return created


def _make_bus() -> ModbusBus:
    return ModbusBus(
        DEVICE_PATH,
        baudrate=19200,
        parity="E",
        stopbits=1,
        bytesize=8,
        request_timeout_seconds=0.2,
        session_timeout_seconds=SESSION_TIMEOUT_SECONDS,
        wakeup_settle_seconds=WAKEUP_SETTLE_SECONDS,
        session_keepalive_margin_seconds=SESSION_KEEPALIVE_MARGIN_SECONDS,
    )


def _wake_ups(client: FakeAsyncModbusSerialClient, slave: int) -> list[tuple[int, int, int]]:
    """The wake-up commands sent to `slave`: a 1-register Device Id read."""
    return [call for call in client.calls if call == (DEVICE_ID_REGISTER, 1, slave)]


async def _connected_bus(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> tuple[ModbusBus, FakeAsyncModbusSerialClient]:
    bus = _make_bus()
    await bus.connect()
    assert len(fake_client) == 1
    return bus, fake_client[0]


async def test_converter_id_is_the_device_path() -> None:
    bus = _make_bus()
    assert bus.converter_id == DEVICE_PATH


async def test_connect_success(fake_client: list[FakeAsyncModbusSerialClient]) -> None:
    bus, client = await _connected_bus(fake_client)
    assert client.connected is True


async def test_connect_failure_raises_bus_scan_error(
    fake_client: list[FakeAsyncModbusSerialClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeAsyncModbusSerialClient, "connect_result", False)
    bus = _make_bus()
    with pytest.raises(BusScanError):
        await bus.connect()


async def test_aclose_without_connect_is_safe() -> None:
    bus = _make_bus()
    await bus.aclose()


async def test_aclose_closes_the_client(fake_client: list[FakeAsyncModbusSerialClient]) -> None:
    bus, client = await _connected_bus(fake_client)
    await bus.aclose()
    assert client.closed is True


async def test_read_holding_registers_returns_values(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    bus, client = await _connected_bus(fake_client)
    client.registers[(5, 100)] = [1, 2, 3]

    result = await bus.read_holding_registers(100, 3, slave=5)

    assert result == [1, 2, 3]


async def test_read_holding_registers_raises_sensor_timeout_error(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    bus, client = await _connected_bus(fake_client)
    client.timeout_keys.add((5, 100))

    with pytest.raises(SensorTimeoutError):
        await bus.read_holding_registers(100, 1, slave=5)


async def test_read_holding_registers_raises_sensor_read_error_on_exception_response(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    bus, client = await _connected_bus(fake_client)
    client.error_keys.add((5, 100))

    with pytest.raises(SensorReadError):
        await bus.read_holding_registers(100, 1, slave=5)


async def test_probe_address_true_on_success(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    bus, _client = await _connected_bus(fake_client)

    assert await bus.probe_address(1) is True


async def test_probe_address_false_on_timeout(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    bus, client = await _connected_bus(fake_client)
    client.timeout_device_ids.add(1)

    assert await bus.probe_address(1) is False


async def test_requests_are_serialized_behind_the_bus_lock(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    bus, client = await _connected_bus(fake_client)
    client.delay_seconds = 0.05

    await asyncio.gather(
        bus.read_holding_registers(100, 1, slave=1),
        bus.read_holding_registers(200, 1, slave=2),
    )

    assert client.max_in_flight == 1


async def test_probe_address_false_on_exception_response(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    bus, client = await _connected_bus(fake_client)
    client.error_keys.add((1, DEVICE_ID_REGISTER))

    assert await bus.probe_address(1) is False


async def test_probe_address_does_not_wake_the_address_first(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    """A scan probes hundreds of addresses, so `hardware.manager` wakes them
    in bulk rather than paying a settle wait inside every probe."""
    bus, client = await _connected_bus(fake_client)

    await bus.probe_address(1)

    assert client.calls == [(DEVICE_ID_REGISTER, 1, 1)]


async def test_first_read_of_an_address_sends_a_wake_up_first(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    """Vendor doc: an idle instrument must be woken by "any Modbus command",
    then given time to wake, before it will answer a real read."""
    bus, client = await _connected_bus(fake_client)

    await bus.read_holding_registers(100, 1, slave=5)

    assert client.calls == [(DEVICE_ID_REGISTER, 1, 5), (100, 1, 5)]


async def test_second_read_within_the_session_does_not_wake_again(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    bus, client = await _connected_bus(fake_client)

    await bus.read_holding_registers(100, 1, slave=5)
    await bus.read_holding_registers(200, 1, slave=5)

    assert len(_wake_ups(client, 5)) == 1


async def test_read_after_the_session_lapses_wakes_again(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    bus, client = await _connected_bus(fake_client)

    await bus.read_holding_registers(100, 1, slave=5)
    await asyncio.sleep(SESSION_TIMEOUT_SECONDS)
    await bus.read_holding_registers(100, 1, slave=5)

    assert len(_wake_ups(client, 5)) == 2


async def test_each_address_is_woken_independently(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    """The end-of-session timeout is a property of each instrument, not of
    the port, so waking address 5 must not mark address 6 as awake."""
    bus, client = await _connected_bus(fake_client)

    await bus.read_holding_registers(100, 1, slave=5)
    await bus.read_holding_registers(100, 1, slave=6)

    assert len(_wake_ups(client, 5)) == 1
    assert len(_wake_ups(client, 6)) == 1


async def test_concurrent_readers_of_one_address_wake_it_once(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    bus, client = await _connected_bus(fake_client)

    await asyncio.gather(
        bus.read_holding_registers(100, 1, slave=5),
        bus.read_holding_registers(200, 1, slave=5),
    )

    assert len(_wake_ups(client, 5)) == 1


async def test_an_unanswered_wake_up_is_not_an_error(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    """The doc's whole point is that the instrument may not answer until it
    has woken, so `wake_address` must not raise on a non-answer."""
    bus, client = await _connected_bus(fake_client)
    client.timeout_device_ids.add(7)

    await bus.wake_address(7)


async def test_a_read_still_happens_when_the_wake_up_goes_unanswered(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    bus, client = await _connected_bus(fake_client)
    client.timeout_keys.add((5, DEVICE_ID_REGISTER))
    client.registers[(5, 100)] = [7]

    assert await bus.read_holding_registers(100, 1, slave=5) == [7]


async def test_a_cancelled_read_stays_cancelled(
    fake_client: list[FakeAsyncModbusSerialClient],
) -> None:
    """pymodbus reports a cancellation of its own in-flight request as a
    ModbusIOException. Translating that into SensorTimeoutError would swallow
    the cancel — a sensor read would keep a shutting-down task alive."""
    bus, client = await _connected_bus(fake_client)
    client.delay_seconds = 5.0
    client.cancel_becomes_io_exception = True

    task = asyncio.create_task(bus.read_holding_registers(100, 1, slave=5))
    await asyncio.sleep(0.02)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
