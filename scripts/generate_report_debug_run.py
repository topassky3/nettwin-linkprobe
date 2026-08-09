from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from nettwin.integrity_engine import IntegrityConfig, finalize_integrity, verify_integrity


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def generate(root: Path) -> Path:
    run = root / "run"
    run.mkdir(parents=True, exist_ok=True)
    start = datetime(2026, 8, 9, 18, 0, 0, tzinfo=timezone.utc)
    interval = 5
    samples = 24
    duration = samples * interval
    interface_rows: list[dict] = []
    host_rows: list[dict] = []
    probe_rows: list[dict] = []
    rx_bytes = 1_000_000_000
    tx_bytes = 500_000_000

    for index in range(samples):
        ts = start + timedelta(seconds=index * interval)
        event = index in (16, 17, 18)
        rx_rate = 92.0 if event else 18.0
        tx_rate = 22.0 if event else 4.0
        rx_delta = int(rx_rate * 1_000_000 * interval / 8)
        tx_delta = int(tx_rate * 1_000_000 * interval / 8)
        rx_bytes += rx_delta
        tx_bytes += tx_delta
        interface_rows.append({
            "timestamp": ts.isoformat(), "interface": "eth0",
            "rx_bytes": rx_bytes, "tx_bytes": tx_bytes,
            "rx_packets": 100_000 + index * 100, "tx_packets": 80_000 + index * 80,
            "rx_errors": 0, "tx_errors": 0,
            "rx_drops": 9 if event else 0, "tx_drops": 0,
            "interface_state": "UP", "reported_link_speed_mbps": 1000, "mtu": 1500,
            "rx_bytes_delta": "" if index == 0 else rx_delta,
            "tx_bytes_delta": "" if index == 0 else tx_delta,
            "rx_packets_delta": "" if index == 0 else 100,
            "tx_packets_delta": "" if index == 0 else 80,
            "rx_errors_delta": "" if index == 0 else 0,
            "tx_errors_delta": "" if index == 0 else 0,
            "rx_drops_delta": "" if index == 0 else (3 if event else 0),
            "tx_drops_delta": "" if index == 0 else 0,
            "counter_event": "initial" if index == 0 else "normal",
            "sample_status": "ok", "error": "",
        })
        host_rows.append({
            "timestamp": ts.isoformat(),
            "cpu_percent": 48.0 if event else 18.0,
            "memory_percent": 36.0,
            "load_1m": 1.1 if event else 0.2,
            "load_5m": 0.4,
            "load_15m": 0.3,
            "uptime_seconds": 100_000 + index * interval,
            "sample_status": "ok", "error": "",
        })
        sent = 100
        received = 96 if event else 100
        probe_rows.append({
            "timestamp": ts.isoformat(), "target": "external_reference",
            "address": "203.0.113.10", "probe_type": "icmp_echo",
            "packets_sent": sent, "packets_received": received,
            "packet_loss_pct": (sent - received) / sent * 100.0,
            "reachability": True,
            "rtt_ms": 52.0 if event else 12.0,
            "rtt_min_ms": 48.0 if event else 11.0,
            "rtt_max_ms": 58.0 if event else 13.0,
            "delay_variation_ms": 18.0 if event else 1.0,
            "payload_bytes": 32,
            "estimated_outbound_payload_bytes": sent * 32,
            "sample_status": "ok", "error": "",
        })

    _write_csv(run / "interface_samples.csv", interface_rows)
    _write_csv(run / "host_samples.csv", host_rows)
    _write_csv(run / "probe_samples.csv", probe_rows)

    experiment = {
        "run": {"run_id": "REPORT-DEBUG-001", "duration_seconds": duration},
        "interface": {"name": "eth0", "interval_seconds": interval, "capacity_mbps": 100.0},
        "host": {"interval_seconds": interval},
        "probes": {"interval_seconds": interval, "timeout_ms": 1000, "payload_bytes": 32, "count_per_target": 100},
        "targets": [{"name": "external_reference", "address": "203.0.113.10", "role": "synthetic_debug_only"}],
        "output": {"directory": "./run", "minimum_free_disk_mb": 1},
    }
    (run / "experiment_config.json").write_text(
        json.dumps(experiment, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (run / "preflight.json").write_text(
        json.dumps({
            "schema_version": "preflight-v1", "status": "PASS", "ready_for_run": True,
            "system": {"timezone_name": "UTC"},
            "network_safety": {"active_probe_performed": False, "dns_resolution_performed": False},
        }, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    ended = start + timedelta(seconds=duration)
    (run / "orchestration.json").write_text(
        json.dumps({
            "schema_version": "linkprobe-orchestrator-v1", "mode": "run-linkprobe",
            "run_id": "REPORT-DEBUG-001", "status": "completed",
            "stop_reason": "duration_elapsed", "collection_started": True,
            "automatic_execution": True, "requested_duration_seconds": duration,
            "elapsed_seconds": duration, "started_utc": start.isoformat(), "ended_utc": ended.isoformat(),
        }, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    finalize_integrity(
        run,
        config=IntegrityConfig(
            run_id="REPORT-DEBUG-001", interface="eth0",
            targets=("external_reference",), requested_duration_seconds=duration,
            config_path=run / "experiment_config.json",
            sensor_version="0.3-dev", analyzer_version="0.3-dev",
        ),
    )
    verified = verify_integrity(run, strict_untracked=True)
    if not verified.valid:
        raise RuntimeError("El run sintético no pasó verify-integrity --strict")
    print("Run sintético de Fase 13 generado y sellado.")
    print(f"Run: {run.resolve()}")
    print(f"Muestras: {samples} interface + {samples} host + {samples} probes = {samples*3}")
    print("Capacidad explícita: 100 Mbps")
    print("Evento sintético multi-métrica: índices 16..18")
    print("Integridad estricta: VÁLIDA")
    return run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="debug_fase13")
    args = parser.parse_args()
    generate(Path(args.output))


if __name__ == "__main__":
    main()
