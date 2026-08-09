from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def generate_demo_csv(output_path: str | Path, seed: int = 20260804) -> Path:
    """Genera datos sintéticos reproducibles para tres escenarios de enlaces.

    Los datos no representan la operación real de ninguna empresa.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    start = pd.Timestamp("2026-07-20 00:00:00")
    timestamps = pd.date_range(start, periods=14 * 96, freq="15min")
    rows: list[dict[str, float | str | pd.Timestamp]] = []

    for ts in timestamps:
        day = (ts.floor("D") - start.floor("D")).days
        hour = ts.hour + ts.minute / 60
        evening = np.exp(-0.5 * ((hour - 20.0) / 2.3) ** 2)
        morning = np.exp(-0.5 * ((hour - 10.0) / 2.8) ** 2)
        workday = 1.0

        # Enlace estable: variación diaria sin crecimiento estructural.
        capacity = 200.0
        rx = (42 + 36 * evening + 10 * morning) * workday + rng.normal(0, 3.0)
        tx = (20 + 18 * evening + 7 * morning) * workday + rng.normal(0, 2.0)
        utilization = max(rx, tx) / capacity
        latency = 22 + rng.normal(0, 1.8)
        loss = max(0.0, 0.08 + rng.normal(0, 0.04))
        availability = 99.96 if rng.random() > 0.002 else 99.40
        rows.append(_row(ts, "HN_CEDENO_ESTABLE", rx, tx, capacity, latency, loss, availability))

        # Enlace con crecimiento: alrededor de 0.30 puntos porcentuales diarios en P95.
        capacity = 200.0
        growth = 0.70 * day
        rx = (74 + growth + 58 * evening + 12 * morning) * workday + rng.normal(0, 1.8)
        tx = (31 + 0.22 * day + 24 * evening + 8 * morning) * workday + rng.normal(0, 1.5)
        utilization = max(rx, tx) / capacity
        latency = 22 + 28 * utilization + 5 * max(0.0, utilization - 0.65) + rng.normal(0, 2.0)
        loss = max(0.0, 0.08 + 0.55 * utilization + 0.9 * max(0.0, utilization - 0.70) + rng.normal(0, 0.07))
        availability = 99.88 if rng.random() > 0.004 else 98.90
        rows.append(_row(ts, "HN_YARUMAL_CRECIMIENTO", rx, tx, capacity, latency, loss, availability))

        # Enlace crítico: alta carga en hora pico, sin exceder la capacidad nominal.
        capacity = 150.0
        rx = (66 + 74 * evening + 10 * morning) * workday + rng.normal(0, 2.3)
        tx = (35 + 32 * evening + 8 * morning) * workday + rng.normal(0, 1.8)
        rx = min(rx, 148.5)
        tx = min(tx, 145.0)
        utilization = max(rx, tx) / capacity
        latency = 25 + 35 * utilization + 75 * max(0.0, utilization - 0.75) + rng.normal(0, 2.5)
        loss = max(0.0, 0.10 + 0.65 * utilization + 8.0 * max(0.0, utilization - 0.78) + rng.normal(0, 0.10))
        availability = 99.75 if rng.random() > 0.006 else 98.40
        rows.append(_row(ts, "HN_CAMPAMENTO_CRITICO", rx, tx, capacity, latency, loss, availability))

    df = pd.DataFrame(rows)
    numeric = ["rx_mbps", "tx_mbps", "capacity_mbps", "latency_ms", "packet_loss_pct", "availability_pct"]
    df[numeric] = df[numeric].round(3)
    df.to_csv(output, index=False, encoding="utf-8-sig")
    return output


def _row(
    timestamp: pd.Timestamp,
    link_id: str,
    rx: float,
    tx: float,
    capacity: float,
    latency: float,
    loss: float,
    availability: float,
) -> dict[str, float | str | pd.Timestamp]:
    return {
        "timestamp": timestamp,
        "link_id": link_id,
        "rx_mbps": max(0.0, rx),
        "tx_mbps": max(0.0, tx),
        "capacity_mbps": capacity,
        "latency_ms": max(0.0, latency),
        "packet_loss_pct": min(100.0, max(0.0, loss)),
        "availability_pct": min(100.0, max(0.0, availability)),
    }
