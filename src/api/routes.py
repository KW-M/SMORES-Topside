"""HTTP handlers for every endpoint in ARCHITECTURE.md §7.

Each handler reads its dependencies (`Config`, `SensorManager`, `Database`,
`data_dir`) off `request.app` (wired by `api.app.create_app`), converts
to/from the marshmallow schemas in `api/schemas.py` at the request/response
boundary, and returns a `web.Response`. `aiohttp-apigami`'s `@docs`/
`@querystring_schema`/`@request_schema` decorators document the shapes at
`GET /api/docs`; no `validation_middleware` is installed, so every handler
parses/validates its own input and picks its own status code (see
`api/schemas.py`'s module docstring).
"""

import csv
import io
import logging
import os
import signal
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp_apigami import docs, querystring_schema, request_schema
from marshmallow import ValidationError
from pydantic import ValidationError as PydanticValidationError

from api.middleware import with_timeout
from api.schemas import (
    ConfigSchema,
    DataDeleteQuerySchema,
    DataRangeQuerySchema,
    ErrorSchema,
    ScanResultSchema,
    SensorReadingSchema,
)
from config.loader import get_config_path, save_config
from config.schema import Config
from constants import BusScanError, ConfigValidationError
from db.database import Database
from hardware.manager import SensorManager
from models.readings import SensorReading

logger = logging.getLogger(__name__)

