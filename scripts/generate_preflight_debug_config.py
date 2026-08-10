from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import socket
from typing import Any

import psutil


_VIRTUAL_HINTS = (
    "tailscale",
    "loopback",
    "virtual",
    "vethernet",
    "hyper-v",
    "vmware",
    "virtualbox",
    "vpn",
    "tap",
    "tun",
    "docker",
    "wsl",
    "zerotier",
    "wireguard",
)

_PHYSICAL_HINTS = (
    "wi-fi",
    "wifi",
    "wireless",
    "wlan",
    "ethernet",
    "eth",
    "en",
)


def _is_virtual_name(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _VIRTUAL_HINTS)


def _is_physical_name(name: str) -> bool:
    lowered = name.lower()
    if _is_virtual_name(name):
        return False
    return any(
        lowered == hint
        or lowered.startswith(hint + " ")
        or lowered.startswith(hint)
        for hint in _PHYSICAL_HINTS
    )


def _ipv4_quality(addresses: list[Any]) -> int:
    best = 0
    for item in addresses:
        if getattr(item, "family", None) != socket.AF_INET:
            continue
        raw = str(getattr(item, "address", "") or "")
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if address.is_loopback:
            best = max(best, 0)
        elif address.is_link_local:
            best = max(best, 10)
        else:
            best = max(best, 40)
    return best


def _interface_score(name: str, addresses: list[Any]) -> tuple[int, str]:
    score = _ipv4_quality(addresses)
    if _is_physical_name(name):
        score += 100
    if _is_virtual_name(name):
        score -= 100
    return score, name.lower()


def _pick_interface(provider: Any = psutil) -> str:
    stats = provider.net_if_stats()
    counters = provider.net_io_counters(pernic=True)
    addresses = provider.net_if_addrs()
    ranked: list[tuple[int, str, str]] = []

    for name in sorted(set(stats) | set(counters) | set(addresses)):
        stat = stats.get(name)
        if stat is None or not bool(stat.isup) or name not in counters:
            continue
        score, lexical = _interface_score(name, list(addresses.get(name, [])))
        ranked.append((score, lexical, name))

    if not ranked:
        raise SystemExit(
            "No se encontró una interfaz UP con contadores; use --interface manualmente."
        )

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][2]


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
    if _is_virtual_name(interface):
        print(
            "AVISO: solo se encontró/seleccionó una interfaz con apariencia virtual; "
            "para el piloto real use --interface explícito según autorización del ISP."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
