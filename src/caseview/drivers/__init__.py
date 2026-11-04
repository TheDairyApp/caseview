"""Display drivers. Third-party drivers register under the `caseview.drivers` entry point."""

from __future__ import annotations

from importlib.metadata import entry_points

from caseview.drivers.base import DisplayDriver
from caseview.drivers.crystalx_v331 import CrystalXV331

BUILTIN: list[type[DisplayDriver]] = [CrystalXV331]


def available_drivers() -> list[type[DisplayDriver]]:
    drivers = list(BUILTIN)
    for ep in entry_points(group="caseview.drivers"):
        cls = ep.load()
        if cls not in drivers:
            drivers.append(cls)
    return drivers


def driver_for(vid: int, pid: int) -> type[DisplayDriver] | None:
    for cls in available_drivers():
        if (vid, pid) in cls.match:
            return cls
    return None
