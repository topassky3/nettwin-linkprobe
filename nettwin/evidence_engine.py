from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvidenceConfig:
    min_support_abs_rho: float = 0.60
    max_correlation_refs: int = 8

    def validate(self) -> None:
        if not 0.0 <= self.min_support_abs_rho <= 1.0:
            raise ValueError("min_support_abs_rho debe estar entre 0 y 1")
        if self.max_correlation_refs <= 0:
            raise ValueError("max_correlation_refs debe ser > 0")


@dataclass(frozen=True)
class EvidenceResult:
    findings: list[dict[str, Any]]
    trace_rows: list[dict[str, Any]]
    metadata: dict[str, Any]


_EVENT_METRIC_TO_CORRELATION = {
    "rtt": "rtt_ms",
    "delay_variation": "delay_variation_ms",
    "packet_loss": "packet_loss_pct",
    "drops": "drops_delta",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: str | Path, label: str) -> tuple[Path, dict[str, Any]]:
    json_path = Path(path)
    if not json_path.exists():
        raise ValueError(f"No existe el archivo {label}: {json_path}")
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"No se pudo leer JSON válido de {json_path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{json_path.name} debe contener un objeto JSON en la raíz")
    return json_path, payload


def _parse_timestamp(value: Any) -> datetime | None:
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


def _validate_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("events.json debe contener una lista 'events'")
    required = {"event_id", "start", "end", "metrics_changed", "severity", "confidence", "evidence"}
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"events[{index}] debe ser un objeto")
        missing = sorted(required - set(event))
        if missing:
            raise ValueError(f"events[{index}] carece de campos requeridos: {', '.join(missing)}")
        if not isinstance(event["metrics_changed"], list):
            raise ValueError(f"events[{index}].metrics_changed debe ser una lista")
        if _parse_timestamp(event["start"]) is None or _parse_timestamp(event["end"]) is None:
            raise ValueError(f"events[{index}] contiene timestamps inválidos")
    return events


def _validate_correlations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    correlations = payload.get("correlations")
    if not isinstance(correlations, list):
        raise ValueError("correlations.json debe contener una lista 'correlations'")
    for index, row in enumerate(correlations):
        if not isinstance(row, dict):
            raise ValueError(f"correlations[{index}] debe ser un objeto")
        if "relation_id" not in row:
            raise ValueError(f"correlations[{index}] carece de relation_id")
    return correlations


def _event_targets(event: dict[str, Any]) -> set[str]:
    targets: set[str] = set()
    evidence = event.get("evidence")
    if not isinstance(evidence, dict):
        return targets
    buckets = evidence.get("buckets")
    if not isinstance(buckets, list):
        return targets
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        detail = bucket.get("detail")
        if not isinstance(detail, dict):
            continue
        for key in ("rtt", "delay_variation", "packet_loss"):
            values = detail.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                if isinstance(item, dict) and item.get("target") not in (None, ""):
                    targets.add(str(item["target"]))
    return targets


def _correlation_overlaps_event(row: dict[str, Any], event: dict[str, Any]) -> bool:
    event_start = _parse_timestamp(event.get("start"))
    event_end = _parse_timestamp(event.get("end"))
    corr_start = _parse_timestamp(row.get("first_timestamp"))
    corr_end = _parse_timestamp(row.get("last_timestamp"))
    if None in (event_start, event_end, corr_start, corr_end):
        return False
    assert event_start is not None and event_end is not None
    assert corr_start is not None and corr_end is not None
    return corr_end >= event_start and corr_start <= event_end


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _correlation_kind(row: dict[str, Any], metrics: set[str]) -> str | None:
    x_metric = str(row.get("x_metric", ""))
    y_metric = str(row.get("y_metric", ""))
    expected_event_metric = next(
        (event_metric for event_metric, corr_metric in _EVENT_METRIC_TO_CORRELATION.items() if y_metric == corr_metric),
        None,
    )
    if expected_event_metric is None or expected_event_metric not in metrics:
        return None
    if x_metric == "utilization_pct" and "utilization" in metrics:
        return "load_support"
    if x_metric == "cpu_percent":
        return "host_context"
    return None


