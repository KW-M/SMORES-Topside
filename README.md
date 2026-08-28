# SMORES-Topside

Backend for a dissolved oxygen sensor array: reads N In-Situ Blue RDO sensors
over Modbus RTU (RS485-to-USB), logs readings to a local SQLite database on a
schedule, and serves a REST/JSON API (aiohttp) for current readings, history,
and configuration.

Target platform: 64-bit Debian (Raspberry Pi OS "trixie") on a Raspberry Pi
3B+, Python 3.13.

See [AGENTS.md](AGENTS.md) for the full functional spec and implementation
plan, and `ARCHITECTURE.md` (added in a later step) for the module layout
once it exists.

**Status:** step 1 of the implementation plan — project scaffold and
dependencies only. `src/main.py` is a placeholder; no sensor, DB, or API
functionality is implemented yet.

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
`pydantic`, `aiohttp`, `aiohttp-apispec`, `marshmallow`, `psutil`.

Dev/test dependencies: `pytest`, `pytest-asyncio`, `pytest-aiohttp`,
`freezegun`, `mypy`, `ruff`.

## Running

Run the backend manually (once implemented) with:

```bash
pipenv run python src/main.py
```

Config file and SQLite database live under `~/SMORES_Data` (created
automatically by the app once the config module is implemented).

## Development

Run tests:

```bash
pipenv run pytest
```

Lint and type-check:

```bash
pipenv run ruff check .
pipenv run mypy src
```

Or activate the virtualenv directly instead of prefixing every command with
`pipenv run`:

```bash
pipenv shell
```

## Project layout

```
src/                  Application source (Python package)
  main.py             Entry point (placeholder for now)
tests/                Unit and integration tests (mocked sensors + real SQLite/HTTP)
documentation/         Vendor docs (Blue RDO Modbus register map, etc.)
Pipfile / Pipfile.lock Dependency manifest (pipenv)
AGENTS.md              Functional spec and implementation plan for this project
```
