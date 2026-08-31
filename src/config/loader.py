"""Load/save `config.json` against the `config.schema.Config` model.

Data directory resolution and atomic file writes live here; `config/schema.py`
owns the schema itself. See ARCHITECTURE.md §3.
"""

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from config.schema import Config
from constants import ConfigValidationError

logger = logging.getLogger(__name__)

DATA_DIR_ENV_VAR = "SMORES_DATA_DIR"
"""Environment variable overriding the default `~/SMORES_Data` data
directory; used by integration tests to isolate their config/DB."""

DEFAULT_DATA_DIR_NAME = "SMORES_Data"
CONFIG_FILENAME = "config.json"

_TEMP_FILE_PREFIX = ".config.json."


def get_data_dir() -> Path:
    """Resolve the data directory: `$SMORES_DATA_DIR` if set, else `~/SMORES_Data`.

    Does not create the directory; callers (`main.py`) are responsible for
    `mkdir(parents=True, exist_ok=True)` during startup.
    """
    override = os.environ.get(DATA_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_DATA_DIR_NAME


def get_config_path(data_dir: Path | None = None) -> Path:
    """Return `<data_dir>/config.json`, defaulting `data_dir` to `get_data_dir()`."""
    base = get_data_dir() if data_dir is None else data_dir
    return base / CONFIG_FILENAME


def load_config(path: Path | None = None) -> Config:
    """Read and validate the config file at `path` (default: `get_config_path()`).

    Raises:
        ConfigValidationError: the file is missing, is not valid JSON, or
            fails `Config` schema validation.
    """
    config_path = get_config_path() if path is None else path
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigValidationError(f"could not read config file {config_path}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigValidationError(f"{config_path} is not valid JSON: {exc}") from exc

    try:
        config = Config.model_validate(payload)
    except ValidationError as exc:
        raise ConfigValidationError(f"{config_path} failed validation: {exc}") from exc

    logger.debug("Loaded config from %s", config_path)
    return config


def save_config(config: Config, path: Path | None = None) -> None:
    """Atomically persist `config` to `path` (default: `get_config_path()`).

    Writes to a temp file in the same directory then `os.replace()`s it
    over the destination, so a crash mid-write can't corrupt the existing
    config file.

    Raises:
        ConfigValidationError: `config` fails schema validation (defensive;
            `config` is expected to already be a validated `Config` instance).
    """
    config_path = get_config_path() if path is None else path

    # `Config` allows plain attribute assignment, and both `main.py` and
    # `GET /api/scan` do assign to `sensor_mapping` before saving, so the
    # in-memory object is re-validated here rather than trusted.
    try:
        validated = Config.model_validate(config.model_dump())
    except ValidationError as exc:
        raise ConfigValidationError(f"refusing to save an invalid config: {exc}") from exc

    body = json.dumps(validated.model_dump(mode="json"), indent=2) + "\n"

    directory = config_path.parent
    fd, temp_name = tempfile.mkstemp(dir=directory, prefix=_TEMP_FILE_PREFIX, suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, config_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    # fsync the directory too, so the rename itself survives a power cut on
    # the Pi's SD card, not just the file contents.
    with contextlib.suppress(OSError):
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    logger.debug("Saved config to %s", config_path)
