from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def generate(output: str | Path) -> tuple[Path, Path, Path]:
    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamps = pd.date_range(
        "2026-08-09T18:00:00+00:00",
        periods=48,
        freq="5s",
    )
    utilization = np.linspace(20.0, 95.0, len(timestamps))

    interface = pd.DataFrame(
        {
            "timestamp": [ts.isoformat() for ts in timestamps],
            "interface": "eth0",
            "utilization_pct": utilization,
            "rx_rate_mbps": utilization,
            "tx_rate_mbps": utilization * 0.10,
            "rx_drops_delta": np.floor(np.maximum(utilization - 50.0, 0.0) / 10.0),
            "tx_drops_delta": 0.0,
            "rx_errors_delta": 0.0,
            "tx_errors_delta": 0.0,
            "sample_ok": True,
        }
    )

    probe_rows: list[dict[str, object]] = []
    for timestamp, util in zip(timestamps, utilization):
        external_lost = int(max(0.0, (util - 60.0) // 8.0))
        probe_rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "target": "external",
                "packets_sent": 100,
                "packets_received": 100 - external_lost,
                "rtt_ms": 10.0 + util * 0.50,
                "delay_variation_ms": 1.0 + util * 0.10,
                "sample_status": "ok",
            }
        )
        probe_rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "target": "internal",
                "packets_sent": 100,
                "packets_received": 100,
                "rtt_ms": 5.0,
                "delay_variation_ms": 1.0,
                "sample_status": "ok",
            }
        )
    probes = pd.DataFrame(probe_rows)

    cpu_pattern = np.tile([20.0, 22.0, 21.0, 23.0, 20.0, 21.0], 8)[: len(timestamps)]
    host = pd.DataFrame(
        {
            "timestamp": [ts.isoformat() for ts in timestamps],
            "cpu_percent": cpu_pattern,
            "memory_percent": 35.0,
            "load_1m": 0.30,
            "load_5m": 0.30,
            "load_15m": 0.30,
            "uptime_seconds": np.arange(len(timestamps), dtype=float) * 5.0 + 3600.0,
            "sample_status": "ok",
            "error": "",
        }
    )

    interface_path = output_dir / "quality_interface_processed.csv"
    probe_path = output_dir / "quality_probe_processed.csv"
    host_path = output_dir / "host_samples.csv"

    interface.to_csv(interface_path, index=False)
    probes.to_csv(probe_path, index=False)
    host.to_csv(host_path, index=False)
    return interface_path, probe_path, host_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generar datos sintéticos reproducibles para depurar Fase 7."
    )
    parser.add_argument("--output", default="debug_fase7")
    args = parser.parse_args()

    interface_path, probe_path, host_path = generate(args.output)
    print("Datos de depuración de Fase 7 generados.")
    print(f"Interfaz: {interface_path.resolve()}")
    print(f"Sondas: {probe_path.resolve()}")
    print(f"Host: {host_path.resolve()}")
    print("Esperado:")
    print("  utilization ↔ RTT external: rho cercano a +1")
    print("  utilization ↔ delay variation external: rho cercano a +1")
    print("  utilization ↔ packet loss external: asociación positiva fuerte")
    print("  utilization ↔ drops: asociación positiva fuerte")
    print("  CPU ↔ RTT/loss external: asociación débil")
    print("  target internal: series de calidad constantes => correlación N/D")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
