from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class EventThresholds:
    bucket_seconds: int = 5
    min_metrics_changed: int = 2
    merge_gap_seconds: int = 10
    utilization_high_pct: float = 80.0
    utilization_delta_pp: float = 20.0
    rtt_ratio: float = 1.5
    rtt_delta_ms: float = 10.0
    delay_variation_ms: float = 10.0
    delay_variation_ratio: float = 2.0
    packet_loss_pct: float = 1.0
    rate_ratio: float = 1.75
    rate_delta_mbps: float = 1.0
    drops_delta: float = 1.0
    errors_delta: float = 1.0

    def validate(self) -> None:
        if self.bucket_seconds <= 0:
            raise ValueError("bucket_seconds debe ser > 0")
        if self.min_metrics_changed <= 0:
            raise ValueError("min_metrics_changed debe ser > 0")
        if self.merge_gap_seconds < self.bucket_seconds:
            raise ValueError("merge_gap_seconds debe ser >= bucket_seconds")
        for name in (
            "utilization_high_pct", "utilization_delta_pp", "rtt_ratio", "rtt_delta_ms",
            "delay_variation_ms", "delay_variation_ratio", "packet_loss_pct",
            "rate_ratio", "rate_delta_mbps", "drops_delta", "errors_delta",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} debe ser >= 0")


@dataclass(frozen=True)
class EventResult:
    events: list[dict[str, Any]]
    timeline: pd.DataFrame
    metadata: dict[str, Any]


def _load_csv(path: str | Path, required: set[str], label: str) -> pd.DataFrame:
    csv_path = Path(path)
    if not csv_path.exists():
        raise ValueError(f"No existe el archivo {label}: {csv_path}")
    df = pd.read_csv(csv_path)
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Faltan columnas requeridas en {csv_path.name}: {', '.join(missing)}")
    if df.empty:
        raise ValueError(f"El archivo {label} no contiene muestras: {csv_path}")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    if df["timestamp"].isna().any():
        raise ValueError(f"Hay timestamps inválidos en {csv_path.name}")
    return df.sort_values("timestamp").reset_index(drop=True)


def _to_numeric(df: pd.DataFrame, columns: tuple[str, ...]) -> None:
    for column in columns:
        if column in df:
            df[column] = pd.to_numeric(df[column], errors="coerce")


def _bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "yes", "si", "sí"})


def _median_or_none(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.median())


def _threshold_from_baseline(
    baseline: float | None,
    *,
    ratio: float,
    delta: float,
    floor: float = 0.0,
) -> float:
    if baseline is None or not np.isfinite(baseline):
        return float(max(floor, delta))
    return float(max(floor, baseline * ratio, baseline + delta))


def _floor_bucket(series: pd.Series, bucket_seconds: int) -> pd.Series:
    return series.dt.floor(f"{bucket_seconds}s")


def _temporal_overlap(interface: pd.DataFrame, probes: pd.DataFrame) -> dict[str, Any]:
    start = max(interface["timestamp"].min(), probes["timestamp"].min())
    end = min(interface["timestamp"].max(), probes["timestamp"].max())
    exists = bool(end > start)
    return {
        "exists": exists,
        "first_timestamp": start.isoformat() if exists else None,
        "last_timestamp": end.isoformat() if exists else None,
        "duration_seconds": float((end - start).total_seconds()) if exists else 0.0,
        "_start": start,
        "_end": end,
    }


