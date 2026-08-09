from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import csv
from pathlib import Path
import time
from typing import Any, Callable

import psutil


COUNTER_FIELDS = (
    "bytes_recv",
    "bytes_sent",
    "packets_recv",
    "packets_sent",
    "errin",
    "errout",
    "dropin",
    "dropout",
)


@dataclass(frozen=True)
class InterfaceSample:
    timestamp: str
    interface: str
    rx_bytes: int | None
    tx_bytes: int | None
    rx_packets: int | None
    tx_packets: int | None
    rx_errors: int | None
    tx_errors: int | None
    rx_drops: int | None
    tx_drops: int | None
    interface_state: str
    reported_link_speed_mbps: int | None
    mtu: int | None
    rx_bytes_delta: int | None = None
    tx_bytes_delta: int | None = None
    rx_packets_delta: int | None = None
    tx_packets_delta: int | None = None
    rx_errors_delta: int | None = None
    tx_errors_delta: int | None = None
    rx_drops_delta: int | None = None
    tx_drops_delta: int | None = None
    counter_event: str = "initial"
    sample_status: str = "ok"
    error: str = ""


def list_interfaces(provider: Any = psutil) -> list[dict[str, Any]]:
    stats = provider.net_if_stats()
    counters = provider.net_io_counters(pernic=True)
    names = sorted(set(stats) | set(counters))
    rows: list[dict[str, Any]] = []
    for name in names:
        st = stats.get(name)
        rows.append(
            {
                "interface": name,
                "is_up": None if st is None else bool(st.isup),
                "speed_mbps": None if st is None or st.speed < 0 else int(st.speed),
                "mtu": None if st is None else int(st.mtu),
                "has_counters": name in counters,
            }
        )
    return rows


def _counter_delta(previous: int, current: int, max_value: int | None = None) -> tuple[int | None, str]:
    if current >= previous:
        return current - previous, "normal"
    if max_value is not None and max_value > 0 and previous > int(max_value * 0.90):
        return (max_value - previous) + current + 1, "overflow"
    return None, "reset"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InterfaceCollector:
    def __init__(
        self,
        interface: str,
        provider: Any = psutil,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self.interface = interface
        self.provider = provider
        self.clock = clock
        self._previous: dict[str, int] | None = None

    def _read_raw(self) -> tuple[Any, Any]:
        counters = self.provider.net_io_counters(pernic=True)
        stats = self.provider.net_if_stats()
        if self.interface not in counters:
            raise ValueError(f"La interfaz '{self.interface}' no tiene contadores disponibles.")
        if self.interface not in stats:
            raise ValueError(f"La interfaz '{self.interface}' no tiene estadísticas disponibles.")
        return counters[self.interface], stats[self.interface]

    def sample(self) -> InterfaceSample:
        timestamp = self.clock()
        try:
            io, stat = self._read_raw()
            current = {field: int(getattr(io, field, 0)) for field in COUNTER_FIELDS}
            deltas: dict[str, int | None] = {}
            events: set[str] = set()
            if self._previous is None:
                for field in COUNTER_FIELDS:
                    deltas[field] = None
                counter_event = "initial"
            else:
                for field in COUNTER_FIELDS:
                    delta, event = _counter_delta(self._previous[field], current[field])
                    deltas[field] = delta
                    if event != "normal":
                        events.add(event)
                counter_event = "+".join(sorted(events)) if events else "normal"
            self._previous = current
            speed = None if getattr(stat, "speed", -1) < 0 else int(stat.speed)
            return InterfaceSample(
                timestamp=timestamp,
                interface=self.interface,
                rx_bytes=current["bytes_recv"],
                tx_bytes=current["bytes_sent"],
                rx_packets=current["packets_recv"],
                tx_packets=current["packets_sent"],
                rx_errors=current["errin"],
                tx_errors=current["errout"],
                rx_drops=current["dropin"],
                tx_drops=current["dropout"],
                interface_state="UP" if bool(stat.isup) else "DOWN",
                reported_link_speed_mbps=speed,
                mtu=int(stat.mtu),
                rx_bytes_delta=deltas["bytes_recv"],
                tx_bytes_delta=deltas["bytes_sent"],
                rx_packets_delta=deltas["packets_recv"],
                tx_packets_delta=deltas["packets_sent"],
                rx_errors_delta=deltas["errin"],
                tx_errors_delta=deltas["errout"],
                rx_drops_delta=deltas["dropin"],
                tx_drops_delta=deltas["dropout"],
                counter_event=counter_event,
            )
        except Exception as exc:
            return InterfaceSample(
                timestamp=timestamp,
                interface=self.interface,
                rx_bytes=None,
                tx_bytes=None,
                rx_packets=None,
                tx_packets=None,
                rx_errors=None,
                tx_errors=None,
                rx_drops=None,
                tx_drops=None,
                interface_state="UNKNOWN",
                reported_link_speed_mbps=None,
                mtu=None,
                counter_event="unavailable",
                sample_status="error",
                error=str(exc),
            )


def collect_interface(
    interface: str,
    output: str | Path,
    interval_seconds: float = 5.0,
    samples: int | None = None,
    duration_seconds: float | None = None,
    provider: Any = psutil,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Path:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds debe ser > 0")
    if samples is None and duration_seconds is None:
        raise ValueError("Debe indicar samples o duration_seconds")
    if samples is not None and samples <= 0:
        raise ValueError("samples debe ser > 0")
    if duration_seconds is not None and duration_seconds < 0:
        raise ValueError("duration_seconds debe ser >= 0")

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    collector = InterfaceCollector(interface, provider=provider)
    started = time.monotonic()
    count = 0
    fieldnames = [field.name for field in fields(InterfaceSample)]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        while True:
            writer.writerow(asdict(collector.sample()))
            handle.flush()
            count += 1
            if samples is not None and count >= samples:
                break
            if duration_seconds is not None and (time.monotonic() - started) >= duration_seconds:
                break
            sleep_fn(interval_seconds)
    return output_path
