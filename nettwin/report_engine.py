from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from nettwin.correlation_engine import analyze_correlations, write_correlations
from nettwin.dataset_quality import (
    DatasetQualityConfig,
    DatasetQualityResult,
    assess_dataset_quality,
    write_dataset_quality,
)
from nettwin.event_engine import analyze_events, write_events
from nettwin.evidence_engine import analyze_evidence, write_evidence
from nettwin.fingerprint_engine import analyze_fingerprints, write_fingerprints
from nettwin.integrity_engine import verify_integrity
from nettwin.quality_engine import QualityResult, analyze_quality, write_quality
from nettwin.report_render import render_html, svg_chart


REPORT_SCHEMA_VERSION = "link-health-audit-v1"


@dataclass(frozen=True)
class ReportConfig:
    company: str = "HacheNet"
    link_name: str = "LINK-01"
    capacity_mbps: float | None = None
    render_pdf: bool = False
    strict_integrity: bool = True

    def validate(self) -> None:
        if not self.company.strip():
            raise ValueError("company no puede estar vacío")
        if not self.link_name.strip():
            raise ValueError("link_name no puede estar vacío")
        if self.capacity_mbps is not None and self.capacity_mbps <= 0:
            raise ValueError("capacity_mbps debe ser > 0")


@dataclass(frozen=True)
class ReportResult:
    run_dir: Path
    output_dir: Path
    report_json: Path
    report_html: Path
    report_pdf: Path | None
    pdf_error: str | None
    quality_status: str
    safe_for_conclusions: bool
    analysis_executed: bool
    integrity_valid: bool


