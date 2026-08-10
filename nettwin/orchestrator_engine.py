from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import csv
import hashlib
import json
import logging
from pathlib import Path
import signal
import threading
import time
from typing import Any, Callable, Sequence

from nettwin import __version__
from nettwin.active_probe import ActiveProbeEngine, ProbeSample, ProbeTarget
from nettwin.host_collector import HostCollector, HostSample
from nettwin.integrity_engine import IntegrityConfig, finalize_integrity, verify_integrity
from nettwin.interface_collector import InterfaceCollector, InterfaceSample
from nettwin.preflight_engine import ExperimentConfig, load_experiment_config
from nettwin.preflight_runtime import run_preflight


ORCHESTRATOR_SCHEMA_VERSION = "linkprobe-orchestrator-v1"
ORCHESTRATION_FILENAME = "orchestration.json"
CONFIG_COPY_FILENAME = "experiment_config.json"
LOG_RELATIVE_PATH = Path("logs") / "linkprobe.log"
READY_TIMEOUT_SECONDS = 10.0


@dataclass
class CollectorStats:
    name: str
    interval_seconds: float
    cycles: int = 0
    rows: int = 0
    error_rows: int = 0
    overrun_cycles: int = 0
    max_scheduler_lag_seconds: float = 0.0
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    fatal_error: str | None = None

    def record(self, timestamp: str, *, is_error: bool) -> None:
        self.rows += 1
        if is_error:
            self.error_rows += 1
        if self.first_timestamp is None:
            self.first_timestamp = timestamp
        self.last_timestamp = timestamp


@dataclass(frozen=True)
class OrchestratorResult:
    run_dir: Path
    status: str
    stop_reason: str
    preflight_ready: bool
    orchestration_path: Path
    integrity_valid: bool | None
    checksums_path: Path | None
    metadata_path: Path | None
    integrity_report_path: Path | None


@dataclass
class _RunWindow:
    start_monotonic: float = 0.0
    deadline_monotonic: float = 0.0
    started_utc: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ensure_clean_run_directory(run_dir: Path) -> None:
    if run_dir.exists():
        if not run_dir.is_dir():
            raise ValueError(f"La salida configurada no es un directorio: {run_dir}")
        existing = list(run_dir.iterdir())
        if existing:
            names = ", ".join(sorted(item.name for item in existing)[:8])
            raise ValueError(
                "El directorio de ejecución ya contiene archivos. "
                f"Use un run_id/directorio nuevo para no sobrescribir evidencia: {run_dir} ({names})"
            )
    else:
        run_dir.mkdir(parents=True, exist_ok=False)


def _copy_exact_config(source: Path, run_dir: Path) -> Path:
    target = run_dir / CONFIG_COPY_FILENAME
    data = source.read_bytes()
    target.write_bytes(data)
    if target.read_bytes() != data:
        raise OSError("La copia de configuración no coincide byte a byte con el original")
    return target


class _UTCFormatter(logging.Formatter):
    converter = time.gmtime


