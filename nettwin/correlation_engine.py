from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CorrelationConfig:
    bucket_seconds: int = 5
    min_pairs: int = 12
    weak_abs_rho: float = 0.30

    def validate(self) -> None:
        if self.bucket_seconds <= 0:
            raise ValueError("bucket_seconds debe ser > 0")
        if self.min_pairs < 3:
            raise ValueError("min_pairs debe ser >= 3")
        if not 0.0 <= self.weak_abs_rho <= 1.0:
            raise ValueError("weak_abs_rho debe estar entre 0 y 1")


@dataclass(frozen=True)
class CorrelationResult:
    correlations: pd.DataFrame
    aligned_pairs: pd.DataFrame
    metadata: dict[str, Any]


def _load_csv(
    path: str | Path,
    required: set[str],
    label: str,
) -> pd.DataFrame:
    csv_path = Path(path)
    if not csv_path.exists():
        raise ValueError(f"No existe el archivo {label}: {csv_path}")
    df = pd.read_csv(csv_path)
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(
            f"Faltan columnas requeridas en {csv_path.name}: {', '.join(missing)}"
        )
    if df.empty:
        raise ValueError(f"El archivo {label} no contiene muestras: {csv_path}")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    if df["timestamp"].isna().any():
        raise ValueError(f"Hay timestamps inválidos en {csv_path.name}")
    return df.sort_values("timestamp").reset_index(drop=True)


def _numeric(df: pd.DataFrame, columns: tuple[str, ...]) -> None:
    for column in columns:
        if column in df:
            df[column] = pd.to_numeric(df[column], errors="coerce")


def _bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "yes", "si", "sí"})


def _bucket(series: pd.Series, seconds: int) -> pd.Series:
    return series.dt.floor(f"{seconds}s")


def _window(df: pd.DataFrame) -> dict[str, Any]:
    first = df["timestamp"].min()
    last = df["timestamp"].max()
    return {
        "first_timestamp": first.isoformat(),
        "last_timestamp": last.isoformat(),
        "duration_seconds": float(max(0.0, (last - first).total_seconds())),
    }


def _overlap(left: pd.DataFrame, right: pd.DataFrame) -> dict[str, Any]:
    start = max(left["timestamp"].min(), right["timestamp"].min())
    end = min(left["timestamp"].max(), right["timestamp"].max())
    exists = bool(end > start)
    return {
        "exists": exists,
        "first_timestamp": start.isoformat() if exists else None,
        "last_timestamp": end.isoformat() if exists else None,
        "duration_seconds": float((end - start).total_seconds()) if exists else 0.0,
    }


def _rank_average(values: np.ndarray) -> np.ndarray:
    return pd.Series(values).rank(method="average").to_numpy(dtype=float)


def spearman_rho(x: pd.Series, y: pd.Series) -> float | None:
    pair = pd.DataFrame(
        {
            "x": pd.to_numeric(x, errors="coerce"),
            "y": pd.to_numeric(y, errors="coerce"),
        }
    ).dropna()
    if len(pair) < 2:
        return None
    x_values = pair["x"].to_numpy(dtype=float)
    y_values = pair["y"].to_numpy(dtype=float)
    if np.all(x_values == x_values[0]) or np.all(y_values == y_values[0]):
        return None
    x_rank = _rank_average(x_values)
    y_rank = _rank_average(y_values)
    value = float(np.corrcoef(x_rank, y_rank)[0, 1])
    if not np.isfinite(value):
        return None
    return max(-1.0, min(1.0, value))


def _strength(rho: float | None) -> str:
    if rho is None:
        return "N/D"
    value = abs(rho)
    if value < 0.20:
        return "very_weak"
    if value < 0.40:
        return "weak"
    if value < 0.60:
        return "moderate"
    if value < 0.80:
        return "strong"
    return "very_strong"


def _direction(rho: float | None) -> str:
    if rho is None:
        return "N/D"
    if rho > 0:
        return "positive"
    if rho < 0:
        return "negative"
    return "none"


