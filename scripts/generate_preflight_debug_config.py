from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket

import psutil


def _pick_interface() -> str:
    stats = psutil.net_if_stats()
    counters = psutil.net_io_counters(pernic=True)
    addresses = psutil.net_if_addrs()
    candidates: list[str] = []
    fallback: list[str] = []
    for name in sorted(set(stats) | set(counters) | set(addresses)):
        stat = stats.get(name)
        if stat is None or not bool(stat.isup) or name not in counters:
            continue
        fallback.append(name)
        for item in addresses.get(name, []):
            if item.family == socket.AF_INET and str(item.address) != "127.0.0.1":
                candidates.append(name)
                break
    if candidates:
        return candidates[0]
    if fallback:
        return fallback[0]
    raise SystemExit("No se encontró una interfaz UP con contadores; use --interface manualmente.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generar configuración local segura para depurar Fase 11."
    )
    parser.add_argument("--output", default="debug_fase11")
    parser.add_argument("--interface")
    parser.add_argument("--duration-seconds", type=float, default=14400.0)
    parser.add_argument("--capacity-mbps", type=float)
    args = parser.parse_args()

    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    interface = args.interface or _pick_interface()
    run_id = "LOCAL-PREFLIGHT-DEBUG-001"
    config = {
        "run": {
            "run_id": run_id,
            "duration_seconds": args.duration_seconds,
        },
        "interface": {
            "name": interface,
            "interval_seconds": 5,
            "capacity_mbps": args.capacity_mbps,
        },
        "host": {"interval_seconds": 5},
        "probes": {
            "interval_seconds": 5,
            "timeout_ms": 1000,
            "payload_bytes": 32,
            "count_per_target": 1,
        },
        "targets": [
            {
                "name": "documentation_reference",
                "address": "192.0.2.1",
                "role": "debug_only_rfc5737",
            }
        ],
        "output": {
            "directory": "./run",
            "minimum_free_disk_mb": 50,
        },
        "debug_notice": (
            "192.0.2.1 pertenece a TEST-NET-1 y se usa solo para validar el preflight. "
            "Fase 11 no envía ICMP ni resuelve DNS."
        ),
    }
    config_path = root / "preflight_config.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("Configuración de depuración de Fase 11 generada.")
    print(f"Interfaz seleccionada: {interface}")
    print(f"Config: {config_path}")
    print(f"Salida esperada: {root / 'run' / 'preflight.json'}")
    print("Target de documentación: 192.0.2.1 (NO se envían sondas en preflight).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
