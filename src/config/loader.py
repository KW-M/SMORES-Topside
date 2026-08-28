"""Load/save `config.json` against the `config.schema.Config` model.

Data directory resolution and atomic file writes live here; `config/schema.py`
owns the schema itself. See ARCHITECTURE.md §3.
"""

from pathlib import Path

from config.schema import Config

DATA_DIR_ENV_VAR = "SMORES_DATA_DIR"
"""Environment variable overriding the default `~/SMORES_Data` data
directory; used by integration tests to isolate their config/DB."""

DEFAULT_DATA_DIR_NAME = "SMORES_Data"
CONFIG_FILENAME = "config.json"


def get_data_dir() -> Path:
    """Resolve the data directory: `$SMORES_DATA_DIR` if set, else `~/SMORES_Data`.

    Does not create the directory; callers (`main.py`) are responsible for
    `mkdir(parents=True, exist_ok=True)` during startup.
    """
    raise NotImplementedError


def get_config_path(data_dir: Path | None = None) -> Path:
    """Return `<data_dir>/config.json`, defaulting `data_dir` to `get_data_dir()`."""
    raise NotImplementedError


def load_config(path: Path | None = None) -> Config:
    """Read and validate the config file at `path` (default: `get_config_path()`).

    Raises:
        ConfigValidationError: the file is missing, is not valid JSON, or
            fails `Config` schema validation.
    """
    raise NotImplementedError


def save_config(config: Config, path: Path | None = None) -> None:
    """Atomically persist `config` to `path` (default: `get_config_path()`).

    Writes to a temp file in the same directory then `os.replace()`s it
    over the destination, so a crash mid-write can't corrupt the existing
    config file.

    Raises:
        ConfigValidationError: `config` fails schema validation (defensive;
            `config` is expected to already be a validated `Config` instance).
    """
    raise NotImplementedError
