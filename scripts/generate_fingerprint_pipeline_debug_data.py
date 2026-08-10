from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generar datos procesados reproducibles para validar "
            "Event Engine → Fingerprint Engine con baseline de pérdida no cero."
        )
    )
    parser.add_argument("--output", default="debug_fase9_pipeline")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    timestamps = pd.date_range("2026-08-09T20:00:00Z", periods=12, freq="5s")
    interface_rows = []
    probe_rows = []

    for index, timestamp in enumerate(timestamps):
        is_event = index in (7, 8)
        interface_rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "interface": "eth0",
                "rx_rate_mbps": 50.0 if is_event else 10.0,
                "tx_rate_mbps": 2.0,
                "utilization_pct": 90.0 if is_event else 20.0,
                "rx_errors_delta": 0,
                "tx_errors_delta": 0,
                "rx_drops_delta": 2 if is_event else 0,
                "tx_drops_delta": 0,
                "sample_ok": True,
            }
        )

        # 1 % de pérdida normal: 99/100 recibidos.
        # En el segundo bucket anómalo: 95/100 => 5 %.
        received = 95 if index == 8 else 99
        probe_rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "target": "external",
                "packets_sent": 100,
                "packets_received": received,
                "rtt_ms": 35.0 if is_event else 10.0,
                "delay_variation_ms": 20.0 if is_event else 1.0,
                "reachability_bool": True,
                "sample_status": "ok",
            }
        )

    interface_path = output / "quality_interface_processed.csv"
    probe_path = output / "quality_probe_processed.csv"
    pd.DataFrame(interface_rows).to_csv(interface_path, index=False)
    pd.DataFrame(probe_rows).to_csv(probe_path, index=False)

    print("Dataset end-to-end de Fase 9 generado.")
    print(f"Interfaz: {interface_path.resolve()}")
    print(f"Sondas:   {probe_path.resolve()}")
    print("Baseline loss esperado: 1.0 %")
    print("Pico loss esperado:     5.0 %")
    print("Δloss esperado:         +4.0 pp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
