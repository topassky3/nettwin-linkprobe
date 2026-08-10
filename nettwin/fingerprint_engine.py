from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


VECTOR_VERSION = "event-fingerprint-v1"


@dataclass(frozen=True)
class FingerprintConfig:
    round_digits: int = 3

    def validate(self) -> None:
        if not 0 <= self.round_digits <= 9:
            raise ValueError("round_digits debe estar entre 0 y 9")


@dataclass(frozen=True)
class FingerprintResult:
    fingerprints: list[dict[str, Any]]
    metadata: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_events(path: str | Path) -> tuple[Path, dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        raise ValueError(f"No existe events.json: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"events.json no contiene JSON válido: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("events.json debe contener un objeto JSON.")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("events.json debe contener una lista 'events'.")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("metadata de events.json debe ser un objeto.")
    return source, payload


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def _pct_delta(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None or baseline == 0:
        return None
    return ((value - baseline) / baseline) * 100.0


def _extreme(
    records: list[dict[str, Any]],
    key: str,
    fallback: str | None = None,
) -> dict[str, Any] | None:
    candidates = [row for row in records if _number(row.get(key)) is not None]
    if not candidates and fallback is not None:
        candidates = [row for row in records if _number(row.get(fallback)) is not None]
        key = fallback
    if not candidates:
        return None
    return max(candidates, key=lambda row: abs(float(row[key])))


def _event_targets(event: dict[str, Any]) -> list[str]:
    targets: set[str] = set()
    evidence = event.get("evidence", {})
    buckets = evidence.get("buckets", []) if isinstance(evidence, dict) else []
    for bucket in buckets if isinstance(buckets, list) else []:
        if not isinstance(bucket, dict):
            continue
        detail = bucket.get("detail", {})
        if not isinstance(detail, dict):
            continue
        for metric in ("rtt", "delay_variation", "packet_loss"):
            rows = detail.get(metric, [])
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and row.get("target") is not None:
                        targets.add(str(row["target"]))
    return sorted(targets)


def _validate_event(event: Any, index: int) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError(f"Evento #{index} debe ser un objeto.")
    required = ("event_id", "start", "end", "duration_seconds", "evidence")
    missing = [key for key in required if key not in event]
    if missing:
        raise ValueError(
            f"Evento #{index} carece de campos requeridos: {', '.join(missing)}"
        )
    if not isinstance(event["evidence"], dict):
        raise ValueError(f"{event['event_id']}: evidence debe ser un objeto.")
    buckets = event["evidence"].get("buckets")
    if not isinstance(buckets, list):
        raise ValueError(f"{event['event_id']}: evidence.buckets debe ser una lista.")
    duration = _number(event["duration_seconds"])
    if duration is None or duration < 0:
        raise ValueError(f"{event['event_id']}: duration_seconds inválido.")
    return event


def _loss_baseline(
    metadata: dict[str, Any],
    target: str,
    row: dict[str, Any],
) -> float | None:
    direct = _number(row.get("baseline_pct"))
    if direct is not None:
        return direct
    probe_baselines = metadata.get("probe_baselines", {})
    if not isinstance(probe_baselines, dict):
        return None
    target_baseline = probe_baselines.get(target, {})
    if not isinstance(target_baseline, dict):
        return None
    return _number(target_baseline.get("packet_loss_pct"))


def _metric_records(
    event: dict[str, Any],
    metadata: dict[str, Any],
    digits: int,
) -> dict[str, Any]:
    utilization_rows: list[dict[str, Any]] = []
    rtt_rows: dict[str, list[dict[str, Any]]] = {}
    delay_rows: dict[str, list[dict[str, Any]]] = {}
    loss_rows: dict[str, list[dict[str, Any]]] = {}
    traffic_rows: dict[str, list[dict[str, Any]]] = {}
    drops_total = 0.0
    errors_total = 0.0
    drops_seen = False
    errors_seen = False
    trace: list[dict[str, Any]] = []

    buckets = event["evidence"].get("buckets", [])
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        timestamp = bucket.get("timestamp")
        detail = bucket.get("detail", {})
        if not isinstance(detail, dict):
            continue

        util = detail.get("utilization")
        if isinstance(util, dict):
            value = _number(util.get("value_pct"))
            baseline = _number(util.get("baseline_pct"))
            if value is not None:
                row = {
                    "timestamp": timestamp,
                    "value_pct": value,
                    "baseline_pct": baseline,
                    "delta_pp": None if baseline is None else value - baseline,
                }
                utilization_rows.append(row)
                trace.append(
                    {
                        "metric": "utilization",
                        "target": None,
                        "timestamp": timestamp,
                        "baseline": baseline,
                        "observed": value,
                        "delta": row["delta_pp"],
                        "unit": "pp",
                    }
                )

        for metric, destination in (
            ("rtt", rtt_rows),
            ("delay_variation", delay_rows),
        ):
            values = detail.get(metric, [])
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict) or item.get("target") is None:
                    continue
                target = str(item["target"])
                value = _number(item.get("value_ms"))
                baseline = _number(item.get("baseline_ms"))
                if value is None:
                    continue
                delta_ms = None if baseline is None else value - baseline
                delta_pct = _pct_delta(value, baseline)
                row = {
                    "timestamp": timestamp,
                    "target": target,
                    "value_ms": value,
                    "baseline_ms": baseline,
                    "delta_ms": delta_ms,
                    "delta_pct": delta_pct,
                }
                destination.setdefault(target, []).append(row)
                trace.append(
                    {
                        "metric": metric,
                        "target": target,
                        "timestamp": timestamp,
                        "baseline": baseline,
                        "observed": value,
                        "delta": delta_pct,
                        "unit": "%",
                    }
                )

        losses = detail.get("packet_loss", [])
        if isinstance(losses, list):
            for item in losses:
                if not isinstance(item, dict) or item.get("target") is None:
                    continue
                target = str(item["target"])
                value = _number(item.get("loss_pct"))
                if value is None:
                    continue
                baseline = _loss_baseline(metadata, target, item)
                delta_pp = None if baseline is None else value - baseline
                row = {
                    "timestamp": timestamp,
                    "target": target,
                    "loss_pct": value,
                    "baseline_pct": baseline,
                    "delta_pp": delta_pp,
                }
                loss_rows.setdefault(target, []).append(row)
                trace.append(
                    {
                        "metric": "packet_loss",
                        "target": target,
                        "timestamp": timestamp,
                        "baseline": baseline,
                        "observed": value,
                        "delta": delta_pp,
                        "unit": "pp",
                    }
                )

        traffic = detail.get("traffic_rate", [])
        if isinstance(traffic, list):
            for item in traffic:
                if not isinstance(item, dict):
                    continue
                direction = str(item.get("direction", "N/D")).upper()
                value = _number(item.get("value_mbps"))
                baseline = _number(item.get("baseline_mbps"))
                if value is None:
                    continue
                delta_mbps = None if baseline is None else value - baseline
                delta_pct = _pct_delta(value, baseline)
                row = {
                    "timestamp": timestamp,
                    "direction": direction,
                    "value_mbps": value,
                    "baseline_mbps": baseline,
                    "delta_mbps": delta_mbps,
                    "delta_pct": delta_pct,
                }
                traffic_rows.setdefault(direction, []).append(row)
                trace.append(
                    {
                        "metric": "traffic_rate",
                        "target": direction,
                        "timestamp": timestamp,
                        "baseline": baseline,
                        "observed": value,
                        "delta": delta_pct,
                        "unit": "%",
                    }
                )

        drops = detail.get("drops")
        if isinstance(drops, dict):
            delta = _number(drops.get("delta"))
            if delta is not None:
                drops_seen = True
                drops_total += delta
                trace.append(
                    {
                        "metric": "drops",
                        "target": None,
                        "timestamp": timestamp,
                        "baseline": None,
                        "observed": delta,
                        "delta": delta,
                        "unit": "count",
                    }
                )

        errors = detail.get("errors")
        if isinstance(errors, dict):
            delta = _number(errors.get("delta"))
            if delta is not None:
                errors_seen = True
                errors_total += delta
                trace.append(
                    {
                        "metric": "errors",
                        "target": None,
                        "timestamp": timestamp,
                        "baseline": None,
                        "observed": delta,
                        "delta": delta,
                        "unit": "count",
                    }
                )

    utilization = _extreme(utilization_rows, "delta_pp", "value_pct")
    util_summary = None
    if utilization is not None:
        util_summary = {
            "baseline_pct": _round(_number(utilization.get("baseline_pct")), digits),
            "peak_pct": _round(_number(utilization.get("value_pct")), digits),
            "delta_pp": _round(_number(utilization.get("delta_pp")), digits),
            "timestamp": utilization.get("timestamp"),
        }

    def per_target_summary(
        grouped: dict[str, list[dict[str, Any]]],
        *,
        value_key: str,
        baseline_key: str,
        delta_key: str,
        absolute_key: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for target in sorted(grouped):
            chosen = _extreme(grouped[target], delta_key, absolute_key or value_key)
            if chosen is None:
                continue
            row = {
                "baseline": _round(_number(chosen.get(baseline_key)), digits),
                "peak": _round(_number(chosen.get(value_key)), digits),
                delta_key: _round(_number(chosen.get(delta_key)), digits),
                "timestamp": chosen.get("timestamp"),
            }
            if "delta_ms" in chosen:
                row["delta_ms"] = _round(_number(chosen.get("delta_ms")), digits)
            result[target] = row
        return result

    rtt = per_target_summary(
        rtt_rows,
        value_key="value_ms",
        baseline_key="baseline_ms",
        delta_key="delta_pct",
        absolute_key="delta_ms",
    )
    delay = per_target_summary(
        delay_rows,
        value_key="value_ms",
        baseline_key="baseline_ms",
        delta_key="delta_pct",
        absolute_key="delta_ms",
    )
    loss = per_target_summary(
        loss_rows,
        value_key="loss_pct",
        baseline_key="baseline_pct",
        delta_key="delta_pp",
        absolute_key="loss_pct",
    )
    traffic = per_target_summary(
        traffic_rows,
        value_key="value_mbps",
        baseline_key="baseline_mbps",
        delta_key="delta_pct",
        absolute_key="delta_mbps",
    )

    return {
        "utilization": util_summary,
        "rtt_by_target": rtt,
        "delay_variation_by_target": delay,
        "packet_loss_by_target": loss,
        "traffic_rate_by_direction": traffic,
        "drops_delta_total": _round(drops_total, digits) if drops_seen else None,
        "errors_delta_total": _round(errors_total, digits) if errors_seen else None,
        "trace": trace,
    }


def _compact_max(
    per_target: dict[str, Any],
    delta_key: str,
) -> tuple[float | None, str | None]:
    rows = []
    for target, values in per_target.items():
        value = _number(values.get(delta_key)) if isinstance(values, dict) else None
        if value is not None:
            rows.append((target, value))
    if not rows:
        return None, None
    target, value = max(rows, key=lambda item: abs(item[1]))
    return value, target


def _fingerprint_hash(fingerprint: dict[str, Any]) -> str:
    canonical = json.dumps(
        fingerprint,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _make_fingerprint(
    event: dict[str, Any],
    metadata: dict[str, Any],
    config: FingerprintConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    event_id = str(event["event_id"])
    details = _metric_records(event, metadata, config.round_digits)

    rtt_delta, rtt_target = _compact_max(details["rtt_by_target"], "delta_pct")
    delay_delta, delay_target = _compact_max(
        details["delay_variation_by_target"], "delta_pct"
    )
    loss_delta, loss_target = _compact_max(
        details["packet_loss_by_target"], "delta_pp"
    )

    observed_loss_peak = None
    observed_loss_target = None
    loss_candidates = []
    for target, row in details["packet_loss_by_target"].items():
        peak = _number(row.get("peak"))
        if peak is not None:
            loss_candidates.append((target, peak))
    if loss_candidates:
        observed_loss_target, observed_loss_peak = max(
            loss_candidates,
            key=lambda item: item[1],
        )

    util_delta = (
        _number(details["utilization"].get("delta_pp"))
        if details["utilization"] is not None
        else None
    )
    core_values = [
        util_delta,
        rtt_delta,
        delay_delta,
        loss_delta,
        _number(details["drops_delta_total"]),
    ]
    available = sum(value is not None for value in core_values)
    completeness = (available / len(core_values)) * 100.0

    limitations: list[str] = []
    metrics_changed = [str(value) for value in event.get("metrics_changed", [])]
    if "packet_loss" in metrics_changed:
        if details["packet_loss_by_target"] and loss_delta is None:
            limitations.append(
                "Δloss N/D: el evento contiene pérdida observada pero no baseline de pérdida; no se asume baseline 0."
            )
    if details["utilization"] is None and "utilization" in metrics_changed:
        limitations.append(
            "Δutilization N/D: la evidencia del evento no contiene baseline/valor utilizable."
        )
    if rtt_target is None and "rtt" in metrics_changed:
        limitations.append(
            "ΔRTT porcentual N/D para el resumen compacto por falta de baseline distinto de cero."
        )
    if delay_target is None and "delay_variation" in metrics_changed:
        limitations.append(
            "Δdelay_var porcentual N/D para el resumen compacto por falta de baseline distinto de cero."
        )

    compact = {
        "delta_utilization_pp": _round(util_delta, config.round_digits),
        "delta_rtt_pct": _round(rtt_delta, config.round_digits),
        "delta_rtt_target": rtt_target,
        "delta_delay_variation_pct": _round(delay_delta, config.round_digits),
        "delta_delay_variation_target": delay_target,
        "delta_loss_pp": _round(loss_delta, config.round_digits),
        "delta_loss_target": loss_target,
        "observed_loss_peak_pct": _round(observed_loss_peak, config.round_digits),
        "observed_loss_target": observed_loss_target,
        "delta_drops": details["drops_delta_total"],
        "duration_seconds": _round(
            _number(event["duration_seconds"]),
            config.round_digits,
        ),
        "core_metrics_available": available,
        "core_metrics_total": len(core_values),
        "core_completeness_pct": _round(completeness, config.round_digits),
    }

    fingerprint = {
        "fingerprint_id": f"FP-{event_id}",
        "vector_version": VECTOR_VERSION,
        "event_id": event_id,
        "start": event["start"],
        "end": event["end"],
        "duration_seconds": _round(
            _number(event["duration_seconds"]),
            config.round_digits,
        ),
        "severity": event.get("severity"),
        "confidence": event.get("confidence"),
        "metrics_changed": sorted(metrics_changed),
        "targets": _event_targets(event),
        "compact_vector": compact,
        "details": {
            key: value
            for key, value in details.items()
            if key != "trace"
        },
        "limitations": limitations,
        "causal_interpretation_allowed": False,
    }
    fingerprint["fingerprint_sha256"] = _fingerprint_hash(fingerprint)

    trace = []
    for row in details["trace"]:
        trace.append(
            {
                "fingerprint_id": fingerprint["fingerprint_id"],
                "event_id": event_id,
                **row,
            }
        )
    return fingerprint, trace


def analyze_fingerprints(
    events_json: str | Path,
    *,
    config: FingerprintConfig | None = None,
) -> FingerprintResult:
    config = config or FingerprintConfig()
    config.validate()
    source, payload = _load_events(events_json)
    metadata = payload.get("metadata", {})
    events = payload["events"]

    fingerprints: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_event in enumerate(events, start=1):
        event = _validate_event(raw_event, index)
        event_id = str(event["event_id"])
        if event_id in seen_ids:
            raise ValueError(f"event_id duplicado en events.json: {event_id}")
        seen_ids.add(event_id)
        fingerprint, trace = _make_fingerprint(event, metadata, config)
        fingerprints.append(fingerprint)
        trace_rows.extend(trace)

    result_metadata: dict[str, Any] = {
        "mode": "event-fingerprint-engine",
        "scope": "observed_window_only",
        "vector_version": VECTOR_VERSION,
        "config": asdict(config),
        "events_source": {
            "path": str(source),
            "sha256": _sha256(source),
            "event_count": len(events),
        },
        "fingerprint_count": len(fingerprints),
        "methodology": {
            "delta_utilization_pp": (
                "máximo cambio observado value_pct - baseline_pct dentro del evento"
            ),
            "delta_rtt_pct": (
                "máximo cambio porcentual absoluto por target respecto al baseline exportado por Event Engine"
            ),
            "delta_delay_variation_pct": (
                "máximo cambio porcentual absoluto por target respecto al baseline exportado por Event Engine"
            ),
            "delta_loss_pp": (
                "máximo cambio loss_pct - baseline_pct cuando el baseline existe; de lo contrario N/D"
            ),
            "delta_drops": (
                "suma de deltas de drops registrados en los buckets anómalos del evento"
            ),
            "duration_seconds": "duración reportada por Event Engine",
            "compact_multi_target_rule": (
                "el vector compacto conserva el cambio de mayor magnitud y registra el target; el detalle por target permanece disponible"
            ),
        },
        "limitations": [
            "El fingerprint resume el evento observado y no demuestra causalidad.",
            "Un campo sin baseline suficiente se conserva como N/D; no se inventan referencias.",
            "El máximo entre targets se usa solo para comparación compacta y no reemplaza el detalle por target.",
            "La comparación futura entre enlaces requiere mantener la misma versión de vector y metodología.",
        ],
        "_trace_rows": trace_rows,
    }
    return FingerprintResult(
        fingerprints=fingerprints,
        metadata=result_metadata,
    )


def write_fingerprints(
    result: FingerprintResult,
    output_dir: str | Path,
) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    json_path = output / "event_fingerprints.json"
    summary_path = output / "event_fingerprint_summary.csv"
    trace_path = output / "event_fingerprint_trace.csv"

    metadata = {
        key: value
        for key, value in result.metadata.items()
        if key != "_trace_rows"
    }
    json_path.write_text(
        json.dumps(
            {"metadata": metadata, "fingerprints": result.fingerprints},
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    summary_fields = [
        "fingerprint_id",
        "event_id",
        "start",
        "end",
        "duration_seconds",
        "severity",
        "confidence",
        "targets",
        "delta_utilization_pp",
        "delta_rtt_pct",
        "delta_rtt_target",
        "delta_delay_variation_pct",
        "delta_delay_variation_target",
        "delta_loss_pp",
        "delta_loss_target",
        "observed_loss_peak_pct",
        "observed_loss_target",
        "delta_drops",
        "core_completeness_pct",
        "fingerprint_sha256",
    ]
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields)
        writer.writeheader()
        for fingerprint in result.fingerprints:
            vector = fingerprint["compact_vector"]
            writer.writerow(
                {
                    "fingerprint_id": fingerprint["fingerprint_id"],
                    "event_id": fingerprint["event_id"],
                    "start": fingerprint["start"],
                    "end": fingerprint["end"],
                    "duration_seconds": fingerprint["duration_seconds"],
                    "severity": fingerprint.get("severity"),
                    "confidence": fingerprint.get("confidence"),
                    "targets": "|".join(fingerprint.get("targets", [])),
                    **{
                        key: vector.get(key)
                        for key in summary_fields
                        if key in vector
                    },
                    "fingerprint_sha256": fingerprint["fingerprint_sha256"],
                }
            )

    trace_fields = [
        "fingerprint_id",
        "event_id",
        "metric",
        "target",
        "timestamp",
        "baseline",
        "observed",
        "delta",
        "unit",
    ]
    with trace_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=trace_fields)
        writer.writeheader()
        for row in result.metadata.get("_trace_rows", []):
            writer.writerow({key: row.get(key) for key in trace_fields})

    return json_path