def _open_logger(run_dir: Path, run_id: str) -> tuple[logging.Logger, Path]:
    log_path = run_dir / LOG_RELATIVE_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"nettwin.linkprobe.{run_id}.{id(log_path)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(
        _UTCFormatter("%(asctime)sZ %(levelname)s %(threadName)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    )
    logger.addHandler(handler)
    return logger, log_path


def _close_logger(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        try:
            handler.flush()
            handler.close()
        finally:
            logger.removeHandler(handler)


def _wait_until(
    stop_event: threading.Event,
    due_monotonic: float,
    monotonic_fn: Callable[[], float],
) -> float:
    now = monotonic_fn()
    delay = due_monotonic - now
    if delay > 0:
        stop_event.wait(delay)
        return 0.0
    return abs(delay)


def _sample_is_error(sample: Any) -> bool:
    return str(getattr(sample, "sample_status", "ok") or "ok").strip().lower() != "ok"


def _run_interface_worker(
    *,
    config: ExperimentConfig,
    output_path: Path,
    start_event: threading.Event,
    stop_event: threading.Event,
    ready_event: threading.Event,
    window: _RunWindow,
    stats: CollectorStats,
    logger: logging.Logger,
    monotonic_fn: Callable[[], float],
    collector_factory: Callable[[str], Any],
) -> None:
    try:
        collector = collector_factory(config.interface_name)
        fieldnames = [field.name for field in fields(InterfaceSample)]
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            handle.flush()
            ready_event.set()
            start_event.wait()
            logger.info("Interface Collector activo: %s", config.interface_name)
            previous_error = False
            cycle = 0
            while not stop_event.is_set() and monotonic_fn() < window.deadline_monotonic:
                sample = collector.sample()
                writer.writerow(asdict(sample))
                handle.flush()
                is_error = _sample_is_error(sample)
                stats.record(sample.timestamp, is_error=is_error)
                stats.cycles += 1
                if is_error and not previous_error:
                    logger.warning("Interface Collector reportó muestra de error: %s", sample.error)
                elif not is_error and previous_error:
                    logger.info("Interface Collector recuperado tras error de muestra")
                previous_error = is_error
                cycle += 1
                due = window.start_monotonic + cycle * config.interface_interval_seconds
                lag = _wait_until(stop_event, due, monotonic_fn)
                if lag > 0:
                    stats.overrun_cycles += 1
                    stats.max_scheduler_lag_seconds = max(stats.max_scheduler_lag_seconds, lag)
    except Exception as exc:
        stats.fatal_error = str(exc)
        ready_event.set()
        logger.exception("Fallo fatal de Interface Collector: %s", exc)
    finally:
        logger.info("Interface Collector finalizado: rows=%d errors=%d", stats.rows, stats.error_rows)


def _run_host_worker(
    *,
    config: ExperimentConfig,
    output_path: Path,
    start_event: threading.Event,
    stop_event: threading.Event,
    ready_event: threading.Event,
    window: _RunWindow,
    stats: CollectorStats,
    logger: logging.Logger,
    monotonic_fn: Callable[[], float],
    collector_factory: Callable[[], Any],
) -> None:
    try:
        collector = collector_factory()
        fieldnames = [field.name for field in fields(HostSample)]
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            handle.flush()
            ready_event.set()
            start_event.wait()
            logger.info("Host Collector activo")
            previous_error = False
            cycle = 0
            while not stop_event.is_set() and monotonic_fn() < window.deadline_monotonic:
                sample = collector.sample()
                writer.writerow(asdict(sample))
                handle.flush()
                is_error = _sample_is_error(sample)
                stats.record(sample.timestamp, is_error=is_error)
                stats.cycles += 1
                if is_error and not previous_error:
                    logger.warning("Host Collector reportó muestra de error: %s", sample.error)
                elif not is_error and previous_error:
                    logger.info("Host Collector recuperado tras error de muestra")
                previous_error = is_error
                cycle += 1
                due = window.start_monotonic + cycle * config.host_interval_seconds
                lag = _wait_until(stop_event, due, monotonic_fn)
                if lag > 0:
                    stats.overrun_cycles += 1
                    stats.max_scheduler_lag_seconds = max(stats.max_scheduler_lag_seconds, lag)
    except Exception as exc:
        stats.fatal_error = str(exc)
        ready_event.set()
        logger.exception("Fallo fatal de Host Collector: %s", exc)
    finally:
        logger.info("Host Collector finalizado: rows=%d errors=%d", stats.rows, stats.error_rows)


def _run_probe_worker(
    *,
    config: ExperimentConfig,
    output_path: Path,
    start_event: threading.Event,
    stop_event: threading.Event,
    ready_event: threading.Event,
    window: _RunWindow,
    stats: CollectorStats,
    logger: logging.Logger,
    monotonic_fn: Callable[[], float],
    engine_factory: Callable[[], Any],
) -> None:
    targets = [ProbeTarget(name=target.name, address=target.address) for target in config.targets]
    try:
        engine = engine_factory()
        fieldnames = [field.name for field in fields(ProbeSample)]
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            handle.flush()
            ready_event.set()
            start_event.wait()
            logger.info("Active Probe Engine activo: targets=%s", ",".join(target.name for target in targets))
            previous_reachability: dict[str, bool] = {}
            cycle = 0
            while not stop_event.is_set() and monotonic_fn() < window.deadline_monotonic:
                samples = engine.probe_all(targets)
                for sample in samples:
                    writer.writerow(asdict(sample))
                    is_error = _sample_is_error(sample)
                    stats.record(sample.timestamp, is_error=is_error)
                    previous = previous_reachability.get(sample.target)
                    current = bool(sample.reachability)
                    if previous is None and not current:
                        logger.warning("Target no alcanzable: %s", sample.target)
                    elif previous is True and not current:
                        logger.warning("Target pasó a no alcanzable: %s", sample.target)
                    elif previous is False and current:
                        logger.info("Target recuperó reachability: %s", sample.target)
                    previous_reachability[sample.target] = current
                handle.flush()
                stats.cycles += 1
                cycle += 1
                due = window.start_monotonic + cycle * config.probe_interval_seconds
                lag = _wait_until(stop_event, due, monotonic_fn)
                if lag > 0:
                    stats.overrun_cycles += 1
                    stats.max_scheduler_lag_seconds = max(stats.max_scheduler_lag_seconds, lag)
    except Exception as exc:
        stats.fatal_error = str(exc)
        ready_event.set()
        logger.exception("Fallo fatal de Active Probe Engine: %s", exc)
    finally:
        logger.info("Active Probe Engine finalizado: rows=%d errors=%d", stats.rows, stats.error_rows)


def _install_signal_handlers(
    stop_event: threading.Event,
    stop_reason: dict[str, str],
) -> tuple[dict[int, Any], bool]:
    previous: dict[int, Any] = {}
    installed = False

    def handler(signum: int, _frame: Any) -> None:
        try:
            name = signal.Signals(signum).name
        except Exception:
            name = str(signum)
        stop_reason["value"] = f"signal:{name}"
        stop_event.set()

    candidates = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        candidates.append(signal.SIGBREAK)  # type: ignore[attr-defined]
    for sig in candidates:
        try:
            previous[int(sig)] = signal.getsignal(sig)
            signal.signal(sig, handler)
            installed = True
        except (ValueError, OSError, RuntimeError):
            continue
    return previous, installed


def _restore_signal_handlers(previous: dict[int, Any]) -> None:
    for signum, handler in previous.items():
        try:
            signal.signal(signum, handler)
        except (ValueError, OSError, RuntimeError):
            pass


def _collector_payload(stats: CollectorStats) -> dict[str, Any]:
    return {
        "interval_seconds": stats.interval_seconds,
        "cycles": stats.cycles,
        "rows": stats.rows,
        "error_rows": stats.error_rows,
        "overrun_cycles": stats.overrun_cycles,
        "max_scheduler_lag_seconds": round(stats.max_scheduler_lag_seconds, 6),
        "first_timestamp": stats.first_timestamp,
        "last_timestamp": stats.last_timestamp,
        "fatal_error": stats.fatal_error,
    }


def _status_for_run(stop_reason: str, stats: Sequence[CollectorStats]) -> str:
    if stop_reason.startswith("signal:") or stop_reason == "keyboard_interrupt":
        return "interrupted"
    if any(item.fatal_error for item in stats):
        return "partial_failure"
    if any(item.error_rows for item in stats):
        return "completed_with_sample_errors"
    return "completed"


def run_linkprobe(
    config_path: str | Path,
    *,
    sensor_version: str = __version__,
    include_hostname: bool = False,
    redact_local_addresses: bool = False,
    install_signal_handlers: bool = True,
    monotonic_fn: Callable[[], float] = time.monotonic,
    interface_collector_factory: Callable[[str], Any] | None = None,
    host_collector_factory: Callable[[], Any] | None = None,
    probe_engine_factory: Callable[[], Any] | None = None,
    preflight_fn: Callable[..., Any] = run_preflight,
) -> OrchestratorResult:
    config = load_experiment_config(config_path)
    run_dir = config.output_directory
    _ensure_clean_run_directory(run_dir)

    original_config = Path(config.config_path)
    preflight = preflight_fn(
        original_config,
        output_path=run_dir / "preflight.json",
        include_hostname=include_hostname,
        redact_local_addresses=redact_local_addresses,
        sensor_version=sensor_version,
    )

    config_copy = _copy_exact_config(original_config, run_dir)
    orchestration_path = run_dir / ORCHESTRATION_FILENAME

    if not bool(preflight.payload.get("ready_for_run")):
        payload = {
            "schema_version": ORCHESTRATOR_SCHEMA_VERSION,
            "mode": "run-linkprobe",
            "run_id": config.run_id,
            "status": "preflight_blocked",
            "stop_reason": "preflight_not_ready",
            "preflight": {
                "status": preflight.payload.get("status"),
                "ready_for_run": False,
                "path": "preflight.json",
            },
            "configuration": {
                "path": CONFIG_COPY_FILENAME,
                "sha256": _sha256_bytes(config_copy.read_bytes()),
            },
            "collection_started": False,
            "network_safety": {
                "payload_capture": False,
                "network_configuration_modified": False,
                "firewall_modified": False,
                "routing_modified": False,
                "unauthorized_discovery": False,
                "active_probes_started": False,
            },
        }
        orchestration_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return OrchestratorResult(
            run_dir=run_dir,
            status="preflight_blocked",
            stop_reason="preflight_not_ready",
            preflight_ready=False,
            orchestration_path=orchestration_path,
            integrity_valid=None,
            checksums_path=None,
            metadata_path=None,
            integrity_report_path=None,
        )

    logger, log_path = _open_logger(run_dir, config.run_id)
    logger.info("Inicio run_id=%s sensor_version=%s", config.run_id, sensor_version)
    logger.info(
        "Configuración: interface=%s interface_interval=%.3fs host_interval=%.3fs probe_interval=%.3fs duration=%.3fs targets=%s",
        config.interface_name,
        config.interface_interval_seconds,
        config.host_interval_seconds,
        config.probe_interval_seconds,
        config.duration_seconds,
        ",".join(target.name for target in config.targets),
    )
    logger.info("Preflight aprobado: status=%s", preflight.payload.get("status"))

    interface_collector_factory = interface_collector_factory or (lambda name: InterfaceCollector(name))
    host_collector_factory = host_collector_factory or (lambda: HostCollector())
    probe_engine_factory = probe_engine_factory or (
        lambda: ActiveProbeEngine(
            count_per_target=config.probe_count_per_target,
            timeout_ms=config.probe_timeout_ms,
            payload_bytes=config.probe_payload_bytes,
        )
    )

    interface_stats = CollectorStats("interface", config.interface_interval_seconds)
    host_stats = CollectorStats("host", config.host_interval_seconds)
    probe_stats = CollectorStats("probes", config.probe_interval_seconds)
    all_stats = [interface_stats, host_stats, probe_stats]

    start_event = threading.Event()
    stop_event = threading.Event()
    ready_events = [threading.Event(), threading.Event(), threading.Event()]
    window = _RunWindow()
    stop_reason = {"value": "duration_elapsed"}

    threads = [
        threading.Thread(
            name="linkprobe-interface",
            target=_run_interface_worker,
            kwargs={
                "config": config,
                "output_path": run_dir / "interface_samples.csv",
                "start_event": start_event,
                "stop_event": stop_event,
                "ready_event": ready_events[0],
                "window": window,
                "stats": interface_stats,
                "logger": logger,
                "monotonic_fn": monotonic_fn,
                "collector_factory": interface_collector_factory,
            },
        ),
        threading.Thread(
            name="linkprobe-host",
            target=_run_host_worker,
            kwargs={
                "config": config,
                "output_path": run_dir / "host_samples.csv",
                "start_event": start_event,
                "stop_event": stop_event,
                "ready_event": ready_events[1],
                "window": window,
                "stats": host_stats,
                "logger": logger,
                "monotonic_fn": monotonic_fn,
                "collector_factory": host_collector_factory,
            },
        ),
        threading.Thread(
            name="linkprobe-probes",
            target=_run_probe_worker,
            kwargs={
                "config": config,
                "output_path": run_dir / "probe_samples.csv",
                "start_event": start_event,
                "stop_event": stop_event,
                "ready_event": ready_events[2],
                "window": window,
                "stats": probe_stats,
                "logger": logger,
                "monotonic_fn": monotonic_fn,
                "engine_factory": probe_engine_factory,
            },
        ),
    ]

    previous_handlers: dict[int, Any] = {}
    signal_handlers_installed = False
    try:
        if install_signal_handlers:
            previous_handlers, signal_handlers_installed = _install_signal_handlers(stop_event, stop_reason)
        for thread in threads:
            thread.start()
        for event in ready_events:
            if not event.wait(READY_TIMEOUT_SECONDS):
                stop_reason["value"] = "collector_start_timeout"
                stop_event.set()
                break

        window.start_monotonic = monotonic_fn()
        window.deadline_monotonic = window.start_monotonic + config.duration_seconds
        window.started_utc = _utc_now()
        logger.info("T0 común establecido: %s", window.started_utc)
        start_event.set()

        if not stop_event.is_set():
            stop_event.wait(config.duration_seconds)
        if not stop_event.is_set():
            stop_reason["value"] = "duration_elapsed"
            stop_event.set()
    except KeyboardInterrupt:
        stop_reason["value"] = "keyboard_interrupt"
        stop_event.set()
    finally:
        start_event.set()
        stop_event.set()
        join_timeout = max(
            5.0,
            (config.probe_timeout_ms / 1000.0) * config.probe_count_per_target * len(config.targets) + 5.0,
        )
        for thread in threads:
            thread.join(join_timeout)
        if install_signal_handlers:
            _restore_signal_handlers(previous_handlers)

    for thread, stats in zip(threads, all_stats):
        if thread.is_alive() and stats.fatal_error is None:
            stats.fatal_error = f"Thread no finalizó dentro de {join_timeout:.1f}s"

    ended_utc = _utc_now()
    elapsed_seconds = max(0.0, monotonic_fn() - window.start_monotonic)
    status = _status_for_run(stop_reason["value"], all_stats)
    logger.info(
        "Captura finalizada: status=%s stop_reason=%s elapsed=%.3fs",
        status,
        stop_reason["value"],
        elapsed_seconds,
    )
    logger.info("Se cerrará el log antes del sellado SHA-256 para preservar integridad")

    orchestration_payload = {
        "schema_version": ORCHESTRATOR_SCHEMA_VERSION,
        "mode": "run-linkprobe",
        "run_id": config.run_id,
        "status": status,
        "stop_reason": stop_reason["value"],
        "collection_started": True,
        "automatic_execution": True,
        "requested_duration_seconds": config.duration_seconds,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "started_utc": window.started_utc,
        "ended_utc": ended_utc,
        "signal_handlers_installed": signal_handlers_installed,
        "preflight": {
            "status": preflight.payload.get("status"),
            "ready_for_run": True,
            "path": "preflight.json",
        },
        "configuration": {
            "path": CONFIG_COPY_FILENAME,
            "sha256": _sha256_bytes(config_copy.read_bytes()),
        },
        "interface": config.interface_name,
        "targets": [target.name for target in config.targets],
        "collectors": {
            "interface": _collector_payload(interface_stats),
            "host": _collector_payload(host_stats),
            "probes": _collector_payload(probe_stats),
        },
        "artifacts": {
            "preflight": "preflight.json",
            "config_copy": CONFIG_COPY_FILENAME,
            "interface_samples": "interface_samples.csv",
            "host_samples": "host_samples.csv",
            "probe_samples": "probe_samples.csv",
            "log": LOG_RELATIVE_PATH.as_posix(),
        },
        "integrity": {
            "seal_requested": True,
            "algorithm": "SHA-256",
            "strict_verification_requested": True,
            "note": "El resultado de la verificación se escribe después en integrity_report.json, archivo excluido del conjunto auto-sellado.",
        },
        "network_safety": {
            "payload_capture": False,
            "customer_communications_inspected": False,
            "network_configuration_modified": False,
            "firewall_modified": False,
            "routing_modified": False,
            "services_modified": False,
            "unauthorized_discovery": False,
            "active_probes_limited_to_configured_targets": True,
        },
        "limitations": [
            "La simultaneidad se implementa con tres workers coordinados por un T0 común; cada operación individual conserva su propio timestamp UTC.",
            "Si una ronda de probes tarda más que el intervalo configurado, no se solapan rondas; se registra scheduler lag y la siguiente ronda inicia tan pronto como sea posible.",
            "Un error de muestra no detiene los otros collectors. Un fallo fatal de un worker deja el run como partial_failure.",
        ],
    }
    orchestration_path.write_text(
        json.dumps(orchestration_payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _close_logger(logger)

    build = finalize_integrity(
        run_dir,
        config=IntegrityConfig(
            run_id=config.run_id,
            interface=config.interface_name,
            targets=tuple(target.name for target in config.targets),
            requested_duration_seconds=config.duration_seconds,
            config_path=config_copy,
            sensor_version=sensor_version,
            analyzer_version=__version__,
        ),
    )
    verified = verify_integrity(run_dir, strict_untracked=True)

    return OrchestratorResult(
        run_dir=run_dir,
        status=status,
        stop_reason=stop_reason["value"],
        preflight_ready=True,
        orchestration_path=orchestration_path,
        integrity_valid=verified.valid,
        checksums_path=build.checksums_path,
        metadata_path=build.metadata_path,
        integrity_report_path=verified.report_path,
    )
