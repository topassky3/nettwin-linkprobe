from __future__ import annotations

import sys

from nettwin.cli import build_parser, main as legacy_main
from nettwin.evidence_cli import main as evidence_main
from nettwin.fingerprint_cli import main as fingerprint_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "analyze-evidence":
        return evidence_main(args[1:])
    if args and args[0] == "analyze-fingerprints":
        return fingerprint_main(args[1:])
    if args in (["--help"], ["-h"]):
        build_parser().print_help()
        print("\nComandos adicionales de LinkProbe v0.3:")
        print("  analyze-evidence       Generar hallazgos HECHO→INTERPRETACIÓN→HIPÓTESIS→CONFIANZA→RECOMENDACIÓN")
        print("  analyze-fingerprints   Generar vectores compactos reproducibles por evento")
        return 0
    return legacy_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
