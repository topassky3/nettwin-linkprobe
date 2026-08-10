from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import socket
from types import SimpleNamespace
from typing import Any, Callable

import psutil

from nettwin import __version__
from nettwin.integrity_engine import verify_integrity
from nettwin.orchestrator_runtime import run_linkprobe
from nettwin.report_cli import write_analysis_manifest
from nettwin.report_engine import ReportConfig, ReportResult, generate_report


DRY_RUN_SCHEMA_VERSION = "linkprobe-dry-run-v1"
DRY_RUN_CONFIG_FILENAME = "dry_run_config.json"
DRY_RUN_SUMMARY_FILENAME = "dry_run_summary.json"
LOOPBACK_TARGET_NAME = "local_loopback"
LOOPBACK_TARGET_ADDRESS = "127.0.0.1"

_VIRTUAL_HINTS = (
    "tailscale", "vpn", "virtual", "veth", "docker", "wsl", "hyper-v",
    "vethernet", "vmware", "virtualbox", "tap", "tun", "loopback",
)
_PHYSICAL_HINTS = ("wi-fi", "wifi", "wlan", "wireless", "ethernet", "eth")


@dataclass(frozen=True)
class DryRunSettings:
    duration_seconds: float = 12.0
    interval_seconds: float = 2.0
    run_id: str = "LOCAL-DRYRUN-001"
    interface_name: str | None = None
    company: str = "HacheNet"
    link_name: str = "LOCAL-DRY-RUN"
    capacity_mbps: float | None = None
    sensor_version: str = __version__
    include_hostname: bool = False
    redact_local_addresses: bool = False

    def validate(self) -> None:
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds debe ser > 0")
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds debe ser > 0")
        if self.duration_seconds < self.interval_seconds * 2:
            raise ValueError("El dry-run debe permitir al menos dos ciclos por collector")
        if not self.run_id.strip():
            raise ValueError("run_id no puede estar vacío")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", self.run_id):
            raise ValueError("run_id solo puede contener letras, números, punto, guion y guion bajo")
        if not self.company.strip():
            raise ValueError("company no puede estar vacío")
        if not self.link_name.strip():
            raise ValueError("link_name no puede estar vacío")
        if self.capacity_mbps is not None and self.capacity_mbps <= 0:
            raise ValueError("capacity_mbps debe ser > 0 cuando se suministra")


@dataclass(frozen=True)
class DryRunResult:
    workspace: Path
    config_path: Path
    run_dir: Path
    report_dir: Path
    summary_path: Path
    status: str
    failed_stage: str | None
    message: str
    orchestrator_result: Any | None
    report_result: ReportResult | None


@dataclass(frozen=True)
class _InterfaceChoice:
    name: str
    reason: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _usable_ipv4(name: str, addresses: dict[str, list[Any]]) -> bool:
    for item in addresses.get(name, []):
        if item.family != socket.AF_INET:
            continue
        try:
            address = ipaddress.ip_address(str(item.address))
        except ValueError:
            continue
        if not address.is_loopback and not address.is_link_local:
            return True
    return False


def select_local_interface(preferred: str | None = None) -> tuple[str, str]:
    """Selecciona una interfaz local para el dry-run sin modificarla.

    Si el usuario suministra `preferred`, se conserva exactamente esa selección y
    Preflight será quien determine si está lista. En modo automático se prefieren
    interfaces físicas UP con contadores e IPv4 útil frente a VPN/virtuales.
    """
    if preferred:
        return preferred, "selección manual; Preflight validará existencia/estado"

    stats = psutil.net_if_stats()
    counters = psutil.net_io_counters(pernic=True)
    addresses = psutil.net_if_addrs()
    candidates: list[tuple[tuple[int, int, int, str], str, str]] = []

    for name in sorted(set(stats) | set(counters) | set(addresses)):
        stat = stats.get(name)
        if stat is None or not bool(stat.isup) or name not in counters:
            continue
        lowered = name.lower()
        virtual = any(hint in lowered for hint in _VIRTUAL_HINTS)
        physical_hint = any(hint in lowered for hint in _PHYSICAL_HINTS)
        useful_ipv4 = _usable_ipv4(name, addresses)
        score = (
            0 if useful_ipv4 else 1,
            0 if physical_hint and not virtual else 1,
            0 if not virtual else 1,
            lowered,
        )
        reason = (
            "interfaz física/preferida con IPv4 útil"
            if useful_ipv4 and physical_hint and not virtual
            else "mejor interfaz UP disponible para dry-run"
        )
        candidates.append((score, name, reason))

    if not candidates:
        raise ValueError("No se encontró una interfaz UP con contadores; use --interface manualmente")
    candidates.sort(key=lambda item: item[0])
    _, name, reason = candidates[0]
    return name, reason


