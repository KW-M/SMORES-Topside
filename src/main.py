"""Entry point for the SMORES-Topside backend.

Owns the full process lifecycle: load config, initialize the DB and sensor
manager, start the periodic sampler and retention background tasks, start
the aiohttp server, then block until SIGTERM/SIGINT before tearing every
subsystem down cleanly. See ARCHITECTURE.md §8.
"""

import asyncio
import functools
import logging
import signal
from pathlib import Path

from aiohttp import web

from api.app import create_app
from config.loader import get_config_path, get_data_dir, load_config, save_config
from config.schema import Config
from constants import ConfigValidationError
from db.database import Database
from db.retention import run_forever as retention_run_forever
from hardware.manager import SensorManager
from sampler import run_forever as sampler_run_forever

logger = logging.getLogger(__name__)

_SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def _load_or_create_config(data_dir: Path) -> Config:
    """Load `<data_dir>/config.json`, writing schema defaults first if the
    file doesn't exist yet (first run on a fresh install)."""
    config_path = get_config_path(data_dir)
    if not config_path.exists():
        logger.info("No config file found at %s; writing defaults", config_path)
        save_config(Config(), config_path)
    return load_config(config_path)


async def _apply_sensor_mapping(manager: SensorManager, config: Config, data_dir: Path) -> None:
    """Establish the sensor manager's address mapping per `config.scan_on_startup`.

    If scanning, persists the discovered mapping back to config.json so it
    survives a restart with `scan_on_startup` later flipped to false.
    """
    if config.scan_on_startup:
        logger.info("scan_on_startup=true; scanning all configured buses")
        await manager.scan_all_buses()
        config.sensor_mapping = manager.get_sensor_mapping()
        save_config(config, get_config_path(data_dir))
        logger.info("Startup scan complete: %s", config.sensor_mapping)
    else:
        logger.info("scan_on_startup=false; applying configured sensor_mapping as-is")
        await manager.save_sensor_mapping(config.sensor_mapping)


def _on_shutdown_signal(sig: signal.Signals, shutdown_event: asyncio.Event) -> None:
    logger.info("Received %s; shutting down", sig.name)
    shutdown_event.set()


async def _shutdown(
    manager: SensorManager,
    database: Database,
    runner: web.AppRunner | None,
    sampler_task: asyncio.Task[None] | None,
    retention_task: asyncio.Task[None] | None,
) -> None:
    """Cancel background tasks and close every subsystem.

    Each step is isolated so one failure (e.g. a serial port that's already
    gone) doesn't prevent the rest of teardown from running.
    """
    for task in (sampler_task, retention_task):
        if task is not None:
            task.cancel()
    for task in (sampler_task, retention_task):
        if task is None:
            continue
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Background task %r raised during shutdown", task.get_name())

    try:
        await manager.aclose()
    except Exception:
        logger.exception("Error closing sensor manager")

    try:
        await database.aclose()
    except Exception:
        logger.exception("Error closing database")

    if runner is not None:
        try:
            await runner.cleanup()
        except Exception:
            logger.exception("Error cleaning up aiohttp runner")

    logger.info("Shutdown complete")


async def run() -> None:
    """Run the backend until a shutdown signal is received, then tear
    every subsystem down cleanly."""
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    config = _load_or_create_config(data_dir)
    logging.getLogger().setLevel(config.log_level)
    logger.info("Loaded config from %s", get_config_path(data_dir))

    database = Database(data_dir / "smores.db")
    manager = SensorManager(config)
    runner: web.AppRunner | None = None
    sampler_task: asyncio.Task[None] | None = None
    retention_task: asyncio.Task[None] | None = None

    try:
        await database.init_schema()
        await manager.start()
        await _apply_sensor_mapping(manager, config, data_dir)

        sampler_task = asyncio.create_task(
            sampler_run_forever(manager, database, config.sample_interval_seconds),
            name="sampler",
        )
        retention_task = asyncio.create_task(
            retention_run_forever(
                database,
                data_dir,
                config.min_free_disk_space_mb,
                config.disk_check_interval_seconds,
                config.retention_delete_batch_size,
            ),
            name="retention",
        )

        app = create_app(config, manager, database, data_dir)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, config.api_host, config.api_port)
        await site.start()
        logger.info("API listening on http://%s:%s", config.api_host, config.api_port)

        shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in _SHUTDOWN_SIGNALS:
            loop.add_signal_handler(
                sig, functools.partial(_on_shutdown_signal, sig, shutdown_event)
            )

        await shutdown_event.wait()
    finally:
        await _shutdown(manager, database, runner, sampler_task, retention_task)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run())
    except ConfigValidationError as exc:
        logger.error("Invalid configuration, refusing to start: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