def _relevant_correlations(
    event: dict[str, Any],
    correlations: list[dict[str, Any]],
    config: EvidenceConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metrics = {str(metric) for metric in event.get("metrics_changed", [])}
    targets = _event_targets(event)
    support: list[dict[str, Any]] = []
    context: list[dict[str, Any]] = []

    for row in correlations:
        kind = _correlation_kind(row, metrics)
        if kind is None:
            continue
        target = row.get("target")
        if target not in (None, "") and targets and str(target) not in targets:
            continue
        if not _correlation_overlaps_event(row, event):
            continue

        normalized = dict(row)
        normalized["_kind"] = kind
        rho = _float_or_none(row.get("spearman_rho"))
        status = str(row.get("evidence_status", ""))
        sufficient = bool(row.get("sufficient_samples", False))
        qualifies = (
            status == "descriptive_association"
            and sufficient
            and rho is not None
            and abs(rho) >= config.min_support_abs_rho
        )
        (support if qualifies else context).append(normalized)

    support.sort(
        key=lambda row: (
            0 if row.get("_kind") == "load_support" else 1,
            -abs(_float_or_none(row.get("spearman_rho")) or 0.0),
            str(row.get("relation_id", "")),
        )
    )
    context.sort(key=lambda row: str(row.get("relation_id", "")))
    return support[: config.max_correlation_refs], context[: config.max_correlation_refs]


def _confidence(event_confidence: str, support: list[dict[str, Any]]) -> tuple[str, str]:
    event_level = str(event_confidence).upper()
    strong_load = [
        row for row in support
        if row.get("_kind") == "load_support"
        and (_float_or_none(row.get("spearman_rho")) or 0.0) > 0
    ]
    if event_level == "ALTA" and strong_load:
        return (
            "ALTA",
            "Evento detectado con confianza ALTA y al menos una asociación descriptiva positiva fuerte, "
            "temporalmente compatible y relevante para las métricas del evento.",
        )
    if event_level in {"ALTA", "MEDIA"}:
        return (
            "MEDIA",
            "La detección del evento es suficiente para un hallazgo descriptivo, pero no hay soporte "
            "correlacional fuerte y relevante que justifique confianza ALTA.",
        )
    return (
        "BAJA",
        "La confianza del detector de eventos es baja y la evidencia correlacional no permite elevarla.",
    )


def _fact(event: dict[str, Any]) -> str:
    metrics = ", ".join(str(metric) for metric in event.get("metrics_changed", []))
    anomalous = event.get("evidence", {}).get("anomalous_buckets")
    buckets_text = f" en {int(anomalous)} buckets anómalos" if isinstance(anomalous, (int, float)) else ""
    duration = _float_or_none(event.get("duration_seconds"))
    duration_text = f" durante {duration:.1f} s" if duration is not None else ""
    return (
        f"Entre {event['start']} y {event['end']} se detectó {event['event_id']}"
        f"{duration_text}{buckets_text}, con cambios simultáneos en {metrics}."
    )


def _interpretation(event: dict[str, Any], support: list[dict[str, Any]]) -> str:
    metrics = {str(metric) for metric in event.get("metrics_changed", [])}
    load_support = [
        row for row in support
        if row.get("_kind") == "load_support"
        and (_float_or_none(row.get("spearman_rho")) or 0.0) > 0
    ]
    if "utilization" in metrics and load_support:
        relations = ", ".join(str(row.get("relation_id")) for row in load_support[:3])
        return (
            "Se observó degradación temporal coincidente con aumento de carga. "
            f"En la ventana analizada también existen asociaciones descriptivas positivas fuertes relevantes "
            f"({relations}); esto refuerza la coincidencia, no demuestra causalidad."
        )
    if len(metrics) >= 2:
        return (
            "Se observó una degradación temporal multi-métrica durante la ventana medida. "
            "Las señales son simultáneas, pero la evidencia disponible no determina una causa física."
        )
    return (
        "Se observó un cambio temporal en la métrica indicada. "
        "La evidencia disponible es descriptiva y requiere contexto adicional."
    )


def _hypothesis_and_recommendation(
    event: dict[str, Any], support: list[dict[str, Any]]
) -> tuple[str, str]:
    metrics = {str(metric) for metric in event.get("metrics_changed", [])}
    positive_load_support = [
        row for row in support
        if row.get("_kind") == "load_support"
        and (_float_or_none(row.get("spearman_rho")) or 0.0) > 0
    ]
    strong_host = [
        row for row in support
        if row.get("_kind") == "host_context"
        and (_float_or_none(row.get("spearman_rho")) or 0.0) > 0
    ]
    quality_metrics = {"rtt", "delay_variation", "packet_loss", "drops"}

    if "utilization" in metrics and metrics.intersection(quality_metrics) and positive_load_support:
        return (
            "Patrón compatible con formación de colas o cuello de botella bajo carga durante la ventana observada; "
            "requiere confirmación con telemetría del elemento aguas arriba.",
            "Repetir la medición durante hora pico y correlacionar el mismo intervalo con telemetría del elemento "
            "inmediatamente aguas arriba (utilización, colas y descartes).",
        )
    if strong_host and metrics.intersection({"rtt", "packet_loss"}):
        return (
            "Patrón compatible con una contribución del host sensor o su entorno de ejecución, sin poder atribuir "
            "causalidad con la evidencia actual.",
            "Repetir el evento con captura simultánea de CPU, load average y métricas del enlace; comparar si la "
            "degradación persiste cuando el host no presenta carga elevada.",
        )
    if "drops" in metrics or "errors" in metrics:
        return (
            "Posible incidencia en colas, interfaz o camino de red durante el evento; la causa exacta requiere "
            "telemetría adicional.",
            "Revisar contadores y colas del elemento autorizado inmediatamente aguas arriba durante una nueva "
            "ventana equivalente y comparar timestamps con el evento.",
        )
    if "packet_loss" in metrics:
        return (
            "Posible degradación localizada en el camino o destino observado; requiere comparación multi-target "
            "para acotar el dominio afectado.",
            "Repetir la medición con targets interno, externo controlado y referencia pública autorizados, "
            "conservando sincronización temporal.",
        )
    return (
        "Cambio operativo o degradación temporal de causa indeterminada con la evidencia actual.",
        "Extender la ventana de medición y recolectar telemetría simultánea adicional únicamente sobre los "
        "elementos autorizados relacionados con las métricas alteradas.",
    )


def _clean_correlation_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "relation_id": row.get("relation_id"),
        "kind": row.get("_kind"),
        "target": row.get("target"),
        "pair_count": int(row.get("pair_count") or 0),
        "spearman_rho": _float_or_none(row.get("spearman_rho")),
        "direction": row.get("direction"),
        "strength": row.get("strength"),
        "evidence_status": row.get("evidence_status"),
        "first_timestamp": row.get("first_timestamp"),
        "last_timestamp": row.get("last_timestamp"),
        "note": row.get("note"),
    }


