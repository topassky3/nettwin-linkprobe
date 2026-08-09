from __future__ import annotations

import argparse
from pathlib import Path

from nettwin import __version__
from nettwin.integrity_engine import (
    IntegrityConfig,
    check_reproducibility,
    finalize_integrity,
    verify_integrity,
)


COMMANDS = {"finalize-integrity", "verify-integrity", "repro-check"}
_REPRO_OWNED_NAMES = {"replay_a", "replay_b", "reproducibility_report.json"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettwin",
        description="Integridad SHA-256 y reproducibilidad de NetTwin LinkProbe.",
    )
    sub = parser.add_subparsers(dest="integrity_command", required=True)

    finalize = sub.add_parser(
        "finalize-integrity",
        help="Crear run_metadata.json y checksums.sha256 para una ejecución.",
    )
    finalize.add_argument("--run-dir", required=True, help="Directorio de la ejecución")
    finalize.add_argument("--run-id", required=True, help="Identificador único de ejecución")
    finalize.add_argument("--interface", help="Interfaz autorizada; si se omite se intenta inferir")
    finalize.add_argument(
        "--target",
        action="append",
        default=[],
        help="Nombre de target autorizado; puede repetirse",
    )
    finalize.add_argument(
        "--requested-duration-seconds",
        type=float,
        help="Duración solicitada del experimento, separada de la duración observada",
    )
    finalize.add_argument("--config", dest="config_path", help="Archivo de configuración usado")
    finalize.add_argument("--sensor-version", default=__version__)
    finalize.add_argument("--analyzer-version", default=__version__)

    verify = sub.add_parser(
        "verify-integrity",
        help="Verificar checksums y detectar archivos faltantes o modificados.",
    )
    verify.add_argument("--run-dir", required=True, help="Directorio de la ejecución")
    verify.add_argument(
        "--strict-untracked",
        action="store_true",
        help="Considerar inválido el run si aparecen archivos no incluidos en checksums.sha256",
    )

    repro = sub.add_parser(
        "repro-check",
        help="Ejecutar dos veces el pipeline analítico y comparar agregados.",
    )
    repro.add_argument("--interface-csv", required=True)
    repro.add_argument("--probe-csv", required=True)
    repro.add_argument("--host-csv")
    repro.add_argument("--capacity-mbps", type=float)
    repro.add_argument("--output", required=True)
    repro.add_argument("--abs-tolerance", type=float, default=1e-9)
    repro.add_argument("--rel-tolerance", type=float, default=1e-9)
    return parser


def _finalize(args: argparse.Namespace) -> int:
    result = finalize_integrity(
        args.run_dir,
        config=IntegrityConfig(
            run_id=args.run_id,
            interface=args.interface,
            targets=tuple(args.target),
            requested_duration_seconds=args.requested_duration_seconds,
            config_path=args.config_path,
            sensor_version=args.sensor_version,
            analyzer_version=args.analyzer_version,
        ),
    )
    metadata = result.metadata
    print("Integridad finalizada correctamente.")
    print(f"Run ID: {metadata['run_id']}")
    print(f"Interfaz: {metadata.get('interface') or 'N/D'}")
    print(f"Targets: {', '.join(metadata.get('targets', [])) or 'N/D'}")
    print(f"Muestras registradas: {metadata['sample_summary']['total_rows']}")
    print(
        "Muestras fallidas conocidas: "
        f"{metadata['sample_summary']['failed_rows_known_total']}"
    )
    print(
        "Duración observada: "
        f"{metadata['observed_window']['duration_seconds'] if metadata['observed_window']['duration_seconds'] is not None else 'N/D'} s"
    )
    print(f"Archivos protegidos: {len(result.tracked_files)}")
    print(f"Metadata: {result.metadata_path.resolve()}")
    print(f"Checksums: {result.checksums_path.resolve()}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    result = verify_integrity(
        args.run_dir,
        strict_untracked=bool(args.strict_untracked),
    )
    print(f"Integridad: {'VÁLIDA' if result.valid else 'INVÁLIDA'}")
    print(f"Archivos verificados: {result.checked_files}")
    print(f"Faltantes: {len(result.missing_files)}")
    print(f"Modificados: {len(result.mismatched_files)}")
    print(f"Líneas malformadas: {len(result.malformed_lines)}")
    print(f"Rutas inseguras: {len(result.unsafe_paths)}")
    print(f"No rastreados: {len(result.untracked_files)}")
    if result.missing_files:
        print("  FALTANTES: " + ", ".join(result.missing_files))
    if result.mismatched_files:
        print("  MODIFICADOS: " + ", ".join(result.mismatched_files))
    if result.untracked_files:
        print("  NO RASTREADOS: " + ", ".join(result.untracked_files))
    print(f"Reporte: {result.report_path.resolve()}")
    return 0 if result.valid else 2


def _validate_repro_output(output: str | Path) -> None:
    root = Path(output)
    if not root.exists():
        return
    if not root.is_dir():
        raise ValueError(f"--output debe ser un directorio: {root}")
    unexpected = sorted(
        child.name for child in root.iterdir()
        if child.name not in _REPRO_OWNED_NAMES
    )
    if unexpected:
        raise ValueError(
            "El directorio de repro-check contiene archivos ajenos a NetTwin y no será eliminado: "
            + ", ".join(unexpected)
        )


def _repro(args: argparse.Namespace) -> int:
    _validate_repro_output(args.output)
    result = check_reproducibility(
        args.interface_csv,
        args.probe_csv,
        host_csv=args.host_csv,
        capacity_mbps=args.capacity_mbps,
        output_dir=args.output,
        abs_tolerance=args.abs_tolerance,
        rel_tolerance=args.rel_tolerance,
    )
    print(
        "Reproducibilidad: "
        + ("CONFIRMADA" if result.reproducible else "NO CONFIRMADA")
    )
    print(f"Artefactos comparados: {len(result.compared_artifacts)}")
    for artifact in result.compared_artifacts:
        print(f"  {artifact}")
    print(f"Diferencias: {len(result.differences)}")
    print(f"Reporte: {result.report_path.resolve()}")
    return 0 if result.reproducible else 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.integrity_command == "finalize-integrity":
            return _finalize(args)
        if args.integrity_command == "verify-integrity":
            return _verify(args)
        if args.integrity_command == "repro-check":
            return _repro(args)
    except ValueError as exc:
        parser.error(str(exc))
    parser.error("Comando de integridad no reconocido")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
