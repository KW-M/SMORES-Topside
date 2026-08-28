"""marshmallow schemas for `aiohttp-apigami` request/response docs and
validation at the HTTP boundary.

Deliberately separate from the pydantic models in `models/readings.py` and
`config/schema.py` (those remain the single source of truth everywhere
else in the codebase) because aiohttp-apigami's `@request_schema`/
`@response_schema` decorators and Swagger generation require marshmallow
schema classes — see ARCHITECTURE.md §2's "two schema systems" note.
Field definitions mirroring `SensorReading`/`ScanResult`/`Config` are added
in AGENTS.md implementation step 6, alongside the routes that use them.
"""

from marshmallow import Schema


class SensorReadingSchema(Schema):
    """Mirrors `models.readings.SensorReading`. Response body shape for
    `/api/sensors/current` (list) and `/api/data` (list)."""


class ScanResultSchema(Schema):
    """Mirrors `models.readings.ScanResult`. Response body shape for
    `/api/scan` (list, one per converter)."""


class SensorMappingSchema(Schema):
    """converter_id -> [sensor addresses], as persisted in
    `config.sensor_mapping` and returned in part by `/api/scan`."""


class ConfigSchema(Schema):
    """Mirrors `config.schema.Config`. Request/response body shape for
    `GET /api/config` and `PUT /api/config`."""


class ErrorSchema(Schema):
    """Structured JSON error body shape for non-2xx responses, e.g.
    `{"error": "...", "detail": "..."}`."""
