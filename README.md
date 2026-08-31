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

**Status:** complete — all 12 implementation steps are done. Every module
is implemented, the full test suite passes, `ruff`/`mypy --strict` are clean,
and the systemd unit in [deploy/](deploy/) is documented below.

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

## Running as a systemd service

For unattended operation, install the unit shipped at
[deploy/smores-topside.service](deploy/smores-topside.service). It runs
`src/main.py` from this repo's pipenv virtualenv as user `pi`, with
`SMORES_DATA_DIR=/home/pi/SMORES_Data`, and logs to the journal.

(The unit sets `PrivateTmp=yes`, so keep `SMORES_DATA_DIR` outside `/tmp` —
under a private `/tmp` the service would silently get its own empty data
directory rather than the one you created.)

### Install

```bash
cd /home/pi/SMORES-Topside
sudo cp deploy/smores-topside.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smores-topside.service
systemctl status smores-topside.service
```

`enable --now` both starts it immediately and makes it come back on boot.

The unit's `ExecStart` hardcodes this repo's virtualenv interpreter,
`/home/pi/.local/share/virtualenvs/SMORES-Topside-<hash>/bin/python`. That
hash is a digest of the absolute path to `Pipfile`, so it stays correct
across `pipenv install` re-runs — but not if the repo moves. If you
relocated the repo or use a different virtualenv, re-point it after copying:

```bash
sudo sed -i "s|^ExecStart=.*|ExecStart=$(pipenv --venv)/bin/python $(pwd)/src/main.py|" \
  /etc/systemd/system/smores-topside.service
sudo systemctl daemon-reload && sudo systemctl restart smores-topside
```

Opening `/dev/ttyUSB*` requires membership in the `dialout` group. The unit
sets `SupplementaryGroups=dialout` so this holds regardless, but if you also
want to run the backend by hand as `pi`, check with `id pi` and add it if
missing (`sudo usermod -aG dialout pi`, then log out and back in).

### First start: point it at your converters

`serial_port_devices` defaults to empty, so a fresh install has no sensors to
read — `/api/sensors/current` returns an empty list and the sampler writes no
rows. On its first start the backend writes `~/SMORES_Data/config.json` with
schema defaults; fill in your converters and restart. List them with:

```bash
ls -l /dev/serial/by-id/
```

Then either edit the file and restart the service:

```bash
nano ~/SMORES_Data/config.json     # set serial_port_devices, scan_max_address
sudo systemctl restart smores-topside
```

...or push the whole config over the API, which validates it, persists it,
and restarts the process for you (see `PUT /api/config` below).

