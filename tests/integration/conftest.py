"""Fixtures shared by the integration tests: a `SensorManager` wired to
mocked sensors (via the root `sensor_factory` fixture) with its mapping
already applied, and the resulting aiohttp `Application`. Tests get an
HTTP client from pytest-aiohttp's `aiohttp_client` fixture:

    async def test_x(aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.get("/api/data")

`manager.start()` (which opens real `ModbusBus` serial connections) is
deliberately never called here — `save_sensor_mapping()` builds the address
table straight from `sensor_factory`'s mocks, so these tests never touch
real hardware, per AGENTS.md's "integration tests ... using mocked Blue RDO
sensors, but a real ... SQLite DB and real network calls to the API."
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from aiohttp import web

from api.app import create_app
from config.schema import Config
from db.database import Database
from hardware.manager import SensorFactory, SensorManager


@pytest_asyncio.fixture
async def sensor_manager(
    test_config: Config, sensor_factory: SensorFactory
) -> AsyncIterator[SensorManager]:
    manager = SensorManager(test_config, sensor_factory=sensor_factory)
    await manager.save_sensor_mapping(test_config.sensor_mapping)
    try:
        yield manager
    finally:
        await manager.aclose()


@pytest_asyncio.fixture
async def app(
    test_config: Config,
    sensor_manager: SensorManager,
    database: Database,
    data_dir: Path,
) -> web.Application:
    return create_app(test_config, sensor_manager, database, data_dir)
