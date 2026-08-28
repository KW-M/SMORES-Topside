# SMORES-Topside Architecture

Step 2 deliverable per `AGENTS.md`. This document fixes the module layout,
config schema, and DB row layout that all later steps (3–12) implement
against. No application logic exists yet — see `src/main.py` (step 1
placeholder).

## 1. Module tree

```
src/                             Python package (root), imported as top-level modules
  __init__.py
  main.py                        Entry point: asyncio loop, config load, subsystem
                                  lifecycle (start/stop), signal handlers
  constants.py                   UNREADABLE_VALUE = -9999, exception classes:
                                  SensorTimeoutError, SensorReadError, BusScanError,
                                  ConfigValidationError
  sampler.py                     Periodic sampling task: drift-corrected asyncio.sleep
                                  loop that calls hardware.manager.query_all_sensors()
                                  and db.database.insert_reading() on config.sample_interval_seconds

  config/
    __init__.py
    schema.py                    Pydantic Config model + nested models (§3), defaults
    loader.py                    load_config(path), save_config(path, config) (atomic
                                  write: write to temp file + os.replace), raises
                                  ConfigValidationError on bad JSON/schema

  models/
    __init__.py
    readings.py                  SensorReading, ScanResult — pydantic models shared by
                                  hardware, db, and api layers (single source of truth
                                  for the "reading" shape; §5)

  hardware/                      Blue RDO sensor array subsystem (3 core files + support)
    __init__.py
    rdo_blue_constants.py        Register map constants transcribed from
                                  documentation/RDO-Blue-Manual-Modbus-Interface.md.
                                  Every constant not directly confirmed by that doc is
                                  tagged `# ASSUMED` at its definition.
    rdo_blue_interface.py        BlueRDOInterface(abc.ABC): read_dissolved_o2_percent,
                                  read_dissolved_o2_mg_l, read_temperature_c,
                                  read_partial_pressure_torr, read_status,
                                  read_serial_num, read_device_id, read_all()
    rdo_blue.py                  BlueRDOSensor(BlueRDOInterface): real implementation,
                                  takes a ModbusBus + integer address
    modbus_bus.py                ModbusBus: one instance per RS485-to-USB converter.
                                  Wraps pymodbus AsyncModbusSerialClient; serializes
                                  all requests on the port behind an asyncio.Lock
                                  (RS485 is half-duplex — a request blocks until its
                                  response arrives before the next request is sent)
    manager.py                   High-level API: scan_all_buses(), get_sensor_mapping(),
                                  save_sensor_mapping(), query_all_sensors(),
                                  query_sensor(address). Owns the ModbusBus instances
                                  and the address→(bus, BlueRDOSensor) table.

  db/
    __init__.py
    database.py                  aiosqlite CRUD only: init_schema(), insert_reading(),
                                  get_readings(start, end), delete_before(cutoff),
                                  delete_oldest(batch_size), count_rows()
    retention.py                 Disk-space-based pruning policy: periodic task using
                                  psutil to check free space, calls
                                  database.delete_oldest(50) in a loop until free space
                                  recovers

  api/
    __init__.py
    app.py                       aiohttp Application factory: registers routes,
                                  installs middleware, wires aiohttp-apigami
                                  (setup_aiohttp_apispec, swagger UI at /api/docs)
    middleware.py                @web.middleware concurrency_limit_middleware
                                  (asyncio.Semaphore(5), 503 + Retry-After when full)
                                  and timeout_middleware (504 on per-route timeout)
    routes.py                    Handlers for all endpoints in §6
    schemas.py                   marshmallow schemas for aiohttp-apigami request/
                                  response docs (see §4 note on two schema systems)

documentation/
  RDO-Blue-Manual-Modbus-Interface.md   (existing vendor doc)
  aiohttp-apigami.README.md             (existing vendor doc)

tests/
  __init__.py
  conftest.py                    Shared fixtures: tmp data dir via env var, mocked
                                  sensors, test config
  mocks/
    __init__.py
    mock_rdo_blue.py             MockBlueRDOSensor(BlueRDOInterface): returns
                                  configurable canned values, can simulate timeouts/
                                  errors per address for negative-path tests
  unit/
    __init__.py
    test_config.py
    test_models.py
    test_rdo_blue.py
    test_modbus_bus.py
    test_database.py
    test_retention.py
  integration/
    __init__.py
    test_api_sensors_current.py
    test_api_data_endpoints.py
    test_sampler_to_db.py

