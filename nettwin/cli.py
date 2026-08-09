from __future__ import annotations

import argparse
from pathlib import Path
import sys
import webbrowser

from . import __version__
from .analytics import analyze
from .config import load_config
from .demo_data import generate_demo_csv
from .host_collector import collect_host
from .interface_collector import collect_interface, list_interfaces
from .io import DataValidationError, load_and_validate_csv
from .link_audit import analyze_link_audit, write_link_audit
from .reporting import generate_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettwin",
        description="NetTwin ISP: análisis local de utilización, calidad, tendencia y capacidad.",
    )
    parser.add_argument("--version", action="version", version=f"NetTwin ISP {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_history_arguments(p: argparse.ArgumentParser) -> None:
        p.add_argument("csv", help="Ruta al archivo CSV")
        p.add_argument("--output", "-o", default="resultados", help="Directorio de salida")
        p.add_argument("--company", default="ISP", help="Nombre de la empresa para el informe")
        p.add_argument("--config", help="Ruta opcional a un archivo JSON de configuración")
        p.add_argument("--mapping", help="JSON opcional: columna_origen -> columna_canonica")
        p.add_argument("--open", action="store_true", help="Abrir el informe al terminar")
        p.add_argument("--demo", action="store_true", help="Marcar claramente el informe como datos sintéticos")

    analyze_parser = subparsers.add_parser("analizar", help="Analizar histórico de métricas de enlaces")
    add_history_arguments(analyze_parser)

    history_parser = subparsers.add_parser("analyze-history", help="Alias explícito del análisis histórico")
    add_history_arguments(history_parser)

    audit_parser = subparsers.add_parser(
        "link-audit",
        help="Auditar una ventana corta sin proyecciones de largo plazo",
    )
    audit_parser.add_argument("csv", help="Ruta al CSV de la ventana observada")
    audit_parser.add_argument("--output", "-o", default="resultados_link_audit", help="Directorio de salida")
    audit_parser.add_argument("--mapping", help="JSON opcional: columna_origen -> columna_canonica")

    subparsers.add_parser("interfaces", help="Listar interfaces de red visibles para el sensor")

    collect_parser = subparsers.add_parser(
        "collect-interface",
        help="Capturar contadores de una interfaz sin modificar la red",
    )
    collect_parser.add_argument("--interface", required=True, help="Nombre exacto de la interfaz")
    collect_parser.add_argument("--output", "-o", default="runs/interface_samples.csv", help="CSV de salida")
    collect_parser.add_argument("--interval", type=float, default=5.0, help="Segundos entre muestras")
    limit_group = collect_parser.add_mutually_exclusive_group(required=True)
    limit_group.add_argument("--samples", type=int, help="Número exacto de muestras")
    limit_group.add_argument("--duration", type=float, help="Duración aproximada en segundos")

    host_parser = subparsers.add_parser(
        "collect-host",
        help="Capturar CPU, RAM, load average y uptime del host",
    )
    host_parser.add_argument("--output", "-o", default="runs/host_samples.csv", help="CSV de salida")
    host_parser.add_argument("--interval", type=float, default=5.0, help="Segundos entre muestras")
    host_limit_group = host_parser.add_mutually_exclusive_group(required=True)
    host_limit_group.add_argument("--samples", type=int, help="Número exacto de muestras")
    host_limit_group.add_argument("--duration", type=float, help="Duración aproximada en segundos")

    validate_parser = subparsers.add_parser("validar", help="Validar la estructura de un CSV")
    validate_parser.add_argument("csv", help="Ruta al archivo CSV")
    validate_parser.add_argument("--mapping", help="JSON opcional de mapeo de columnas")

    demo_parser = subparsers.add_parser("generar-demo", help="Generar un CSV sintético reproducible")
    demo_parser.add_argument("--output", "-o", default="data/hachenet_demo.csv", help="CSV de salida")
    demo_parser.add_argument("--seed", type=int, default=20260804, help="Semilla aleatoria")
    return parser


def _print_summary(summary) -> None:
    columns = [
        "link_id", "status", "p95_utilization_pct", "dominant_direction",
        "trend_confidence", "days_to_critical", "recommended_capacity_mbps",
    ]
    printable = summary[columns].copy()
    printable["p95_utilization_pct"] = printable["p95_utilization_pct"].map(lambda v: f"{v:.1f}%")
    printable["days_to_critical"] = printable["days_to_critical"].map(
        lambda v: "N/D" if v != v else ("superado" if v == 0 else f"{v:.0f}")
    )
    print("\nResumen:")
    print(printable.to_string(index=False))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "generar-demo":
            path = generate_demo_csv(args.output, args.seed)
            print(f"CSV sintético generado: {path.resolve()}")
            print("Aviso: estos datos son únicamente de demostración.")
            return 0

        if args.command == "interfaces":
            rows = list_interfaces()
            if not rows:
                print("No se detectaron interfaces.")
                return 1
            print("interface\tstate\tspeed_mbps\tmtu\tcounters")
            for row in rows:
                state = "UP" if row["is_up"] else ("DOWN" if row["is_up"] is False else "N/D")
                speed = "N/D" if row["speed_mbps"] is None else row["speed_mbps"]
                mtu = "N/D" if row["mtu"] is None else row["mtu"]
                counters = "sí" if row["has_counters"] else "no"
                print(f"{row['interface']}\t{state}\t{speed}\t{mtu}\t{counters}")
            return 0

        if args.command == "collect-interface":
            path = collect_interface(
                interface=args.interface,
                output=args.output,
                interval_seconds=args.interval,
                samples=args.samples,
                duration_seconds=args.duration,
            )
            print("Captura de interfaz completada.")
            print(f"Interfaz: {args.interface}")
            print(f"Intervalo: {args.interval} s")
            print(f"Archivo: {path.resolve()}")
            return 0

        if args.command == "collect-host":
            path = collect_host(
                output=args.output,
                interval_seconds=args.interval,
                samples=args.samples,
                duration_seconds=args.duration,
            )
            print("Captura del host completada.")
            print(f"Intervalo: {args.interval} s")
            print(f"Archivo: {path.resolve()}")
            return 0

        validation = load_and_validate_csv(args.csv, getattr(args, "mapping", None))
        if args.command == "validar":
            print(f"CSV válido: {args.csv}")
            print(f"Registros totales: {validation.total_rows}")
            print(f"Registros aceptados: {validation.accepted_rows}")
            print(f"Registros rechazados: {validation.rejected_rows}")
            print(f"Enlaces: {validation.data['link_id'].nunique()}")
            if validation.mapped_columns:
                print("Columnas mapeadas:")
                for source, target in validation.mapped_columns.items():
                    print(f"  {source} -> {target}")
            for warning in validation.warnings:
                print(f"ADVERTENCIA: {warning}")
            return 0

        if args.command == "link-audit":
            audit = analyze_link_audit(validation.data)
            manifest_path = write_link_audit(audit, Path(args.output))
            print("Link audit completado correctamente.")
            print(f"Registros aceptados: {validation.accepted_rows}")
            print(f"Registros rechazados: {validation.rejected_rows}")
            print(f"Enlaces auditados: {len(audit.summary)}")
            print("Proyecciones de largo plazo: DESACTIVADAS")
            print(audit.summary.to_string(index=False))
            print(f"\nManifiesto: {manifest_path.resolve()}")
            return 0

        config = load_config(args.config)
        result = analyze(validation.data, config)
        report_path = generate_report(
            result=result,
            validation=validation,
            config=config,
            output_dir=Path(args.output),
            company=args.company,
            source_file=str(args.csv),
            demo_mode=args.demo,
        )

        print("Análisis completado correctamente.")
        print(f"Registros aceptados: {validation.accepted_rows}")
        print(f"Registros rechazados: {validation.rejected_rows}")
        print(f"Enlaces analizados: {len(result.summary)}")
        for warning in validation.warnings:
            print(f"ADVERTENCIA: {warning}")
        _print_summary(result.summary)
        print(f"\nInforme: {report_path.resolve()}")
        if args.open:
            webbrowser.open(report_path.resolve().as_uri())
        return 0
    except (DataValidationError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR INESPERADO: {exc}", file=sys.stderr)
        return 3
