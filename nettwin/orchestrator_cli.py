from __future__ import annotations

import argparse

from nettwin import __version__
from nettwin.orchestrator_engine import run_linkprobe


COMMAND = "run-linkprobe"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettwin run-linkprobe",
        description=(
            "Ejecutar una ventana coordinada de Interface Collector, Host Collector y "
            "Active Probe Engine. El preflight es obligatorio y los probes activos solo "
            "comienzan si el entorno queda listo."
        ),
    )
    parser.add_argument("--config", required=True, help="Configuración JSON externa del run")
    parser.add_argument(
        "--sensor-version",
        default=__version__,
        help="Versión del sensor registrada en preflight, metadata e integridad",
    )
    parser.add_argument(
        "--include-hostname",
        action="store_true",
        help="Permitir hostname en claro dentro de preflight.json",
    )
    parser.add_argument(
        "--redact-local-addresses",
        action="store_true",
        help="Redactar IPs locales en preflight.json después de verificar la interfaz/gateway",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run_linkprobe(
            args.config,
            sensor_version=args.sensor_version,
            include_hostname=bool(args.include_hostname),
            redact_local_addresses=bool(args.redact_local_addresses),
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    print("Ejecución LinkProbe finalizada.")
    print(f"Estado: {result.status}")
    print(f"Motivo de parada: {result.stop_reason}")
    print(f"Run dir: {result.run_dir.resolve()}")
    print("Preflight listo: " + ("SÍ" if result.preflight_ready else "NO"))
    if result.integrity_valid is None:
        print("Integridad: NO GENERADA (captura no iniciada)")
    else:
        print("Integridad estricta: " + ("VÁLIDA" if result.integrity_valid else "INVÁLIDA"))
        print(f"Metadata: {result.metadata_path}")
        print(f"Checksums: {result.checksums_path}")
        print(f"Reporte integridad: {result.integrity_report_path}")
    print(f"Orquestación: {result.orchestration_path}")

    if not result.preflight_ready:
        return 2
    if result.integrity_valid is not True:
        return 3
    if result.status in {"partial_failure", "interrupted"}:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