def _ensure_clean_workspace(workspace: Path) -> None:
    if workspace.exists():
        if not workspace.is_dir():
            raise ValueError(f"La salida del dry-run no es un directorio: {workspace}")
        existing = list(workspace.iterdir())
        if existing:
            names = ", ".join(sorted(item.name for item in existing)[:8])
            raise ValueError(
                "El workspace del dry-run ya contiene archivos. Use un directorio nuevo para no "
                f"mezclar evidencia: {workspace} ({names})"
            )
    else:
        workspace.mkdir(parents=True, exist_ok=False)


def build_dry_run_config(
    workspace: str | Path,
    settings: DryRunSettings,
    *,
    interface_name: str,
) -> Path:
    settings.validate()
    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    expected_cycles = int(math.ceil(settings.duration_seconds / settings.interval_seconds))
    payload = {
        "run": {
            "run_id": settings.run_id,
            "duration_seconds": settings.duration_seconds,
        },
        "interface": {
            "name": interface_name,
            "interval_seconds": settings.interval_seconds,
            "capacity_mbps": settings.capacity_mbps,
        },
        "host": {"interval_seconds": settings.interval_seconds},
        "probes": {
            "interval_seconds": settings.interval_seconds,
            "timeout_ms": 1000,
            "payload_bytes": 32,
            "count_per_target": 1,
        },
        "targets": [
            {
                "name": LOOPBACK_TARGET_NAME,
                "address": LOOPBACK_TARGET_ADDRESS,
                "role": "phase14_local_loopback_only",
            }
        ],
        "output": {
            "directory": "./run",
            "minimum_free_disk_mb": 50,
        },
        "dry_run": {
            "phase": 14,
            "automatic_execution": True,
            "expected_cycles_per_collector": expected_cycles,
            "active_probe_scope": "loopback_only",
            "external_network_probe_performed": False,
            "purpose": (
                "Validar localmente el pipeline completo antes de HacheNet: preflight, captura, "
                "almacenamiento, integridad, análisis y reporte."
            ),
        },
    }
    path = root / DRY_RUN_CONFIG_FILENAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _required_run_artifacts(run_dir: Path) -> tuple[str, ...]:
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


def _required_report_artifacts(report_dir: Path) -> tuple[str, ...]:
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


def _artifact_status(root: Path, names: tuple[str, ...]) -> dict[str, bool]:
    return {name: (root / name).is_file() for name in names}


def _write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _base_summary(
    *,
    settings: DryRunSettings,
    interface_name: str,
    interface_reason: str,
    config_path: Path,
    run_dir: Path,
    report_dir: Path,
) -> dict[str, Any]:
    return {
        "schema_version": DRY_RUN_SCHEMA_VERSION,
        "phase": 14,
        "mode": "dry-run-local",
        "status": "RUNNING",
        "failed_stage": None,
        "automatic_execution": True,
        "manual_intervention_after_start": False,
        "run_id": settings.run_id,
        "interface": {
            "name": interface_name,
            "selection_reason": interface_reason,
        },
        "window": {
            "duration_seconds": settings.duration_seconds,
            "interval_seconds": settings.interval_seconds,
            "expected_cycles_per_collector": int(math.ceil(settings.duration_seconds / settings.interval_seconds)),
        },
        "active_probe": {
            "target_name": LOOPBACK_TARGET_NAME,
            "target_address": LOOPBACK_TARGET_ADDRESS,
            "scope": "loopback_only",
            "payload_bytes": 32,
            "count_per_target": 1,
            "external_network_probe_performed": False,
        },
        "paths": {
            "config": str(config_path),
            "run": str(run_dir),
            "report": str(report_dir),
        },
        "stages": {
            "prepare": "PASS",
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
            "external_network_probe_performed": False,
        },
    }


