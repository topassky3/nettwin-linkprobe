from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import csv
from pathlib import Path
import time
from typing import Any, Callable

import psutil


@dataclass(frozen=True)
class HostSample:
    timestamp: str
    cpu_percent: float | None
    memory_percent: float | None
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None
    uptime_seconds: float | None
    sample_status: str = "ok"
    error: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_average(provider: Any) -> tuple[float | None, float | None, float | None]:
    try:
        values = provider.getloadavg()
        if values is None or len(values) != 3:
            return None, None, None
        return tuple(float(v) for v in values)  # type: ignore[return-value]
    except (AttributeError, OSError, NotImplementedError):
        return None, None, None


class HostCollector:
    def __init__(
        self,
        provider: Any = psutil,
        clock: Callable[[], str] = _utc_now,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.provider = provider
        self.clock = clock
        self.wall_clock = wall_clock

    def sample(self) -> HostSample:
        timestamp = self.clock()
        try:
            cpu = float(self.provider.cpu_percent(interval=None))
            memory = float(self.provider.virtual_memory().percent)
            load_1m, load_5m, load_15m = _load_average(self.provider)
            boot_time = float(self.provider.boot_time())
            uptime = max(0.0, float(self.wall_clock()) - boot_time)
            return HostSample(
                timestamp=timestamp,
                cpu_percent=cpu,
                memory_percent=memory,
                load_1m=load_1m,
                load_5m=load_5m,
                load_15m=load_15m,
                uptime_seconds=uptime,
            )
        except Exception as exc:
            return HostSample(
                timestamp=timestamp,
                cpu_percent=None,
                memory_percent=None,
                load_1m=None,
                load_5m=None,
                load_15m=None,
                uptime_seconds=None,
                sample_status="error",
                error=str(exc),
            )


def collect_host(
    output: str | Path,
    interval_seconds: float = 5.0,
    samples: int | None = None,
    duration_seconds: float | None = None,
    provider: Any = psutil,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
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
    collector = HostCollector(provider=provider, wall_clock=wall_clock)
    started = monotonic_fn()
    count = 0
    fieldnames = list(asdict(HostSample("", None, None, None, None, None, None)).keys())

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        while True:
            writer.writerow(asdict(collector.sample()))
            handle.flush()
            count += 1
            if samples is not None and count >= samples:
                break
            if duration_seconds is not None and (monotonic_fn() - started) >= duration_seconds:
                break
            sleep_fn(interval_seconds)
    return output_path
