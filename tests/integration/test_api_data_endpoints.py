"""Integration tests for `GET /api/data`, `GET /api/data/csv`, and
`DELETE /api/data`: a real aiohttp app over a real (temp-file) SQLite DB —
no mocked sensors involved, since these endpoints only ever talk to
`db.database.Database`.
"""

import csv
import io

from aiohttp import web
from aiohttp.pytest_plugin import AiohttpClient

from db.database import Database
from models.readings import SensorReading


def _reading(sensor_address: int, timestamp_utc: float) -> SensorReading:
    return SensorReading(
        sensor_address=sensor_address,
        serial_converter_id="/dev/mock-bus0",
        timestamp_utc=timestamp_utc,
        temperature_c=20.0 + sensor_address,
        do_percent_saturation=95.0,
        do_partial_pressure_torr=150.0,
        do_mg_l=8.0,
        status_code=0,
        status_text="OK",
    )


async def _seed(database: Database) -> None:
    for address, timestamp in ((1, 10.0), (2, 20.0), (1, 30.0), (2, 40.0)):
        await database.insert_reading(_reading(address, timestamp))


async def test_get_data_with_no_rows_returns_empty_list(
    aiohttp_client: AiohttpClient, app: web.Application
) -> None:
    client = await aiohttp_client(app)

    resp = await client.get("/api/data")

    assert resp.status == 200
    assert await resp.json() == []


async def test_get_data_with_no_range_returns_every_row(
    aiohttp_client: AiohttpClient, app: web.Application, database: Database
) -> None:
    await _seed(database)
    client = await aiohttp_client(app)

    resp = await client.get("/api/data")

    body = await resp.json()
    assert [row["timestamp_utc"] for row in body] == [10.0, 20.0, 30.0, 40.0]


async def test_get_data_range_is_inclusive_of_both_bounds(
    aiohttp_client: AiohttpClient, app: web.Application, database: Database
) -> None:
    await _seed(database)
    client = await aiohttp_client(app)

    resp = await client.get("/api/data", params={"start": "20", "end": "30"})

    body = await resp.json()
    assert [row["timestamp_utc"] for row in body] == [20.0, 30.0]


async def test_get_data_rejects_non_numeric_start(
    aiohttp_client: AiohttpClient, app: web.Application, database: Database
) -> None:
    await _seed(database)
    client = await aiohttp_client(app)

    resp = await client.get("/api/data", params={"start": "not-a-number"})

    assert resp.status == 400
    body = await resp.json()
    assert "error" in body


async def test_get_data_csv_returns_matching_rows(
    aiohttp_client: AiohttpClient, app: web.Application, database: Database
) -> None:
    await _seed(database)
    client = await aiohttp_client(app)

    resp = await client.get("/api/data/csv", params={"start": "20", "end": "30"})

    assert resp.status == 200
    assert resp.content_type == "text/csv"
    text = await resp.text()
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [row["timestamp_utc"] for row in rows] == ["20.0", "30.0"]
    assert [row["sensor_address"] for row in rows] == ["2", "1"]


async def test_get_data_csv_with_no_rows_still_has_a_header(
    aiohttp_client: AiohttpClient, app: web.Application
) -> None:
    client = await aiohttp_client(app)

    resp = await client.get("/api/data/csv")

    text = await resp.text()
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    assert "sensor_address" in header
    assert list(reader) == []


async def test_delete_data_removes_rows_before_cutoff(
    aiohttp_client: AiohttpClient, app: web.Application, database: Database
) -> None:
    await _seed(database)
    client = await aiohttp_client(app)

    resp = await client.delete("/api/data", params={"cutoff": "25"})

    assert resp.status == 200
    body = await resp.json()
    assert body["deleted"] == 2
    remaining = await database.get_readings()
    assert [r.timestamp_utc for r in remaining] == [30.0, 40.0]


async def test_delete_data_requires_cutoff(
    aiohttp_client: AiohttpClient, app: web.Application, database: Database
) -> None:
    await _seed(database)
    client = await aiohttp_client(app)

    resp = await client.delete("/api/data")

    assert resp.status == 400
