"""aiohttp `Application` factory: wires routes, middleware, and
aiohttp-apigami Swagger docs together. Built once by `main.py`; the
resulting `Application` is handed to an `AppRunner`/`TCPSite` there.
"""

from aiohttp import web

from config.schema import Config
from db.database import Database
from hardware.manager import SensorManager


def create_app(config: Config, manager: SensorManager, database: Database) -> web.Application:
    """Build the fully wired `Application`.

    Stores `config`, `manager`, and `database` on `app` (e.g.
    `app["config"]`) for `api/routes.py` handlers to read, installs
    `api.middleware.create_concurrency_limit_middleware`/
    `create_timeout_middleware`, registers every route via
    `api.routes.register_routes`, and sets up `aiohttp-apigami`
    (`setup_aiohttp_apispec`) so `GET /api/docs` serves Swagger UI.
    """
    raise NotImplementedError
