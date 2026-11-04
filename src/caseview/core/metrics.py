"""Cheap system metrics (psutil) merged with sensor picks, plus short history for sparklines.

psutil reads Windows performance counters, which costs far less than asking
LibreHardwareMonitor for loads, memory and disks.
"""

from __future__ import annotations

import ctypes
import gc
import sys
import time
from collections import deque
from collections.abc import Iterable

import psutil

from caseview.core.config import PowerSettings
from caseview.sensors import Reading

HISTORY_LEN = 60  # one minute at 1 sample/s

# keys shown by the dashboard, taskbar bar and phone page
LABELS = {
    "cpu_load": "CPU",
    "gpu_load": "GPU",
    "ram_pct": "RAM",
    "disk_pct": "Disk",
    "cpu_temp": "CPU temp",
    "gpu_temp": "GPU temp",
    "cpu_fan": "CPU fan",
    "net_down": "Down",
    "net_up": "Up",
}

# Everything a case display readout can show (value key -> label).
DISPLAY_SOURCES = {
    "cpu_temp": "CPU temperature (°C)",
    "gpu_temp": "GPU temperature (°C)",
    "gpu_hotspot": "GPU hot spot (°C)",
    "cpu_fan": "CPU fan (rpm)",
    "cpu_load": "CPU usage %",
    "gpu_load": "GPU usage %",
    "ram_pct": "RAM usage %",
    "gpu_vram_pct": "GPU memory (VRAM) usage %",
    "disk_pct": "Disk C: used %",
    "cpu_clock": "CPU clock (MHz)",
    "gpu_clock": "GPU core clock (MHz)",
    "gpu_mem_clock": "GPU memory clock (MHz)",
    "cpu_power": "CPU power (W)",
    "gpu_power": "GPU power (W)",
    "total_power": "PC power, estimated (W)",
    "wall_power": "Wall power, estimated (W)",
    "cpu_amps": "CPU current at 12 V (A)",
    "gpu_amps": "GPU current at 12 V (A)",
    "total_amps": "CPU + GPU current at 12 V (A)",
    "wall_amps": "Wall current, estimated (A)",
}

TWELVE_VOLT = 12.0


class SystemMetrics:
    def __init__(self, disk: str = "C:\\", power: PowerSettings | None = None) -> None:
        self._disk = disk
        self.power = power or PowerSettings()
        self._net = psutil.net_io_counters()
        self._net_t = time.monotonic()
        psutil.cpu_percent(None)  # first call primes the counter

    def sample(self, readings: Iterable[Reading]) -> dict[str, float]:
        mem = psutil.virtual_memory()
        out: dict[str, float] = {
            "cpu_load": psutil.cpu_percent(None),
            "ram_pct": mem.percent,
            "ram_used_gb": mem.used / 1e9,
            "ram_total_gb": mem.total / 1e9,
        }
        try:
            disk = psutil.disk_usage(self._disk)
            out["disk_pct"] = disk.percent
            out["disk_free_gb"] = disk.free / 1e9
        except OSError:
            pass
        net, now = psutil.net_io_counters(), time.monotonic()
        dt = max(now - self._net_t, 1e-3)
        out["net_down"] = (net.bytes_recv - self._net.bytes_recv) / dt  # bytes/s
        out["net_up"] = (net.bytes_sent - self._net.bytes_sent) / dt
        self._net, self._net_t = net, now
        for r in readings:
            if r.value is None:
                continue
            if r.kind == "Load" and r.hardware_type.startswith("Gpu") and r.name == "GPU Core":
                out["gpu_load"] = r.value
            elif (
                r.kind == "Temperature"
                and r.hardware_type.startswith("Gpu")
                and (r.name == "GPU Hot Spot")
            ):
                out["gpu_hotspot"] = r.value
            elif r.kind == "Power" and r.hardware_type == "Cpu" and r.name == "CPU Package":
                if r.value > 0:  # reads 0 W without administrator rights
                    out["cpu_power"] = r.value
            elif (
                r.kind == "Power"
                and r.hardware_type.startswith("Gpu")
                and (r.name == "GPU Package")
            ):
                out["gpu_power"] = r.value
            elif r.kind == "Load" and r.hardware_type.startswith("Gpu") and r.name == "GPU Memory":
                out["gpu_vram_pct"] = r.value
            elif r.kind == "Clock" and r.hardware_type.startswith("Gpu"):
                if r.name == "GPU Core":
                    out["gpu_clock"] = r.value
                elif r.name == "GPU Memory":
                    out["gpu_mem_clock"] = r.value
            elif r.kind == "Clock" and r.hardware_type == "Cpu" and "Core" in r.name:
                # fastest core = the clock people quote ("5.4 GHz")
                out["cpu_clock"] = max(out.get("cpu_clock", 0.0), r.value)
        out.update(power_estimates(out, self.power))
        return out


def power_estimates(values: dict[str, float], p: PowerSettings) -> dict[str, float]:
    """Amps and totals derived from the measured CPU/GPU watts.

    Measured: CPU package and GPU board power. Both are fed from the PSU's 12 V rails,
    so their current is watts / 12 V (CPU: before VRM losses, close enough for display).
    Estimated: PC total = CPU + GPU + other_watts; wall = total / PSU efficiency;
    wall current = wall watts / mains voltage.
    """
    out: dict[str, float] = {}
    cpu, gpu = values.get("cpu_power"), values.get("gpu_power")
    if cpu is not None:
        out["cpu_amps"] = cpu / TWELVE_VOLT
    if gpu is not None:
        out["gpu_amps"] = gpu / TWELVE_VOLT
    if cpu is not None or gpu is not None:
        out["total_amps"] = out.get("cpu_amps", 0.0) + out.get("gpu_amps", 0.0)
        total = (cpu or 0.0) + (gpu or 0.0) + p.other_watts
        out["total_power"] = total
        out["wall_power"] = total / p.psu_efficiency
        out["wall_amps"] = out["wall_power"] / p.mains_voltage
    return out


class History:
    """Last HISTORY_LEN values per key; bounded, so memory never grows."""

    def __init__(self, length: int = HISTORY_LEN) -> None:
        self._length = length
        self._data: dict[str, deque[float]] = {}

    def add(self, values: dict[str, float]) -> None:
        for k, v in values.items():
            self._data.setdefault(k, deque(maxlen=self._length)).append(v)

    def series(self, key: str) -> list[float]:
        return list(self._data.get(key, ()))


def human_rate(bytes_per_s: float) -> str:
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bytes_per_s < 1000 or unit == "GB/s":
            return f"{bytes_per_s:.0f} {unit}" if unit == "B/s" else f"{bytes_per_s:.1f} {unit}"
        bytes_per_s /= 1000
    return ""


def trim_memory() -> None:
    """Return unused memory to Windows (after startup or closing a window)."""
    gc.collect()
    try:
        from System import GC  # type: ignore[import-not-found]

        GC.Collect()
    except ImportError:
        pass
    if sys.platform == "win32":
        try:
            k32 = ctypes.windll.kernel32
            k32.SetProcessWorkingSetSize(
                k32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1)
            )
        except (AttributeError, OSError):
            pass
