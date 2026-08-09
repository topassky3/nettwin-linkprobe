from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from nettwin.preflight_engine import PreflightResult
from nettwin.preflight_engine import run_preflight as _engine_run_preflight


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _redact_addresses(payload: dict[str, Any]) -> None:
    for row in payload.get("interface", {}).get("addresses", []):
        address = row.get("address")
        if address:
            row["address_sha256"] = hashlib.sha256(
                str(address).encode("utf-8")
            ).hexdigest()
        row["address"] = None
        row["netmask"] = None
    payload.get("interface", {})["local_addresses_redacted"] = True


def _selected_ipv4s(payload: dict[str, Any]) -> set[str]:
    return {
        str(row["address"])
        for row in payload.get("interface", {}).get("addresses", [])
        if row.get("family") == "ipv4" and row.get("address")
    }


def _gateway_belongs_to_selected_interface(payload: dict[str, Any]) -> bool | None:
    gateway = payload.get("gateway", {})
    if gateway.get("status") != "available":
        return None

    selected = str(payload.get("interface", {}).get("selected") or "")
    system_name = str(payload.get("system", {}).get("os") or "").lower()

    if system_name.startswith("win"):
        interface_ip = gateway.get("interface_ip")
        selected_ipv4s = _selected_ipv4s(payload)
        if not interface_ip or not selected_ipv4s:
            return False
        return str(interface_ip) in selected_ipv4s

    observed_interface = gateway.get("interface")
    if observed_interface:
        return str(observed_interface) == selected

    return None


def _downgrade_mismatched_gateway(payload: dict[str, Any]) -> None:
    belongs = _gateway_belongs_to_selected_interface(payload)
    gateway = payload.get("gateway", {})
    gateway["selected_interface_match"] = belongs
    if belongs is not False:
        return

    selected = str(payload.get("interface", {}).get("selected") or "N/D")
    gateway["gateway"] = None
    gateway["interface"] = None
    gateway["interface_ip"] = None
    gateway["status"] = "unavailable"
    gateway["error"] = (
        "La ruta default observada no pertenece de forma verificable a la "
        f"interfaz seleccionada '{selected}'; el gateway queda N/D."
    )

    for check in payload.get("checks", []):
        if check.get("id") == "gateway.observed":
            check["status"] = "WARN"
            check["detail"] = f"Gateway N/D: {gateway['error']}"
            break

    checks = payload.get("checks", [])
    failures = [item for item in checks if item.get("status") == "FAIL"]
    warnings = [item for item in checks if item.get("status") == "WARN"]
    payload["check_summary"] = {
        "pass": sum(item.get("status") == "PASS" for item in checks),
        "warn": len(warnings),
        "fail": len(failures),
    }
    payload["ready_for_run"] = not failures
    payload["status"] = "FAIL" if failures else "WARN" if warnings else "PASS"


def _prepare_bom_compatible_config(config_path: str | Path) -> tuple[Path, Path | None]:
    """Normaliza temporalmente UTF-8 BOM sin cambiar el archivo del usuario.

    El temporal se crea en el directorio temporal del sistema. Para conservar la
    semántica de Fase 11, `output.directory` se convierte a absoluto respecto del
    archivo original antes de delegar en el engine. El SHA-256 final sigue siendo
    el de los bytes originales suministrados por el administrador.
    """
    original = Path(config_path).resolve()
    if not original.exists() or not original.is_file() or original.suffix.lower() != ".json":
        return original, None

    raw = original.read_bytes()
    if not raw.startswith(b"\xef\xbb\xbf"):
        return original, None

    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido en {original}: {exc}") from exc

    if isinstance(payload, dict):
        output = payload.get("output")
        if isinstance(output, dict):
            raw_directory = str(output.get("directory") or "").strip()
            if raw_directory:
                directory = Path(raw_directory)
                if not directory.is_absolute():
                    output["directory"] = str((original.parent / directory).resolve())

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json",
        prefix="nettwin_preflight_bom_",
        delete=False,
    )
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    finally:
        handle.close()
    return original, temporary


def _restore_original_config_identity(
    payload: dict[str, Any], original: Path, temporary: Path | None
) -> None:
    if temporary is None:
        return
    payload["configuration"]["path"] = str(original)
    payload["configuration"]["sha256"] = _sha256(original)
    payload["configuration"]["encoding"] = "utf-8-sig"


def _rewrite_artifact(result: PreflightResult) -> None:
    result.output_path.write_text(
        json.dumps(result.payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def run_preflight(*args: Any, redact_local_addresses: bool = False, **kwargs: Any) -> PreflightResult:
    """User-facing preflight wrapper.

    - acepta JSON UTF-8 con o sin BOM;
    - preserva el SHA-256 de los bytes originales de configuración;
    - verifica que el gateway observado pertenezca a la interfaz seleccionada;
    - redacta IPs locales solo después de usar la IP real para asociar rutas.

    Ninguna de estas operaciones envía tráfico de red ni modifica configuración
    de red, rutas, firewall, interfaces o servicios.
    """
    if args:
        config_argument = args[0]
        trailing_args = args[1:]
    elif "config_path" in kwargs:
        config_argument = kwargs.pop("config_path")
        trailing_args = ()
    else:
        raise ValueError("Debe suministrar la ruta de configuración")

    original_config, temporary_config = _prepare_bom_compatible_config(
        config_argument
    )
    call_args = (temporary_config or original_config, *trailing_args)

    try:
        try:
            result = _engine_run_preflight(
                *call_args,
                redact_local_addresses=False,
                **kwargs,
            )
        except OSError as exc:
            raise ValueError(f"No se pudo preparar la salida del preflight: {exc}") from exc

        _restore_original_config_identity(
            result.payload, original_config, temporary_config
        )
        _downgrade_mismatched_gateway(result.payload)

        if redact_local_addresses:
            _redact_addresses(result.payload)

        # El artefacto debe reflejar exactamente el payload final, incluida la
        # decisión sobre pertenencia del gateway y cualquier redacción.
        _rewrite_artifact(result)
        return result
    finally:
        if temporary_config is not None:
            try:
                temporary_config.unlink(missing_ok=True)
            except OSError:
                pass