def _prepare_interface(df: pd.DataFrame, bucket_seconds: int) -> pd.DataFrame:
    work = df.copy()
    _numeric(
        work,
        (
            "utilization_pct",
            "rx_rate_mbps",
            "tx_rate_mbps",
            "rx_drops_delta",
            "tx_drops_delta",
            "rx_errors_delta",
            "tx_errors_delta",
        ),
    )
    if "sample_ok" in work:
        work = work[_bool_series(work["sample_ok"])]
    elif "sample_status" in work:
        work = work[work["sample_status"].astype(str).str.lower().eq("ok")]
    if work.empty:
        return pd.DataFrame(
            columns=["bucket", "utilization_pct", "drops_delta", "errors_delta"]
        )
    work["bucket"] = _bucket(work["timestamp"], bucket_seconds)
    work["drops_delta"] = (
        work[["rx_drops_delta", "tx_drops_delta"]].fillna(0).sum(axis=1)
    )
    work["errors_delta"] = (
        work[["rx_errors_delta", "tx_errors_delta"]].fillna(0).sum(axis=1)
    )
    return (
        work.groupby("bucket", sort=True)
        .agg(
            utilization_pct=("utilization_pct", "median"),
            rx_rate_mbps=("rx_rate_mbps", "median"),
            tx_rate_mbps=("tx_rate_mbps", "median"),
            drops_delta=("drops_delta", "sum"),
            errors_delta=("errors_delta", "sum"),
        )
        .reset_index()
    )


def _prepare_probes(df: pd.DataFrame, bucket_seconds: int) -> pd.DataFrame:
    work = df.copy()
    _numeric(
        work,
        ("packets_sent", "packets_received", "rtt_ms", "delay_variation_ms"),
    )
    if "sample_status" in work:
        work = work[work["sample_status"].astype(str).str.lower().eq("ok")]
    if work.empty:
        return pd.DataFrame(
            columns=[
                "bucket",
                "target",
                "rtt_ms",
                "delay_variation_ms",
                "packet_loss_pct",
            ]
        )
    work["bucket"] = _bucket(work["timestamp"], bucket_seconds)
    rows: list[dict[str, Any]] = []
    for (bucket, target), group in work.groupby(["bucket", "target"], sort=True):
        sent = float(group["packets_sent"].fillna(0).sum())
        received = float(group["packets_received"].fillna(0).sum())
        lost = max(0.0, sent - received)
        loss = float((lost / sent) * 100.0) if sent > 0 else np.nan
        rows.append(
            {
                "bucket": bucket,
                "target": str(target),
                "rtt_ms": (
                    None
                    if group["rtt_ms"].dropna().empty
                    else float(group["rtt_ms"].median())
                ),
                "delay_variation_ms": (
                    None
                    if group["delay_variation_ms"].dropna().empty
                    else float(group["delay_variation_ms"].median())
                ),
                "packet_loss_pct": loss,
            }
        )
    return pd.DataFrame(rows)


def _prepare_host(df: pd.DataFrame, bucket_seconds: int) -> pd.DataFrame:
    work = df.copy()
    _numeric(work, ("cpu_percent", "memory_percent", "load_1m"))
    if "sample_status" in work:
        work = work[work["sample_status"].astype(str).str.lower().eq("ok")]
    if work.empty:
        return pd.DataFrame(columns=["bucket", "cpu_percent"])
    work["bucket"] = _bucket(work["timestamp"], bucket_seconds)
    return (
        work.groupby("bucket", sort=True)
        .agg(cpu_percent=("cpu_percent", "median"))
        .reset_index()
    )


