from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def generate(output_dir: str | Path) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    timestamps = pd.date_range("2026-08-09T17:00:00Z", periods=24, freq="5s")

    interface_rows = []
    for index, timestamp in enumerate(timestamps):
        utilization = 30.0
        rx_rate = 30.0
        tx_rate = 5.0
        rx_drops = 0

        if index in (12, 13, 14):
            utilization = (88.0, 92.0, 86.0)[index - 12]
            rx_rate = utilization
            if index == 13:
                rx_drops = 3

        interface_rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "interface": "eth0",
                "rx_rate_mbps": rx_rate,
                "tx_rate_mbps": tx_rate,
                "utilization_pct": utilization,
                "rx_errors_delta": 0,
                "tx_errors_delta": 0,
                "rx_drops_delta": rx_drops,
                "tx_drops_delta": 0,
                "sample_ok": True,
            }
        )

    probe_rows = []
    for index, timestamp in enumerate(timestamps):
        for target, baseline_rtt in (("internal", 5.0), ("external", 15.0)):
            rtt = baseline_rtt
            delay_variation = 1.0
            received = 1

            if target == "external" and index in (12, 13, 14):
                rtt = (45.0, 55.0, 50.0)[index - 12]
                delay_variation = (18.0, 22.0, 20.0)[index - 12]
                if index == 14:
                    received = 0

            probe_rows.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "target": target,
                    "packets_sent": 1,
                    "packets_received": received,
                    "rtt_ms": rtt if received else None,
                    "delay_variation_ms": delay_variation if received else None,
                    "reachability_bool": bool(received),
                    "sample_status": "ok",
                }
            )

    interface_path = output / "quality_interface_processed.csv"
    probe_path = output / "quality_probe_processed.csv"
    pd.DataFrame(interface_rows).to_csv(interface_path, index=False)
    pd.DataFrame(probe_rows).to_csv(probe_path, index=False)
    return interface_path, probe_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generar datos sintéticos reproducibles para depurar Fase 6 / Event Engine."
    )
    parser.add_argument("--output", default="debug_fase6", help="Directorio de salida")
    args = parser.parse_args()

    interface_path, probe_path = generate(args.output)
    print("Datos de depuración de Fase 6 generados.")
    print(f"Interfaz: {interface_path.resolve()}")
    print(f"Sondas: {probe_path.resolve()}")
    print("Evento esperado: una ventana alrededor de 17:01:00 UTC con utilización, RTT, variación, drops y pérdida.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