ARCHITECTURE.md                  (this file)
AGENTS.md / CLAUDE.md            Functional spec (existing)
README.md                        (existing)
Pipfile / Pipfile.lock           (existing)
```

Rationale for a few naming choices:

- `hardware/` holds five files, not three — the "3 files" in `AGENTS.md`
  are `manager.py`, `modbus_bus.py`, and `rdo_blue.py`. `rdo_blue_constants.py`
  and `rdo_blue_interface.py` are support files explicitly called for
  elsewhere in `AGENTS.md` (dedicated constants file; abstract interface
  satisfied by real and mock implementations).
- `sampler.py` (periodic polling → DB) lives at the top level of `src/`,
  not inside `db/` or `hardware/`, per the note that periodic
  polling/saving is a higher-level concern than either subsystem.

## 2. External library usage

| Library                | Used in                                      | Purpose                                                             |
| ----------------------- | --------------------------------------------- | -------------------------------------------------------------------- |
| pymodbus (>=3.6)        | `hardware/modbus_bus.py`                      | `AsyncModbusSerialClient` — async Modbus RTU client over serial     |
| pyserial-asyncio        | `hardware/modbus_bus.py` (transitively, via pymodbus's asyncio serial transport) | Async serial I/O backing pymodbus |
| aiosqlite               | `db/database.py`                              | Non-blocking SQLite access from the event loop                      |
| psutil                  | `db/retention.py`                             | `psutil.disk_usage()` for free-space checks                         |
| pydantic (v2)           | `config/schema.py`, `models/readings.py`      | Typed config schema + validation; shared reading/scan-result models |
| aiohttp                 | `api/app.py`, `api/routes.py`, `api/middleware.py`, `main.py` | HTTP server, routing, middleware, `AppRunner` lifecycle   |
| aiohttp-apigami         | `api/app.py`, `api/routes.py`                 | `@docs`/`@request_schema`/`@response_schema` decorators, Swagger UI at `/api/docs` (maintained `aiohttp-apispec` fork, py3.13-compatible) |
| marshmallow             | `api/schemas.py`                              | Schema classes consumed by aiohttp-apigami (see note below)          |
| pytest / pytest-asyncio / pytest-aiohttp | `tests/`                    | Async test runner, `aiohttp_client` fixture for integration tests    |
| freezegun               | `tests/unit/test_retention.py`, `tests/integration/test_sampler_to_db.py` | Deterministic control of `sample_interval_seconds` timing checks |

**Two schema systems, deliberately:** `config/schema.py` and
`models/readings.py` use **pydantic**, the single source of truth for
config validation and the reading/scan-result shape used internally and
for DB rows. `api/schemas.py` uses **marshmallow**, because
aiohttp-apigami's `@request_schema`/`@response_schema` decorators and
Swagger generation require marshmallow schema classes. The marshmallow
schemas in `api/schemas.py` mirror the pydantic models field-for-field
purely for request validation and doc generation at the HTTP boundary;
`routes.py` converts to/from the pydantic models immediately at the
handler boundary so the rest of the codebase only ever sees pydantic
models/dataclasses.

## 3. Configuration

Config file: `<data_dir>/config.json`. `data_dir` defaults to
`~/SMORES_Data`, overridable via the `SMORES_DATA_DIR` environment
variable (used by integration tests to avoid touching real data).
DB file: `<data_dir>/smores.db`.

`GET /api/config` returns this file's contents as-is. `PUT /api/config`
validates the posted JSON against the same pydantic model, writes it
atomically, then exits the process (systemd `Restart=always` brings it
back up with the new config).

| Field                            | Type                    | Default            | Notes                                                                 |
| --------------------------------- | ----------------------- | ------------------- | ---------------------------------------------------------------------- |
| `serial_port_devices`                      | `list[str]`             | `[]`                 | Stable device identifiers for RS485 to usb serial adapters, e.g. `/dev/serial/by-id/usb-FTDI_...-port0` |
| `sensor_mapping`                  | `dict[str, list[int]]`  | `{}`                 | converter device path → list of Modbus addresses present on it. Populated by startup scan or `GET /api/scan`; used directly (no scan) when `scan_on_startup` is false |
| `scan_on_startup`                 | `bool`                  | `true`               | If false, trust `sensor_mapping` as-is and skip the startup bus scan   |
| `sample_interval_seconds`         | `float`                 | `60.0`               | Interval between DB-writing polls (drift-corrected loop in `sampler.py`) |
| `modbus_baudrate`                 | `int`                   | `19200`              | Serial params match RDO Blue factory defaults (per vendor doc)        |
| `modbus_parity`                   | `"N"\|"E"\|"O"`         | `"E"`                | ″                                                                      |
| `modbus_stopbits`                 | `int`                   | `1`                  | ″                                                                      |
| `modbus_bytesize`                 | `int`                   | `8`                  | ″                                                                      |
| `modbus_request_timeout_seconds`  | `float`                 | `1.0`                | Per-Modbus-request (single register read) timeout — "serial timeout" |
| `sensor_read_timeout_seconds`     | `float`                 | `3.0`                | Timeout for one sensor's full multi-register read (`read_all()`) — "sensor timeout" |
| `scan_probe_timeout_seconds`      | `float`                 | `1.0`                | Timeout probing the modbuss address space of one RS485-to-usb serial converter.                    |
| `api_host`                        | `str`                   | `"0.0.0.0"`          | aiohttp bind address                                                   |
| `api_port`                        | `int`                   | `8080`               | aiohttp bind port                                                      |
| `api_request_timeout_seconds`     | `float`                 | `10.0`               | Default per-request timeout for most endpoints (504 on expiry)         |
| `poll_timeout_seconds`            | `float`                 | `8.0`                | Timeout specific to `GET /api/sensors/current` (spec calls this out by name) |
| `api_max_concurrent_clients`      | `int`                   | `5`                  | `asyncio.Semaphore` size in `api/middleware.py`                        |
| `min_free_disk_space_mb`                | `int`                   | `500`                | Free space floor; below this, retention starts deleting oldest rows    |
| `disk_check_interval_seconds`     | `float`                 | `300.0`              | How often `db/retention.py` checks free space                          |
| `retention_delete_batch_size`            | `int`                   | `50`                 | Rows deleted per batch until free space recovers (spec-mandated value, kept configurable) |
| `log_level`                       | `str`                   | `"INFO"`             | Passed to `logging.basicConfig`                                        |

`modbus_baudrate`/`parity`/`stopbits`/`bytesize` aren't named explicitly
in `AGENTS.md`'s config bullet list but are required to construct the
serial client and match the vendor doc's factory defaults (RTU, 19200,
8E1) — included here for completeness and to keep them out of hardcoded
logic per the "keep register/config assumptions easy to patch" guidance.

Example `config.json`:

```json
{
  "serial_port_devices": [
    "/dev/serial/by-id/usb-FTDI_USB-RS485_Cable-if00-port0",
    "/dev/serial/by-id/usb-FTDI_USB-RS485_Cable-if01-port0"
  ],
  "sensor_mapping": {
    "/dev/serial/by-id/usb-FTDI_USB-RS485_Cable-if00-port0": [1, 2, 3],
    "/dev/serial/by-id/usb-FTDI_USB-RS485_Cable-if01-port0": [4, 5]
  },
  "scan_on_startup": true,
  "sample_interval_seconds": 60.0,
  "modbus_baudrate": 19200,
  "modbus_parity": "E",
  "modbus_stopbits": 1,
  "modbus_bytesize": 8,
  "modbus_request_timeout_seconds": 1.0,
  "sensor_read_timeout_seconds": 3.0,
  "scan_probe_timeout_seconds": 1.0,
  "api_host": "0.0.0.0",
  "api_port": 8080,
  "api_request_timeout_seconds": 10.0,
  "poll_timeout_seconds": 8.0,
  "api_max_concurrent_clients": 5,
  "min_free_disk_space_mb": 500,
  "disk_check_interval_seconds": 300.0,
  "retention_delete_batch_size": 50,
  "log_level": "INFO"
}
```

## 4. Concurrency & locking model

- **Per-bus serialization:** each `ModbusBus` (one per RS485-to-USB
  converter) holds an `asyncio.Lock`. Every request on that bus — scan
  probes, scheduled sampling reads, and `/api/sensors/current` reads —
  acquires the bus's lock for the duration of that single request/response
  round trip, then releases it. Concurrent callers (e.g. the sampler loop
  and an API request landing at the same moment) interleave at the
  request level in a nondeterministic but safe order; each caller still
  gets the correct response for the request it made, because the lock
  scope is exactly one request/response pair and callers await their own
  awaitable.
- **Scan mutual exclusion:** `hardware/manager.py` holds a single
  module-level `asyncio.Lock` (or in-progress `asyncio.Task` reference)
  around `scan_all_buses()`. A scan request arriving while one is already
  running does not start a second concurrent scan; `AGENTS.md` allows
  either erroring or waiting on the in-progress result — this project
  waits on the existing task and returns its result to avoid needlessly
  failing a second caller.
- **API concurrency cap:** `api/middleware.py`'s
  `concurrency_limit_middleware` uses an `asyncio.Semaphore(config.api_max_concurrent_clients)`.
  Acquisition is non-blocking (`asyncio.wait_for(sem.acquire(), timeout=0)`):
  if the semaphore has no capacity *right now*, the middleware immediately
  returns `503` with a `Retry-After` header rather than queuing the
  request. This satisfies "rather than being silently dropped or queued
  indefinitely."
- **Per-route timeout:** `timeout_middleware` wraps the handler call in
  `asyncio.wait_for(handler(request), timeout=route_timeout)`, returning
  `504` on `TimeoutError`. `route_timeout` defaults to
  `config.api_request_timeout_seconds`; `/api/sensors/current` is
  registered with an override of `config.poll_timeout_seconds` (routes
  carry their timeout as route metadata read by the middleware).

## 5. Shared data models (`models/readings.py`)

```python
class SensorReading(BaseModel):
    row_id: int | None          # DB autoincrement id; None for a fresh, unsaved poll
    sensor_address: int
    serial_converter_id: str
    timestamp_utc: float        # unix epoch seconds, UTC
    temperature_c: float        # constants.UNREADABLE_VALUE (-9999) if unreadable
    do_percent_saturation: float
    do_partial_pressure_torr: float
    do_mg_l: float
    status_code: int            # worst-case Data Quality ID across the 4 parameters,
                                 # or a negative internal code for timeout/unreachable
    status_text: str            # human-readable, e.g. "OK", "Sensor timeout",
                                 # "temperature: Error reading parameter"

