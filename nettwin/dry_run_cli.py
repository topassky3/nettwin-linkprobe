from __future__ import annotations

import argparse

from nettwin import __version__
from nettwin.dry_run_engine import DryRunSettings, run_dry_run


COMMAND = "dry-run"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettwin dry-run",
        description=(
            "Ejecutar el pipeline local completo antes de HacheNet: Preflight, captura real, "
            "almacenamiento, integridad estricta, análisis y Link Health Audit. Las sondas "
            "activas se limitan a 127.0.0.1."
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Workspace NUEVO del dry-run. Se rechaza si ya contiene archivos.",
    )
    parser.add_argument(
        "--interface",
        help="Interfaz local explícita. Si se omite, se prefiere Wi-Fi/Ethernet UP con IPv4 útil.",
    )
    parser.add_argument("--duration-seconds", type=float, default=12.0)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--run-id", default="LOCAL-DRYRUN-001")
    parser.add_argument("--company", default="HacheNet")
    parser.add_argument("--link", default="LOCAL-DRY-RUN")
    parser.add_argument(
        "--capacity-mbps",
        type=float,
        help=(
            "Capacidad explícita solo si realmente se conoce. Si se omite, utilización queda N/D; "
            "la velocidad de la NIC nunca se usa como sustituto."
        ),
    )
    parser.add_argument(
        "--sensor-version",
        default=__version__,
        help="Versión registrada en preflight, metadata e integridad.",
    )
    parser.add_argument(
        "--include-hostname",
        action="store_true",
        help="Permitir hostname en claro en preflight.json.",
    )
    parser.add_argument(
        "--redact-local-addresses",
        action="store_true",
        help="Redactar IPs locales persistidas en preflight.json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run_dry_run(
            args.output,
            settings=DryRunSettings(
                duration_seconds=float(args.duration_seconds),
                interval_seconds=float(args.interval_seconds),
                run_id=str(args.run_id),
                interface_name=args.interface,
                company=str(args.company),
                link_name=str(args.link),
                capacity_mbps=args.capacity_mbps,
                sensor_version=str(args.sensor_version),
                include_hostname=bool(args.include_hostname),
                redact_local_addresses=bool(args.redact_local_addresses),
            ),
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    print("Dry-run Fase 14 finalizado.")
    print(f"Estado: {result.status}")
    if result.failed_stage:
        print(f"Etapa fallida: {result.failed_stage}")
    print(f"Detalle: {result.message}")
    print(f"Workspace: {result.workspace}")
    print(f"Config: {result.config_path}")
    print(f"Run: {result.run_dir}")
    print(f"Report: {result.report_dir}")
    print(f"Resumen: {result.summary_path}")
    print("Target activo del dry-run: local_loopback -> 127.0.0.1")
    print("Tráfico activo externo ejecutado por dry-run: NO")

    if result.orchestrator_result is not None:
        print(f"Captura: {getattr(result.orchestrator_result, 'status', 'N/D')}")
        print(
            "Integridad de captura: "
            + ("VÁLIDA" if getattr(result.orchestrator_result, "integrity_valid", None) is True else "INVÁLIDA/N.D.")
        )
    if result.report_result is not None:
        print(f"Quality Gate: {result.report_result.quality_status}")
        print("Análisis ejecutado: " + ("SÍ" if result.report_result.analysis_executed else "NO"))
        print(f"HTML: {result.report_result.report_html}")

    return 0 if result.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