def _finding(
    number: int,
    event: dict[str, Any],
    correlations: list[dict[str, Any]],
    config: EvidenceConfig,
) -> dict[str, Any]:
    support, context = _relevant_correlations(event, correlations, config)
    confidence, confidence_basis = _confidence(str(event.get("confidence", "")), support)
    hypothesis, recommendation = _hypothesis_and_recommendation(event, support)
    limitations = [
        "El hallazgo describe únicamente la ventana observada.",
        "Coincidencia temporal y correlación no demuestran causalidad.",
        "La hipótesis requiere confirmación con telemetría o experimentos adicionales.",
    ]
    if not support:
        limitations.append(
            "No se encontró correlación fuerte, suficiente, temporalmente compatible y relevante para elevar "
            "la confianza del hallazgo."
        )
    return {
        "finding_id": f"FINDING-{number:03d}",
        "event_id": event["event_id"],
        "start": event["start"],
        "end": event["end"],
        "duration_seconds": _float_or_none(event.get("duration_seconds")),
        "severity": event.get("severity"),
        "metrics_changed": list(event.get("metrics_changed", [])),
        "targets": sorted(_event_targets(event)),
        "fact": _fact(event),
        "interpretation": _interpretation(event, support),
        "hypothesis": hypothesis,
        "confidence": confidence,
        "confidence_basis": confidence_basis,
        "recommendation": recommendation,
        "evidence": {
            "event": {
                "event_id": event["event_id"],
                "severity": event.get("severity"),
                "detector_confidence": event.get("confidence"),
                "anomalous_buckets": event.get("evidence", {}).get("anomalous_buckets"),
                "bucket_seconds": event.get("evidence", {}).get("bucket_seconds"),
                "buckets": event.get("evidence", {}).get("buckets", []),
            },
            "supporting_correlations": [_clean_correlation_ref(row) for row in support],
            "context_correlations": [_clean_correlation_ref(row) for row in context],
        },
        "limitations": limitations,
        "causal_claim_allowed": False,
    }


