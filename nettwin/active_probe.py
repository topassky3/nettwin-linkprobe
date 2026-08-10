from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import csv
from pathlib import Path
import platform
import re
import statistics
import subprocess
import time
import unicodedata
from typing import Callable, Iterable, Sequence


DEFAULT_PAYLOAD_BYTES = 32


@dataclass(frozen=True)
class ProbeTarget:
    name: str
    address: str


@dataclass(frozen=True)
class ProbeSample:
    timestamp: str
    target: str
    address: str
    probe_type: str
    packets_sent: int
    packets_received: int
    packet_loss_pct: float
    reachability: bool
    rtt_ms: float | None
    rtt_min_ms: float | None
    rtt_max_ms: float | None
    delay_variation_ms: float | None
    payload_bytes: int
    estimated_outbound_payload_bytes: int
    sample_status: str = "ok"
    error: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_ping_command(
    address: str,
    *,
    count: int,
    timeout_ms: int,
    payload_bytes: int,
    system_name: str | None = None,
) -> list[str]:
    system_name = (system_name or platform.system()).lower()
    if system_name.startswith("win"):
        return [
            "ping",
            "-n", str(count),
            "-w", str(timeout_ms),
            "-l", str(payload_bytes),
            address,
        ]

    timeout_seconds = max(1, int((timeout_ms + 999) / 1000))
    if system_name == "darwin":
        return [
            "ping",
            "-c", str(count),
            "-W", str(timeout_ms),
            "-s", str(payload_bytes),
            address,
        ]

    return [
        "ping",
        "-c", str(count),
        "-W", str(timeout_seconds),
        "-s", str(payload_bytes),
        address,
    ]


def _parse_rtts(output: str) -> list[float]:
    """Extrae RTT de respuestas de ping en inglés o español.

    Soporta ``time=12ms``, ``time<1ms``, ``tiempo=18ms`` y ``tiempo<1ms``.
    Para ``<1ms`` usa 0.5 ms como aproximación reproducible.
    """
    pattern = re.compile(
        r"(?:time|tiempo)\s*([=<])\s*(\d+(?:[\.,]\d+)?)\s*ms",
        flags=re.IGNORECASE,
    )
    values: list[float] = []
    for operator, raw in pattern.findall(output):
        value = float(raw.replace(",", "."))
        if operator == "<":
            value = value / 2.0
        values.append(value)
    return values


