from __future__ import annotations

import sys

from nettwin.cli import build_parser, main as legacy_main
from nettwin.evidence_cli import main as evidence_main
from nettwin.fingerprint_cli import main as fingerprint_main
from nettwin.integrity_cli import COMMANDS as INTEGRITY_COMMANDS
from nettwin.integrity_cli import main as integrity_main
from nettwin.orchestrator_cli import main as orchestrator_main
from nettwin.preflight_cli import main as preflight_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "preflight":
        return preflight_main(args[1:])
    if args and args[0] == "run-linkprobe":
        return orchestrator_main(args[1:])
    if args and args[0] == "analyze-evidence":
        return evidence_main(args[1:])
    if args and args[0] == "analyze-fingerprints":
        return fingerprint_main(args[1:])
    if args and args[0] in INTEGRITY_COMMANDS:
        return integrity_main(args)
    if args in (["--help"], ["-h"]):
        build_parser().print_help()
        print("\nComandos adicionales de LinkProbe v0.3:")
        print("  preflight              Validar entorno de forma observacional antes del piloto")
        print("  run-linkprobe          Ejecutar collectors coordinados, cerrar y sellar el run")
        print("  analyze-evidence       Generar hallazgos HECHO→INTERPRETACIÓN→HIPÓTESIS→CONFIANZA→RECOMENDACIÓN")
        print("  analyze-fingerprints   Generar vectores compactos reproducibles por evento")
        print("  finalize-integrity     Generar run_metadata.json y checksums.sha256")
        print("  verify-integrity       Verificar integridad SHA-256 de una ejecución")
        print("  repro-check            Reanalizar dos veces y comparar agregados con tolerancia")
        return 0
    return legacy_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
