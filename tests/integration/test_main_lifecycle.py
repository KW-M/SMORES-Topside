"""Integration test for `main.run()`'s startup/shutdown ordering.

Runs the real entry point — real config file, real SQLite DB, real aiohttp
listener on a real TCP port, real SIGTERM. `main.run()` builds its own
`SensorManager` with no test seam, so the two hardware-facing names inside
`hardware.manager` are patched instead: `ModbusBus` becomes a slow-probing
fake (so the startup scan lasts long enough to make requests during it), and
`BlueRDOSensor` becomes the shared `MockBlueRDOSensor`.

The property under test: `main.run()` starts serving *before* it brings the
sensors up, so a client polling during a startup scan gets the manager's
"scan in progress" 503 rather than a refused connection. A full 1-247
address-space scan takes minutes at the default probe timeout, so this is
the difference between an unreachable backend and an informative one.
"""

import asyncio
import contextlib
import json
import os
import signal
import socket
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio

import hardware.manager as manager_module
import main
from config.loader import CONFIG_FILENAME
from config.schema import Config
from hardware.rdo_blue_interface import BlueRDOInterface
from tests.mocks.mock_rdo_blue import MockBlueRDOSensor

CONVERTER = "/dev/fake-lifecycle-converter"
PRESENT_ADDRESS = 2
SCAN_MIN_ADDRESS = 1
SCAN_MAX_ADDRESS = 3
PROBE_DELAY_SECONDS = 0.15


class SlowFakeBus:
    """`hardware.modbus_bus.ModbusBus` stand-in whose probes are slow enough
    that the startup scan is still running when the test's first request
    arrives."""

    def __init__(self, device_path: str, **kwargs: object) -> None:
        self.converter_id = device_path
        self.wakeup_settle_seconds = 0.0
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def aclose(self) -> None:
        self.connected = False

    async def probe_address(self, address: int, timeout_seconds: float | None = None) -> bool:
        await asyncio.sleep(PROBE_DELAY_SECONDS)
        return address == PRESENT_ADDRESS


def _mock_sensor(
    bus: SlowFakeBus, address: int, read_timeout_seconds: float
) -> BlueRDOInterface:
    """Stands in for `hardware.rdo_blue.BlueRDOSensor` at the exact seam
    `SensorManager._build_real_sensor` uses, so the manager's own sensor
    construction still runs."""
    return MockBlueRDOSensor(address, bus.converter_id, temperature_c=20.0 + address)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


@pytest.fixture
def lifecycle_port() -> int:
    return _free_port()


@pytest.fixture
def lifecycle_config(data_dir: Path, lifecycle_port: int) -> Config:
    """Write the config file `main.run()` will load. `scan_on_startup` is on:
    the startup scan is the thing being raced against."""
    config = Config(
        serial_port_devices=[CONVERTER],
        scan_on_startup=True,
        scan_min_address=SCAN_MIN_ADDRESS,
        scan_max_address=SCAN_MAX_ADDRESS,
        scan_probe_timeout_seconds=1.0,
        sample_interval_seconds=0.05,
        api_host="127.0.0.1",
        api_port=lifecycle_port,
        disk_check_interval_seconds=60.0,
    )
    (data_dir / CONFIG_FILENAME).write_text(json.dumps(config.model_dump(mode="json")))
    return config


@pytest_asyncio.fixture
async def running_backend(
    lifecycle_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[asyncio.Task[None]]:
    """`main.run()` as a task, torn down by cancellation if the test didn't
    already stop it with a signal."""
    monkeypatch.setattr(manager_module, "ModbusBus", SlowFakeBus, raising=True)
    monkeypatch.setattr(manager_module, "BlueRDOSensor", _mock_sensor, raising=True)

    task = asyncio.create_task(main.run(), name="main-run")
    try:
        yield task
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _get_once_listening(
    session: aiohttp.ClientSession, url: str
) -> tuple[int, object]:
    """Poll `url` until the server accepts a connection, returning the first
    (status, JSON body) it answers with. Fails the test rather than hanging
    if the listener never comes up."""
    async with asyncio.timeout(5):
        while True:
            try:
                async with session.get(url) as resp:
                    return resp.status, await resp.json()
            except aiohttp.ClientConnectorError:
                await asyncio.sleep(0.005)


async def test_api_answers_during_the_startup_scan_and_after_it(
    running_backend: asyncio.Task[None],
    lifecycle_port: int,
    data_dir: Path,
) -> None:
    base_url = f"http://127.0.0.1:{lifecycle_port}"

    async with aiohttp.ClientSession() as session:
        # The listener must come up without waiting for the scan, which takes
        # (SCAN_MAX_ADDRESS - SCAN_MIN_ADDRESS + 1) * PROBE_DELAY_SECONDS.
        status, body = await _get_once_listening(session, f"{base_url}/api/sensors/current")
        assert status == 503
        assert isinstance(body, dict)
        assert "scan" in body["error"].lower()

        # ... and the same client sees real readings once the scan lands, with
        # no restart and no reconnect.
        async with asyncio.timeout(10):
            while True:
                async with session.get(f"{base_url}/api/sensors/current") as resp:
                    if resp.status == 200:
                        readings = await resp.json()
                        break
                await asyncio.sleep(0.02)

    assert [reading["sensor_address"] for reading in readings] == [PRESENT_ADDRESS]

    # The scan's mapping is persisted, so a later restart can run with
    # scan_on_startup=false.
    saved = json.loads((data_dir / CONFIG_FILENAME).read_text())
    assert saved["sensor_mapping"] == {CONVERTER: [PRESENT_ADDRESS]}


async def test_sigterm_shuts_down_cleanly_while_the_sampler_is_running(
    running_backend: asyncio.Task[None],
    lifecycle_port: int,
    data_dir: Path,
) -> None:
    base_url = f"http://127.0.0.1:{lifecycle_port}"

    async with aiohttp.ClientSession() as session:
        await _get_once_listening(session, f"{base_url}/api/data")

        # Wait for the scan to finish and the sampler to write real rows, so
        # teardown happens with every subsystem live.
        async with asyncio.timeout(10):
            while True:
                async with session.get(f"{base_url}/api/data") as resp:
                    rows = await resp.json()
                if rows:
                    break
                await asyncio.sleep(0.02)

    # The same path `PUT /api/config` uses to restart the process.
    os.kill(os.getpid(), signal.SIGTERM)

    async with asyncio.timeout(5):
        await running_backend

    assert running_backend.done() and running_backend.exception() is None
