"""REST/JSON HTTP API subsystem (aiohttp + aiohttp-apigami)."""

from api.app import create_app

__all__ = ["create_app"]