def _read_json(path: Path, *, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise ValueError(f"No existe el archivo requerido: {path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON inválido en {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} debe contener un objeto JSON")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _required_run_files(run: Path) -> list[Path]:
    return [
        run / "experiment_config.json",
        run / "preflight.json",
        run / "interface_samples.csv",
        run / "host_samples.csv",
        run / "probe_samples.csv",
        run / "orchestration.json",
        run / "run_metadata.json",
        run / "checksums.sha256",
    ]


def _source_hashes(run: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in _required_run_files(run):
        if path.exists() and path.is_file():
            result[path.name] = _sha256(path)
    return dict(sorted(result.items()))


def _capacity(config: ReportConfig, experiment: dict[str, Any]) -> float | None:
    if config.capacity_mbps is not None:
        return float(config.capacity_mbps)
    interface = experiment.get("interface") if isinstance(experiment.get("interface"), dict) else {}
    configured = _number(interface.get("capacity_mbps"))
    return configured if configured is not None and configured > 0 else None


def _target_names(experiment: dict[str, Any]) -> list[str]:
    targets = experiment.get("targets") if isinstance(experiment.get("targets"), list) else []
    result = []
    for target in targets:
        if isinstance(target, dict) and str(target.get("name", "")).strip():
            result.append(str(target["name"]))
    return result


def _record(frame: pd.DataFrame, key: str, value: str) -> dict[str, Any] | None:
    if frame.empty or key not in frame.columns:
        return None
    matches = frame[frame[key].astype(str) == str(value)]
    if matches.empty:
        return None
    return matches.iloc[0].replace({pd.NA: None}).to_dict()


def _headline_metrics(
    quality: QualityResult | None,
    *,
    primary_target: str | None,
    event_count: int,
) -> dict[str, Any]:
    metrics = {
        "availability_observed_pct": None,
        "rtt_median_ms": None,
        "rtt_p95_ms": None,
        "rtt_p99_ms": None,
        "loss_percent": None,
        "delay_variation_p95_ms": None,
        "utilization_mean_pct": None,
        "utilization_p95_pct": None,
        "utilization_max_pct": None,
        "rx_drops_delta_total": None,
        "tx_drops_delta_total": None,
        "event_count": int(event_count),
    }
    if quality is None:
        return metrics
    if not quality.interface_summary.empty:
        interface = quality.interface_summary.iloc[0]
        for key in (
            "utilization_mean_pct", "utilization_p95_pct", "utilization_max_pct",
            "rx_drops_delta_total", "tx_drops_delta_total",
        ):
            metrics[key] = _number(interface.get(key))
    target = None
    if primary_target:
        target = _record(quality.probe_summary, "target", primary_target)
    if target is None and not quality.probe_summary.empty:
        target = quality.probe_summary.iloc[0].to_dict()
    if target is not None:
        for key in (
            "availability_observed_pct", "rtt_median_ms", "rtt_p95_ms",
            "rtt_p99_ms", "loss_percent", "delay_variation_p95_ms",
        ):
            metrics[key] = _number(target.get(key))
    return metrics


def _duration_text(seconds: float | None) -> str:
    if seconds is None:
        return "la ventana observada"
    if seconds >= 3600:
        return f"una ventana observada de {seconds / 3600:.2f} horas"
    if seconds >= 60:
        return f"una ventana observada de {seconds / 60:.2f} minutos"
    return f"una ventana observada de {seconds:.1f} segundos"


def _executive_summary(
    *,
    company: str,
    link_name: str,
    quality_gate: DatasetQualityResult,
    event_count: int,
    findings: list[dict[str, Any]],
    requested_duration: float | None,
) -> dict[str, str]:
    summary = quality_gate.summary
    duration_text = (
        f"{requested_duration / 3600:.2f} horas"
        if requested_duration is not None and requested_duration >= 3600
        else (f"{requested_duration:.1f} segundos" if requested_duration is not None else "una ventana observada")
    )
    base = (
        f"Se realizó una auditoría instrumentada del enlace {link_name} para {company} durante {duration_text}. "
        f"El Quality Gate registró {summary.get('valid_rows_total', 0)} filas válidas de "
        f"{summary.get('expected_rows_total', 0)} esperadas, con integridad temporal de "
        f"{float(summary.get('temporal_integrity_pct', 0.0)):.2f}%. "
    )
    if not quality_gate.safe_for_conclusions:
        return {
            "class": "note",
            "summary": base + "La calidad o integridad del dataset no habilita conclusiones técnicas en esta ejecución.",
            "key_message": "El informe documenta el fallo de calidad; el análisis causal/descriptivo posterior queda bloqueado hasta corregir la evidencia.",
        }
    if findings:
        top = findings[0]
        return {
            "class": "good",
            "summary": base + f"Se identificaron {event_count} eventos y {len(findings)} hallazgos Evidence Engine.",
            "key_message": (
                f"Hallazgo principal {top.get('finding_id')}: {top.get('fact')} "
                "La interpretación sigue siendo descriptiva y no demuestra causalidad."
            ),
        }
    return {
        "class": "good",
        "summary": base + f"Se identificaron {event_count} eventos bajo los umbrales configurados y no se generaron hallazgos Evidence Engine.",
        "key_message": "La ausencia de hallazgos en esta ventana no demuestra ausencia histórica de degradación ni comportamiento semanal/mensual.",
    }


def _methodology(
    experiment: dict[str, Any],
    quality: QualityResult | None,
    capacity_mbps: float | None,
) -> list[str]:
    interface = experiment.get("interface") if isinstance(experiment.get("interface"), dict) else {}
    host = experiment.get("host") if isinstance(experiment.get("host"), dict) else {}
    probes = experiment.get("probes") if isinstance(experiment.get("probes"), dict) else {}
    targets = _target_names(experiment)
    methods = [
        f"Sensor ejecutado sobre la interfaz autorizada {interface.get('name', 'N/D')}.",
        f"Interface Collector: intervalo={interface.get('interval_seconds', 'N/D')} s.",
        f"Host Collector: intervalo={host.get('interval_seconds', 'N/D')} s.",
        f"Active Probe Engine: intervalo={probes.get('interval_seconds', 'N/D')} s, targets={', '.join(targets) if targets else 'N/D'}.",
        "RTT se obtiene de las respuestas ICMP cuando el formato del sistema operativo permite extraerlo; reachability/pérdida se derivan del conteo de paquetes y no dependen de poder parsear RTT.",
        "Variación de retardo = cambio absoluto entre RTT mediano actual y anterior por target; no se presenta como jitter unidireccional.",
        "Pérdida = (paquetes enviados - paquetes recibidos) / paquetes enviados × 100 durante la ventana observada.",
    ]
    if capacity_mbps is None:
        methods.append("Utilización = N/D porque no se suministró capacidad explícita del enlace; la velocidad reportada por la NIC no se usa como sustituto.")
    else:
        methods.append(f"Utilización calculada con capacidad explícita={capacity_mbps:.3f} Mbps y max(RX rate, TX rate) / capacidad × 100.")
    if quality is not None:
        methods.extend(str(item) for item in quality.metadata.get("limitations", []))
    return _dedupe(methods)


def _limitations(
    quality_gate: DatasetQualityResult,
    quality: QualityResult | None,
    events_metadata: dict[str, Any] | None,
    correlations_metadata: dict[str, Any] | None,
    evidence_metadata: dict[str, Any] | None,
    observed_duration_seconds: float | None,
) -> list[str]:
    items = [
        f"{_duration_text(observed_duration_seconds).capitalize()} caracteriza únicamente el periodo observado y no representa necesariamente el comportamiento semanal o mensual del enlace.",
        "La disponibilidad reportada es disponibilidad observada durante la captura; no es un SLA ni disponibilidad histórica.",
        "Correlación y coincidencia temporal no demuestran causalidad.",
        "La causa física exacta de un evento no se afirma cuando la evidencia disponible no la identifica.",
        "Las métricas no disponibles se presentan como N/D y no se sustituyen por valores inventados.",
    ]
    items.extend(quality_gate.limitations)
    for metadata in (
        quality.metadata if quality is not None else None,
        events_metadata,
        correlations_metadata,
        evidence_metadata,
    ):
        if isinstance(metadata, dict):
            items.extend(str(item) for item in metadata.get("limitations", []))
    return _dedupe(items)


def _chart_data(
    quality: QualityResult | None,
    host_csv: Path,
) -> tuple[dict[str, str], datetime | None, datetime | None]:
    if quality is None:
        empty = {
            key: svg_chart(title, [], global_start=None, global_end=None, unit=unit)
            for key, title, unit in (
                ("utilization", "Utilización", "%"),
                ("rtt", "RTT", " ms"),
                ("delay", "Variación temporal de retardo", " ms"),
                ("loss", "Pérdida", "%"),
                ("drops", "Drops", ""),
                ("cpu", "CPU del host", "%"),
            )
        }
        return empty, None, None

    interface = quality.interface_processed.copy()
    probes = quality.probe_processed.copy()
    host = pd.read_csv(host_csv)
    interface["timestamp"] = pd.to_datetime(interface["timestamp"], utc=True, errors="coerce")
    probes["timestamp"] = pd.to_datetime(probes["timestamp"], utc=True, errors="coerce")
    host["timestamp"] = pd.to_datetime(host["timestamp"], utc=True, errors="coerce")
    all_ts = pd.concat([interface["timestamp"], probes["timestamp"], host["timestamp"]]).dropna()
    start = all_ts.min().to_pydatetime() if not all_ts.empty else None
    end = all_ts.max().to_pydatetime() if not all_ts.empty else None

    def points(frame: pd.DataFrame, column: str) -> list[tuple[str, Any]]:
        if column not in frame:
            return []
        return [
            (ts.isoformat(), value)
            for ts, value in zip(frame["timestamp"], frame[column])
            if not pd.isna(ts) and not pd.isna(value)
        ]

    utilization = [{"label": "utilización", "points": points(interface, "utilization_pct")}]
    rtt = []
    delay = []
    loss = []
    if "target" in probes:
        for target, group in probes.groupby("target", sort=True):
            rtt.append({"label": str(target), "points": points(group, "rtt_ms")})
            delay.append({"label": str(target), "points": points(group, "delay_variation_ms")})
            loss.append({"label": str(target), "points": points(group, "packet_loss_pct")})
    drops_frame = interface.copy()
    for column in ("rx_drops_delta", "tx_drops_delta"):
        if column not in drops_frame:
            drops_frame[column] = 0.0
        drops_frame[column] = pd.to_numeric(drops_frame[column], errors="coerce").fillna(0.0)
    drops_frame["drops_delta_total"] = drops_frame["rx_drops_delta"] + drops_frame["tx_drops_delta"]
    cpu = [{"label": "CPU", "points": points(host, "cpu_percent")}]
    charts = {
        "utilization": svg_chart("Utilización", utilization, global_start=start, global_end=end, unit="%", y_min=0.0),
        "rtt": svg_chart("RTT por target", rtt, global_start=start, global_end=end, unit=" ms", y_min=0.0),
        "delay": svg_chart("Variación temporal de retardo", delay, global_start=start, global_end=end, unit=" ms", y_min=0.0),
        "loss": svg_chart("Pérdida por target", loss, global_start=start, global_end=end, unit="%", y_min=0.0),
        "drops": svg_chart("Drops por intervalo", [{"label": "drops", "points": points(drops_frame, "drops_delta_total")}], global_start=start, global_end=end, y_min=0.0),
        "cpu": svg_chart("CPU del host", cpu, global_start=start, global_end=end, unit="%", y_min=0.0),
    }
    return charts, start, end


def _write_pdf(html_path: Path, pdf_path: Path) -> str | None:
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:
        return f"PDF no generado: WeasyPrint no está disponible ({exc.__class__.__name__})."
    try:
        HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        return None
    except Exception as exc:
        return f"PDF no generado: {exc}"


def generate_report(
    run_dir: str | Path,
    output_dir: str | Path | None = None,
    *,
    config: ReportConfig | None = None,
    quality_config: DatasetQualityConfig | None = None,
) -> ReportResult:
    cfg = config or ReportConfig()
    cfg.validate()
    run = Path(run_dir).resolve()
    if not run.exists() or not run.is_dir():
        raise ValueError(f"No existe el directorio de run: {run}")
    missing = [path.name for path in _required_run_files(run) if not path.exists()]
    if missing:
        raise ValueError(f"Run incompleto; faltan: {', '.join(missing)}")

    experiment = _read_json(run / "experiment_config.json")
    metadata = _read_json(run / "run_metadata.json")
    orchestration = _read_json(run / "orchestration.json")
    preflight = _read_json(run / "preflight.json")
    run_id = str(metadata.get("run_id") or orchestration.get("run_id") or "RUN-ND")
    output = Path(output_dir).resolve() if output_dir is not None else (Path.cwd() / "reports" / run_id).resolve()
    if output == run or run in output.parents:
        raise ValueError("El reporte debe escribirse fuera del directorio sellado del run.")
    output.mkdir(parents=True, exist_ok=True)
    analysis_dir = output / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    verified = verify_integrity(run, strict_untracked=cfg.strict_integrity)
    quality_gate = assess_dataset_quality(
        run,
        integrity_valid=verified.valid,
        config=quality_config,
    )
    quality_gate_path = write_dataset_quality(quality_gate, output)

    capacity = _capacity(cfg, experiment)
    targets = _target_names(experiment)
    primary_target = targets[0] if targets else None
    analysis_executed = False
    quality_result: QualityResult | None = None
    event_result = None
    correlation_result = None
    evidence_result = None
    fingerprint_result = None
    analysis_artifacts: dict[str, str] = {}

    if quality_gate.safe_for_conclusions:
        quality_result = analyze_quality(
            run / "interface_samples.csv",
            run / "probe_samples.csv",
            capacity_mbps=capacity,
        )
        quality_manifest = write_quality(quality_result, analysis_dir)
        event_result = analyze_events(
            analysis_dir / "quality_interface_processed.csv",
            analysis_dir / "quality_probe_processed.csv",
        )
        events_path = write_events(event_result, analysis_dir)
        correlation_result = analyze_correlations(
            analysis_dir / "quality_interface_processed.csv",
            analysis_dir / "quality_probe_processed.csv",
            host_csv=run / "host_samples.csv",
        )
        correlations_path = write_correlations(correlation_result, analysis_dir)
        evidence_result = analyze_evidence(events_path, correlations_path)
        evidence_path = write_evidence(evidence_result, analysis_dir)
        fingerprint_result = analyze_fingerprints(events_path)
        fingerprints_path = write_fingerprints(fingerprint_result, analysis_dir)
        analysis_executed = True
        analysis_artifacts = {
            "quality": quality_manifest.name,
            "events": events_path.name,
            "correlations": correlations_path.name,
            "evidence": evidence_path.name,
            "fingerprints": fingerprints_path.name,
        }

    events = list(event_result.events) if event_result is not None else []
    raw_findings = list(evidence_result.findings) if evidence_result is not None else []
    event_by_id = {str(event.get("event_id")): event for event in events}
    findings: list[dict[str, Any]] = []
    for raw in raw_findings:
        row = dict(raw)
        row["event"] = event_by_id.get(str(row.get("event_id")), {})
        findings.append(row)
    fingerprints = list(fingerprint_result.fingerprints) if fingerprint_result is not None else []
    headline = _headline_metrics(quality_result, primary_target=primary_target, event_count=len(events))

    run_cfg = experiment.get("run") if isinstance(experiment.get("run"), dict) else {}
    requested_duration = _number(run_cfg.get("duration_seconds"))
    software = metadata.get("software") if isinstance(metadata.get("software"), dict) else {}
    sensor_version = software.get("sensor_version") or preflight.get("sensor_version") or "N/D"
    started_utc = orchestration.get("started_utc") or metadata.get("observed_window", {}).get("first_timestamp")
    ended_utc = orchestration.get("ended_utc") or metadata.get("observed_window", {}).get("last_timestamp")
    observed_duration = _number(orchestration.get("elapsed_seconds"))
    if observed_duration is None:
        observed_duration = _number(metadata.get("observed_window", {}).get("duration_seconds"))

    executive = _executive_summary(
        company=cfg.company,
        link_name=cfg.link_name,
        quality_gate=quality_gate,
        event_count=len(events),
        findings=findings,
        requested_duration=requested_duration,
    )
    recommendations_note = None
    if quality_gate.safe_for_conclusions and not findings:
        recommendations_note = "No se generan recomendaciones técnicas específicas porque Evidence Engine no produjo hallazgos en esta ventana."

    commercial = None
    if quality_gate.safe_for_conclusions and findings:
        commercial = {
            "rationale": "La siguiente etapa se presenta únicamente después de los resultados técnicos y busca confirmar si los patrones observados se repiten en una ventana más larga o en otros enlaces.",
            "options": [
                {"stage": "Fase comercial 1", "scope": "Varios enlaces · 7 días · comparación entre enlaces"},
                {"stage": "Fase comercial 2", "scope": "Monitoreo continuo · alertas · tendencias · capacidad"},
                {"stage": "Fase comercial 3", "scope": "Network Intelligence · comparación histórica · planificación"},
            ],
        }

    quality_payload = {
        "status": quality_gate.status,
        "safe_for_conclusions": quality_gate.safe_for_conclusions,
        "summary": quality_gate.summary,
        "sources": quality_gate.sources,
        "checks": quality_gate.checks,
    }
    payload: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "company": cfg.company,
        "link_name": cfg.link_name,
        "run_id": run_id,
        "sensor_version": sensor_version,
        "started_utc": started_utc,
        "ended_utc": ended_utc,
        "requested_duration_seconds": requested_duration,
        "observed_duration_seconds": observed_duration,
        "capacity_mbps": capacity,
        "primary_target": primary_target,
        "targets": targets,
        "integrity": {
            "valid": verified.valid,
            "strict_untracked": cfg.strict_integrity,
            "checked_files": verified.checked_files,
        },
        "dataset_quality": quality_payload,
        "analysis_executed": analysis_executed,
        "analysis_artifacts": analysis_artifacts,
        "headline_metrics": headline,
        "executive_summary": executive,
        "events": events,
        "findings": findings,
        "fingerprints": fingerprints,
        "methodology": _methodology(experiment, quality_result, capacity),
        "limitations": _limitations(
            quality_gate,
            quality_result,
            event_result.metadata if event_result is not None else None,
            correlation_result.metadata if correlation_result is not None else None,
            evidence_result.metadata if evidence_result is not None else None,
            observed_duration,
        ),
        "recommendations_note": recommendations_note,
        "commercial_next_step": commercial,
        "source_sha256": _source_hashes(run),
        "quality_artifact": quality_gate_path.name,
        "security": {
            "payload_capture": False,
            "customer_communications_inspected": False,
            "network_configuration_modified": False,
            "causal_claims_generated": False,
        },
        "pdf_requested": bool(cfg.render_pdf),
        "reproducibility": {
            "deterministic_report_timestamp_source": "run/orchestration metadata; no wall-clock generation timestamp is embedded",
            "nd_policy": "Toda métrica no medible se conserva como null en JSON y N/D en HTML.",
        },
    }
    payload["report_sha256"] = _payload_hash(payload)

    charts, _, _ = _chart_data(quality_result, run / "host_samples.csv")
    report_json = output / "report.json"
    report_html = output / "report.html"
    report_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    report_html.write_text(render_html(payload, charts), encoding="utf-8")

    report_pdf: Path | None = None
    pdf_error: str | None = None
    if cfg.render_pdf:
        candidate = output / "report.pdf"
        pdf_error = _write_pdf(report_html, candidate)
        if pdf_error is None:
            report_pdf = candidate

    return ReportResult(
        run_dir=run,
        output_dir=output,
        report_json=report_json,
        report_html=report_html,
        report_pdf=report_pdf,
        pdf_error=pdf_error,
        quality_status=quality_gate.status,
        safe_for_conclusions=quality_gate.safe_for_conclusions,
        analysis_executed=analysis_executed,
        integrity_valid=verified.valid,
    )