def _normalized_ping_text(output: str) -> str:
    """Normaliza solo para reconocer resúmenes localizados de ping.

    No altera ni almacena el output original. Quita diacríticos y pasa a
    minúsculas para soportar variantes como ``Estadísticas`` / ``estadisticas``.
    """
    normalized = unicodedata.normalize("NFKD", output)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _parse_packet_counts(output: str) -> tuple[int, int] | None:
    """Obtiene ``(sent, received)`` del resumen de ping cuando está disponible.

    Se soportan los formatos comunes de Windows en inglés/español y el resumen
    tradicional de ping tipo Unix. Esto separa reachability/pérdida del parser de
    RTT: una localización desconocida no debe convertirse falsamente en 100% loss.
    """
    text = _normalized_ping_text(output)
    patterns = (
        re.compile(
            r"packets\s*:\s*sent\s*=\s*(\d+)\s*,\s*received\s*=\s*(\d+)\s*,\s*lost\s*=\s*(\d+)",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"paquetes\s*:\s*enviados\s*=\s*(\d+)\s*,\s*recibidos\s*=\s*(\d+)\s*,\s*perdidos\s*=\s*(\d+)",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"(\d+)\s+packets?\s+transmitted\s*,\s*(\d+)\s+(?:packets?\s+)?received",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"(\d+)\s+paquetes?\s+transmitidos\s*,\s*(\d+)\s+(?:paquetes?\s+)?recibidos",
            flags=re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        sent = int(match.group(1))
        received = int(match.group(2))
        if sent < 0 or received < 0 or received > sent:
            return None
        return sent, received
    return None


def _count_reply_lines(output: str) -> int:
    """Fallback conservador: una línea con TTL representa una respuesta ICMP.

    Windows y los pings Unix suelen imprimir TTL/ttl por respuesta. Se usa solo
    cuando no hay resumen de paquetes parseable.
    """
    return sum(
        1
        for line in output.splitlines()
        if re.search(r"\bttl\s*[=:]\s*\d+\b", line, flags=re.IGNORECASE)
    )


def _delay_variation(current_rtt: float | None, previous_rtt: float | None) -> float | None:
    """Cambio absoluto entre el RTT mediano actual y el anterior.

    Es una métrica operacional de variación temporal para el piloto; no se
    presenta como jitter unidireccional.
    """
    if current_rtt is None or previous_rtt is None:
        return None
    return abs(current_rtt - previous_rtt)


def _default_runner(command: Sequence[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    # Sin ``encoding=`` explícito: Python usa el encoding local de la consola.
    # Esto es importante en Windows en español, donde ping puede emitir cp1252/
    # OEM en vez de UTF-8. ``errors=replace`` evita que una localización extraña
    # tumbe el collector.
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout_seconds,
        check=False,
        shell=False,
    )


class ActiveProbeEngine:
    def __init__(
        self,
        *,
        count_per_target: int = 1,
        timeout_ms: int = 1000,
        payload_bytes: int = DEFAULT_PAYLOAD_BYTES,
        runner: Callable[[Sequence[str], float], subprocess.CompletedProcess[str]] = _default_runner,
        clock: Callable[[], str] = _utc_now,
        system_name: str | None = None,
    ) -> None:
        if count_per_target <= 0:
            raise ValueError("count_per_target debe ser > 0")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms debe ser > 0")
        if payload_bytes <= 0 or payload_bytes > 1400:
            raise ValueError("payload_bytes debe estar entre 1 y 1400")

        self.count_per_target = count_per_target
        self.timeout_ms = timeout_ms
        self.payload_bytes = payload_bytes
        self.runner = runner
        self.clock = clock
        self.system_name = system_name
        self._previous_rtt: dict[str, float] = {}

    def probe(self, target: ProbeTarget) -> ProbeSample:
        timestamp = self.clock()
        command = _build_ping_command(
            target.address,
            count=self.count_per_target,
            timeout_ms=self.timeout_ms,
            payload_bytes=self.payload_bytes,
            system_name=self.system_name,
        )
        timeout_seconds = max(2.0, (self.timeout_ms / 1000.0) * self.count_per_target + 2.0)

        try:
            completed = self.runner(command, timeout_seconds)
            output = (completed.stdout or "") + "\n" + (completed.stderr or "")
            rtts = _parse_rtts(output)
            packet_counts = _parse_packet_counts(output)

            sent = self.count_per_target
            if packet_counts is not None:
                parsed_sent, parsed_received = packet_counts
                if parsed_sent > 0:
                    sent = parsed_sent
                received = min(max(parsed_received, 0), sent)
            else:
                reply_lines = _count_reply_lines(output)
                received = min(max(len(rtts), reply_lines), sent)

            loss = ((sent - received) / sent) * 100.0
            reachable = received > 0
            median_rtt = statistics.median(rtts) if rtts else None
            previous = self._previous_rtt.get(target.name)
            variation = _delay_variation(median_rtt, previous)
            if median_rtt is not None:
                self._previous_rtt[target.name] = median_rtt

            return ProbeSample(
                timestamp=timestamp,
                target=target.name,
                address=target.address,
                probe_type="icmp_echo",
                packets_sent=sent,
                packets_received=received,
                packet_loss_pct=loss,
                reachability=reachable,
                rtt_ms=median_rtt,
                rtt_min_ms=min(rtts) if rtts else None,
                rtt_max_ms=max(rtts) if rtts else None,
                delay_variation_ms=variation,
                payload_bytes=self.payload_bytes,
                estimated_outbound_payload_bytes=sent * self.payload_bytes,
                sample_status="ok",
                error="",
            )
        except Exception as exc:
            return ProbeSample(
                timestamp=timestamp,
                target=target.name,
                address=target.address,
                probe_type="icmp_echo",
                packets_sent=self.count_per_target,
                packets_received=0,
                packet_loss_pct=100.0,
                reachability=False,
                rtt_ms=None,
                rtt_min_ms=None,
                rtt_max_ms=None,
                delay_variation_ms=None,
                payload_bytes=self.payload_bytes,
                estimated_outbound_payload_bytes=self.count_per_target * self.payload_bytes,
                sample_status="error",
                error=str(exc),
            )

    def probe_all(self, targets: Iterable[ProbeTarget]) -> list[ProbeSample]:
        # Cada target se ejecuta independientemente: uno caído no bloquea los demás.
        return [self.probe(target) for target in targets]


def collect_probes(
    targets: Sequence[ProbeTarget],
    output: str | Path,
    *,
    interval_seconds: float = 5.0,
    cycles: int | None = None,
    duration_seconds: float | None = None,
    count_per_target: int = 1,
    timeout_ms: int = 1000,
    payload_bytes: int = DEFAULT_PAYLOAD_BYTES,
    runner: Callable[[Sequence[str], float], subprocess.CompletedProcess[str]] = _default_runner,
    sleep_fn: Callable[[float], None] = time.sleep,
    system_name: str | None = None,
) -> Path:
    if not targets:
        raise ValueError("Debe indicar al menos un target")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds debe ser > 0")
    if cycles is None and duration_seconds is None:
        raise ValueError("Debe indicar cycles o duration_seconds")
    if cycles is not None and cycles <= 0:
        raise ValueError("cycles debe ser > 0")
    if duration_seconds is not None and duration_seconds < 0:
        raise ValueError("duration_seconds debe ser >= 0")

    names = [target.name for target in targets]
    if len(names) != len(set(names)):
        raise ValueError("Los nombres de target deben ser únicos")

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    engine = ActiveProbeEngine(
        count_per_target=count_per_target,
        timeout_ms=timeout_ms,
        payload_bytes=payload_bytes,
        runner=runner,
        system_name=system_name,
    )
    fieldnames = [field.name for field in fields(ProbeSample)]

    started = time.monotonic()
    cycle = 0
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        while True:
            for sample in engine.probe_all(targets):
                writer.writerow(asdict(sample))
            handle.flush()
            cycle += 1

            if cycles is not None and cycle >= cycles:
                break
            if duration_seconds is not None and (time.monotonic() - started) >= duration_seconds:
                break
            sleep_fn(interval_seconds)

    return output_path
