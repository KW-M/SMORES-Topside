"""HTTP handlers for every endpoint in ARCHITECTURE.md §7.

Each handler reads its dependencies (`Config`, `SensorManager`, `Database`)
off `request.app` (wired by `api.app.create_app`), converts to/from the
marshmallow schemas in `api/schemas.py` at the request/response boundary,
and returns a `web.Response`/`web.StreamResponse`. `aiohttp-apigami`
request/response documentation decorators are added in a later step.
"""

from aiohttp import web


async def index(request: web.Request) -> web.Response:
    """`GET /` — basic HTML page describing the system, linking to `/api/docs`."""
    raise NotImplementedError


async def get_current_readings(request: web.Request) -> web.Response:
    """`GET /api/sensors/current` — lock-guarded fresh poll of every
    configured sensor via `hardware.manager.SensorManager.query_all_sensors()`.
    Does not write a DB row. Returns the same JSON shape as a stored row,
    including `constants.UNREADABLE_VALUE`/error status for unreachable
    sensors. Bounded by `config.poll_timeout_seconds`
    (`api.middleware.with_timeout`); exceeding it yields `504`.
    """
    raise NotImplementedError


async def get_data(request: web.Request) -> web.Response:
    """`GET /api/data` — stored readings as JSON.

    Query params:
        start, end: optional inclusive UTC unix timestamps.
    """
    raise NotImplementedError


async def get_data_csv(request: web.Request) -> web.Response:
    """`GET /api/data/csv` — same rows as `get_data`, CSV-formatted.
    Omitting `start`/`end` returns every stored row."""
    raise NotImplementedError


async def delete_data(request: web.Request) -> web.Response:
    """`DELETE /api/data` — delete stored rows with `timestamp_utc < cutoff`.

    Query params:
        cutoff: required UTC unix timestamp.

    Responses:
        400 if `cutoff` is missing/invalid.
    """
    raise NotImplementedError


async def get_config(request: web.Request) -> web.Response:
    """`GET /api/config` — current `config.json` contents, verbatim."""
    raise NotImplementedError


async def put_config(request: web.Request) -> web.Response:
    """`PUT /api/config` — validate the posted JSON against `Config`,
    persist it via `config.loader.save_config`, then trigger a full
    process restart (`sys.exit`, picked up by systemd `Restart=always`).

    Responses:
        400 if the posted JSON fails `Config` validation.
    """
    raise NotImplementedError


async def scan_buses(request: web.Request) -> web.Response:
    """`GET /api/scan` — scan every configured converter for present
    Modbus addresses via `hardware.manager.SensorManager.scan_all_buses()`,
    persist the resulting mapping to `config.json` (overwriting any
    existing `sensor_mapping`), and return it. Bounded by
    `config.scan_probe_timeout_seconds * len(config.serial_port_devices)`.
    """
    raise NotImplementedError


def register_routes(app: web.Application) -> None:
    """Add every route above to `app`, called once by `api.app.create_app`."""
    raise NotImplementedError