def _prepare_interface_timeline(
    interface: pd.DataFrame,
    *,
    overlap_start: pd.Timestamp,
    overlap_end: pd.Timestamp,
    thresholds: EventThresholds,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    work = interface[(interface["timestamp"] >= overlap_start) & (interface["timestamp"] <= overlap_end)].copy()
    _to_numeric(
        work,
        (
            "rx_rate_mbps", "tx_rate_mbps", "utilization_pct",
            "rx_errors_delta", "tx_errors_delta", "rx_drops_delta", "tx_drops_delta",
        ),
    )
    if "sample_ok" in work:
        work = work[_bool_series(work["sample_ok"])]
    elif "sample_status" in work:
        work = work[work["sample_status"].astype(str).str.lower().eq("ok")]
    if work.empty:
        return pd.DataFrame(), {}

    work["bucket"] = _floor_bucket(work["timestamp"], thresholds.bucket_seconds)
    work["errors_delta_total"] = work[["rx_errors_delta", "tx_errors_delta"]].fillna(0).sum(axis=1)
    work["drops_delta_total"] = work[["rx_drops_delta", "tx_drops_delta"]].fillna(0).sum(axis=1)

    grouped = work.groupby("bucket", sort=True).agg(
        interface_samples=("timestamp", "size"),
        rx_rate_mbps=("rx_rate_mbps", "max"),
        tx_rate_mbps=("tx_rate_mbps", "max"),
        utilization_pct=("utilization_pct", "max"),
        errors_delta=("errors_delta_total", "sum"),
        drops_delta=("drops_delta_total", "sum"),
    ).reset_index()

    baselines = {
        "utilization_pct": _median_or_none(work["utilization_pct"]),
        "rx_rate_mbps": _median_or_none(work["rx_rate_mbps"]),
        "tx_rate_mbps": _median_or_none(work["tx_rate_mbps"]),
    }

    return grouped, baselines


def _prepare_probe_timeline(
    probes: pd.DataFrame,
    *,
    overlap_start: pd.Timestamp,
    overlap_end: pd.Timestamp,
    thresholds: EventThresholds,
) -> tuple[pd.DataFrame, dict[str, dict[str, float | None]]]:
    work = probes[(probes["timestamp"] >= overlap_start) & (probes["timestamp"] <= overlap_end)].copy()
    _to_numeric(work, ("packets_sent", "packets_received", "rtt_ms", "delay_variation_ms"))
    if "reachability_bool" in work:
        work["reachability_bool"] = _bool_series(work["reachability_bool"])
    elif "reachability" in work:
        work["reachability_bool"] = _bool_series(work["reachability"])
    else:
        work["reachability_bool"] = work["packets_received"].fillna(0) > 0
    if "sample_status" in work:
        work = work[work["sample_status"].astype(str).str.lower().eq("ok")]
    if work.empty:
        return pd.DataFrame(), {}

    work["bucket"] = _floor_bucket(work["timestamp"], thresholds.bucket_seconds)
    baselines: dict[str, dict[str, float | None]] = {}
    for target, group in work.groupby("target", sort=True):
        baselines[str(target)] = {
            "rtt_ms": _median_or_none(group["rtt_ms"]),
            "delay_variation_ms": _median_or_none(group["delay_variation_ms"]),
        }

    rows: list[dict[str, Any]] = []
    for (bucket, target), group in work.groupby(["bucket", "target"], sort=True):
        sent = float(group["packets_sent"].fillna(0).sum())
        received = float(group["packets_received"].fillna(0).sum())
        lost = max(0.0, sent - received)
        loss_pct = float((lost / sent) * 100.0) if sent > 0 else np.nan
        rows.append({
            "bucket": bucket,
            "target": str(target),
            "probe_samples": int(len(group)),
            "packets_sent": sent,
            "packets_received": received,
            "packet_loss_pct": loss_pct,
            "reachability": bool(group["reachability_bool"].all()) if len(group) else False,
            "rtt_ms": _median_or_none(group["rtt_ms"]),
            "delay_variation_ms": (
                None if group["delay_variation_ms"].dropna().empty
                else float(pd.to_numeric(group["delay_variation_ms"], errors="coerce").max())
            ),
        })
    return pd.DataFrame(rows), baselines


def _build_timeline(
    interface_bucketed: pd.DataFrame,
    probe_bucketed: pd.DataFrame,
    interface_baselines: dict[str, Any],
    probe_baselines: dict[str, dict[str, float | None]],
    thresholds: EventThresholds,
) -> pd.DataFrame:
    buckets = sorted(set(interface_bucketed.get("bucket", [])) | set(probe_bucketed.get("bucket", [])))
    rows: list[dict[str, Any]] = []
    interface_lookup = (
        interface_bucketed.set_index("bucket").to_dict(orient="index")
        if not interface_bucketed.empty else {}
    )

    for bucket in buckets:
        iface = interface_lookup.get(bucket, {})
        target_rows = (
            probe_bucketed[probe_bucketed["bucket"] == bucket].to_dict(orient="records")
            if not probe_bucketed.empty else []
        )
        metrics: list[str] = []
        detail: dict[str, Any] = {}

        utilization = iface.get("utilization_pct")
        util_baseline = interface_baselines.get("utilization_pct")
        if utilization is not None and pd.notna(utilization):
            high = float(utilization) >= thresholds.utilization_high_pct
            delta_high = (
                util_baseline is not None
                and float(utilization) - float(util_baseline) >= thresholds.utilization_delta_pp
            )
            if high or delta_high:
                metrics.append("utilization")
                detail["utilization"] = {
                    "value_pct": float(utilization),
                    "baseline_pct": util_baseline,
                    "high_threshold_pct": thresholds.utilization_high_pct,
                    "delta_threshold_pp": thresholds.utilization_delta_pp,
                }

        rate_hits: list[dict[str, Any]] = []
        for direction in ("rx", "tx"):
            value = iface.get(f"{direction}_rate_mbps")
            baseline = interface_baselines.get(f"{direction}_rate_mbps")
            if value is None or pd.isna(value):
                continue
            trigger = _threshold_from_baseline(
                baseline,
                ratio=thresholds.rate_ratio,
                delta=thresholds.rate_delta_mbps,
                floor=thresholds.rate_delta_mbps,
            )
            if float(value) >= trigger:
                rate_hits.append({
                    "direction": direction.upper(),
                    "value_mbps": float(value),
                    "baseline_mbps": baseline,
                    "threshold_mbps": trigger,
                })
        if rate_hits and (utilization is None or pd.isna(utilization)):
            metrics.append("traffic_rate")
            detail["traffic_rate"] = rate_hits

        drops = float(iface.get("drops_delta", 0.0) or 0.0)
        if drops >= thresholds.drops_delta:
            metrics.append("drops")
            detail["drops"] = {"delta": drops, "threshold": thresholds.drops_delta}

        errors = float(iface.get("errors_delta", 0.0) or 0.0)
        if errors >= thresholds.errors_delta:
            metrics.append("errors")
            detail["errors"] = {"delta": errors, "threshold": thresholds.errors_delta}

        rtt_hits: list[dict[str, Any]] = []
        variation_hits: list[dict[str, Any]] = []
        loss_hits: list[dict[str, Any]] = []
        for target_row in target_rows:
            target = target_row["target"]
            baseline = probe_baselines.get(target, {})
            rtt = target_row.get("rtt_ms")
            if rtt is not None and pd.notna(rtt):
                rtt_threshold = _threshold_from_baseline(
                    baseline.get("rtt_ms"),
                    ratio=thresholds.rtt_ratio,
                    delta=thresholds.rtt_delta_ms,
                )
                if float(rtt) >= rtt_threshold:
                    rtt_hits.append({
                        "target": target,
                        "value_ms": float(rtt),
                        "baseline_ms": baseline.get("rtt_ms"),
                        "threshold_ms": rtt_threshold,
                    })

            delay = target_row.get("delay_variation_ms")
            if delay is not None and pd.notna(delay):
                delay_threshold = _threshold_from_baseline(
                    baseline.get("delay_variation_ms"),
                    ratio=thresholds.delay_variation_ratio,
                    delta=0.0,
                    floor=thresholds.delay_variation_ms,
                )
                if float(delay) >= delay_threshold:
                    variation_hits.append({
                        "target": target,
                        "value_ms": float(delay),
                        "baseline_ms": baseline.get("delay_variation_ms"),
                        "threshold_ms": delay_threshold,
                    })

            loss = target_row.get("packet_loss_pct")
            if loss is not None and pd.notna(loss):
                if float(loss) >= thresholds.packet_loss_pct or not bool(target_row.get("reachability", True)):
                    loss_hits.append({
                        "target": target,
                        "loss_pct": float(loss),
                        "reachability": bool(target_row.get("reachability", False)),
                        "threshold_pct": thresholds.packet_loss_pct,
                    })

        if rtt_hits:
            metrics.append("rtt")
            detail["rtt"] = rtt_hits
        if variation_hits:
            metrics.append("delay_variation")
            detail["delay_variation"] = variation_hits
        if loss_hits:
            metrics.append("packet_loss")
            detail["packet_loss"] = loss_hits

        metrics = sorted(set(metrics))
        rows.append({
            "timestamp": bucket,
            "interface_samples": int(iface.get("interface_samples", 0) or 0),
            "probe_targets": int(len(target_rows)),
            "utilization_pct": utilization,
            "rx_rate_mbps": iface.get("rx_rate_mbps"),
            "tx_rate_mbps": iface.get("tx_rate_mbps"),
            "drops_delta": drops,
            "errors_delta": errors,
            "metrics_changed": "|".join(metrics),
            "metrics_changed_count": len(metrics),
            "event_candidate": len(metrics) >= thresholds.min_metrics_changed,
            "evidence_detail": detail,
        })

    return pd.DataFrame(rows)


def _severity(max_metrics: int) -> str:
    if max_metrics >= 5:
        return "critical"
    if max_metrics == 4:
        return "high"
    if max_metrics == 3:
        return "medium"
    return "low"


def _confidence(max_metrics: int, bucket_count: int) -> str:
    if max_metrics >= 4 or (max_metrics >= 3 and bucket_count >= 2):
        return "ALTA"
    if max_metrics >= 3 or bucket_count >= 2:
        return "MEDIA"
    return "BAJA"


def _make_event(event_id: str, rows: pd.DataFrame, thresholds: EventThresholds) -> dict[str, Any]:
    rows = rows.sort_values("timestamp")
    start = rows["timestamp"].iloc[0]
    last_bucket = rows["timestamp"].iloc[-1]
    end = last_bucket + pd.to_timedelta(float(thresholds.bucket_seconds), unit="s")
    metrics_union: set[str] = set()
    evidence_buckets: list[dict[str, Any]] = []

    for row in rows.to_dict(orient="records"):
        changed = [item for item in str(row["metrics_changed"]).split("|") if item]
        metrics_union.update(changed)
        evidence_buckets.append({
            "timestamp": row["timestamp"].isoformat(),
            "metrics_changed": changed,
            "detail": row["evidence_detail"],
        })

    max_metrics = int(rows["metrics_changed_count"].max())
    metrics_sorted = sorted(metrics_union)
    return {
        "event_id": event_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "duration_seconds": float((end - start).total_seconds()),
        "metrics_changed": metrics_sorted,
        "severity": _severity(max_metrics),
        "evidence": {
            "bucket_seconds": thresholds.bucket_seconds,
            "anomalous_buckets": int(len(rows)),
            "buckets": evidence_buckets,
        },
        "interpretation": (
            "Se observó una ventana con cambios simultáneos en "
            + ", ".join(metrics_sorted)
            + " durante el periodo medido."
        ),
        "hypothesis": (
            "Patrón compatible con un cambio operativo o degradación temporal; "
            "la causa no se determina en Event Engine y requiere correlación/evidencia adicional."
        ),
        "confidence": _confidence(max_metrics, len(rows)),
        "confidence_scope": "detección_del_evento_no_causalidad",
    }


def _extract_events(timeline: pd.DataFrame, thresholds: EventThresholds) -> list[dict[str, Any]]:
    if timeline.empty:
        return []
    candidates = timeline[timeline["event_candidate"]].copy()
    if candidates.empty:
        return []

    groups: list[list[int]] = []
    current: list[int] = []
    previous_ts: pd.Timestamp | None = None
    for idx, row in candidates.iterrows():
        timestamp = row["timestamp"]
        if previous_ts is None or (timestamp - previous_ts).total_seconds() <= thresholds.merge_gap_seconds:
            current.append(idx)
        else:
            groups.append(current)
            current = [idx]
        previous_ts = timestamp
    if current:
        groups.append(current)

    events: list[dict[str, Any]] = []
    for number, indexes in enumerate(groups, start=1):
        events.append(_make_event(f"EVENT-{number:03d}", timeline.loc[indexes], thresholds))
    return events


def analyze_events(
    interface_processed_csv: str | Path,
    probe_processed_csv: str | Path,
    *,
    thresholds: EventThresholds | None = None,
) -> EventResult:
    thresholds = thresholds or EventThresholds()
    thresholds.validate()

    interface_required = {
        "timestamp", "interface", "rx_rate_mbps", "tx_rate_mbps", "utilization_pct",
        "rx_errors_delta", "tx_errors_delta", "rx_drops_delta", "tx_drops_delta",
    }
    probe_required = {
        "timestamp", "target", "packets_sent", "packets_received",
        "rtt_ms", "delay_variation_ms",
    }
    interface = _load_csv(interface_processed_csv, interface_required, "de interfaz procesada")
    probes = _load_csv(probe_processed_csv, probe_required, "de sondas procesadas")
    overlap = _temporal_overlap(interface, probes)
    public_overlap = {k: v for k, v in overlap.items() if not k.startswith("_")}

    metadata: dict[str, Any] = {
        "mode": "event-engine",
        "scope": "observed_window_only",
        "temporal_overlap": public_overlap,
        "thresholds": asdict(thresholds),
        "event_detection_executed": False,
        "event_count": 0,
        "limitations": [
            "Event Engine identifica coincidencias temporales y no demuestra causalidad.",
            "La severidad representa intensidad del detector según señales simultáneas, no impacto comercial ni SLA.",
            "La confianza corresponde a la detección del evento, no a una hipótesis causal.",
        ],
    }

    if not overlap["exists"]:
        metadata["limitations"].append(
            "No existe solapamiento temporal entre interfaz y sondas; la detección conjunta de eventos se omite."
        )
        return EventResult(events=[], timeline=pd.DataFrame(), metadata=metadata)

    interface_bucketed, interface_baselines = _prepare_interface_timeline(
        interface,
        overlap_start=overlap["_start"],
        overlap_end=overlap["_end"],
        thresholds=thresholds,
    )
    probe_bucketed, probe_baselines = _prepare_probe_timeline(
        probes,
        overlap_start=overlap["_start"],
        overlap_end=overlap["_end"],
        thresholds=thresholds,
    )
    timeline = _build_timeline(
        interface_bucketed,
        probe_bucketed,
        interface_baselines,
        probe_baselines,
        thresholds,
    )
    events = _extract_events(timeline, thresholds)
    metadata["event_detection_executed"] = True
    metadata["event_count"] = len(events)
    metadata["interface_baselines"] = interface_baselines
    metadata["probe_baselines"] = probe_baselines
    metadata["timeline_buckets"] = int(len(timeline))
    metadata["candidate_buckets"] = int(timeline["event_candidate"].sum()) if not timeline.empty else 0
    return EventResult(events=events, timeline=timeline, metadata=metadata)


def write_events(result: EventResult, output_dir: str | Path) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    events_path = output / "events.json"
    timeline_path = output / "event_timeline.csv"
    summary_path = output / "event_summary.csv"

    payload = {
        "metadata": result.metadata,
        "events": result.events,
    }
    events_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    timeline = result.timeline.copy()
    if not timeline.empty:
        timeline["evidence_detail"] = timeline["evidence_detail"].map(
            lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
        )
    timeline.to_csv(timeline_path, index=False)

    summary_columns = [
        "event_id", "start", "end", "duration_seconds", "severity",
        "confidence", "metrics_changed", "interpretation", "hypothesis",
    ]
    summary_rows = []
    for event in result.events:
        row = {key: event.get(key) for key in summary_columns}
        row["metrics_changed"] = "|".join(event.get("metrics_changed", []))
        summary_rows.append(row)
    pd.DataFrame(summary_rows, columns=summary_columns).to_csv(summary_path, index=False)
    return events_path