from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

import numpy as np
import pandas as pd

from .config import AnalysisConfig


COMMERCIAL_CAPACITIES = [
    10, 20, 30, 50, 75, 100, 150, 200, 250, 300, 400, 500, 600,
    800, 1000, 1500, 2000, 3000, 5000, 10000, 20000,
]


@dataclass(frozen=True)
class TrendResult:
    slope_utilization_per_day: float | None
    current_utilization: float | None
    days_to_threshold: float | None
    r2: float | None
    confidence: str
    direction: str
    reliable: bool
    observations: int


@dataclass(frozen=True)
class AnalysisResult:
    summary: pd.DataFrame
    alerts: pd.DataFrame
    daily: pd.DataFrame
    hourly: pd.DataFrame
    processed: pd.DataFrame


def _round_capacity(required_mbps: float) -> float:
    if not np.isfinite(required_mbps) or required_mbps <= 0:
        return 0.0
    for capacity in COMMERCIAL_CAPACITIES:
        if capacity >= required_mbps:
            return float(capacity)
    return float(ceil(required_mbps / 1000.0) * 1000)


def _trend_metrics(
    daily_link: pd.DataFrame,
    threshold: float,
    config: AnalysisConfig,
) -> TrendResult:
    daily_link = daily_link.sort_values("date")
    observations = len(daily_link)
    if observations < 3:
        return TrendResult(None, None, None, None, "INSUFICIENTE", "SIN_DATOS", False, observations)

    x = (daily_link["date"] - daily_link["date"].min()).dt.days.to_numpy(dtype=float)
    y = daily_link["daily_p95_utilization"].to_numpy(dtype=float)
    tail = daily_link.tail(min(3, observations))
    current = float(tail["daily_p95_utilization"].mean())

    if len(np.unique(x)) < 3:
        return TrendResult(None, current, None, None, "INSUFICIENTE", "SIN_DATOS", False, observations)

    slope, intercept = np.polyfit(x, y, 1)
    slope = float(slope)
    predicted = intercept + slope * x
    ss_res = float(np.sum((y - predicted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 if ss_tot <= 1e-12 and ss_res <= 1e-12 else (0.0 if ss_tot <= 1e-12 else max(0.0, 1 - ss_res / ss_tot))

    slope_pp = slope * 100
    min_slope = config.minimum_growth_pp_per_day
    if slope_pp >= min_slope:
        direction = "CRECIENTE"
    elif slope_pp <= -min_slope:
        direction = "DECRECIENTE"
    else:
        direction = "ESTABLE"

    enough_days = observations >= config.minimum_trend_days
    reliable = enough_days and r2 >= config.minimum_trend_r2 and direction != "ESTABLE"

    if not enough_days:
        confidence = "INSUFICIENTE"
    elif r2 >= 0.70 and observations >= 14:
        confidence = "ALTA"
    elif r2 >= config.minimum_trend_r2:
        confidence = "MEDIA"
    else:
        confidence = "BAJA"

    if current >= threshold:
        days = 0.0
    elif reliable and direction == "CRECIENTE" and slope > 0:
        days = max(0.0, (threshold - current) / slope)
    else:
        days = None

    return TrendResult(slope, current, days, r2, confidence, direction, reliable, observations)


def _status_for(
    p95: float,
    trend: TrendResult,
    config: AnalysisConfig,
) -> str:
    if p95 >= config.critical_threshold:
        return "CRÍTICO"
    if trend.reliable and trend.days_to_threshold is not None and trend.days_to_threshold <= config.critical_days:
        return "CRÍTICO"
    if p95 >= config.warning_threshold:
        return "ALTO"
    if trend.reliable and trend.days_to_threshold is not None and trend.days_to_threshold <= config.warning_days:
        return "ALTO"
    if p95 >= 0.60:
        return "MEDIO"
    return "NORMAL"


def _safe_spearman(a: pd.Series, b: pd.Series) -> float | None:
    """Calcula Spearman sin depender de SciPy.

    Spearman equivale a la correlación de Pearson entre los rangos de ambas
    variables. Usar ``Series.corr(method="spearman")`` hace que pandas
    importe SciPy de forma implícita; este MVP evita esa dependencia pesada.
    """
    pair = pd.DataFrame({"a": a, "b": b}).dropna()
    if len(pair) < 20 or pair["a"].nunique() < 3 or pair["b"].nunique() < 3:
        return None

    rank_a = pair["a"].rank(method="average").to_numpy(dtype=float)
    rank_b = pair["b"].rank(method="average").to_numpy(dtype=float)

    if np.std(rank_a) <= 1e-12 or np.std(rank_b) <= 1e-12:
        return None

    value = float(np.corrcoef(rank_a, rank_b)[0, 1])
    return None if not np.isfinite(value) else value


def _load_delta(group: pd.DataFrame, column: str, quantile: float) -> float | None:
    pair = group[["utilization", column]].dropna()
    if len(pair) < 20 or pair[column].nunique() < 3:
        return None
    high_cut = pair["utilization"].quantile(quantile)
    low_cut = pair["utilization"].quantile(1 - quantile)
    high = pair.loc[pair["utilization"] >= high_cut, column]
    low = pair.loc[pair["utilization"] <= low_cut, column]
    if high.empty or low.empty:
        return None
    return float(high.mean() - low.mean())


def _quality_association(
    latency_corr: float | None,
    loss_corr: float | None,
    latency_delta: float | None,
    loss_delta: float | None,
    config: AnalysisConfig,
) -> str:
    strong_latency = (
        latency_corr is not None
        and latency_corr >= config.quality_correlation_threshold
        and latency_delta is not None
        and latency_delta >= config.latency_high_load_delta_ms
    )
    strong_loss = (
        loss_corr is not None
        and loss_corr >= config.quality_correlation_threshold
        and loss_delta is not None
        and loss_delta >= config.loss_high_load_delta_pct
    )
    if strong_latency or strong_loss:
        return "FUERTE"
    if any(v is not None and v >= config.quality_correlation_threshold for v in (latency_corr, loss_corr)):
        return "MODERADA"
    if latency_corr is None and loss_corr is None:
        return "SIN_DATOS"
    return "NO_CONCLUYENTE"


def _recommended_action(status: str, quality_association: str, delta_capacity: float) -> str:
    if status == "CRÍTICO":
        if delta_capacity > 0:
            return "Validar el enlace y planificar ampliación de capacidad con prioridad inmediata."
        return "Revisar congestión, shaping y causas operativas con prioridad inmediata."
    if status == "ALTO":
        return "Programar revisión de capacidad y confirmar la tendencia con más datos."
    if quality_association == "FUERTE":
        return "Investigar la asociación entre carga y degradación antes de ampliar."
    if status == "MEDIO":
        return "Mantener seguimiento semanal y revisar crecimiento en horas pico."
    return "Mantener capacidad y continuar monitoreo periódico."


def analyze(df: pd.DataFrame, config: AnalysisConfig) -> AnalysisResult:
    daily = (
        df.groupby(["link_id", "date"], as_index=False)
        .agg(
            daily_p95_utilization=("utilization", lambda s: float(s.quantile(0.95))),
            daily_p95_rx_utilization=("rx_utilization", lambda s: float(s.quantile(0.95))),
            daily_p95_tx_utilization=("tx_utilization", lambda s: float(s.quantile(0.95))),
            daily_mean_utilization=("utilization", "mean"),
            daily_peak_traffic_mbps=("traffic_mbps", "max"),
            capacity_mbps=("capacity_mbps", "median"),
        )
    )

    hourly = (
        df.groupby(["link_id", "hour"], as_index=False)
        .agg(
            mean_utilization=("utilization", "mean"),
            p95_utilization=("utilization", lambda s: float(s.quantile(0.95))),
            p95_rx_utilization=("rx_utilization", lambda s: float(s.quantile(0.95))),
            p95_tx_utilization=("tx_utilization", lambda s: float(s.quantile(0.95))),
        )
    )

    summary_rows: list[dict[str, Any]] = []
    alert_rows: list[dict[str, Any]] = []

    for link_id, group in df.groupby("link_id", sort=True):
        group = group.sort_values("timestamp")
        link_daily = daily[daily["link_id"] == link_id]
        link_hourly = hourly[hourly["link_id"] == link_id]

        rx_p95 = float(group["rx_utilization"].quantile(0.95))
        tx_p95 = float(group["tx_utilization"].quantile(0.95))
        dominant_direction = "RX" if rx_p95 >= tx_p95 else "TX"
        dominant_traffic_column = "rx_mbps" if dominant_direction == "RX" else "tx_mbps"

        p95_util = float(group["utilization"].quantile(0.95))
        p99_util = float(group["utilization"].quantile(0.99))
        avg_util = float(group["utilization"].mean())
        max_util = float(group["utilization"].max())
        p95_traffic = float(group[dominant_traffic_column].quantile(0.95))
        capacity = float(group["capacity_mbps"].median())
        technical_required = p95_traffic / config.target_utilization
        recommended_capacity = max(capacity, _round_capacity(technical_required))
        delta_capacity = max(0.0, recommended_capacity - capacity)

        peak_hour_row = link_hourly.loc[link_hourly["p95_utilization"].idxmax()]
        peak_hour = int(peak_hour_row["hour"])

        trend = _trend_metrics(link_daily, config.critical_threshold, config)
        status = _status_for(p95_util, trend, config)

        latency_p95 = None
        loss_p95 = None
        availability_min = None
        if "latency_ms" in group.columns and group["latency_ms"].notna().any():
            latency_p95 = float(group["latency_ms"].dropna().quantile(0.95))
        if "packet_loss_pct" in group.columns and group["packet_loss_pct"].notna().any():
            loss_p95 = float(group["packet_loss_pct"].dropna().quantile(0.95))
        if "availability_pct" in group.columns and group["availability_pct"].notna().any():
            availability_min = float(group["availability_pct"].dropna().min())

        latency_corr = _safe_spearman(group["utilization"], group["latency_ms"]) if "latency_ms" in group else None
        loss_corr = _safe_spearman(group["utilization"], group["packet_loss_pct"]) if "packet_loss_pct" in group else None
        latency_delta = _load_delta(group, "latency_ms", config.high_load_quantile) if "latency_ms" in group else None
        loss_delta = _load_delta(group, "packet_loss_pct", config.high_load_quantile) if "packet_loss_pct" in group else None
        quality_association = _quality_association(latency_corr, loss_corr, latency_delta, loss_delta, config)

        over_capacity_samples = int(group["over_capacity"].sum())
        action = _recommended_action(status, quality_association, delta_capacity)

        summary_rows.append(
            {
                "link_id": link_id,
                "status": status,
                "samples": len(group),
                "days_observed": int(group["date"].nunique()),
                "first_timestamp": group["timestamp"].min(),
                "last_timestamp": group["timestamp"].max(),
                "capacity_mbps": capacity,
                "dominant_direction": dominant_direction,
                "avg_utilization_pct": avg_util * 100,
                "rx_p95_utilization_pct": rx_p95 * 100,
                "tx_p95_utilization_pct": tx_p95 * 100,
                "p95_utilization_pct": p95_util * 100,
                "p99_utilization_pct": p99_util * 100,
                "max_utilization_pct": max_util * 100,
                "p95_traffic_mbps": p95_traffic,
                "peak_hour": peak_hour,
                "trend_direction": trend.direction,
                "trend_pct_points_per_day": None if trend.slope_utilization_per_day is None else trend.slope_utilization_per_day * 100,
                "trend_r2": trend.r2,
                "trend_confidence": trend.confidence,
                "trend_reliable": trend.reliable,
                "current_daily_p95_pct": None if trend.current_utilization is None else trend.current_utilization * 100,
                "days_to_critical": trend.days_to_threshold,
                "technical_required_capacity_mbps": technical_required,
                "recommended_capacity_mbps": recommended_capacity,
                "additional_capacity_mbps": delta_capacity,
                "over_capacity_samples": over_capacity_samples,
                "latency_p95_ms": latency_p95,
                "packet_loss_p95_pct": loss_p95,
                "availability_min_pct": availability_min,
                "util_latency_spearman": latency_corr,
                "util_loss_spearman": loss_corr,
                "latency_high_vs_low_delta_ms": latency_delta,
                "loss_high_vs_low_delta_pct": loss_delta,
                "quality_load_association": quality_association,
                "recommended_action": action,
            }
        )

        if p95_util >= config.critical_threshold:
            alert_rows.append({
                "link_id": link_id,
                "severity": "CRÍTICA",
                "type": "CAPACIDAD",
                "message": f"P95 dominante de {p95_util * 100:.1f}% ({dominant_direction}), por encima del umbral crítico.",
            })
        elif p95_util >= config.warning_threshold:
            alert_rows.append({
                "link_id": link_id,
                "severity": "ALTA",
                "type": "CAPACIDAD",
                "message": f"P95 dominante de {p95_util * 100:.1f}% ({dominant_direction}), cerca del límite operativo.",
            })

        if trend.reliable and trend.days_to_threshold is not None and 0 < trend.days_to_threshold <= config.warning_days:
            severity = "CRÍTICA" if trend.days_to_threshold <= config.critical_days else "ALTA"
            alert_rows.append({
                "link_id": link_id,
                "severity": severity,
                "type": "TENDENCIA",
                "message": (
                    f"La tendencia alcanzaría {config.critical_threshold * 100:.0f}% en aproximadamente "
                    f"{trend.days_to_threshold:.0f} días (R²={trend.r2:.2f}, confianza {trend.confidence.lower()})."
                ),
            })
        elif trend.direction == "CRECIENTE" and not trend.reliable:
            alert_rows.append({
                "link_id": link_id,
                "severity": "BAJA",
                "type": "TENDENCIA",
                "message": "Se observa crecimiento, pero la evidencia estadística aún no es suficiente para proyectar una fecha.",
            })

        if over_capacity_samples:
            alert_rows.append({
                "link_id": link_id,
                "severity": "MEDIA",
                "type": "CALIDAD_DATOS",
                "message": f"{over_capacity_samples} muestras superan 100% de la capacidad nominal; validar unidades o configuración.",
            })

        if latency_p95 is not None and latency_p95 > config.latency_threshold_ms:
            alert_rows.append({
                "link_id": link_id,
                "severity": "ALTA",
                "type": "LATENCIA",
                "message": f"Latencia P95 de {latency_p95:.1f} ms, superior al umbral de {config.latency_threshold_ms:.1f} ms.",
            })
        if loss_p95 is not None and loss_p95 > config.packet_loss_threshold_pct:
            alert_rows.append({
                "link_id": link_id,
                "severity": "ALTA",
                "type": "PÉRDIDA",
                "message": f"Pérdida P95 de {loss_p95:.2f}%, superior al umbral de {config.packet_loss_threshold_pct:.2f}%.",
            })
        if availability_min is not None and availability_min < config.availability_threshold_pct:
            alert_rows.append({
                "link_id": link_id,
                "severity": "MEDIA",
                "type": "DISPONIBILIDAD",
                "message": f"Disponibilidad mínima de {availability_min:.2f}%, inferior al objetivo de {config.availability_threshold_pct:.2f}%.",
            })
        if quality_association == "FUERTE":
            alert_rows.append({
                "link_id": link_id,
                "severity": "ALTA",
                "type": "CARGA_CALIDAD",
                "message": "Existe una asociación estadística fuerte entre mayor carga y degradación de calidad; no implica causalidad y debe validarse técnicamente.",
            })

    status_order = pd.CategoricalDtype(["CRÍTICO", "ALTO", "MEDIO", "NORMAL"], ordered=True)
    summary = pd.DataFrame(summary_rows)
    summary["status"] = summary["status"].astype(status_order)
    summary = summary.sort_values(["status", "p95_utilization_pct"], ascending=[True, False]).reset_index(drop=True)
    summary["status"] = summary["status"].astype(str)

    alerts = pd.DataFrame(alert_rows, columns=["link_id", "severity", "type", "message"])
    if not alerts.empty:
        severity_order = pd.CategoricalDtype(["CRÍTICA", "ALTA", "MEDIA", "BAJA"], ordered=True)
        alerts["severity"] = alerts["severity"].astype(severity_order)
        alerts = alerts.sort_values(["severity", "link_id", "type"]).reset_index(drop=True)
        alerts["severity"] = alerts["severity"].astype(str)

    return AnalysisResult(summary=summary, alerts=alerts, daily=daily, hourly=hourly, processed=df)