def _failure_result(
    *,
    workspace: Path,
    config_path: Path,
    run_dir: Path,
    report_dir: Path,
    summary_path: Path,
    summary: dict[str, Any],
    stage: str,
    message: str,
    orchestrator_result: Any | None,
    report_result: ReportResult | None,
) -> DryRunResult:
    summary["status"] = "FAIL"
    summary["failed_stage"] = stage
    summary["message"] = message
    if stage in summary.get("stages", {}):
        summary["stages"][stage] = "FAIL"
    _write_summary(summary_path, summary)
    return DryRunResult(
        workspace=workspace,
        config_path=config_path,
        run_dir=run_dir,
        report_dir=report_dir,
        summary_path=summary_path,
        status="FAIL",
        failed_stage=stage,
        message=message,
        orchestrator_result=orchestrator_result,
        report_result=report_result,
    )


def run_dry_run(
    workspace: str | Path,
    *,
    settings: DryRunSettings | None = None,
    interface_selector: Callable[[str | None], tuple[str, str]] = select_local_interface,
    orchestrator_fn: Callable[..., Any] = run_linkprobe,
    report_fn: Callable[..., ReportResult] = generate_report,
    analysis_manifest_fn: Callable[[ReportResult], Path] = write_analysis_manifest,
    verify_fn: Callable[..., Any] = verify_integrity,
) -> DryRunResult:
    """Ejecuta Fase 14 de principio a fin sin pasos manuales intermedios."""
    cfg = settings or DryRunSettings()
    cfg.validate()
    root = Path(workspace).resolve()
    _ensure_clean_workspace(root)

    interface_name, interface_reason = interface_selector(cfg.interface_name)
    config_path = build_dry_run_config(root, cfg, interface_name=interface_name)
    run_dir = root / "run"
    report_dir = root / "report"
    summary_path = root / DRY_RUN_SUMMARY_FILENAME
    summary = _base_summary(
        settings=cfg,
        interface_name=interface_name,
        interface_reason=interface_reason,
        config_path=config_path,
        run_dir=run_dir,
        report_dir=report_dir,
    )
    _write_summary(summary_path, summary)

    orchestrator_result: Any | None = None
    report_result: ReportResult | None = None
    try:
        orchestrator_result = orchestrator_fn(
            config_path,
            sensor_version=cfg.sensor_version,
            include_hostname=cfg.include_hostname,
            redact_local_addresses=cfg.redact_local_addresses,
        )
    except Exception as exc:
        return _failure_result(
            workspace=root, config_path=config_path, run_dir=run_dir, report_dir=report_dir,
            summary_path=summary_path, summary=summary, stage="preflight_capture",
            message=f"Orquestador falló: {exc}", orchestrator_result=None, report_result=None,
        )

    capture_ok = (
        bool(getattr(orchestrator_result, "preflight_ready", False))
        and getattr(orchestrator_result, "integrity_valid", None) is True
        and str(getattr(orchestrator_result, "status", "")) == "completed"
    )
    summary["capture"] = {
        "status": getattr(orchestrator_result, "status", None),
        "stop_reason": getattr(orchestrator_result, "stop_reason", None),
        "preflight_ready": bool(getattr(orchestrator_result, "preflight_ready", False)),
        "integrity_valid": getattr(orchestrator_result, "integrity_valid", None),
        "artifacts": _artifact_status(run_dir, _required_run_artifacts(run_dir)),
    }
    if not capture_ok:
        return _failure_result(
            workspace=root, config_path=config_path, run_dir=run_dir, report_dir=report_dir,
            summary_path=summary_path, summary=summary, stage="preflight_capture",
            message=(
                "La captura local no terminó limpia: se requiere preflight listo, status=completed "
                "e integridad estricta válida."
            ),
            orchestrator_result=orchestrator_result, report_result=None,
        )
    if not all(summary["capture"]["artifacts"].values()):
        return _failure_result(
            workspace=root, config_path=config_path, run_dir=run_dir, report_dir=report_dir,
            summary_path=summary_path, summary=summary, stage="preflight_capture",
            message="La captura terminó pero faltan artefactos obligatorios del run.",
            orchestrator_result=orchestrator_result, report_result=None,
        )
    summary["stages"]["preflight_capture"] = "PASS"

    try:
        verified_before = verify_fn(run_dir, strict_untracked=True)
    except Exception as exc:
        return _failure_result(
            workspace=root, config_path=config_path, run_dir=run_dir, report_dir=report_dir,
            summary_path=summary_path, summary=summary, stage="integrity_before_report",
            message=f"Verificación previa al reporte falló: {exc}",
            orchestrator_result=orchestrator_result, report_result=None,
        )
    if not bool(getattr(verified_before, "valid", False)):
        return _failure_result(
            workspace=root, config_path=config_path, run_dir=run_dir, report_dir=report_dir,
            summary_path=summary_path, summary=summary, stage="integrity_before_report",
            message="Integridad estricta inválida antes del análisis.",
            orchestrator_result=orchestrator_result, report_result=None,
        )
    summary["stages"]["integrity_before_report"] = "PASS"
    manifest_path = run_dir / "checksums.sha256"
    manifest_sha_before = _sha256(manifest_path)

    try:
        report_result = report_fn(
            run_dir,
            report_dir,
            config=ReportConfig(
                company=cfg.company,
                link_name=cfg.link_name,
                capacity_mbps=cfg.capacity_mbps,
                render_pdf=False,
            ),
        )
        analysis_manifest = analysis_manifest_fn(report_result)
    except Exception as exc:
        return _failure_result(
            workspace=root, config_path=config_path, run_dir=run_dir, report_dir=report_dir,
            summary_path=summary_path, summary=summary, stage="analysis_report",
            message=f"Pipeline de análisis/reporte falló: {exc}",
            orchestrator_result=orchestrator_result, report_result=report_result,
        )

    report_artifacts = _artifact_status(report_dir, _required_report_artifacts(report_dir))
    report_ok = (
        bool(report_result.integrity_valid)
        and bool(report_result.safe_for_conclusions)
        and bool(report_result.analysis_executed)
        and analysis_manifest.is_file()
        and all(report_artifacts.values())
    )
    report_payload: dict[str, Any] = {}
    try:
        report_payload = json.loads(report_result.report_json.read_text(encoding="utf-8"))
    except Exception:
        report_ok = False
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
        return _failure_result(
            workspace=root, config_path=config_path, run_dir=run_dir, report_dir=report_dir,
            summary_path=summary_path, summary=summary, stage="analysis_report",
            message=(
                "El informe no cumplió aceptación: se requiere integridad válida, Quality Gate "
                "apto para conclusiones, análisis ejecutado y todos los artefactos obligatorios."
            ),
            orchestrator_result=orchestrator_result, report_result=report_result,
        )
    summary["stages"]["analysis_report"] = "PASS"

    try:
        verified_after = verify_fn(run_dir, strict_untracked=True)
    except Exception as exc:
        return _failure_result(
            workspace=root, config_path=config_path, run_dir=run_dir, report_dir=report_dir,
            summary_path=summary_path, summary=summary, stage="integrity_after_report",
            message=f"Verificación posterior al reporte falló: {exc}",
            orchestrator_result=orchestrator_result, report_result=report_result,
        )
    manifest_sha_after = _sha256(manifest_path)
    run_unchanged = bool(getattr(verified_after, "valid", False)) and manifest_sha_before == manifest_sha_after
    summary["integrity_preservation"] = {
        "valid_after_report": bool(getattr(verified_after, "valid", False)),
        "checksums_sha256_before": manifest_sha_before,
        "checksums_sha256_after": manifest_sha_after,
        "sealed_run_unchanged": run_unchanged,
    }
    if not run_unchanged:
        return _failure_result(
            workspace=root, config_path=config_path, run_dir=run_dir, report_dir=report_dir,
            summary_path=summary_path, summary=summary, stage="integrity_after_report",
            message="El run sellado cambió o perdió integridad después de generar el reporte.",
            orchestrator_result=orchestrator_result, report_result=report_result,
        )
    summary["stages"]["integrity_after_report"] = "PASS"

    summary["status"] = "PASS"
    summary["failed_stage"] = None
    summary["message"] = "Dry-run completo: sensor → captura → almacenamiento → validación → análisis → informe."
    _write_summary(summary_path, summary)
    return DryRunResult(
        workspace=root,
        config_path=config_path,
        run_dir=run_dir,
        report_dir=report_dir,
        summary_path=summary_path,
        status="PASS",
        failed_stage=None,
        message=summary["message"],
        orchestrator_result=orchestrator_result,
        report_result=report_result,
    )