def _trace(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for finding in findings:
        event = finding["evidence"]["event"]
        rows.append({
            "finding_id": finding["finding_id"],
            "source_type": "event",
            "source_id": event["event_id"],
            "target": "",
            "pair_count": "",
            "spearman_rho": "",
            "evidence_status": "event_evidence",
            "note": f"{event.get('anomalous_buckets')} buckets anómalos",
        })
        for category in ("supporting_correlations", "context_correlations"):
            for row in finding["evidence"][category]:
                rows.append({
                    "finding_id": finding["finding_id"],
                    "source_type": category,
                    "source_id": row.get("relation_id"),
                    "target": row.get("target") or "",
                    "pair_count": row.get("pair_count"),
                    "spearman_rho": row.get("spearman_rho"),
                    "evidence_status": row.get("evidence_status"),
                    "note": row.get("note"),
                })
    return rows


def analyze_evidence(
    events_json: str | Path,
    correlations_json: str | Path | None = None,
    *,
    config: EvidenceConfig | None = None,
) -> EvidenceResult:
    config = config or EvidenceConfig()
    config.validate()
    events_path, events_payload = _load_json(events_json, "de eventos")
    events = _validate_events(events_payload)

    correlations_path: Path | None = None
    correlations: list[dict[str, Any]] = []
    if correlations_json is not None:
        correlations_path, correlations_payload = _load_json(correlations_json, "de correlaciones")
        correlations = _validate_correlations(correlations_payload)

    findings = [_finding(number, event, correlations, config) for number, event in enumerate(events, start=1)]
    trace_rows = _trace(findings)
    metadata = {
        "mode": "evidence-engine",
        "scope": "observed_window_only",
        "structure": ["HECHO", "INTERPRETACIÓN", "HIPÓTESIS", "CONFIANZA", "RECOMENDACIÓN"],
        "config": asdict(config),
        "events_source": {
            "path": str(events_path),
            "sha256": _sha256(events_path),
            "event_count": len(events),
        },
        "correlations_source": (
            {
                "path": str(correlations_path),
                "sha256": _sha256(correlations_path),
                "correlation_count": len(correlations),
            }
            if correlations_path is not None else None
        ),
        "findings_count": len(findings),
        "causal_inference_performed": False,
        "limitations": [
            "Los hallazgos caracterizan únicamente la ventana observada.",
            "Correlation Engine aporta asociación descriptiva; no causalidad.",
            "Evidence Engine no determina causa física definitiva.",
            "Las recomendaciones se limitan a acciones de validación ligadas a la evidencia disponible.",
        ],
    }
    return EvidenceResult(findings=findings, trace_rows=trace_rows, metadata=metadata)


def write_evidence(result: EvidenceResult, output_dir: str | Path) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "evidence_records.json"
    summary_path = output / "evidence_summary.csv"
    trace_path = output / "evidence_trace.csv"

    json_path.write_text(
        json.dumps({"metadata": result.metadata, "findings": result.findings}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_fields = [
        "finding_id", "event_id", "start", "end", "duration_seconds", "severity", "confidence",
        "metrics_changed", "targets", "fact", "interpretation", "hypothesis", "recommendation",
        "supporting_correlation_count", "causal_claim_allowed",
    ]
    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields)
        writer.writeheader()
        for finding in result.findings:
            writer.writerow({
                "finding_id": finding["finding_id"],
                "event_id": finding["event_id"],
                "start": finding["start"],
                "end": finding["end"],
                "duration_seconds": finding["duration_seconds"],
                "severity": finding["severity"],
                "confidence": finding["confidence"],
                "metrics_changed": "|".join(finding["metrics_changed"]),
                "targets": "|".join(finding["targets"]),
                "fact": finding["fact"],
                "interpretation": finding["interpretation"],
                "hypothesis": finding["hypothesis"],
                "recommendation": finding["recommendation"],
                "supporting_correlation_count": len(finding["evidence"]["supporting_correlations"]),
                "causal_claim_allowed": finding["causal_claim_allowed"],
            })

    trace_fields = [
        "finding_id", "source_type", "source_id", "target", "pair_count", "spearman_rho",
        "evidence_status", "note",
    ]
    with trace_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=trace_fields)
        writer.writeheader()
        writer.writerows(result.trace_rows)
    return json_path
