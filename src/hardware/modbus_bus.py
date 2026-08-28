"""Async Modbus RTU wrapper for one RS485-to-USB converter.

One `ModbusBus` instance exists per configured `serial_port_devices` entry.
Wraps a pymodbus `AsyncModbusSerialClient`. Because RS485 is half-duplex,
every request on the port is serialized behind an `asyncio.Lock`: a caller
awaits the lock, sends its request, awaits the response (or timeout), then
releases the lock — so a concurrent caller (the sampler loop, an API poll)
can interleave at the request level without corrupting either party's
response, per ARCHITECTURE.md §4.
"""


class ModbusBus:
    """One serialized Modbus RTU connection over one RS485-to-USB converter."""

    def __init__(
        self,
        device_path: str,
        baudrate: int,
        parity: str,
        stopbits: int,
        bytesize: int,
        request_timeout_seconds: float,
    ) -> None:
        """Construct the bus wrapper. Does not open the serial port yet —
        call `connect()` before issuing any requests.

        Args:
            device_path: stable device identifier, e.g.
                `/dev/serial/by-id/usb-FTDI_...-port0`.
            baudrate, parity, stopbits, bytesize: serial params; must match
                the instruments' configured communication settings.
            request_timeout_seconds: per-request ("serial") timeout applied
                to each individual register read.
        """
        raise NotImplementedError

    @property
    def converter_id(self) -> str:
        """This bus's stable device identifier (the `device_path` given at
        construction), used as `SensorReading.serial_converter_id`."""
        raise NotImplementedError

    async def connect(self) -> None:
        """Open the underlying serial connection.

        Raises:
            BusScanError: the serial device could not be opened (e.g. the
                `/dev/serial/by-id/...` path doesn't exist).
        """
        raise NotImplementedError

    async def aclose(self) -> None:
        """Close the underlying serial connection. Safe to call even if
        `connect()` was never called or already failed."""
        raise NotImplementedError

    async def read_holding_registers(self, address: int, count: int, slave: int) -> list[int]:
        """Read `count` contiguous holding registers starting at `address`
        from unit `slave`, serialized behind this bus's lock.

        Args:
            address: zero-based holding register address.
            count: number of 16-bit registers to read.
            slave: Modbus unit/slave address of the target sensor.

        Returns:
            The raw register values, most-significant register first.

        Raises:
            SensorTimeoutError: no response within `request_timeout_seconds`.
            SensorReadError: a Modbus exception response was returned.
        """
        raise NotImplementedError

    async def probe_address(self, address: int, timeout_seconds: float | None = None) -> bool:
        """Check whether a sensor is present at Modbus unit `address` on
        this bus, used by `hardware.manager.scan_all_buses`.

        Args:
            address: Modbus unit/slave address to probe.
            timeout_seconds: overrides `request_timeout_seconds` for this
                probe only (scanning typically uses a shorter timeout, per
                `config.scan_probe_timeout_seconds`).

        Returns:
            `True` if a well-formed response was received, `False` on a
            timeout or no-response (absence is not an error during a scan).

        Raises:
            SensorTimeoutError: never raised for absence; only for
                unexpected transport-level failures distinct from a plain
                timeout (e.g. the serial connection itself dropping).
        """
        raise NotImplementedError
