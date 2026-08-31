"""Entry point for the SMORES-Topside backend.

Owns the full process lifecycle: load config, initialize the DB, start
serving HTTP, then bring the sensors up (scan) in the background while the
periodic sampler and retention tasks run, and block until SIGTERM/SIGINT
before tearing every subsystem down cleanly. See ARCHITECTURE.md §8.
"""

import asyncio
import functools
import logging
import signal
from collections.abc import Sequence
from pathlib import Path

from aiohttp import web

from api.app import create_app
from config.loader import get_config_path, get_data_dir, load_config, save_config
from config.schema import Config
from constants import BusScanError, ConfigValidationError
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


async def _bring_up_sensors(manager: SensorManager, config: Config, data_dir: Path) -> None:
    """Open the serial ports and establish the sensor mapping.

    Runs as a background task rather than inline in `run()` so the HTTP
    server is already listening while it works: a full 1-247 address-space
    scan takes minutes (see `hardware.manager.estimate_scan_duration_seconds`),
    and during it `/api/sensors/current` must be able to answer with the
    manager's "scan in progress" 503 instead of the port simply refusing
    connections.

    A hardware failure here is logged but not fatal — the API stays up so an
    operator can correct `serial_port_devices` via `PUT /api/config` (which
    restarts the process) or retry with `GET /api/scan`, rather than the
    process exiting into a systemd `Restart=always` loop.
    """
    try:
        await manager.start()
        await _apply_sensor_mapping(manager, config, data_dir)
    except asyncio.CancelledError:
        raise
    except BusScanError:
        logger.exception(
            "Sensor startup failed; the API stays up so the configuration can be "
            "corrected (PUT /api/config) or the scan retried (GET /api/scan)"
        )
    except Exception:
        logger.exception("Unexpected error during sensor startup")


def _on_shutdown_signal(sig: signal.Signals, shutdown_event: asyncio.Event) -> None:
    logger.info("Received %s; shutting down", sig.name)
    shutdown_event.set()


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop, shutdown_event: asyncio.Event
) -> None:
    """Route SIGTERM/SIGINT to `shutdown_event`.

    Installed before any subsystem starts, so a stop or restart arriving
    during DB init or a long startup scan is still handled gracefully
    instead of killing the process mid-write.
    """
    for sig in _SHUTDOWN_SIGNALS:
        loop.add_signal_handler(sig, functools.partial(_on_shutdown_signal, sig, shutdown_event))


def _remove_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """Restore default signal disposition once teardown has begun, so a
    second SIGTERM (e.g. systemd losing patience) terminates the process
    immediately rather than being swallowed by a handler that has already
    fired."""
    for sig in _SHUTDOWN_SIGNALS:
        loop.remove_signal_handler(sig)


async def _cancel_tasks(tasks: Sequence[asyncio.Task[None] | None]) -> None:
    """Cancel every task, then await them all so their `finally` blocks run
    before the subsystems they use are closed."""
    live = [task for task in tasks if task is not None]
    for task in live:
        task.cancel()
    for task in live:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Background task %r raised during shutdown", task.get_name())


async def _shutdown(
    manager: SensorManager,
    database: Database,
    runner: web.AppRunner | None,
    tasks: Sequence[asyncio.Task[None] | None],
) -> None:
    """Cancel background tasks and close every subsystem.

    Each step is isolated so one failure (e.g. a serial port that's already
    gone) doesn't prevent the rest of teardown from running.
    """
    await _cancel_tasks(tasks)

    # The runner goes first so no new request can reach a half-closed
    # manager or database.
    if runner is not None:
        try:
            await runner.cleanup()
        except Exception:
            logger.exception("Error cleaning up aiohttp runner")

    try:
        await manager.aclose()
    except Exception:
        logger.exception("Error closing sensor manager")

    try:
        await database.aclose()
    except Exception:
        logger.exception("Error closing database")

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
    startup_task: asyncio.Task[None] | None = None
    sampler_task: asyncio.Task[None] | None = None
    retention_task: asyncio.Task[None] | None = None

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    _install_signal_handlers(loop, shutdown_event)

    try:
        await database.init_schema()

        app = create_app(config, manager, database, data_dir)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, config.api_host, config.api_port)
        await site.start()
        logger.info("API listening on http://%s:%s", config.api_host, config.api_port)

        # Only now that the API can answer do the sensors come up: the scan
        # below can take minutes, and callers get a "scan in progress" 503
        # throughout instead of a refused connection.
        startup_task = asyncio.create_task(
            _bring_up_sensors(manager, config, data_dir),
            name="sensor-startup",
        )
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

        await shutdown_event.wait()
    finally:
        _remove_signal_handlers(loop)
        await _shutdown(manager, database, runner, (startup_task, sampler_task, retention_task))


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
