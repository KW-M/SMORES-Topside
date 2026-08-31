"""Shared fixtures for unit and integration tests: an isolated data
directory (via `SMORES_DATA_DIR`), a fast/test-tuned `Config`, a real
(test-only) `Database`, and a small array of `MockBlueRDOSensor` instances
plus the `hardware.manager.SensorFactory` that returns them — never real
hardware. See ARCHITECTURE.md §9.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from config.loader import DATA_DIR_ENV_VAR
from config.schema import Config
from db.database import Database
from hardware.manager import SensorFactory
from hardware.modbus_bus import ModbusBus
from hardware.rdo_blue_interface import BlueRDOInterface
from tests.mocks.mock_rdo_blue import MockBlueRDOSensor

CONVERTER_0 = "/dev/mock-bus0"
CONVERTER_1 = "/dev/mock-bus1"


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated `SMORES_DATA_DIR` for this test only."""
    path = tmp_path / "SMORES_Data"
    path.mkdir()
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(path))
    return path


@pytest.fixture
def test_config(data_dir: Path) -> Config:
    """A `Config` tuned for fast, deterministic tests: short timeouts, a
    short sample interval, and a fixed two-converter/three-sensor mapping
    matching the `mock_sensors` fixture. `scan_on_startup` is off so tests
    apply this mapping directly via `SensorManager.save_sensor_mapping`
    rather than triggering a (real-hardware) bus scan."""
    return Config(
        serial_port_devices=[CONVERTER_0, CONVERTER_1],
        sensor_mapping={CONVERTER_0: [1, 2], CONVERTER_1: [3]},
        scan_on_startup=False,
        sample_interval_seconds=0.05,
        modbus_request_timeout_seconds=0.2,
        sensor_read_timeout_seconds=0.5,
        scan_probe_timeout_seconds=0.2,
        api_request_timeout_seconds=5.0,
        poll_timeout_seconds=5.0,
        disk_check_interval_seconds=0.05,
    )


@pytest_asyncio.fixture
async def database(data_dir: Path) -> AsyncIterator[Database]:
    """A real, test-only `Database` backed by a file under `data_dir`."""
    db = Database(data_dir / "smores_test.db")
    await db.init_schema()
    try:
        yield db
    finally:
        await db.aclose()


@pytest.fixture
def mock_sensors(test_config: Config) -> dict[int, MockBlueRDOSensor]:
    """One `MockBlueRDOSensor` per address in `test_config.sensor_mapping`,
    each with distinct canned values so tests can tell readings apart."""
    sensors: dict[int, MockBlueRDOSensor] = {}
    for converter_id, addresses in test_config.sensor_mapping.items():
        for address in addresses:
            sensors[address] = MockBlueRDOSensor(
                address,
                converter_id,
                temperature_c=20.0 + address,
                do_percent_saturation=90.0 + address,
                do_mg_l=8.0 + address / 10,
                do_partial_pressure_torr=150.0 + address,
            )
    return sensors


@pytest.fixture
def sensor_factory(mock_sensors: dict[int, MockBlueRDOSensor]) -> SensorFactory:
    """`hardware.manager.SensorFactory` returning the pre-built mock for a
    given address, so `SensorManager` never constructs a real
    `hardware.rdo_blue.BlueRDOSensor` (and never needs a connected
    `ModbusBus`) during tests."""

    def factory(bus: ModbusBus, address: int) -> BlueRDOInterface:
        return mock_sensors[address]

    return factory