class ScanResult(BaseModel):
    converter_id: str
    sensor_addresses: list[int]
    scanned_at: float           # unix epoch seconds
```

`SensorReading` is the single shape used by: `BlueRDOSensor.read_all()`
output (via `hardware/manager.py`), the DB row (`db/database.py` maps
1:1 to/from this model), the JSON body of `/api/sensors/current` and
`/api/data`, and each CSV row of `/api/data/csv`.

## 6. DB row layout (`db/database.py`)

Single table, `sensor_readings`:

| Column                     | Type      | Constraints          | Notes                                                        |
| --------------------------- | --------- | --------------------- | -------------------------------------------------------------- |
| `id`                        | INTEGER   | PRIMARY KEY AUTOINCREMENT | Monotonically incrementing row index/counter (spec-mandated); `AUTOINCREMENT` guarantees no id reuse even after deletes |
| `timestamp_utc`             | REAL      | NOT NULL, indexed     | Unix epoch seconds, UTC                                        |
| `sensor_address`            | INTEGER   | NOT NULL              | Globally unique Modbus address                                 |
| `converter_id`              | TEXT      | NOT NULL              | Which RS485-to-USB converter this sensor was read from          |
| `temperature_c`             | REAL      | NOT NULL              | `-9999` (constants.UNREADABLE_VALUE) if unreadable              |
| `do_percent_saturation`     | REAL      | NOT NULL              | ″                                                                |
| `do_partial_pressure_torr`  | REAL      | NOT NULL              | ″                                                                |
| `do_mg_l`                   | REAL      | NOT NULL              | ″                                                                |
| `status_code`               | INTEGER   | NOT NULL              | Worst-case Data Quality ID, or negative internal code for timeout/unreachable |
| `status_text`               | TEXT      | NOT NULL              | Human-readable status/error summary                             |

```sql
CREATE TABLE IF NOT EXISTS sensor_readings (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc           REAL    NOT NULL,
    sensor_address          INTEGER NOT NULL,
    converter_id            TEXT    NOT NULL,
    temperature_c           REAL    NOT NULL,
    do_percent_saturation   REAL    NOT NULL,
    do_partial_pressure_torr REAL   NOT NULL,
    do_mg_l                 REAL    NOT NULL,
    status_code             INTEGER NOT NULL,
    status_text             TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sensor_readings_timestamp ON sensor_readings(timestamp_utc);
```

One row is written **per sensor, per sampling interval** (not one row
per poll cycle) — i.e. a system with 27 sensors and a 60s interval writes
27 rows/minute.

**Retention & disk space (`db/retention.py`):** SQLite does not shrink
its on-disk file on `DELETE` by default, which would make free-space-based
pruning ineffective. The DB is opened with:

```sql
PRAGMA journal_mode = WAL;
PRAGMA auto_vacuum = INCREMENTAL;
```

`retention.py`'s periodic task (every `disk_check_interval_seconds`)
checks `psutil.disk_usage(data_dir).free` against `min_free_disk_mb`; if
below threshold, it repeatedly calls `database.delete_oldest(retention_batch_size)`
followed by `PRAGMA incremental_vacuum(retention_batch_size)` to actually
release freed pages back to the filesystem, re-checking free space after
each batch, until free space recovers or the table is empty.

## 7. API endpoints → handlers

All handlers live in `api/routes.py`; request/response shapes documented
via `api/schemas.py` marshmallow schemas + `@docs` decorators, surfaced
at `GET /api/docs`.

| Route                     | Method | Handler                | Timeout source                     |
| -------------------------- | ------ | ------------------------ | ------------------------------------ |
| `/`                        | GET    | `index`                  | `api_request_timeout_seconds`        |
| `/api/docs`                | GET    | (aiohttp-apigami/swagger, auto-registered) | —            |
| `/api/sensors/current`     | GET    | `get_current_readings`   | `poll_timeout_seconds` (override)    |
| `/api/data`                | GET    | `get_data`                | `api_request_timeout_seconds`        |
| `/api/data/csv`            | GET    | `get_data_csv`            | `api_request_timeout_seconds`        |
| `/api/data`                | DELETE | `delete_data`             | `api_request_timeout_seconds`        |
| `/api/config`              | GET    | `get_config`              | `api_request_timeout_seconds`        |
| `/api/config`              | PUT    | `put_config`              | `api_request_timeout_seconds`        |
| `/api/scan`                | GET    | `scan_buses`              | `scan_probe_timeout_seconds` * number of RS485-to-usb serial devices configured. |

`get_current_readings` calls `hardware.manager.query_all_sensors()`
directly (no DB write). `scan_buses` calls
`hardware.manager.scan_all_buses()` then `config.loader.save_config()`
to overwrite `sensor_mapping` (no process restart — unlike `PUT /api/config`,
this only ever changes one field the manager already applied to its own
in-memory state during the scan it just ran).

## 8. Lifecycle (`main.py`)

1. Read `SMORES_DATA_DIR` env var (default `~/SMORES_Data`), ensure it exists.
2. `config.loader.load_config()`.
3. Construct `db.database.Database`, run `init_schema()`.
4. Construct `hardware.manager.SensorManager`; if `scan_on_startup`, run
   `scan_all_buses()` and persist the result, else load `sensor_mapping`
   from config as-is.
5. Start `sampler.py`'s periodic task and `db.retention`'s periodic task
   as `asyncio.Task`s.
6. Build and start the aiohttp `AppRunner`/`TCPSite`.
7. Register `SIGTERM`/`SIGINT` handlers that cancel the two background
   tasks and call `.close()`/`.aclose()` on the manager (closes each
   `ModbusBus`'s serial connection), the database, and the aiohttp
   runner, then exit — systemd's `Restart=always` handles restart-on-exit
   for both normal SIGTERM/SIGINT and the `PUT /api/config` restart path.

## 9. Testing structure

- `tests/mocks/mock_rdo_blue.py` implements `BlueRDOInterface` so unit
  tests never touch real serial hardware; it's also injected into
  `hardware.manager.SensorManager` for integration tests, satisfying
  "integration tests for API calls using mocked Blue RDO sensors, but a
  real SQLite DB and real network calls."
- Integration tests spin up the real aiohttp app via `pytest-aiohttp`'s
  `aiohttp_client` fixture, point `SMORES_DATA_DIR` at a pytest `tmp_path`,
  and use a short `sample_interval_seconds` with `freezegun`/real sleeps
  to assert row counts after a bounded runtime.

---

Once this is approved, step 3 implements `config/schema.py` and
`models/readings.py` against the tables above.
