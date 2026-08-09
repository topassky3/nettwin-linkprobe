from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class QualityResult:
    interface_summary: pd.DataFrame
    probe_summary: pd.DataFrame
    interface_processed: pd.DataFrame
    probe_processed: pd.DataFrame
    metadata: dict[str, Any]


def _percentile(series: pd.Series, q: float) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.quantile(q))


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _load_csv(path: str | Path, required: set[str]) -> pd.DataFrame:
    csv_path = Path(path)
    if not csv_path.exists():
        raise ValueError(f"No existe el archivo: {csv_path}")
    df = pd.read_csv(csv_path)
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Faltan columnas requeridas en {csv_path.name}: {', '.join(missing)}")
    if df.empty:
        raise ValueError(f"El archivo no contiene muestras: {csv_path}")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    if df["timestamp"].isna().any():
        raise ValueError(f"Hay timestamps inválidos en {csv_path.name}")
    return df.sort_values("timestamp").reset_index(drop=True)


def _prepare_interface(df: pd.DataFrame, capacity_mbps: float | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    if capacity_mbps is not None and capacity_mbps <= 0:
        raise ValueError("capacity_mbps debe ser > 0")

    work = df.copy()
    for column in (
        "rx_bytes_delta", "tx_bytes_delta", "rx_errors_delta", "tx_errors_delta",
        "rx_drops_delta", "tx_drops_delta",
    ):
        work[column] = _numeric(work[column])

    if "sample_status" in work:
        work["sample_ok"] = work["sample_status"].astype(str).str.lower().eq("ok")
    else:
        work["sample_ok"] = True

    # Los deltas del collector se calculan contra la última lectura válida de
    # contadores. Por tanto, si existe una fila de error intermedia, el tiempo
    # para calcular el rate debe abarcar también ese hueco y no solo el último
    # intervalo nominal.
    work["elapsed_seconds"] = np.nan
    for _, group in work.groupby("interface", sort=False):
        valid_index = group.index[group["sample_ok"]]
        valid_timestamps = work.loc[valid_index, "timestamp"]
        elapsed = valid_timestamps.diff().dt.total_seconds()
        work.loc[valid_index, "elapsed_seconds"] = elapsed.to_numpy()

    valid_elapsed = work["sample_ok"] & (work["elapsed_seconds"] > 0)
    work["rx_rate_mbps"] = np.where(
        valid_elapsed,
        work["rx_bytes_delta"] * 8.0 / work["elapsed_seconds"] / 1_000_000.0,
        np.nan,
    )
    work["tx_rate_mbps"] = np.where(
        valid_elapsed,
        work["tx_bytes_delta"] * 8.0 / work["elapsed_seconds"] / 1_000_000.0,
        np.nan,
    )
    work.loc[work["rx_rate_mbps"] < 0, "rx_rate_mbps"] = np.nan
    work.loc[work["tx_rate_mbps"] < 0, "tx_rate_mbps"] = np.nan

    if capacity_mbps is not None:
        work["rx_utilization_pct"] = (work["rx_rate_mbps"] / capacity_mbps) * 100.0
        work["tx_utilization_pct"] = (work["tx_rate_mbps"] / capacity_mbps) * 100.0
        work["utilization_pct"] = work[["rx_utilization_pct", "tx_utilization_pct"]].max(axis=1)
    else:
        work["rx_utilization_pct"] = np.nan
        work["tx_utilization_pct"] = np.nan
        work["utilization_pct"] = np.nan

    rows: list[dict[str, Any]] = []
    for interface, group in work.groupby("interface", sort=True):
        status_ok = group["sample_ok"]
        valid = group[status_ok]
        rows.append(
            {
                "interface": interface,
                "samples": int(len(group)),
                "valid_samples": int(status_ok.sum()),
                "first_timestamp": group["timestamp"].min(),
                "last_timestamp": group["timestamp"].max(),
                "duration_seconds": float(max(0.0, (group["timestamp"].max() - group["timestamp"].min()).total_seconds())),
                "capacity_mbps": capacity_mbps,
                "rx_rate_mean_mbps": None if valid["rx_rate_mbps"].dropna().empty else float(valid["rx_rate_mbps"].mean()),
                "rx_rate_p95_mbps": _percentile(valid["rx_rate_mbps"], 0.95),
                "rx_rate_max_mbps": _percentile(valid["rx_rate_mbps"], 1.0),
                "tx_rate_mean_mbps": None if valid["tx_rate_mbps"].dropna().empty else float(valid["tx_rate_mbps"].mean()),
                "tx_rate_p95_mbps": _percentile(valid["tx_rate_mbps"], 0.95),
                "tx_rate_max_mbps": _percentile(valid["tx_rate_mbps"], 1.0),
                "utilization_mean_pct": None if capacity_mbps is None or valid["utilization_pct"].dropna().empty else float(valid["utilization_pct"].mean()),
                "utilization_p95_pct": None if capacity_mbps is None else _percentile(valid["utilization_pct"], 0.95),
                "utilization_max_pct": None if capacity_mbps is None else _percentile(valid["utilization_pct"], 1.0),
                "rx_errors_delta_total": int(valid["rx_errors_delta"].fillna(0).sum()),
                "tx_errors_delta_total": int(valid["tx_errors_delta"].fillna(0).sum()),
                "rx_drops_delta_total": int(valid["rx_drops_delta"].fillna(0).sum()),
                "tx_drops_delta_total": int(valid["tx_drops_delta"].fillna(0).sum()),
            }
        )
    return work, pd.DataFrame(rows)


def _prepare_probes(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = df.copy()
    for column in (
        "packets_sent", "packets_received", "packet_loss_pct", "rtt_ms",
        "rtt_min_ms", "rtt_max_ms", "delay_variation_ms",
    ):
        if column in work:
            work[column] = _numeric(work[column])

    work["reachability_bool"] = work["reachability"].astype(str).str.lower().isin({"true", "1", "yes", "si", "sí"})

    rows: list[dict[str, Any]] = []
    for target, group in work.groupby("target", sort=True):
        sent = int(group["packets_sent"].fillna(0).sum())
        received = int(group["packets_received"].fillna(0).sum())
        lost = max(0, sent - received)
        loss_percent = None if sent <= 0 else float((lost / sent) * 100.0)
        availability = float(group["reachability_bool"].mean() * 100.0)
        rows.append(
            {
                "target": target,
                "address": str(group["address"].iloc[0]),
                "samples": int(len(group)),
                "first_timestamp": group["timestamp"].min(),
                "last_timestamp": group["timestamp"].max(),
                "duration_seconds": float(max(0.0, (group["timestamp"].max() - group["timestamp"].min()).total_seconds())),
                "total_probes": sent,
                "successful_probes": received,
                "lost_probes": lost,
                "loss_percent": loss_percent,
                "availability_observed_pct": availability,
                "rtt_min_ms": _percentile(group["rtt_ms"], 0.0),
                "rtt_median_ms": _percentile(group["rtt_ms"], 0.50),
                "rtt_p95_ms": _percentile(group["rtt_ms"], 0.95),
                "rtt_p99_ms": _percentile(group["rtt_ms"], 0.99),
                "rtt_max_ms": _percentile(group["rtt_ms"], 1.0),
                "delay_variation_median_ms": _percentile(group["delay_variation_ms"], 0.50),
                "delay_variation_p95_ms": _percentile(group["delay_variation_ms"], 0.95),
                "delay_variation_max_ms": _percentile(group["delay_variation_ms"], 1.0),
            }
        )
    return work, pd.DataFrame(rows)


def analyze_quality(
    interface_csv: str | Path,
    probe_csv: str | Path,
    *,
    capacity_mbps: float | None = None,
) -> QualityResult:
    interface_required = {
        "timestamp", "interface", "rx_bytes_delta", "tx_bytes_delta",
        "rx_errors_delta", "tx_errors_delta", "rx_drops_delta", "tx_drops_delta",
    }
    probe_required = {
        "timestamp", "target", "address", "packets_sent", "packets_received",
        "reachability", "rtt_ms", "delay_variation_ms",
    }
    interface_df = _load_csv(interface_csv, interface_required)
    probe_df = _load_csv(probe_csv, probe_required)
    interface_processed, interface_summary = _prepare_interface(interface_df, capacity_mbps)
    probe_processed, probe_summary = _prepare_probes(probe_df)

    first_ts = min(interface_df["timestamp"].min(), probe_df["timestamp"].min())
    last_ts = max(interface_df["timestamp"].max(), probe_df["timestamp"].max())
    metadata: dict[str, Any] = {
        "mode": "quality-engine",
        "scope": "observed_window_only",
        "first_timestamp": first_ts.isoformat(),
        "last_timestamp": last_ts.isoformat(),
        "duration_seconds": float(max(0.0, (last_ts - first_ts).total_seconds())),
        "capacity_mbps": capacity_mbps,
        "availability_definition": "porcentaje de ciclos con reachability=True durante la ventana observada por target",
        "loss_definition": "(paquetes_enviados - paquetes_recibidos) / paquetes_enviados * 100, agregado por target",
        "delay_variation_definition": "abs(RTT mediano actual - RTT mediano anterior) por target; no es jitter unidireccional",
        "rate_definition": "bytes_delta * 8 / segundos transcurridos desde la última lectura válida de contadores / 1e6",
        "utilization_definition": "max(rx_rate_mbps, tx_rate_mbps) / capacity_mbps * 100; solo si capacity_mbps fue suministrada explícitamente",
        "limitations": [
            "La disponibilidad describe únicamente la ventana observada.",
            "La velocidad reportada por la NIC no se usa como capacidad del enlace del ISP.",
            "La variación temporal de RTT no se presenta como jitter unidireccional.",
        ],
    }
    return QualityResult(
        interface_summary=interface_summary,
        probe_summary=probe_summary,
        interface_processed=interface_processed,
        probe_processed=probe_processed,
        metadata=metadata,
    )


def write_quality(result: QualityResult, output_dir: str | Path) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    result.interface_summary.to_csv(output / "quality_interface_summary.csv", index=False)
    result.probe_summary.to_csv(output / "quality_probe_summary.csv", index=False)
    result.interface_processed.to_csv(output / "quality_interface_processed.csv", index=False)
    result.probe_processed.to_csv(output / "quality_probe_processed.csv", index=False)
    manifest = output / "quality_summary.json"
    payload = {
        "metadata": result.metadata,
        "interfaces": result.interface_summary.replace({np.nan: None}).to_dict(orient="records"),
        "targets": result.probe_summary.replace({np.nan: None}).to_dict(orient="records"),
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return manifest
