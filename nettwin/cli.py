from __future__ import annotations

import argparse
from pathlib import Path
import sys
import webbrowser

from . import __version__
from .active_probe import ProbeTarget, collect_probes
from .analytics import analyze
from .config import load_config
from .correlation_engine import CorrelationConfig, analyze_correlations, write_correlations
from .demo_data import generate_demo_csv
from .event_engine import EventThresholds, analyze_events, write_events
from .host_collector import collect_host
from .interface_collector import collect_interface, list_interfaces
from .io import DataValidationError, load_and_validate_csv
from .link_audit import analyze_link_audit, write_link_audit
from .quality_engine import analyze_quality, write_quality
from .reporting import generate_report


def _parse_target(value: str) -> ProbeTarget:
    if "=" not in value:
        raise argparse.ArgumentTypeError("Target inválido. Use NOMBRE=DIRECCION")
    name, address = value.split("=", 1)
    name = name.strip()
    address = address.strip()
    if not name or not address:
        raise argparse.ArgumentTypeError("Target inválido. Use NOMBRE=DIRECCION")
    return ProbeTarget(name=name, address=address)


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
    audit_parser.add_argument("--mapping", help="JSON opcional de mapeo de columnas")

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

    probes_parser = subparsers.add_parser(
        "collect-probes",
        help="Medir RTT, pérdida, reachability y variación temporal con ICMP pequeño",
    )
    probes_parser.add_argument(
        "--target",
        action="append",
        type=_parse_target,
        required=True,
        help="Target autorizado en formato NOMBRE=DIRECCION; repetir para varios destinos",
    )
    probes_parser.add_argument("--output", "-o", default="runs/probe_samples.csv", help="CSV de salida")
    probes_parser.add_argument("--interval", type=float, default=5.0, help="Segundos entre ciclos")
    probes_parser.add_argument("--count-per-target", type=int, default=1, help="ICMP echo por target y ciclo")
    probes_parser.add_argument("--timeout-ms", type=int, default=1000, help="Timeout por ICMP echo")
    probes_parser.add_argument("--payload-bytes", type=int, default=32, help="Payload ICMP por echo")
    probes_limit_group = probes_parser.add_mutually_exclusive_group(required=True)
    probes_limit_group.add_argument("--cycles", type=int, help="Número de ciclos de medición")
    probes_limit_group.add_argument("--duration", type=float, help="Duración aproximada en segundos")

    quality_parser = subparsers.add_parser(
        "analyze-quality",
        help="Calcular métricas de calidad sobre la ventana observada",
    )
    quality_parser.add_argument("--interface-csv", required=True, help="CSV generado por collect-interface")
    quality_parser.add_argument("--probe-csv", required=True, help="CSV generado por collect-probes")
    quality_parser.add_argument(
        "--capacity-mbps",
        type=float,
        help="Capacidad autorizada/conocida del enlace para calcular utilización; no se infiere de la NIC",
    )
    quality_parser.add_argument("--output", "-o", default="resultados_quality", help="Directorio de salida")

    events_parser = subparsers.add_parser(
        "analyze-events",
        help="Detectar ventanas con cambios simultáneos sin afirmar causalidad",
    )
    events_parser.add_argument(
        "--interface-csv",
        required=True,
        help="quality_interface_processed.csv generado por Quality Engine",
    )
    events_parser.add_argument(
        "--probe-csv",
        required=True,
        help="quality_probe_processed.csv generado por Quality Engine",
    )
    events_parser.add_argument("--output", "-o", default="resultados_events", help="Directorio de salida")
    events_parser.add_argument("--bucket-seconds", type=int, default=5, help="Tamaño del bucket temporal")
    events_parser.add_argument("--min-metrics", type=int, default=2, help="Mínimo de señales simultáneas para candidato")
    events_parser.add_argument("--merge-gap-seconds", type=int, default=10, help="Separación máxima para unir buckets candidatos")
    events_parser.add_argument("--utilization-high-pct", type=float, default=80.0, help="Umbral absoluto de utilización")
    events_parser.add_argument("--utilization-delta-pp", type=float, default=20.0, help="Incremento vs baseline en puntos porcentuales")
    events_parser.add_argument("--rtt-ratio", type=float, default=1.5, help="Factor de RTT respecto al baseline por target")
    events_parser.add_argument("--rtt-delta-ms", type=float, default=10.0, help="Incremento mínimo de RTT respecto al baseline")
    events_parser.add_argument("--delay-variation-ms", type=float, default=10.0, help="Piso de variación temporal de retardo")
    events_parser.add_argument("--delay-variation-ratio", type=float, default=2.0, help="Factor de variación respecto al baseline")
    events_parser.add_argument("--packet-loss-pct", type=float, default=1.0, help="Pérdida mínima para activar señal")
    events_parser.add_argument("--rate-ratio", type=float, default=1.75, help="Factor de RX/TX rate si utilización es N/D")
    events_parser.add_argument("--rate-delta-mbps", type=float, default=1.0, help="Incremento mínimo de rate si utilización es N/D")
    events_parser.add_argument("--drops-delta", type=float, default=1.0, help="Deltas de drops para activar señal")
    events_parser.add_argument("--errors-delta", type=float, default=1.0, help="Deltas de errors para activar señal")

    correlations_parser = subparsers.add_parser(
        "analyze-correlations",
        help="Calcular asociaciones temporales de Spearman sin inferir causalidad",
    )
    correlations_parser.add_argument(
        "--interface-csv",
        required=True,
        help="quality_interface_processed.csv generado por Quality Engine",
    )
    correlations_parser.add_argument(
        "--probe-csv",
        required=True,
        help="quality_probe_processed.csv generado por Quality Engine",
    )
    correlations_parser.add_argument(
        "--host-csv",
        help="host_samples.csv; opcional, necesario para CPU ↔ RTT/loss",
    )
    correlations_parser.add_argument(
        "--output",
        "-o",
        default="resultados_correlations",
        help="Directorio de salida",
    )
    correlations_parser.add_argument(
        "--bucket-seconds",
        type=int,
        default=5,
        help="Tamaño del bucket para alinear series",
    )
    correlations_parser.add_argument(
        "--min-pairs",
        type=int,
        default=12,
        help="Pares válidos mínimos para interpretar una asociación",
    )
    correlations_parser.add_argument(
        "--weak-abs-rho",
        type=float,
        default=0.30,
        help="|rho| por debajo del cual la asociación se marca como débil",
    )

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

        if args.command == "collect-probes":
            path = collect_probes(
                targets=args.target,
                output=args.output,
                interval_seconds=args.interval,
                cycles=args.cycles,
                duration_seconds=args.duration,
                count_per_target=args.count_per_target,
                timeout_ms=args.timeout_ms,
                payload_bytes=args.payload_bytes,
            )
            estimated_per_cycle = len(args.target) * args.count_per_target * args.payload_bytes
            print("Captura de sondas completada.")
            print(f"Targets: {len(args.target)}")
            print(f"Intervalo: {args.interval} s")
            print(f"Payload ICMP: {args.payload_bytes} bytes")
            print(f"Payload saliente estimado por ciclo: {estimated_per_cycle} bytes")
            print(f"Archivo: {path.resolve()}")
            return 0

        if args.command == "analyze-quality":
            quality = analyze_quality(
                interface_csv=args.interface_csv,
                probe_csv=args.probe_csv,
                capacity_mbps=args.capacity_mbps,
            )
            manifest_path = write_quality(quality, args.output)
            print("Quality Engine completado correctamente.")
            print("Alcance: únicamente ventana observada")
            print(f"Interfaces analizadas: {len(quality.interface_summary)}")
            print(f"Targets analizados: {len(quality.probe_summary)}")
            if args.capacity_mbps is None:
                print("Utilización: N/D (no se suministró capacidad explícita del enlace)")
            else:
                print(f"Capacidad usada para utilización: {args.capacity_mbps} Mbps")
            print("\nInterfaces:")
            print(quality.interface_summary.to_string(index=False))
            print("\nTargets:")
            print(quality.probe_summary.to_string(index=False))
            print(f"\nResumen: {manifest_path.resolve()}")
            return 0

        if args.command == "analyze-events":
            thresholds = EventThresholds(
                bucket_seconds=args.bucket_seconds,
                min_metrics_changed=args.min_metrics,
                merge_gap_seconds=args.merge_gap_seconds,
                utilization_high_pct=args.utilization_high_pct,
                utilization_delta_pp=args.utilization_delta_pp,
                rtt_ratio=args.rtt_ratio,
                rtt_delta_ms=args.rtt_delta_ms,
                delay_variation_ms=args.delay_variation_ms,
                delay_variation_ratio=args.delay_variation_ratio,
                packet_loss_pct=args.packet_loss_pct,
                rate_ratio=args.rate_ratio,
                rate_delta_mbps=args.rate_delta_mbps,
                drops_delta=args.drops_delta,
                errors_delta=args.errors_delta,
            )
            event_result = analyze_events(
                interface_processed_csv=args.interface_csv,
                probe_processed_csv=args.probe_csv,
                thresholds=thresholds,
            )
            events_path = write_events(event_result, args.output)
            overlap = event_result.metadata["temporal_overlap"]
            print("Event Engine completado correctamente.")
            print("Alcance: únicamente ventana observada")
            print(f"Solapamiento temporal: {overlap['duration_seconds']:.3f} s")
            print(
                "Detección ejecutada: "
                + ("sí" if event_result.metadata["event_detection_executed"] else "no")
            )
            print(f"Eventos detectados: {len(event_result.events)}")
            for event in event_result.events:
                metrics = ", ".join(event["metrics_changed"])
                print(
                    f"  {event['event_id']} | {event['severity']} | "
                    f"{event['confidence']} | {event['duration_seconds']:.1f} s | {metrics}"
                )
            if not overlap["exists"]:
                print("ADVERTENCIA: interfaz y sondas no se solapan; no se generaron eventos conjuntos.")
            print("Causalidad: NO inferida por Event Engine")
            print(f"Eventos: {events_path.resolve()}")
            return 0

        if args.command == "analyze-correlations":
            correlation_config = CorrelationConfig(
                bucket_seconds=args.bucket_seconds,
                min_pairs=args.min_pairs,
                weak_abs_rho=args.weak_abs_rho,
            )
            correlation_result = analyze_correlations(
                interface_processed_csv=args.interface_csv,
                probe_processed_csv=args.probe_csv,
                host_csv=args.host_csv,
                config=correlation_config,
            )
            correlations_path = write_correlations(correlation_result, args.output)
            metadata = correlation_result.metadata
            print("Correlation Engine completado correctamente.")
            print("Alcance: únicamente ventana observada")
            print(f"Bucket temporal: {metadata['bucket_seconds']} s")
            print(
                "Solapamiento interfaz↔sondas: "
                f"{metadata['network_overlap']['duration_seconds']:.3f} s"
            )
            if metadata["host_supplied"]:
                print(
                    "Solapamiento host↔sondas: "
                    f"{metadata['host_probe_overlap']['duration_seconds']:.3f} s"
                )
            else:
                print("Host: N/D (no se suministró host_samples.csv)")
            print(f"Relaciones evaluadas: {len(correlation_result.correlations)}")
            for row in correlation_result.correlations.to_dict(orient="records"):
                rho = row["spearman_rho"]
                rho_text = "N/D" if rho is None or rho != rho else f"{rho:+.3f}"
                target = "" if row["target"] is None or row["target"] != row["target"] else f" [{row['target']}]"
                print(
                    f"  {row['x_metric']} ↔ {row['y_metric']}{target} | "
                    f"n={row['pair_count']} | rho={rho_text} | "
                    f"{row['strength']} | {row['evidence_status']}"
                )
            print("Causalidad: NO inferida por Correlation Engine")
            print("p-value inferencial: NO calculado por autocorrelación temporal potencial")
            print(f"Correlaciones: {correlations_path.resolve()}")
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
