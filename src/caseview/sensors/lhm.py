"""LibreHardwareMonitor provider (Windows), loaded through pythonnet.

Read-only by design: this module must never call any sensor `Control` API
(SetSoftware/SetDefault). tests/test_sensors.py enforces that.

CPU and Super-I/O (motherboard fan) sensors need administrator rights and the
PawnIO driver; without them those values are None and only GPU sensors work.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Any

from caseview.sensors.base import Reading

KINDS = {"Temperature", "Fan", "Load", "Clock", "Power"}
LEAN_KINDS = {"Temperature", "Fan", "Load", "Power", "Clock"}
LEAN_POWER = {"CPU Package", "GPU Package"}  # the only power sensors we use
LEAN_GPU_CLOCKS = {"GPU Core", "GPU Memory"}
# Hardware we refresh in lean mode; memory/storage/network come from psutil instead.
LEAN_HARDWARE = {"Cpu", "GpuNvidia", "GpuAmd", "GpuIntel", "Motherboard", "SuperIO"}


def default_lib_dir() -> Path:
    # Frozen builds ship the DLLs in the bundle (PyInstaller: _internal/lhm);
    # dev checkouts use vendor/lhm.
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return bundle / "lhm"
    return Path(__file__).resolve().parents[3] / "vendor" / "lhm"


class LhmProvider:
    """`full=False` (default) keeps only temperatures, fans and loads of CPU, GPU and the
    motherboard fan chip, and caches the sensor list after the first read; this roughly
    halves CPU time and allocations per tick. `full=True` lists everything (diagnostics).
    """

    def __init__(self, lib_dir: Path | None = None, full: bool = False) -> None:
        lib_dir = lib_dir or default_lib_dir()
        if not (lib_dir / "LibreHardwareMonitorLib.dll").exists():
            raise FileNotFoundError(
                f"LibreHardwareMonitorLib.dll not found in {lib_dir} "
                "(run: python -I tools/fetch_lhm.py)"
            )
        from pythonnet import load

        with contextlib.suppress(RuntimeError):  # runtime already loaded
            load("netfx")
        import clr

        if str(lib_dir) not in sys.path:
            sys.path.append(str(lib_dir))
        clr.AddReference("LibreHardwareMonitorLib")
        from LibreHardwareMonitor.Hardware import Computer

        self._full = full
        self._computer: Any = Computer()
        self._computer.IsCpuEnabled = True
        self._computer.IsGpuEnabled = True
        self._computer.IsMotherboardEnabled = True
        self._computer.Open()
        # [(hardware, [(sensor, hw_type, hw_name, kind, name)])], built on first read
        self._plan: list[tuple[Any, list[tuple[Any, str, str, str, str]]]] | None = None

    def _build_plan(self) -> list[tuple[Any, list[tuple[Any, str, str, str, str]]]]:
        kinds = KINDS if self._full else LEAN_KINDS
        plan: list[tuple[Any, list[tuple[Any, str, str, str, str]]]] = []

        def visit(hw: Any) -> None:
            hw_type, hw_name = str(hw.HardwareType), str(hw.Name)
            if self._full or hw_type in LEAN_HARDWARE:
                hw.Update()
                sensors = []
                for s in hw.Sensors:
                    kind, name = str(s.SensorType), str(s.Name)
                    lean_ok = (
                        "Distance to TjMax" not in name
                        and not name.startswith("D3D")  # ~20 Direct3D engine loads we never use
                        and (kind != "Power" or name in LEAN_POWER)
                        and (kind != "Clock" or hw_type == "Cpu" or name in LEAN_GPU_CLOCKS)
                    )
                    if kind in kinds and (self._full or lean_ok):
                        sensors.append((s, hw_type, hw_name, kind, name))
                plan.append((hw, sensors))
            for sub in hw.SubHardware:
                visit(sub)

        for hw in self._computer.Hardware:
            visit(hw)
        return plan

    def read(self) -> list[Reading]:
        if self._plan is None:
            self._plan = self._build_plan()
        else:
            for hw, _ in self._plan:
                hw.Update()
        out: list[Reading] = []
        for _, sensors in self._plan:
            for s, hw_type, hw_name, kind, name in sensors:
                v = s.Value
                out.append(Reading(hw_type, hw_name, kind, name, None if v is None else float(v)))
        return out

    def close(self) -> None:
        self._computer.Close()