def _relation_row(
    *,
    relation_id: str,
    x_metric: str,
    y_metric: str,
    target: str | None,
    frame: pd.DataFrame,
    x_column: str,
    y_column: str,
    config: CorrelationConfig,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    if unavailable_reason is not None:
        return {
            "relation_id": relation_id,
            "x_metric": x_metric,
            "y_metric": y_metric,
            "target": target,
            "pair_count": 0,
            "spearman_rho": None,
            "abs_rho": None,
            "direction": "N/D",
            "strength": "N/D",
            "sufficient_samples": False,
            "evidence_status": "unavailable",
            "descriptive_significance": "N/D",
            "first_timestamp": None,
            "last_timestamp": None,
            "causal_interpretation_allowed": False,
            "note": unavailable_reason,
        }

    pair = frame[["bucket", x_column, y_column]].copy()
    pair[x_column] = pd.to_numeric(pair[x_column], errors="coerce")
    pair[y_column] = pd.to_numeric(pair[y_column], errors="coerce")
    pair = pair.dropna()
    count = int(len(pair))
    first = pair["bucket"].min().isoformat() if count else None
    last = pair["bucket"].max().isoformat() if count else None

    if count == 0:
        rho = None
        status = "no_valid_pairs"
        note = "No existen pares temporales válidos para esta relación."
    elif pair[x_column].nunique(dropna=True) <= 1 or pair[y_column].nunique(dropna=True) <= 1:
        rho = None
        status = "constant_series"
        note = "Una de las series es constante; Spearman no es definible."
    else:
        rho = spearman_rho(pair[x_column], pair[y_column])
        if count < config.min_pairs:
            status = "insufficient_samples"
            note = (
                f"Solo hay {count} pares válidos; se requieren al menos "
                f"{config.min_pairs} para interpretar la asociación."
            )
        elif rho is None:
            status = "not_computable"
            note = "La correlación no pudo calcularse con los pares disponibles."
        elif abs(rho) < config.weak_abs_rho:
            status = "weak_association"
            note = (
                "Asociación descriptiva débil; no debe usarse como evidencia "
                "suficiente de relación operativa."
            )
        else:
            status = "descriptive_association"
            note = (
                "Asociación descriptiva durante la ventana observada; "
                "no implica causalidad."
            )

    if status in {"no_valid_pairs", "constant_series", "not_computable"}:
        descriptive_significance = "N/D"
    elif status == "insufficient_samples":
        descriptive_significance = "insufficient"
    elif status == "weak_association":
        descriptive_significance = "low"
    else:
        descriptive_significance = "adequate_descriptive"

    return {
        "relation_id": relation_id,
        "x_metric": x_metric,
        "y_metric": y_metric,
        "target": target,
        "pair_count": count,
        "spearman_rho": rho,
        "abs_rho": None if rho is None else abs(rho),
        "direction": _direction(rho),
        "strength": _strength(rho),
        "sufficient_samples": count >= config.min_pairs,
        "evidence_status": status,
        "descriptive_significance": descriptive_significance,
        "first_timestamp": first,
        "last_timestamp": last,
        "causal_interpretation_allowed": False,
        "note": note,
    }


def _aligned_network(
    interface_bucketed: pd.DataFrame,
    probe_bucketed: pd.DataFrame,
) -> pd.DataFrame:
    if interface_bucketed.empty or probe_bucketed.empty:
        return pd.DataFrame()
    return probe_bucketed.merge(interface_bucketed, on="bucket", how="inner")


def _aligned_host(
    host_bucketed: pd.DataFrame,
    probe_bucketed: pd.DataFrame,
) -> pd.DataFrame:
    if host_bucketed.empty or probe_bucketed.empty:
        return pd.DataFrame()
    return probe_bucketed.merge(host_bucketed, on="bucket", how="inner")


def analyze_correlations(
    interface_processed_csv: str | Path,
    probe_processed_csv: str | Path,
    *,
    host_csv: str | Path | None = None,
    config: CorrelationConfig | None = None,
) -> CorrelationResult:
    config = config or CorrelationConfig()
    config.validate()

    interface_required = {
        "timestamp",
        "interface",
        "utilization_pct",
        "rx_rate_mbps",
        "tx_rate_mbps",
        "rx_drops_delta",
        "tx_drops_delta",
        "rx_errors_delta",
        "tx_errors_delta",
    }
    probe_required = {
        "timestamp",
        "target",
        "packets_sent",
        "packets_received",
        "rtt_ms",
        "delay_variation_ms",
    }
    host_required = {"timestamp", "cpu_percent"}

    interface = _load_csv(
        interface_processed_csv, interface_required, "de interfaz procesada"
    )
    if interface["interface"].astype(str).nunique() != 1:
        raise ValueError(
            "Correlation Engine requiere exactamente una interfaz por ejecución."
        )
    probes = _load_csv(
        probe_processed_csv, probe_required, "de sondas procesadas"
    )
    host = (
        _load_csv(host_csv, host_required, "del host")
        if host_csv is not None
        else None
    )

    interface_bucketed = _prepare_interface(interface, config.bucket_seconds)
    probe_bucketed = _prepare_probes(probes, config.bucket_seconds)
    host_bucketed = (
        _prepare_host(host, config.bucket_seconds)
        if host is not None
        else pd.DataFrame()
    )

    network_aligned = _aligned_network(interface_bucketed, probe_bucketed)
    host_aligned = _aligned_host(host_bucketed, probe_bucketed)

    rows: list[dict[str, Any]] = []
    targets = sorted(str(value) for value in probe_bucketed["target"].dropna().unique())

    utilization_available = (
        not interface_bucketed.empty
        and interface_bucketed["utilization_pct"].notna().any()
    )
    network_overlap = _overlap(interface, probes)
    host_probe_overlap = _overlap(host, probes) if host is not None else None

    for target in targets:
        target_network = network_aligned[network_aligned["target"] == target].copy()
        if not network_overlap["exists"]:
            util_reason = (
                "Interfaz y sondas no se solapan temporalmente; "
                "no se calcula correlación conjunta."
            )
        elif not utilization_available:
            util_reason = (
                "Utilización N/D porque no se suministró capacidad explícita del enlace."
            )
        else:
            util_reason = None

        rows.append(
            _relation_row(
                relation_id=f"utilization__rtt__{target}",
                x_metric="utilization_pct",
                y_metric="rtt_ms",
                target=target,
                frame=target_network,
                x_column="utilization_pct",
                y_column="rtt_ms",
                config=config,
                unavailable_reason=util_reason,
            )
        )
        rows.append(
            _relation_row(
                relation_id=f"utilization__delay_variation__{target}",
                x_metric="utilization_pct",
                y_metric="delay_variation_ms",
                target=target,
                frame=target_network,
                x_column="utilization_pct",
                y_column="delay_variation_ms",
                config=config,
                unavailable_reason=util_reason,
            )
        )
        rows.append(
            _relation_row(
                relation_id=f"utilization__packet_loss__{target}",
                x_metric="utilization_pct",
                y_metric="packet_loss_pct",
                target=target,
                frame=target_network,
                x_column="utilization_pct",
                y_column="packet_loss_pct",
                config=config,
                unavailable_reason=util_reason,
            )
        )

        if host is None:
            host_reason = "No se suministró host_samples.csv; relaciones CPU N/D."
        elif not host_probe_overlap or not host_probe_overlap["exists"]:
            host_reason = (
                "Host y sondas no se solapan temporalmente; relaciones CPU N/D."
            )
        else:
            host_reason = None
        target_host = host_aligned[host_aligned["target"] == target].copy()
        rows.append(
            _relation_row(
                relation_id=f"cpu__rtt__{target}",
                x_metric="cpu_percent",
                y_metric="rtt_ms",
                target=target,
                frame=target_host,
                x_column="cpu_percent",
                y_column="rtt_ms",
                config=config,
                unavailable_reason=host_reason,
            )
        )
        rows.append(
            _relation_row(
                relation_id=f"cpu__packet_loss__{target}",
                x_metric="cpu_percent",
                y_metric="packet_loss_pct",
                target=target,
                frame=target_host,
                x_column="cpu_percent",
                y_column="packet_loss_pct",
                config=config,
                unavailable_reason=host_reason,
            )
        )

    if not utilization_available:
        drops_reason = (
            "Utilización N/D porque no se suministró capacidad explícita del enlace."
        )
    else:
        drops_reason = None

    # utilization ↔ drops es una relación dentro de la propia interfaz;
    # no requiere solapamiento con sondas.
    rows.append(
        _relation_row(
            relation_id="utilization__drops",
            x_metric="utilization_pct",
            y_metric="drops_delta",
            target=None,
            frame=interface_bucketed,
            x_column="utilization_pct",
            y_column="drops_delta",
            config=config,
            unavailable_reason=drops_reason,
        )
    )

    correlations = pd.DataFrame(rows)

    aligned_frames: list[pd.DataFrame] = []
    if not network_aligned.empty:
        network_export = network_aligned.copy()
        network_export["source"] = "interface_probe"
        aligned_frames.append(network_export)
    if not host_aligned.empty:
        host_export = host_aligned.copy()
        host_export["source"] = "host_probe"
        aligned_frames.append(host_export)
    aligned_pairs = (
        pd.concat(aligned_frames, ignore_index=True, sort=False)
        if aligned_frames
        else pd.DataFrame()
    )

    metadata: dict[str, Any] = {
        "mode": "correlation-engine",
        "scope": "observed_window_only",
        "method": "Spearman rank correlation on time-aligned buckets",
        "bucket_seconds": config.bucket_seconds,
        "min_pairs": config.min_pairs,
        "weak_abs_rho": config.weak_abs_rho,
        "interface_window": _window(interface),
        "probe_window": _window(probes),
        "network_overlap": network_overlap,
        "host_supplied": host is not None,
        "host_window": _window(host) if host is not None else None,
        "host_probe_overlap": host_probe_overlap,
        "targets": targets,
        "relation_count": int(len(correlations)),
        "limitations": [
            "Las correlaciones describen únicamente la ventana observada.",
            "Spearman mide asociación monotónica y no demuestra causalidad.",
            "No se calculan retardos causales ni dirección temporal de influencia.",
            "No se reporta p-value inferencial porque las series temporales pueden presentar autocorrelación.",
            "Pocos pares válidos o |rho| bajo se marcan explícitamente como evidencia insuficiente o débil.",
            "La utilización solo se analiza cuando existe capacidad de enlace suministrada explícitamente en Quality Engine.",
        ],
    }

    return CorrelationResult(
        correlations=correlations,
        aligned_pairs=aligned_pairs,
        metadata=metadata,
    )


def write_correlations(
    result: CorrelationResult,
    output_dir: str | Path,
) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    json_path = output / "correlations.json"
    summary_path = output / "correlation_summary.csv"
    aligned_path = output / "correlation_aligned_pairs.csv"

    payload = {
        "metadata": result.metadata,
        "correlations": result.correlations.replace({np.nan: None}).to_dict(
            orient="records"
        ),
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    result.correlations.to_csv(summary_path, index=False)
    result.aligned_pairs.to_csv(aligned_path, index=False)
    return json_path
