"""Unit tests for `config.schema.Config` (validation/defaults) and
`config.loader` (data dir resolution, atomic load/save). See
ARCHITECTURE.md §3.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import config.loader as loader
from api.schemas import ConfigSchema
from config.loader import DATA_DIR_ENV_VAR, get_config_path, get_data_dir, load_config, save_config
from config.schema import Config
from constants import ConfigValidationError


class TestConfigSchema:
    def test_defaults_construct_without_arguments(self) -> None:
        config = Config()
        assert config.serial_port_devices == []
        assert config.sensor_mapping == {}
        assert config.scan_on_startup is True
        assert config.sample_interval_seconds == 60.0
        assert config.api_port == 8080
        assert config.log_level == "INFO"

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            Config(this_field_does_not_exist=True)  # type: ignore[call-arg]

    @pytest.mark.parametrize("bad_value", [0, -1.0])
    def test_rejects_non_positive_sample_interval(self, bad_value: float) -> None:
        with pytest.raises(ValidationError):
            Config(sample_interval_seconds=bad_value)

    def test_rejects_invalid_modbus_parity(self) -> None:
        with pytest.raises(ValidationError):
            Config(modbus_parity="X")  # type: ignore[arg-type]

    def test_log_level_is_uppercased(self) -> None:
        config = Config(log_level="debug")
        assert config.log_level == "DEBUG"

    def test_rejects_unknown_log_level(self) -> None:
        with pytest.raises(ValidationError):
            Config(log_level="VERBOSE")


class TestGetDataDir:
    def test_uses_env_var_when_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path / "custom"))
        assert get_data_dir() == tmp_path / "custom"

    def test_defaults_to_home_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(DATA_DIR_ENV_VAR, raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert get_data_dir() == tmp_path / loader.DEFAULT_DATA_DIR_NAME

    def test_does_not_create_the_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "not_yet_created"
        monkeypatch.setenv(DATA_DIR_ENV_VAR, str(target))
        get_data_dir()
        assert not target.exists()


class TestGetConfigPath:
    def test_appends_config_filename(self, tmp_path: Path) -> None:
        assert get_config_path(tmp_path) == tmp_path / "config.json"

    def test_defaults_to_get_data_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path))
        assert get_config_path() == tmp_path / "config.json"


class TestLoadSaveConfig:
    def test_round_trips_a_config(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        original = Config(serial_port_devices=["/dev/serial/by-id/usb-FTDI-port0"], api_port=9090)
        save_config(original, path)
        loaded = load_config(path)
        assert loaded == original

    def test_save_writes_readable_json(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        save_config(Config(), path)
        with path.open() as f:
            data = json.load(f)
        assert data["api_port"] == 8080

    def test_save_leaves_no_stray_temp_files(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        save_config(Config(), path)
        assert {p.name for p in tmp_path.iterdir()} == {"config.json"}

    def test_save_overwrites_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        save_config(Config(api_port=1111), path)
        save_config(Config(api_port=2222), path)
        assert load_config(path).api_port == 2222

    def test_load_raises_config_validation_error_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigValidationError):
            load_config(tmp_path / "does_not_exist.json")

    def test_load_raises_config_validation_error_on_malformed_json(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text("{ not valid json")
        with pytest.raises(ConfigValidationError):
            load_config(path)

    def test_load_raises_config_validation_error_on_schema_violation(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"api_port": "not-a-port"}))
        with pytest.raises(ConfigValidationError):
            load_config(path)

    def test_load_defaults_to_get_config_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path))
        save_config(Config(api_port=3333))
        assert load_config().api_port == 3333


class TestConfigSchemaMatchesApiSchema:
    """`GET /api/config` is documented as returning the config file's
    contents, and `PUT /api/config` fills any field the caller omits with a
    `Config` default — so a field present in `Config` but missing from
    `api.schemas.ConfigSchema` is silently dropped on GET and then silently
    reset to its default by a GET-edit-PUT round trip. Keep the two in sync.
    """

    def test_api_schema_covers_every_config_field(self) -> None:
        config_fields = set(Config.model_fields)
        api_fields = set(ConfigSchema().fields)
        assert config_fields == api_fields

    def test_api_schema_dump_round_trips_a_full_config(self) -> None:
        original = Config(scan_min_address=2, scan_max_address=27, api_port=9999)
        dumped = ConfigSchema().dump(original.model_dump())
        assert Config.model_validate(dumped) == original
