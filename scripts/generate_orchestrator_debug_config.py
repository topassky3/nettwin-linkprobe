from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import socket

import psutil


_VIRTUAL_HINTS = (
    "tailscale", "vpn", "virtual", "veth", "docker", "wsl", "hyper-v",
    "vethernet", "vmware", "virtualbox", "tap", "tun", "loopback",
)
_PHYSICAL_HINTS = ("wi-fi", "wifi", "wlan", "wireless", "ethernet", "eth")


def _usable_ipv4(name: str, addresses: dict[str, list]) -> bool:
    for item in addresses.get(name, []):
        if item.family != socket.AF_INET:
            continue
        try:
            address = ipaddress.ip_address(str(item.address))
        except ValueError:
            continue
        if not address.is_loopback and not address.is_link_local:
            return True
    return False


def _pick_interface() -> tuple[str, str]:
    stats = psutil.net_if_stats()
    counters = psutil.net_io_counters(pernic=True)
    addresses = psutil.net_if_addrs()
    candidates: list[tuple[tuple[int, int, int, str], str, str]] = []

    for name in sorted(set(stats) | set(counters) | set(addresses)):
        stat = stats.get(name)
        if stat is None or not bool(stat.isup) or name not in counters:
            continue
        lowered = name.lower()
        virtual = any(hint in lowered for hint in _VIRTUAL_HINTS)
        physical_hint = any(hint in lowered for hint in _PHYSICAL_HINTS)
        useful_ipv4 = _usable_ipv4(name, addresses)
        score = (
            0 if useful_ipv4 else 1,
            0 if physical_hint and not virtual else 1,
            0 if not virtual else 1,
            lowered,
        )
        reason = (
            "interfaz física/preferida con IPv4 útil"
            if useful_ipv4 and physical_hint and not virtual
            else "mejor interfaz UP disponible para depuración"
        )
        candidates.append((score, name, reason))

    if not candidates:
        raise SystemExit("No se encontró una interfaz UP con contadores; use --interface manualmente.")
    candidates.sort(key=lambda item: item[0])
    _, name, reason = candidates[0]
    return name, reason


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generar una configuración local de Fase 12. Los probes activos se "
            "limitan a 127.0.0.1 para no generar tráfico fuera del propio host."
        )
    )
    parser.add_argument("--output", default="debug_fase12")
    parser.add_argument("--interface")
    parser.add_argument("--duration-seconds", type=float, default=12.0)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    args = parser.parse_args()

    if args.duration_seconds <= 0:
        parser.error("--duration-seconds debe ser > 0")
    if args.interval_seconds <= 0:
        parser.error("--interval-seconds debe ser > 0")

    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.interface:
        interface = args.interface
        reason = "selección manual"
    else:
        interface, reason = _pick_interface()

    config = {
        "run": {
            "run_id": "LOCAL-ORCHESTRATOR-DEBUG-001",
            "duration_seconds": args.duration_seconds,
        },
        "interface": {
            "name": interface,
            "interval_seconds": args.interval_seconds,
            "capacity_mbps": None,
        },
        "host": {"interval_seconds": args.interval_seconds},
        "probes": {
            "interval_seconds": args.interval_seconds,
            "timeout_ms": 1000,
            "payload_bytes": 32,
            "count_per_target": 1,
        },
        "targets": [
            {
                "name": "local_loopback",
                "address": "127.0.0.1",
                "role": "local_debug_only",
            }
        ],
        "output": {
            "directory": "./run",
            "minimum_free_disk_mb": 50,
        },
        "debug_notice": (
            "Fase 12 sí ejecuta Active Probe Engine después de que Preflight aprueba. "
            "Esta configuración usa exclusivamente 127.0.0.1, por lo que el ICMP "
            "permanece en el propio host. Para HacheNet se deben usar únicamente "
            "targets previamente autorizados."
        ),
    }

    config_path = root / "orchestrator_config.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("Configuración local de Fase 12 generada.")
    print(f"Interfaz: {interface} ({reason})")
    print(f"Duración: {args.duration_seconds:g} s")
    print(f"Intervalo: {args.interval_seconds:g} s")
    print("Target activo: local_loopback -> 127.0.0.1")
    print("Carga activa: 1 ICMP de 32 bytes por ciclo, solo al loopback local.")
    print(f"Config: {config_path}")
    print(f"Run esperado: {root / 'run'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
