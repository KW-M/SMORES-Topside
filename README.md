# SMORES-Topside

Backend for a dissolved oxygen sensor array: reads N In-Situ Blue RDO sensors
over Modbus RTU (RS485-to-USB), logs readings to a local SQLite database on a
schedule, and serves a REST/JSON API (aiohttp) for current readings, history,
and configuration.

Target platform: 64-bit Debian (Raspberry Pi OS "trixie") on a Raspberry Pi
3B+, Python 3.13.

See [AGENTS.md](AGENTS.md) for the full functional spec and implementation
plan, and [ARCHITECTURE.md](ARCHITECTURE.md) for the module layout, config
schema, and DB row layout.

**Status:** step 11 of the implementation plan — every module is
implemented and the full test suite passes (`ruff`/`mypy --strict` clean).
Remaining: step 12, the systemd unit and install/operate instructions.

## Prerequisites

- Raspberry Pi OS / Debian 64-bit, Python 3.13 (`python3 --version`).
- `pipenv` for dependency and virtualenv management. This system's Python is
  "externally managed" (PEP 668), so install pipenv via apt rather than
  `pip install`:

  ```bash
  sudo apt install pipenv
  ```

  (Alternative if apt is unavailable: `pipx install pipenv`, or
  `pip install --user --break-system-packages pipenv`.)

## Install dependencies

From the repo root:

```bash
pipenv install --dev
```

This creates a project-local virtualenv (via pipenv's standard venv
location under `~/.local/share/virtualenvs/`) and installs both runtime and
development dependencies pinned in `Pipfile.lock`.

Runtime dependencies: `pymodbus`, `pyserial-asyncio`, `aiosqlite`,
`pydantic`, `aiohttp`, `aiohttp-apigami`, `marshmallow`, `psutil`.

Dev/test dependencies: `pytest`, `pytest-asyncio`, `pytest-aiohttp`,
`freezegun`, `mypy`, `ruff`.

## Running

Run the backend manually with:

```bash
pipenv run python src/main.py
```

Config file (`config.json`) and SQLite database (`smores.db`) live under
`~/SMORES_Data`, created automatically on first run — the config file is
written out with schema defaults if it doesn't exist yet. Set
`SMORES_DATA_DIR` to use a different directory (the integration tests do
this to keep off the real one).

Stop it with Ctrl-C or `SIGTERM`; both run the same graceful shutdown, which
is also what `PUT /api/config` triggers so the process comes back up under
the new config.

### The API is up before the sensors are

The HTTP listener starts *first*, and opening the serial ports and
establishing the sensor mapping (a bus scan, if `scan_on_startup` is set)
happens in the background — a full-range scan takes minutes (see below), and
the backend answering during it is more useful than a refused connection.
While the mapping is still being built, `GET /api/sensors/current` and
`GET /api/data` behave differently on purpose:

- `/api/sensors/current` returns `503` with `{"error": "Bus scan in
  progress", ...}` — there is nothing to poll yet.
- `/api/data` works immediately: it reads the database, which is open before
  the listener starts.

If sensor startup fails outright (e.g. a `/dev/serial/by-id/...` path that
isn't plugged in), the process does **not** exit — that would just restart-
loop under systemd. It logs the failure, keeps serving that same `503`, and
waits for you to fix `serial_port_devices` with `PUT /api/config` or retry
with `GET /api/scan`.

### Bus scans take as long as the address range you give them

With `scan_on_startup: true`, startup probes every Modbus address in
`[scan_min_address, scan_max_address]` on every converter. An address with
nothing on it can only be ruled out by letting its probe time out, and each
address is probed up to twice (the first probe doubles as the instrument
wake-up the vendor doc requires). So the worst case is roughly:

```
2 x (scan_max_address - scan_min_address + 1) x scan_probe_timeout_seconds
```

The defaults (`1`-`247`, 1 s) therefore allow up to ~8 minutes. Set
`scan_max_address` to the highest address actually installed — e.g. `27` for
a 27-sensor array, which brings the same scan down to under a minute — or
leave `scan_on_startup: false` and let the saved `sensor_mapping` be used
as-is, re-scanning on demand with `GET /api/scan`. The startup log states
the address count and the worst-case estimate, and warns when that estimate
exceeds a minute.

## Development

Run tests:

```bash
pipenv run pytest
```

Lint and type-check (settings for both live in `pyproject.toml`, including
`mypy_path`/`explicit_package_bases` so intra-`src` imports resolve as the
top-level modules they're written as, e.g. `config.schema` not
`src.config.schema`):

```bash
pipenv run ruff check .
pipenv run mypy src tests
```

Or activate the virtualenv directly instead of prefixing every command with
`pipenv run`:

```bash
pipenv shell
```

## Project layout

```
src/                    Application source (Python package, imported as top-level modules)
  main.py               Entry point: config load, subsystem lifecycle, signal handlers
  constants.py          UNREADABLE_VALUE + shared exception classes
  sampler.py            Periodic sensor-poll-to-DB task (drift-corrected loop)
  config/
    schema.py           Config pydantic model (typed schema, defaults, validation)
    loader.py           load_config/save_config (atomic write via temp file + os.replace)
  models/
    readings.py         SensorReading, ScanResult pydantic models (shared DB/API/hardware shape)
  hardware/              Blue RDO sensor array subsystem (implemented)
    rdo_blue_constants.py  Register map transcribed from the vendor doc
    rdo_blue_interface.py  BlueRDOInterface abstract base
    rdo_blue.py            BlueRDOSensor (register decoding, per-parameter status)
    modbus_bus.py          ModbusBus (per RS485-to-USB converter: half-duplex
                           request lock, no retries, instrument wake-up)
    manager.py             SensorManager (scan/query high-level API)
  db/                    SQLite storage subsystem (implemented)
    database.py            Database CRUD
    retention.py           Disk-space-based pruning policy
  api/                   REST/JSON HTTP API subsystem (implemented)
    app.py                 aiohttp Application factory
    middleware.py          Concurrency limit + per-route timeout middleware
    routes.py              Endpoint handlers
    schemas.py             marshmallow schemas for aiohttp-apigami
tests/                  Unit and integration tests (mocked sensors + real SQLite/HTTP)
  mocks/mock_rdo_blue.py  Configurable fake BlueRDOInterface (canned values,
                          simulated per-parameter/whole-sensor faults)
  unit/                   Per-module tests
  integration/            Full aiohttp app + real DB + mocked sensors, plus a
                          test that runs main.run() end to end (real port,
                          real SIGTERM)
documentation/          Vendor docs (Blue RDO Modbus register map, etc.)
Pipfile / Pipfile.lock  Dependency manifest (pipenv)
pyproject.toml          ruff/mypy configuration
AGENTS.md               Functional spec and implementation plan for this project
ARCHITECTURE.md         Module tree, config schema, and DB row layout
```