_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SMORES-Topside</title>
</head>
<body>
<h1>SMORES-Topside</h1>
<p>Dissolved oxygen sensor array backend: reads N Blue RDO Modbus sensors
over one or more RS485-to-USB converters, saves readings to SQLite on a
timer, and serves them over this HTTP API.</p>
<ul>
<li><a href="/api/docs">API documentation (Swagger UI)</a></li>
<li><a href="/api/sensors/current">GET /api/sensors/current</a> — fresh sensor poll</li>
<li><a href="/api/data">GET /api/data</a> — stored readings</li>
<li><a href="/api/config">GET /api/config</a> — current configuration</li>
</ul>
</body>
</html>
"""


def _error_response(status: int, error: str, detail: str | None = None) -> web.Response:
    """Build a structured JSON error body shaped like `api.schemas.ErrorSchema`."""
    body: dict[str, str] = {"error": error}
    if detail is not None:
        body["detail"] = detail
    return web.json_response(ErrorSchema().dump(body), status=status)


def _validation_error_response(exc: ValidationError) -> web.Response:
    return _error_response(400, "Invalid request parameters", str(exc.messages))


def _dump_readings(readings: list[SensorReading]) -> list[dict[str, Any]]:
    schema = SensorReadingSchema(many=True)
    result: list[dict[str, Any]] = schema.dump([reading.model_dump() for reading in readings])
    return result


async def index(request: web.Request) -> web.Response:
    """`GET /` — basic HTML page describing the system, linking to `/api/docs`."""
    return web.Response(text=_INDEX_HTML, content_type="text/html")


@docs(
    tags=["sensors"],
    summary="Fresh sensor poll",
    description=(
        "Triggers a fresh, lock-guarded poll of every configured sensor via "
        "hardware.manager.query_all_sensors(). Does not write a DB row. "
        "Unreachable sensors are still included, with -9999 fields and a "
        "non-OK status. Bounded by config.poll_timeout_seconds."
    ),
    responses={
        200: {
            "schema": SensorReadingSchema(many=True),
            "description": "One reading per configured sensor.",
        },
        503: {"schema": ErrorSchema, "description": "A bus scan is currently in progress."},
        504: {"schema": ErrorSchema, "description": "Poll exceeded poll_timeout_seconds."},
    },
)
async def get_current_readings(request: web.Request) -> web.Response:
    """`GET /api/sensors/current` — lock-guarded fresh poll of every
    configured sensor via `hardware.manager.SensorManager.query_all_sensors()`.
    Does not write a DB row. Returns the same JSON shape as a stored row,
    including `constants.UNREADABLE_VALUE`/error status for unreachable
    sensors. Bounded by `config.poll_timeout_seconds`
    (`api.middleware.with_timeout`); exceeding it yields `504`.
    """
    manager: SensorManager = request.app["manager"]
    try:
        readings = await manager.query_all_sensors()
    except BusScanError as exc:
        return _error_response(503, "Bus scan in progress", str(exc))
    return web.json_response(_dump_readings(readings))


@docs(
    tags=["data"],
    summary="Query stored readings",
    description=(
        "Stored readings with start <= timestamp_utc <= end. Either bound "
        "may be omitted; omitting both returns every stored row."
    ),
    responses={
        200: {"schema": SensorReadingSchema(many=True), "description": "Matching stored readings."},
        400: {"schema": ErrorSchema, "description": "Invalid start/end query parameters."},
    },
)
@querystring_schema(DataRangeQuerySchema)
async def get_data(request: web.Request) -> web.Response:
    """`GET /api/data` — stored readings as JSON.

    Query params:
        start, end: optional inclusive UTC unix timestamps.
    """
    database: Database = request.app["database"]
    try:
        query = DataRangeQuerySchema().load(request.query)
    except ValidationError as exc:
        return _validation_error_response(exc)
    readings = await database.get_readings(start=query["start"], end=query["end"])
    return web.json_response(_dump_readings(readings))


@docs(
    tags=["data"],
    summary="Query stored readings as CSV",
    description="Same rows as GET /api/data, CSV-formatted. Omitting start/end returns every row.",
    responses={
        200: {"description": "CSV file, one row per stored reading."},
        400: {"schema": ErrorSchema, "description": "Invalid start/end query parameters."},
    },
)
@querystring_schema(DataRangeQuerySchema)
async def get_data_csv(request: web.Request) -> web.Response:
    """`GET /api/data/csv` — same rows as `get_data`, CSV-formatted.
    Omitting `start`/`end` returns every stored row."""
    database: Database = request.app["database"]
    try:
        query = DataRangeQuerySchema().load(request.query)
    except ValidationError as exc:
        return _validation_error_response(exc)
    readings = await database.get_readings(start=query["start"], end=query["end"])

    row_schema = SensorReadingSchema()
    fieldnames = list(row_schema.fields.keys())
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for reading in readings:
        writer.writerow(row_schema.dump(reading.model_dump()))

    return web.Response(
        text=buffer.getvalue(),
        content_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="smores_data.csv"'},
    )


@docs(
    tags=["data"],
    summary="Delete old readings",
    description="Deletes stored rows with timestamp_utc < cutoff.",
    responses={
        200: {"description": "Number of rows deleted, e.g. {\"deleted\": 42}."},
        400: {"schema": ErrorSchema, "description": "Missing or invalid cutoff parameter."},
    },
)
@querystring_schema(DataDeleteQuerySchema)
async def delete_data(request: web.Request) -> web.Response:
    """`DELETE /api/data` — delete stored rows with `timestamp_utc < cutoff`.

    Query params:
        cutoff: required UTC unix timestamp.

    Responses:
        400 if `cutoff` is missing/invalid.
    """
    database: Database = request.app["database"]
    try:
        query = DataDeleteQuerySchema().load(request.query)
    except ValidationError as exc:
        return _validation_error_response(exc)
    deleted = await database.delete_before(query["cutoff"])
    return web.json_response({"deleted": deleted})


@docs(
    tags=["config"],
    summary="Get current configuration",
    description="Returns the current contents of config.json, verbatim.",
    responses={200: {"schema": ConfigSchema, "description": "Current configuration."}},
)
async def get_config(request: web.Request) -> web.Response:
    """`GET /api/config` — current `config.json` contents, verbatim."""
    config: Config = request.app["config"]
    return web.json_response(ConfigSchema().dump(config.model_dump()))


@docs(
    tags=["config"],
    summary="Replace configuration and restart",
    description=(
        "Validates and persists new config JSON, then sends this process "
        "SIGTERM so systemd's Restart=always brings it back up with the new "
        "config — the same shutdown path used for a normal SIGTERM/SIGINT, "
        "per AGENTS.md's 'one way to stop.'"
    ),
    responses={
        200: {"schema": ConfigSchema, "description": "The configuration that was saved."},
        400: {
            "schema": ErrorSchema,
            "description": "Invalid JSON body or config validation failure.",
        },
    },
)
@request_schema(ConfigSchema, location="json")
async def put_config(request: web.Request) -> web.Response:
    """`PUT /api/config` — validate the posted JSON against `Config`,
    persist it via `config.loader.save_config`, then trigger a full
    process restart (`sys.exit`, picked up by systemd `Restart=always`).

    Responses:
        400 if the posted JSON fails `Config` validation.
    """
    try:
        payload = await request.json()
    except ValueError as exc:
        return _error_response(400, "Invalid JSON body", str(exc))

    try:
        new_config = Config.model_validate(payload)
    except PydanticValidationError as exc:
        return _error_response(400, "Invalid configuration", str(exc))

    data_dir: Path = request.app["data_dir"]
    try:
        save_config(new_config, get_config_path(data_dir))
    except ConfigValidationError as exc:
        logger.exception("save_config rejected an already-validated Config")
        return _error_response(500, "Internal configuration error", str(exc))

    logger.info("Config replaced via PUT /api/config; sending SIGTERM to restart")
    logging.shutdown()
    os.kill(os.getpid(), signal.SIGTERM)

    return web.json_response(ConfigSchema().dump(new_config.model_dump()))


@docs(
    tags=["sensors"],
    summary="Scan Modbus buses",
    description=(
        "Scans every configured RS485-to-USB converter's Modbus address "
        "space for present sensors, persists the resulting mapping to "
        "config.json (overwriting any existing sensor_mapping), and "
        "returns it. Same underlying scan used at startup."
    ),
    responses={
        200: {
            "schema": ScanResultSchema(many=True),
            "description": "One result per configured converter.",
        },
        503: {
            "schema": ErrorSchema,
            "description": "A scan is already in progress, or a converter is unusable.",
        },
        504: {
            "schema": ErrorSchema,
            "description": "Scan exceeded scan_probe_timeout_seconds * number of converters.",
        },
    },
)
async def scan_buses(request: web.Request) -> web.Response:
    """`GET /api/scan` — scan every configured converter for present
    Modbus addresses via `hardware.manager.SensorManager.scan_all_buses()`,
    persist the resulting mapping to `config.json` (overwriting any
    existing `sensor_mapping`), and return it. Bounded by
    `config.scan_probe_timeout_seconds * len(config.serial_port_devices)`.
    """
    manager: SensorManager = request.app["manager"]
    config: Config = request.app["config"]
    data_dir: Path = request.app["data_dir"]

    try:
        results = await manager.scan_all_buses()
    except BusScanError as exc:
        return _error_response(503, "Bus scan failed", str(exc))

    config.sensor_mapping = manager.get_sensor_mapping()
    try:
        save_config(config, get_config_path(data_dir))
    except ConfigValidationError as exc:
        logger.exception("save_config rejected the post-scan config")
        return _error_response(500, "Internal configuration error", str(exc))

    schema = ScanResultSchema(many=True)
    return web.json_response(schema.dump([result.model_dump() for result in results]))


def register_routes(app: web.Application) -> None:
    """Add every route above to `app`, called once by `api.app.create_app`.

    `/api/sensors/current` and `/api/scan` get a per-route timeout override
    (`poll_timeout_seconds`, `scan_probe_timeout_seconds * num converters`)
    applied via a direct `with_timeout(...)` call against the live `Config`
    on `app`, rather than a `@with_timeout(...)` decorator above the `def` —
    the override value depends on the config loaded at process startup,
    which isn't available yet when this module (and its decorators) are
    first imported.
    """
    config: Config = app["config"]

    app.router.add_get("/", index)
    app.router.add_get(
        "/api/sensors/current",
        with_timeout(config.poll_timeout_seconds)(get_current_readings),
    )
    app.router.add_get("/api/data", get_data)
    app.router.add_get("/api/data/csv", get_data_csv)
    app.router.add_delete("/api/data", delete_data)
    app.router.add_get("/api/config", get_config)
    app.router.add_put("/api/config", put_config)

    num_converters = max(len(config.serial_port_devices), 1)
    scan_timeout = config.scan_probe_timeout_seconds * num_converters
    app.router.add_get("/api/scan", with_timeout(scan_timeout)(scan_buses))
