from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def generate(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    start = datetime(2026, 8, 9, 18, 0, 0, tzinfo=timezone.utc)
    interface_rows: list[dict] = []
    host_rows: list[dict] = []
    probe_rows: list[dict] = []

    rx_bytes = 1_000_000_000
    tx_bytes = 500_000_000
    for index in range(24):
        timestamp = start + timedelta(seconds=5 * index)
        event = index in (16, 17)
        rx_rate_mbps = 90.0 if event else 10.0
        tx_rate_mbps = 12.0 if event else 2.0
        rx_delta = int(rx_rate_mbps * 1_000_000 * 5 / 8)
        tx_delta = int(tx_rate_mbps * 1_000_000 * 5 / 8)
        rx_bytes += rx_delta
        tx_bytes += tx_delta
        interface_rows.append({
            "timestamp": timestamp.isoformat(),
            "interface": "eth0",
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes,
            "rx_packets": 100_000 + index * 100,
            "tx_packets": 80_000 + index * 80,
            "rx_errors": 0,
            "tx_errors": 0,
            "rx_drops": 4 if event else 0,
            "tx_drops": 0,
            "interface_state": "UP",
            "reported_link_speed_mbps": 1000,
            "mtu": 1500,
            "rx_bytes_delta": rx_delta if index else "",
            "tx_bytes_delta": tx_delta if index else "",
            "rx_packets_delta": 100 if index else "",
            "tx_packets_delta": 80 if index else "",
            "rx_errors_delta": 0 if index else "",
            "tx_errors_delta": 0 if index else "",
            "rx_drops_delta": 2 if event else (0 if index else ""),
            "tx_drops_delta": 0 if index else "",
            "counter_event": "initial" if index == 0 else "normal",
            "sample_status": "ok",
            "error": "",
        })
        host_rows.append({
            "timestamp": timestamp.isoformat(),
            "cpu_percent": 42.0 if event else 20.0,
            "memory_percent": 35.0,
            "load_average": 0.4,
            "uptime": 100_000 + index * 5,
            "sample_status": "ok",
            "error": "",
        })
        sent = 100
        received = 95 if index == 17 else 99
        probe_rows.append({
            "timestamp": timestamp.isoformat(),
            "target": "external",
            "address": "203.0.113.10",
            "probe_type": "icmp_echo",
            "packets_sent": sent,
            "packets_received": received,
            "packet_loss_pct": (sent - received) / sent * 100.0,
            "reachability": True,
            "rtt_ms": 35.0 if event else 10.0,
            "rtt_min_ms": 35.0 if event else 10.0,
            "rtt_max_ms": 35.0 if event else 10.0,
            "delay_variation_ms": 20.0 if event else 1.0,
            "payload_bytes": 32,
            "estimated_outbound_payload_bytes": 32,
            "sample_status": "ok",
            "error": "",
        })

    _write_csv(output / "interface_samples.csv", interface_rows)
    _write_csv(output / "host_samples.csv", host_rows)
    _write_csv(output / "probe_samples.csv", probe_rows)
    (output / "run_config.json").write_text(
        json.dumps(
            {
                "run": {"duration_seconds": 115},
                "interface": {"name": "eth0", "interval_seconds": 5},
                "probes": {"interval_seconds": 5, "targets": ["external"]},
                "capacity_mbps": 100.0,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print("Dataset de depuración de Fase 10 generado.")
    print(f"Run dir: {output.resolve()}")
    print("Interfaz: eth0")
    print("Target: external")
    print("Muestras: 24 interfaz + 24 host + 24 probes = 72")
    print("Duración observada esperada: 115 s")
    print("Capacidad explícita para repro-check: 100 Mbps")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="debug_fase10_run")
    args = parser.parse_args()
    generate(Path(args.output))


if __name__ == "__main__":
    main()
