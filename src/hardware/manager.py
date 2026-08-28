"""High-level sensor array management API.

Owns one `ModbusBus` per configured converter and the resulting
address -> (bus, BlueRDOSensor) table. This is the only module the rest of
the codebase (sampler, API routes) talks to for anything sensor-related.
"""

from config.schema import Config
from models.readings import ScanResult, SensorReading


class SensorManager:
    """Owns all `ModbusBus`/`BlueRDOSensor` instances and mediates every
    scan and read against them."""

    def __init__(self, config: Config) -> None:
        """Construct (but do not yet connect) one `ModbusBus` per entry in
        `config.serial_port_devices`. Call `start()` before scanning or
        querying."""
        raise NotImplementedError

    @property
    def is_scanning(self) -> bool:
        """`True` while a `scan_all_buses()` call is in progress.

        `query_all_sensors`/`query_sensor` raise `BusScanError` while this
        is `True` and the sensor mapping isn't yet established, per
        AGENTS.md's "API calls made during scanning should return an
        informative error."
        """
        raise NotImplementedError

    async def start(self) -> None:
        """Open every configured `ModbusBus`'s serial connection.

        If `config.scan_on_startup` is set, callers (`main.py`) run
        `scan_all_buses()` immediately after this; otherwise callers apply
        `config.sensor_mapping` directly via `save_sensor_mapping()`.
        """
        raise NotImplementedError

    async def aclose(self) -> None:
        """Close every `ModbusBus`'s serial connection. Safe to call even
        if `start()` was never called."""
        raise NotImplementedError

    async def scan_all_buses(self) -> list[ScanResult]:
        """Probe every configured converter's Modbus address space for
        present sensors, rebuilding the internal sensor table from the
        result.

        Mutual exclusion: if a scan is already in progress, this call
        waits on and returns that scan's result rather than starting a
        second concurrent scan (ARCHITECTURE.md §4).

        Returns:
            One `ScanResult` per configured converter.

        Raises:
            BusScanError: a converter's serial connection is unusable.
        """
        raise NotImplementedError

    def get_sensor_mapping(self) -> dict[str, list[int]]:
        """Return the current converter_id -> [sensor addresses] mapping
        this manager is using, in `config.sensor_mapping`'s shape."""
        raise NotImplementedError

    async def save_sensor_mapping(self, mapping: dict[str, list[int]]) -> None:
        """Rebuild the internal address -> (bus, BlueRDOSensor) table from
        `mapping` (converter_id -> sensor addresses), without probing
        hardware. Used both to apply `config.sensor_mapping` at startup
        when `scan_on_startup` is false, and after `scan_all_buses()`.

        Does not itself persist `mapping` to `config.json`; callers use
        `config.loader.save_config` for that (ARCHITECTURE.md §7).

        Raises:
            BusScanError: `mapping` references a converter_id this manager
                has no `ModbusBus` for.
        """
        raise NotImplementedError

    async def query_all_sensors(self) -> list[SensorReading]:
        """Read every sensor in the current mapping, one `read_all()` per
        sensor. Never raises for an individual sensor's failure — each
        unreachable sensor's `SensorReading` carries
        `constants.UNREADABLE_VALUE` fields and a non-OK status instead.

        Raises:
            BusScanError: a scan is currently in progress and no mapping
                is established yet.
        """
        raise NotImplementedError

    async def query_sensor(self, address: int) -> SensorReading:
        """Read one sensor by its Modbus address.

        Raises:
            BusScanError: a scan is currently in progress and no mapping
                is established yet, or `address` is not in the current
                mapping.
        """
        raise NotImplementedError
