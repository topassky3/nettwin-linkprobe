from __future__ import annotations

import argparse
import json
from pathlib import Path

from nettwin.dry_run_engine import select_local_interface
from nettwin.pilot_engine import (
    LOCAL_MODE,
    PRODUCTION_MODE,
    build_pilot_config,
    run_pilot,
    validate_pilot_config,
)


COMMANDS = {"pilot-config", "pilot-validate", "pilot-run"}


def _config_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettwin pilot-config",
        description=(
            "Generar una configuración de Fase 15. Por defecto crea una plantilla HacheNet "
            "deliberadamente NO ejecutable hasta completar interfaz, targets y autorización."
        ),
    )
    parser.add_argument("--output", required=True, help="Archivo JSON nuevo a crear")
    parser.add_argument("--interface", help="Interfaz explícita")
    parser.add_argument("--run-id", help="run_id explícito")
    parser.add_argument(
        "--local-acceptance",
        action="store_true",
        help="Crear configuración segura local: 12 s, 127.0.0.1 y sin tráfico externo",
    )
    return parser


def _validate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettwin pilot-validate",
        description=(
            "Validar estáticamente configuración de Fase 15 sin ejecutar probes ni modificar la red."
        ),
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--json", action="store_true", help="Emitir resultado completo como JSON")
    return parser


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettwin pilot-run",
        description=(
            "Ejecutar Fase 15: validación, preflight, captura, sellado, Quality Gate, análisis e informe. "
            "El modo HacheNet exige configuración autorizada y --authorized."
        ),
    )
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--authorized",
        action="store_true",
        help=(
            "Confirmación explícita adicional para modo hachenet_pilot. Úsela únicamente cuando los targets "
            "y la ventana hayan sido aprobados por HacheNet."
        ),
    )
    parser.add_argument("--include-hostname", action="store_true")
    parser.add_argument("--redact-local-addresses", action="store_true")
    return parser


def _validation_payload(result) -> dict:
    return {
        "valid": result.valid,
        "mode": result.mode,
        "config": str(result.config_path),
        "errors": list(result.errors),
        "warnings": list(result.warnings),
        "estimate": result.estimate,
    }


def config_main(argv: list[str] | None = None) -> int:
    parser = _config_parser()
    args = parser.parse_args(argv)
    interface = args.interface
    if args.local_acceptance and not interface:
        try:
            interface, reason = select_local_interface(None)
        except ValueError as exc:
            parser.error(str(exc))
            return 2
        print(f"Interfaz local seleccionada: {interface} ({reason})")
    try:
        path = build_pilot_config(
            args.output,
            local_acceptance=bool(args.local_acceptance),
            interface_name=interface,
            run_id=args.run_id,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    print("Configuración Fase 15 generada.")
    print(f"Archivo: {path}")
    if args.local_acceptance:
        print("Modo: local_acceptance")
        print("Target: local_loopback -> 127.0.0.1")
        print("Tráfico externo: NO")
        print("Esta configuración sí puede usarse para aceptación local.")
    else:
        print("Modo: hachenet_pilot")
        print("Estado: PLANTILLA NO EJECUTABLE")
        print("Debe reemplazar interfaz, targets y datos de autorización antes de pilot-run.")
        print("No se incluyeron direcciones reales ni targets inventados.")
    return 0


def validate_main(argv: list[str] | None = None) -> int:
    parser = _validate_parser()
    args = parser.parse_args(argv)
    try:
        result = validate_pilot_config(args.config)
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    payload = _validation_payload(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Validación Fase 15")
        print(f"Modo: {result.mode or 'N/D'}")
        print("Estado: " + ("PASS" if result.valid else "FAIL"))
        if result.errors:
            print("Errores:")
            for error in result.errors:
                print(f"  - {error}")
        if result.warnings:
            print("Advertencias:")
            for warning in result.warnings:
                print(f"  - {warning}")
        if result.estimate:
            print("Estimación de ejecución:")
            print(f"  duración: {result.estimate.get('duration_hours', 0):.3f} h")
            print(f"  targets: {result.estimate.get('target_count')}")
            print(f"  ciclos probes: {result.estimate.get('probe_cycles')}")
            print(f"  ICMP echo requests: {result.estimate.get('icmp_echo_requests')}")
            print(
                "  payload ICMP saliente estimado: "
                f"{result.estimate.get('outbound_icmp_payload_bytes_estimate')} bytes"
            )
            rows = result.estimate.get("expected_rows", {})
            print(f"  filas esperadas totales: {rows.get('total')}")
        print("Esta validación no ejecutó probes ni modificó configuración de red.")
    return 0 if result.valid else 2


def run_main(argv: list[str] | None = None) -> int:
    parser = _run_parser()
    args = parser.parse_args(argv)
    try:
        result = run_pilot(
            args.config,
            authorized_execution=bool(args.authorized),
            include_hostname=bool(args.include_hostname),
            redact_local_addresses=bool(args.redact_local_addresses),
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    print("Fase 15 finalizada.")
    print(f"Estado: {result.status}")
    print(f"Detalle: {result.message}")
    print(f"Config: {result.config_path}")
    if result.run_dir is not None:
        print(f"Run: {result.run_dir}")
    if result.report_dir is not None:
        print(f"Report: {result.report_dir}")
    print(f"Resumen: {result.summary_path}")
    print(f"Modo: {result.validation.mode}")
    print("Targets configurados:")
    if result.validation.experiment is not None:
        for target in result.validation.experiment.targets:
            print(f"  - {target.name} ({target.role})")
    print("Payload capture: NO")
    print("Escaneo/descubrimiento automático: NO")
    if result.report_result is not None:
        print(f"Quality Gate: {result.report_result.quality_status}")
        print("Análisis ejecutado: " + ("SÍ" if result.report_result.analysis_executed else "NO"))
        print(f"HTML: {result.report_result.report_html}")
    return 0 if result.status == "PASS" else 2


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if not args:
        raise SystemExit("Se requiere pilot-config, pilot-validate o pilot-run")
    command, rest = args[0], args[1:]
    if command == "pilot-config":
        return config_main(rest)
    if command == "pilot-validate":
        return validate_main(rest)
    if command == "pilot-run":
        return run_main(rest)
    raise SystemExit(f"Comando de piloto desconocido: {command}")