Set `scan_max_address` to the highest Modbus address actually installed while
you're in there: the default of 247 makes every startup scan take ~8 minutes
(see [Bus scans take as long as the address range you give
them](#bus-scans-take-as-long-as-the-address-range-you-give-them) above).

### Serving on port 80

The listen address and port come from the config file, not the unit:

```json
{ "api_host": "0.0.0.0", "api_port": 80 }
```

Ports below 1024 are privileged, and the service runs as `pi`, not root. The
shipped unit covers this with

```ini
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
```

so `"api_port": 80` needs nothing beyond the config change — set it with
`nano ~/SMORES_Data/config.json && sudo systemctl restart smores-topside`, or
push it with `PUT /api/config` (which restarts the process itself). The API
then answers on plain `http://<pi>/`, with the docs at
`http://<pi>/api/docs`. Nothing else may already be listening on port 80 —
check with `sudo ss -ltnp '( sport = :80 )'` first; on a Pi image with a web
server installed, `sudo systemctl disable --now apache2` (or `nginx`,
`lighttpd`) frees it.

`AmbientCapabilities=` grants only the right to bind low ports, not root:
systemd hands the capability across the switch to `pi`, and
`CapabilityBoundingSet=` caps the process at that one capability for its
whole lifetime. If you never serve below port 1024, both lines can be
deleted.

Two things to know if you go off this path:

- **If the unit lacks the capability** (an older copy in
  `/etc/systemd/system/`, or the lines removed) **and the config asks for port
  80**, the bind fails with `Permission denied` at startup and `Restart=always`
  retries it every 2 s indefinitely. `journalctl -u smores-topside -n 20`
  shows the `PermissionError`. Fix it by re-copying the unit
  (`sudo cp deploy/smores-topside.service /etc/systemd/system/ && sudo
  systemctl daemon-reload`), or by editing `api_port` back to 8080 in
  `~/SMORES_Data/config.json` — the API is unreachable in this state, so the
  config has to be corrected on disk.
- **Running it by hand** (`pipenv run python src/main.py`, no systemd) gets no
  capability, so a config with `api_port` 80 fails the same way. For manual
  runs either keep the port at 8080, or lower the privileged range once per
  boot with `sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80`.

### Operate

```bash
sudo systemctl status smores-topside          # state + last few log lines
sudo systemctl restart smores-topside         # graceful SIGTERM, then start
sudo systemctl stop smores-topside
sudo systemctl disable --now smores-topside   # stop, and don't start at boot
```

The unit is `Restart=always`, deliberately not `Restart=on-failure`:
`PUT /api/config` saves the new config and then exits through the same
SIGTERM shutdown path as a normal stop, so it exits **0** and systemd still
has to bring it back for the new config to take effect. `on-failure` would
leave the service dead after every config change.
`StartLimitIntervalSec=0` disables systemd's start rate limit for the same
reason — repeated config pushes must not latch the unit into `failed`.

A missing or unplugged serial adapter is *not* fatal (see [The API is up
before the sensors are](#the-api-is-up-before-the-sensors-are)), so
`Restart=always` doesn't turn a hardware fault into a restart loop; the
service stays up serving `503` from `/api/sensors/current` and logs the
reason.

### Logs

Everything logs to stdout, which systemd captures into the journal under the
identifier `smores-topside`:

```bash
journalctl -u smores-topside -f                     # follow live
journalctl -u smores-topside -n 200                 # last 200 lines
journalctl -u smores-topside -b                     # since this boot
journalctl -u smores-topside --since "1 hour ago"
journalctl -u smores-topside -p warning             # warnings and worse only
journalctl -u smores-topside --since today > smores-today.log
```

For per-register Modbus traffic and per-address scan probes, set
`"log_level": "DEBUG"` in `config.json` and restart.

## Using the API

The backend binds `api_host`:`api_port`, `0.0.0.0:8080` by default — so
`http://localhost:8080` on the Pi itself, or the Pi's LAN address from
another machine. Examples below use `localhost:8080`; adjust them if you
changed the port. Port 80 (dropping the `:8080` from every URL below) is
supported under the shipped systemd unit — see [Serving on port
80](#serving-on-port-80).

### Live API documentation

Interactive Swagger UI, generated from the route decorators by
aiohttp-apigami, is served at:

```
http://localhost:8080/api/docs
```

The raw OpenAPI/Swagger JSON is at `/api/docs/swagger.json`, and the landing
page at `http://localhost:8080/` links to the docs.

### Endpoints

| Method and path | Purpose |
| --- | --- |
| `GET /` | HTML overview page, links to the docs |
| `GET /api/docs` | Swagger UI |
| `GET /api/sensors/current` | Fresh poll of every configured sensor; does **not** write a DB row |
| `GET /api/data` | Stored readings as JSON; optional `start`/`end` |
| `GET /api/data/csv` | The same rows, CSV |
| `DELETE /api/data` | Delete rows older than a required `cutoff` |
| `GET /api/config` | Current `config.json` contents |
| `PUT /api/config` | Validate and persist a new config, then restart the process |
| `GET /api/scan` | Re-scan every converter, save the mapping to `config.json`, return it |

`start`, `end`, and `cutoff` are UTC unix timestamps in seconds (fractional
allowed). `start`/`end` are both **inclusive**; `cutoff` is **exclusive** —
`DELETE` removes rows with `timestamp_utc < cutoff`.

### CSV export for a date range

Generate the bounds with `date -u`:

```bash
curl -s -o smores_2026-08-31.csv \
  "http://localhost:8080/api/data/csv?start=$(date -u -d '2026-08-31 00:00:00' +%s)&end=$(date -u -d '2026-08-31 23:59:59' +%s)"
```

Pass only one bound for an open-ended range, or neither to export every
stored row:

```bash
curl -s -o smores_all.csv http://localhost:8080/api/data/csv
curl -s "http://localhost:8080/api/data/csv?start=$(date -u -d '1 hour ago' +%s)"
```

The response carries `Content-Type: text/csv; charset=utf-8` and
`Content-Disposition: attachment; filename="smores_data.csv"`, so a browser
hitting the same URL downloads a file.

Other useful calls:

```bash
curl -s http://localhost:8080/api/sensors/current | python3 -m json.tool
curl -s http://localhost:8080/api/config > my_config.json
curl -s -X PUT -H 'Content-Type: application/json' \
  --data @my_config.json http://localhost:8080/api/config      # restarts the process
curl -s -X DELETE "http://localhost:8080/api/data?cutoff=$(date -u -d '30 days ago' +%s)"
curl -s http://localhost:8080/api/scan | python3 -m json.tool   # can take minutes
```

### CSV output format

A header row followed by one row per stored reading, in this column order
(the second row below is a healthy sensor, the third an unreachable one):

```csv
row_id,sensor_address,serial_converter_id,timestamp_utc,temperature_c,do_percent_saturation,do_partial_pressure_torr,do_mg_l,status_code,status_text
1,1,/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A5XK3RJT-if00-port0,1756598400.0,21.437,98.62,157.04,8.712,0,OK
2,2,/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A5XK3RJT-if00-port0,1756598400.0,-9999.0,-9999.0,-9999.0,-9999.0,-1,Sensor timeout
```

| Column | Meaning |
| --- | --- |
| `row_id` | Monotonically incrementing DB row counter (`INTEGER PRIMARY KEY AUTOINCREMENT`); empty for `/api/sensors/current`, which doesn't store a row |
| `sensor_address` | The sensor's globally unique Modbus address |
| `serial_converter_id` | `/dev/serial/by-id/...` path of the converter it was read through |
| `timestamp_utc` | Unix epoch seconds, UTC, when the reading was taken |
| `temperature_c` | Degrees Celsius |
| `do_percent_saturation` | Dissolved O2, % saturation |
| `do_partial_pressure_torr` | Dissolved O2 partial pressure, torr |
| `do_mg_l` | Dissolved O2 concentration, mg/L |
| `status_code` | Numeric status; see below |
| `status_text` | Human-readable status |

Any of the four measurement columns that could not be read holds **`-9999`**
(`constants.UNREADABLE_VALUE`) rather than a blank or `null`, so the column
stays numeric in every consumer. `/api/data` returns exactly these fields as
JSON, with `row_id: null` in place of an empty `row_id`.

### `status_code` meanings

`status_code` is the *worst* outcome across the sensor's four parameters, and
`status_text` is the per-parameter detail joined with `; ` (for example
`Temperature: Timed out reading parameter; DO Concentration: Modbus read
error`). Non-negative codes are Data Quality IDs reported by the instrument
itself; negative codes are this backend's own.

| Code | `status_text` | Meaning |
| --- | --- | --- |
| `0` | `OK` | Every parameter read cleanly |
| `3` | `...: Error reading parameter` | Instrument reported Data Quality ID 3 for that parameter |
| `5` | `...: RDO Cap expired` | The RDO sensing cap is past its service life — replace it; readings are suspect |
| other `> 0` | `...: Unknown data quality id N` | A Data Quality ID the vendor doc doesn't enumerate. In-Situ's guidance is to contact their technical support |
| `-1` | `Sensor timeout` | The instrument never answered; all four values are `-9999` |
| `-2` | `...: Timed out reading parameter` | Some (not all) parameters timed out while the instrument kept answering others |
| `-3` | `...: Modbus read error` | The instrument answered with a Modbus exception, or a short/malformed reply |

A `-1` row still gets written every sampling interval, so a dead or
unplugged sensor shows up as a continuous run of `-9999`s in the data rather
than as a gap.

### HTTP status codes

| Code | When |
| --- | --- |
| `200` | Success |
| `400` | Bad query parameter (`start`/`end`/`cutoff` not a number, `cutoff` missing) or a `PUT /api/config` body that fails schema validation. Body is `{"error": ..., "detail": ...}` |
| `404` / `405` | Unknown path / wrong method for that path |
| `500` | Internal error (also returned if `config.json` can't be written) |
| `503` | Either **at capacity** — more than `api_max_concurrent_clients` (default 5) requests in flight, with a `Retry-After: 1` header, never queued — or **not ready**: `/api/sensors/current` and `/api/scan` while a bus scan is in progress or before the sensor mapping is established. The `error` field distinguishes them |
| `504` | The handler exceeded its timeout: `poll_timeout_seconds` for `/api/sensors/current`, the scan worst-case estimate for `/api/scan`, `api_request_timeout_seconds` for everything else |

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
deploy/
  smores-topside.service  systemd unit (user pi, SMORES_DATA_DIR=/home/pi/SMORES_Data,
                          Restart=always, CAP_NET_BIND_SERVICE so api_port
                          can be 80)
documentation/          Vendor docs (Blue RDO Modbus register map, etc.)
Pipfile / Pipfile.lock  Dependency manifest (pipenv)
pyproject.toml          ruff/mypy configuration
AGENTS.md               Functional spec and implementation plan for this project
ARCHITECTURE.md         Module tree, config schema, and DB row layout
```
