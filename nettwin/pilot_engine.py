from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
from typing import Any, Callable

from nettwin import __version__
from nettwin.integrity_engine import verify_integrity
from nettwin.orchestrator_runtime import run_linkprobe
from nettwin.preflight_runtime import load_experiment_config_compatible
from nettwin.report_cli import write_analysis_manifest
from nettwin.report_engine import ReportConfig, ReportResult, generate_report


PILOT_SCHEMA_VERSION = "linkprobe-pilot-v1"
PILOT_SUMMARY_FILENAME = "pilot_summary.json"
PRODUCTION_MODE = "hachenet_pilot"
LOCAL_MODE = "local_acceptance"
PRODUCTION_DURATION_SECONDS = 4 * 60 * 60
DEFAULT_INTERVAL_SECONDS = 5.0
MAX_TARGETS = 3
MAX_PROBE_PAYLOAD_BYTES = 128
MAX_PROBE_COUNT_PER_TARGET = 3
MIN_PROBE_INTERVAL_SECONDS = 2.0

_DOCUMENTATION_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("2001:db8::/32"),
)
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class PilotValidationResult:
    config_path: Path
    mode: str | None
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    estimate: dict[str, Any]
    experiment: Any | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class PilotRunResult:
    status: str
    failed_stage: str | None
    message: str
    config_path: Path
    run_dir: Path | None
    report_dir: Path | None
    summary_path: Path
    validation: PilotValidationResult
    orchestrator_result: Any | None
    report_result: ReportResult | None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: str | Path) -> tuple[Path, dict[str, Any]]:
    config_path = Path(path).resolve()
    if not config_path.exists() or not config_path.is_file():
        raise ValueError(f"No existe el archivo de configuración: {config_path}")
    if config_path.suffix.lower() != ".json":
        raise ValueError("La configuración del piloto debe ser JSON")
    try:
        payload = json.loads(config_path.read_bytes().decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON inválido en {config_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("La raíz de la configuración del piloto debe ser un objeto JSON")
    return config_path, payload


def _resolve_external_path(config_path: Path, value: Any, default_name: str) -> Path:
    text = str(value or default_name).strip() or default_name
    path = Path(text)
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    return path


def _is_documentation_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in _DOCUMENTATION_NETWORKS)


def _unsafe_production_address(address: str) -> str | None:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    if parsed.is_loopback:
        return "loopback"
    if parsed.is_unspecified:
        return "unspecified"
    if parsed.is_multicast:
        return "multicast"
    if parsed.is_link_local:
        return "link-local"
    if _is_documentation_address(address):
        return "documentation/example"
    return None


def _target_raw_map(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    targets = raw.get("targets")
    if not isinstance(targets, list):
        return result
    for item in targets:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                result[name] = item
    return result


def _estimate(experiment: Any | None) -> dict[str, Any]:
    if experiment is None:
        return {}
    cycles = int(math.ceil(experiment.duration_seconds / experiment.probe_interval_seconds))
    target_count = len(experiment.targets)
    echo_requests = cycles * target_count * experiment.probe_count_per_target
    payload_bytes = echo_requests * experiment.probe_payload_bytes
    expected_interface_rows = int(math.ceil(experiment.duration_seconds / experiment.interface_interval_seconds))
    expected_host_rows = int(math.ceil(experiment.duration_seconds / experiment.host_interval_seconds))
    expected_probe_rows = cycles * target_count
    return {
        "duration_seconds": experiment.duration_seconds,
        "duration_hours": experiment.duration_seconds / 3600.0,
        "target_count": target_count,
        "probe_cycles": cycles,
        "icmp_echo_requests": echo_requests,
        "outbound_icmp_payload_bytes_estimate": payload_bytes,
        "expected_rows": {
            "interface": expected_interface_rows,
            "host": expected_host_rows,
            "probes": expected_probe_rows,
            "total": expected_interface_rows + expected_host_rows + expected_probe_rows,
        },
        "note": "Estimación de payload ICMP saliente; no incluye cabeceras IP/ICMP ni respuestas.",
    }


def validate_pilot_config(config_path: str | Path) -> PilotValidationResult:
    path, raw = _read_json(config_path)
    errors: list[str] = []
    warnings: list[str] = []

    pilot = raw.get("pilot")
    if not isinstance(pilot, dict):
        pilot = {}
        errors.append("Falta objeto requerido 'pilot'")

    mode = str(pilot.get("mode") or "").strip() or None
    if mode not in {PRODUCTION_MODE, LOCAL_MODE}:
        errors.append("pilot.mode debe ser 'hachenet_pilot' o 'local_acceptance'")

    if str(pilot.get("client") or "").strip().lower() != "hachenet":
        errors.append("pilot.client debe ser 'HacheNet'")

    if str(pilot.get("schema_version") or "").strip() != PILOT_SCHEMA_VERSION:
        errors.append(f"pilot.schema_version debe ser '{PILOT_SCHEMA_VERSION}'")

    experiment = None
    try:
        experiment = load_experiment_config_compatible(path)
    except ValueError as exc:
        errors.append(f"Configuración base inválida: {exc}")

    if experiment is not None:
        if not _RUN_ID_RE.fullmatch(experiment.run_id):
            errors.append("run.run_id solo puede contener letras, números, punto, guion y guion bajo")
        if len(experiment.targets) > MAX_TARGETS:
            errors.append(f"El piloto admite como máximo {MAX_TARGETS} targets autorizados")
        if experiment.probe_interval_seconds < MIN_PROBE_INTERVAL_SECONDS:
            errors.append(
                f"probes.interval_seconds debe ser >= {MIN_PROBE_INTERVAL_SECONDS:g} s para limitar carga activa"
            )
        if experiment.probe_payload_bytes > MAX_PROBE_PAYLOAD_BYTES:
            errors.append(
                f"probes.payload_bytes debe ser <= {MAX_PROBE_PAYLOAD_BYTES} bytes en Fase 15"
            )
        if experiment.probe_count_per_target > MAX_PROBE_COUNT_PER_TARGET:
            errors.append(
                f"probes.count_per_target debe ser <= {MAX_PROBE_COUNT_PER_TARGET} en Fase 15"
            )

        raw_targets = _target_raw_map(raw)
        if mode == PRODUCTION_MODE:
            if abs(experiment.duration_seconds - PRODUCTION_DURATION_SECONDS) > 1e-9:
                errors.append("El piloto HacheNet de Fase 15 debe durar exactamente 14400 s (4 h)")
            if not experiment.run_id.upper().startswith("HACHENET-"):
                errors.append("En modo HacheNet, run.run_id debe comenzar por 'HACHENET-'")

            authorization = pilot.get("authorization")
            if not isinstance(authorization, dict):
                errors.append("Falta pilot.authorization")
            else:
                if authorization.get("confirmed") is not True:
                    errors.append("pilot.authorization.confirmed debe ser true antes del piloto real")
                if not str(authorization.get("reference") or "").strip():
                    errors.append("pilot.authorization.reference debe identificar la aprobación del piloto")
                if not str(authorization.get("approved_by") or "").strip():
                    errors.append("pilot.authorization.approved_by debe identificar quién aprobó los targets")

            for target in experiment.targets:
                raw_target = raw_targets.get(target.name, {})
                if raw_target.get("authorized") is not True:
                    errors.append(f"Target '{target.name}' debe declarar authorized=true")
                if str(raw_target.get("role") or "").strip().lower() in {"", "unspecified"}:
                    errors.append(f"Target '{target.name}' debe declarar un role técnico explícito")
                unsafe = _unsafe_production_address(target.address)
                if unsafe:
                    errors.append(
                        f"Target '{target.name}' usa una dirección {unsafe} no válida para el piloto real: {target.address}"
                    )

        elif mode == LOCAL_MODE:
            if experiment.duration_seconds > 300:
                errors.append("local_acceptance debe durar <= 300 s")
            if len(experiment.targets) != 1:
                errors.append("local_acceptance debe usar exactamente un target")
            elif experiment.targets[0].address != "127.0.0.1":
                errors.append("local_acceptance solo permite 127.0.0.1")
            if experiment.probe_count_per_target != 1:
                errors.append("local_acceptance requiere probes.count_per_target=1")
            if experiment.probe_payload_bytes > 64:
                errors.append("local_acceptance requiere probes.payload_bytes <= 64")

        if experiment.capacity_mbps is None:
            warnings.append(
                "interface.capacity_mbps es N/D; utilización quedará N/D y no se sustituirá con velocidad NIC"
            )

    if not __version__.startswith("0.3"):
        errors.append(f"Fase 15 requiere NetTwin LinkProbe v0.3.x; versión actual={__version__}")

    return PilotValidationResult(
        config_path=path,
        mode=mode,
        valid=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        estimate=_estimate(experiment),
        experiment=experiment,
        raw=raw,
    )


def build_pilot_config(
    output_path: str | Path,
    *,
    local_acceptance: bool = False,
    interface_name: str | None = None,
    run_id: str | None = None,
) -> Path:
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f"El archivo ya existe; no se sobrescribe configuración del piloto: {path}")

    if local_acceptance:
        if not interface_name:
            raise ValueError("local_acceptance requiere interface_name explícita")
        mode = LOCAL_MODE
        selected_run_id = run_id or "LOCAL-PILOT-ACCEPTANCE-001"
        payload = {
            "run": {"run_id": selected_run_id, "duration_seconds": 12},
            "interface": {"name": interface_name, "interval_seconds": 2, "capacity_mbps": None},
            "host": {"interval_seconds": 2},
            "probes": {"interval_seconds": 2, "timeout_ms": 1000, "payload_bytes": 32, "count_per_target": 1},
            "targets": [
                {
                    "name": "local_loopback",
                    "address": "127.0.0.1",
                    "role": "phase15_local_acceptance_only",
                    "authorized": True,
                }
            ],
            "output": {"directory": "./run", "minimum_free_disk_mb": 50},
            "pilot": {
                "schema_version": PILOT_SCHEMA_VERSION,
                "phase": 15,
                "mode": mode,
                "client": "HacheNet",
                "link_name": "LOCAL-PILOT-ACCEPTANCE",
                "report_directory": "./report",
                "summary_path": "./pilot_summary.json",
                "authorization": {
                    "confirmed": False,
                    "reference": "local-loopback-no-external-target",
                    "approved_by": "local-user",
                },
                "safety": {
                    "external_network_probe_performed": False,
                    "payload_capture": False,
                    "unauthorized_discovery": False,
                },
            },
        }
    else:
        mode = PRODUCTION_MODE
        selected_run_id = run_id or "HACHENET-YYYYMMDD-LINK01-001"
        payload = {
            "run": {"run_id": selected_run_id, "duration_seconds": PRODUCTION_DURATION_SECONDS},
            "interface": {
                "name": interface_name or "REEMPLAZAR_INTERFAZ_AUTORIZADA",
                "interval_seconds": DEFAULT_INTERVAL_SECONDS,
                "capacity_mbps": None,
            },
            "host": {"interval_seconds": DEFAULT_INTERVAL_SECONDS},
            "probes": {
                "interval_seconds": DEFAULT_INTERVAL_SECONDS,
                "timeout_ms": 1000,
                "payload_bytes": 32,
                "count_per_target": 1,
            },
            "targets": [
                {
                    "name": "REEMPLAZAR_TARGET_AUTORIZADO",
                    "address": "REEMPLAZAR_IP_AUTORIZADA",
                    "role": "REEMPLAZAR_ROL",
                    "authorized": False,
                }
            ],
            "output": {"directory": "./run", "minimum_free_disk_mb": 100},
            "pilot": {
                "schema_version": PILOT_SCHEMA_VERSION,
                "phase": 15,
                "mode": mode,
                "client": "HacheNet",
                "link_name": "LINK-01",
                "report_directory": "./report",
                "summary_path": "./pilot_summary.json",
                "authorization": {
                    "confirmed": False,
                    "reference": "REEMPLAZAR_REFERENCIA_DE_APROBACION",
                    "approved_by": "REEMPLAZAR_RESPONSABLE_HACHENET",
                },
                "safety": {
                    "payload_capture": False,
                    "customer_communications_inspected": False,
                    "network_configuration_modified": False,
                    "firewall_modified": False,
                    "routing_modified": False,
                    "unauthorized_discovery": False,
                    "aggressive_throughput_test": False,
                },
            },
        }

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _required_run_artifacts() -> tuple[str, ...]:
    return (
        "experiment_config.json",
        "preflight.json",
        "interface_samples.csv",
        "host_samples.csv",
        "probe_samples.csv",
        "orchestration.json",
        "run_metadata.json",
        "checksums.sha256",
        "logs/linkprobe.log",
    )


def _required_report_artifacts() -> tuple[str, ...]:
    return (
        "dataset_quality.json",
        "dataset_quality_summary.csv",
        "analysis.json",
        "report.json",
        "report.html",
        "analysis/quality_summary.json",
        "analysis/events.json",
        "analysis/correlations.json",
        "analysis/evidence_records.json",
        "analysis/event_fingerprints.json",
    )


def _artifacts(root: Path, names: tuple[str, ...]) -> dict[str, bool]:
    return {name: (root / name).is_file() for name in names}


def _write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _failure(
    *,
    validation: PilotValidationResult,
    summary_path: Path,
    summary: dict[str, Any],
    stage: str,
    message: str,
    orchestrator_result: Any | None = None,
    report_result: ReportResult | None = None,
) -> PilotRunResult:
    summary["status"] = "FAIL"
    summary["failed_stage"] = stage
    summary["message"] = message
    if stage in summary.get("stages", {}):
        summary["stages"][stage] = "FAIL"
    _write_summary(summary_path, summary)
    return PilotRunResult(
        status="FAIL",
        failed_stage=stage,
        message=message,
        config_path=validation.config_path,
        run_dir=None if validation.experiment is None else validation.experiment.output_directory,
        report_dir=None,
        summary_path=summary_path,
        validation=validation,
        orchestrator_result=orchestrator_result,
        report_result=report_result,
    )


def run_pilot(
    config_path: str | Path,
    *,
    authorized_execution: bool = False,
    include_hostname: bool = False,
    redact_local_addresses: bool = False,
    orchestrator_fn: Callable[..., Any] = run_linkprobe,
    report_fn: Callable[..., ReportResult] = generate_report,
    analysis_manifest_fn: Callable[[ReportResult], Path] = write_analysis_manifest,
    verify_fn: Callable[..., Any] = verify_integrity,
) -> PilotRunResult:
    validation = validate_pilot_config(config_path)
    pilot = validation.raw.get("pilot") if isinstance(validation.raw.get("pilot"), dict) else {}
    summary_path = _resolve_external_path(
        validation.config_path, pilot.get("summary_path"), PILOT_SUMMARY_FILENAME
    )

    if not validation.valid or validation.experiment is None:
        summary = {
            "schema_version": PILOT_SCHEMA_VERSION,
            "phase": 15,
            "status": "FAIL",
            "failed_stage": "validation",
            "message": "La configuración no cumple los requisitos de Fase 15.",
            "validation": {"errors": list(validation.errors), "warnings": list(validation.warnings)},
        }
        _write_summary(summary_path, summary)
        return PilotRunResult(
            status="FAIL", failed_stage="validation", message=summary["message"],
            config_path=validation.config_path, run_dir=None, report_dir=None,
            summary_path=summary_path, validation=validation,
            orchestrator_result=None, report_result=None,
        )

    if validation.mode == PRODUCTION_MODE and not authorized_execution:
        summary = {
            "schema_version": PILOT_SCHEMA_VERSION,
            "phase": 15,
            "status": "FAIL",
            "failed_stage": "authorization_gate",
            "message": "El piloto real requiere confirmación explícita de ejecución autorizada.",
            "validation": {"errors": [], "warnings": list(validation.warnings)},
        }
        _write_summary(summary_path, summary)
        return PilotRunResult(
            status="FAIL", failed_stage="authorization_gate", message=summary["message"],
            config_path=validation.config_path, run_dir=validation.experiment.output_directory,
            report_dir=None, summary_path=summary_path, validation=validation,
            orchestrator_result=None, report_result=None,
        )

    run_dir = validation.experiment.output_directory
    report_dir = _resolve_external_path(
        validation.config_path, pilot.get("report_directory"), "report"
    )
    try:
        report_dir.resolve().relative_to(run_dir.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("pilot.report_directory debe quedar fuera del run sellado")

    if report_dir.exists() and any(report_dir.iterdir()):
        raise ValueError(f"El directorio de reporte ya contiene archivos: {report_dir}")
    if summary_path.exists():
        raise ValueError(f"El resumen del piloto ya existe y no se sobrescribe: {summary_path}")

    summary: dict[str, Any] = {
        "schema_version": PILOT_SCHEMA_VERSION,
        "phase": 15,
        "mode": validation.mode,
        "status": "RUNNING",
        "failed_stage": None,
        "run_id": validation.experiment.run_id,
        "software_version": __version__,
        "automatic_execution": True,
        "authorization_gate_passed": validation.mode == LOCAL_MODE or authorized_execution,
        "validation": {
            "errors": [],
            "warnings": list(validation.warnings),
            "estimate": validation.estimate,
        },
        "paths": {
            "config": str(validation.config_path),
            "run": str(run_dir),
            "report": str(report_dir),
            "summary": str(summary_path),
        },
        "stages": {
            "validation": "PASS",
            "preflight_capture": "PENDING",
            "integrity_before_report": "PENDING",
            "analysis_report": "PENDING",
            "integrity_after_report": "PENDING",
        },
        "security": {
            "payload_capture": False,
            "customer_communications_inspected": False,
            "network_configuration_modified": False,
            "firewall_modified": False,
            "routing_modified": False,
            "unauthorized_discovery": False,
            "aggressive_throughput_test": False,
            "active_probes_limited_to_configured_targets": True,
        },
    }
    _write_summary(summary_path, summary)

    try:
        orchestrator = orchestrator_fn(
            validation.config_path,
            sensor_version=__version__,
            include_hostname=include_hostname,
            redact_local_addresses=redact_local_addresses,
        )
    except Exception as exc:
        return _failure(
            validation=validation, summary_path=summary_path, summary=summary,
            stage="preflight_capture", message=f"Orquestador falló: {exc}"
        )

    capture_artifacts = _artifacts(run_dir, _required_run_artifacts())
    capture_ok = (
        bool(getattr(orchestrator, "preflight_ready", False))
        and getattr(orchestrator, "integrity_valid", None) is True
        and str(getattr(orchestrator, "status", "")) == "completed"
        and all(capture_artifacts.values())
    )
    summary["capture"] = {
        "status": getattr(orchestrator, "status", None),
        "stop_reason": getattr(orchestrator, "stop_reason", None),
        "preflight_ready": bool(getattr(orchestrator, "preflight_ready", False)),
        "integrity_valid": getattr(orchestrator, "integrity_valid", None),
        "artifacts": capture_artifacts,
    }
    if not capture_ok:
        return _failure(
            validation=validation, summary_path=summary_path, summary=summary,
            stage="preflight_capture",
            message="La captura no terminó limpia o faltan artefactos obligatorios.",
            orchestrator_result=orchestrator,
        )
    summary["stages"]["preflight_capture"] = "PASS"

    try:
        verified_before = verify_fn(run_dir, strict_untracked=True)
    except Exception as exc:
        return _failure(
            validation=validation, summary_path=summary_path, summary=summary,
            stage="integrity_before_report", message=f"Verificación previa falló: {exc}",
            orchestrator_result=orchestrator,
        )
    if not bool(getattr(verified_before, "valid", False)):
        return _failure(
            validation=validation, summary_path=summary_path, summary=summary,
            stage="integrity_before_report", message="Integridad estricta inválida antes del análisis.",
            orchestrator_result=orchestrator,
        )
    summary["stages"]["integrity_before_report"] = "PASS"
    manifest_path = run_dir / "checksums.sha256"
    manifest_before = _sha256(manifest_path)

    try:
        report_result = report_fn(
            run_dir,
            report_dir,
            config=ReportConfig(
                company="HacheNet",
                link_name=str(pilot.get("link_name") or validation.experiment.run_id),
                capacity_mbps=validation.experiment.capacity_mbps,
                render_pdf=False,
            ),
        )
        analysis_manifest = analysis_manifest_fn(report_result)
    except Exception as exc:
        return _failure(
            validation=validation, summary_path=summary_path, summary=summary,
            stage="analysis_report", message=f"Análisis/reporte falló: {exc}",
            orchestrator_result=orchestrator,
        )

    report_artifacts = _artifacts(report_dir, _required_report_artifacts())
    report_payload: dict[str, Any] = {}
    try:
        report_payload = json.loads(report_result.report_json.read_text(encoding="utf-8"))
    except Exception:
        pass
    report_ok = (
        bool(report_result.integrity_valid)
        and bool(report_result.safe_for_conclusions)
        and bool(report_result.analysis_executed)
        and analysis_manifest.is_file()
        and all(report_artifacts.values())
    )
    summary["report"] = {
        "integrity_valid": bool(report_result.integrity_valid),
        "quality_status": report_result.quality_status,
        "safe_for_conclusions": bool(report_result.safe_for_conclusions),
        "analysis_executed": bool(report_result.analysis_executed),
        "event_count": len(report_payload.get("events", [])),
        "finding_count": len(report_payload.get("findings", [])),
        "fingerprint_count": len(report_payload.get("fingerprints", [])),
        "artifacts": report_artifacts,
    }
    if not report_ok:
        return _failure(
            validation=validation, summary_path=summary_path, summary=summary,
            stage="analysis_report",
            message="El Quality Gate/reporte no cumplió los criterios de aceptación.",
            orchestrator_result=orchestrator, report_result=report_result,
        )
    summary["stages"]["analysis_report"] = "PASS"

    try:
        verified_after = verify_fn(run_dir, strict_untracked=True)
    except Exception as exc:
        return _failure(
            validation=validation, summary_path=summary_path, summary=summary,
            stage="integrity_after_report", message=f"Verificación posterior falló: {exc}",
            orchestrator_result=orchestrator, report_result=report_result,
        )
    manifest_after = _sha256(manifest_path)
    unchanged = bool(getattr(verified_after, "valid", False)) and manifest_before == manifest_after
    summary["integrity_preservation"] = {
        "valid_after_report": bool(getattr(verified_after, "valid", False)),
        "checksums_sha256_before": manifest_before,
        "checksums_sha256_after": manifest_after,
        "sealed_run_unchanged": unchanged,
    }
    if not unchanged:
        return _failure(
            validation=validation, summary_path=summary_path, summary=summary,
            stage="integrity_after_report",
            message="El run sellado cambió o perdió integridad después del reporte.",
            orchestrator_result=orchestrator, report_result=report_result,
        )
    summary["stages"]["integrity_after_report"] = "PASS"
    summary["status"] = "PASS"
    summary["failed_stage"] = None
    summary["message"] = (
        "Fase 15 completó validación, preflight, captura, integridad, análisis e informe sin alterar evidencia."
    )
    _write_summary(summary_path, summary)
    return PilotRunResult(
        status="PASS", failed_stage=None, message=summary["message"],
        config_path=validation.config_path, run_dir=run_dir, report_dir=report_dir,
        summary_path=summary_path, validation=validation,
        orchestrator_result=orchestrator, report_result=report_result,
    )
