from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LinkAuditResult:
    summary: pd.DataFrame
    processed: pd.DataFrame
    metadata: dict[str, object]


def _percentile(series: pd.Series, q: float) -> float | None:
    clean = series.dropna()
    if clean.empty:
        return None
    return float(clean.quantile(q))


def _safe_value(value: float | None, multiplier: float = 1.0) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return float(value * multiplier)


def analyze_link_audit(df: pd.DataFrame) -> LinkAuditResult:
    """Analyze a short observation window without long-term projections.

    This mode deliberately excludes trend extrapolation, days-to-threshold and
    recommended future capacity. It is intended for instrumented audits that
    describe only the observed window.
    """
    if df.empty:
        raise ValueError("No hay datos para ejecutar link-audit.")

    rows: list[dict[str, object]] = []
    first_global = df["timestamp"].min()
    last_global = df["timestamp"].max()

    for link_id, group in df.groupby("link_id", sort=True):
        group = group.sort_values("timestamp")
        first_ts = group["timestamp"].min()
        last_ts = group["timestamp"].max()
        duration_minutes = max(0.0, (last_ts - first_ts).total_seconds() / 60.0)

        rx_p95 = _percentile(group["rx_utilization"], 0.95)
        tx_p95 = _percentile(group["tx_utilization"], 0.95)
        dominant_direction = "RX" if (rx_p95 or 0.0) >= (tx_p95 or 0.0) else "TX"

        latency = group["latency_ms"] if "latency_ms" in group else pd.Series(dtype=float)
        loss = group["packet_loss_pct"] if "packet_loss_pct" in group else pd.Series(dtype=float)
        availability = group["availability_pct"] if "availability_pct" in group else pd.Series(dtype=float)

        rows.append(
            {
                "link_id": link_id,
                "samples": int(len(group)),
                "first_timestamp": first_ts,
                "last_timestamp": last_ts,
                "duration_minutes": duration_minutes,
                "capacity_mbps": float(group["capacity_mbps"].median()),
                "dominant_direction": dominant_direction,
                "avg_utilization_pct": float(group["utilization"].mean() * 100.0),
                "p50_utilization_pct": _safe_value(_percentile(group["utilization"], 0.50), 100.0),
                "p95_utilization_pct": _safe_value(_percentile(group["utilization"], 0.95), 100.0),
                "p99_utilization_pct": _safe_value(_percentile(group["utilization"], 0.99), 100.0),
                "max_utilization_pct": float(group["utilization"].max() * 100.0),
                "latency_min_ms": _percentile(latency, 0.00),
                "latency_median_ms": _percentile(latency, 0.50),
                "latency_p95_ms": _percentile(latency, 0.95),
                "latency_p99_ms": _percentile(latency, 0.99),
                "latency_max_ms": _percentile(latency, 1.00),
                "packet_loss_mean_pct": None if loss.dropna().empty else float(loss.dropna().mean()),
                "packet_loss_p95_pct": _percentile(loss, 0.95),
                "packet_loss_max_pct": _percentile(loss, 1.00),
                "availability_min_pct": _percentile(availability, 0.00),
                "over_capacity_samples": int(group["over_capacity"].sum()),
            }
        )

    summary = pd.DataFrame(rows)
    metadata: dict[str, object] = {
        "mode": "link-audit",
        "scope": "observed_window_only",
        "first_timestamp": first_global.isoformat(),
        "last_timestamp": last_global.isoformat(),
        "duration_minutes": max(0.0, (last_global - first_global).total_seconds() / 60.0),
        "links": int(summary["link_id"].nunique()),
        "samples": int(len(df)),
        "long_term_projections_enabled": False,
        "trend_projection_enabled": False,
        "capacity_forecast_enabled": False,
        "limitations": [
            "Los resultados describen únicamente la ventana observada.",
            "No se calcula fecha futura de saturación.",
            "No se recomienda capacidad futura a partir de esta ventana corta.",
        ],
    }
    return LinkAuditResult(summary=summary, processed=df.copy(), metadata=metadata)


def write_link_audit(result: LinkAuditResult, output_dir: str | Path) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    summary_path = output / "link_audit_summary.csv"
    processed_path = output / "link_audit_processed.csv"
    manifest_path = output / "link_audit.json"

    result.summary.to_csv(summary_path, index=False)
    result.processed.to_csv(processed_path, index=False)
    manifest_path.write_text(
        json.dumps(result.metadata, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return manifest_path
