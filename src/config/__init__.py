"""Config module: typed schema and load/save for config.json."""

from config.loader import get_config_path, get_data_dir, load_config, save_config
from config.schema import Config

__all__ = [
    "Config",
    "get_config_path",
    "get_data_dir",
    "load_config",
    "save_config",
]
