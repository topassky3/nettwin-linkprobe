from __future__ import annotations

import argparse

from nettwin import __version__
from nettwin.preflight_engine import run_preflight


COMMAND = "preflight"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettwin preflight",
        description=(
            "Diagnóstico operacional de solo lectura previo a una ejecución "
            "NetTwin LinkProbe."
        ),
    )
    parser.add_argument("--config", required=True, help="Configuración JSON del experimento")
    parser.add_argument(
        "--output",
        help=(
            "Ruta de preflight.json; si se omite usa "
            "<output.directory>/preflight.json"
        ),
    )
    parser.add_argument(
        "--include-hostname",
        action="store_true",
        help="Incluir hostname en texto claro; por defecto solo se guarda hash por run",
    )
    parser.add_argument(
        "--redact-local-addresses",
        action="store_true",
        help="No guardar IPs locales en claro; conservar hash SHA-256",
    )
    parser.add_argument(
        "--sensor-version",
        default=__version__,
        help="Versión del sensor que se registrará en preflight.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run_preflight(
            args.config,
            output_path=args.output,
            include_hostname=bool(args.include_hostname),
            redact_local_addresses=bool(args.redact_local_addresses),
            sensor_version=args.sensor_version,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    payload = result.payload
    summary = payload["check_summary"]
    print("Preflight completado.")
    print(f"Run ID: {payload['run_id']}")
    print(f"Estado: {payload['status']}")
    print("Listo para ejecución: " + ("SÍ" if payload["ready_for_run"] else "NO"))
    print(f"Interfaz: {payload['interface']['selected']}")
    print("Interfaz UP: " + ("sí" if payload["interface"]["is_up"] is True else "no/N/D"))
    print("Gateway observado: " + str(payload["gateway"].get("gateway") or "N/D"))
    print(
        "ping: "
        + (
            str(payload["tools"]["ping"].get("path"))
            if payload["tools"]["ping"]["available"]
            else "NO DISPONIBLE"
        )
    )
    print(f"Checks: PASS={summary['pass']} WARN={summary['warn']} FAIL={summary['fail']}")
    for check in payload["checks"]:
        if check["status"] != "PASS":
            print(f"  {check['status']} {check['id']}: {check['detail']}")
    estimate = payload["experiment"]
    expected = estimate["expected_samples"]
    traffic = estimate["probe_traffic_estimate"]
    print(f"Muestras esperadas aproximadas: {expected['total_rows']}")
    print(
        "Payload ICMP saliente estimado: "
        f"{traffic['estimated_outbound_payload_bytes']} bytes"
    )
    print("Tráfico activo ejecutado por preflight: NO")
    print(f"Preflight: {result.output_path.resolve()}")
    return 0 if payload["ready_for_run"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
