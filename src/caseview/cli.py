"""Command-line entry point.

Commands: run, sensors, config, list-devices, send, test-pattern.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from collections.abc import Sequence

from caseview import __version__
from caseview.core import config as config_mod
from caseview.core import conflicts, logs
from caseview.core.config import ConfigError, OnConflict
from caseview.core.engine import Engine, State, Status
from caseview.core.metrics import DISPLAY_SOURCES, SystemMetrics
from caseview.drivers import available_drivers, driver_for
from caseview.drivers.base import DisplayDriver
from caseview.rgb.effects import format_color
from caseview.rgb.output import RgbOutput
from caseview.sensors import SensorProvider, pick_values
from caseview.sensors.fake import FakeProvider
from caseview.transport.hid import HidapiLink, HidError, enumerate_devices


def open_display() -> DisplayDriver:
    for dev in enumerate_devices():
        cls = driver_for(dev.vid, dev.pid)
        if cls:
            return cls(HidapiLink(dev.path))
    raise HidError("no supported display found (run `caseview list-devices`)")


def cmd_list_devices(_: argparse.Namespace) -> int:
    seen: set[str] = set()
    for dev in enumerate_devices():
        cls = driver_for(dev.vid, dev.pid)
        key = f"{dev.usb_id} {dev.usage_page:04X}:{dev.usage:04X}"
        if key in seen:
            continue
        seen.add(key)
        mark = f"  <- {cls.name}" if cls else ""
        desc = " ".join(s for s in (dev.manufacturer, dev.product) if s) or "-"
        print(f"{dev.usb_id}  usage {dev.usage_page:04X}:{dev.usage:04X}  {desc}{mark}")
    print("\nSupported:", ", ".join(f"{c.name} ({c.id})" for c in available_drivers()))
    return 0


def _run(driver: DisplayDriver, frames: Sequence[dict[str, float]], hold_s: float) -> None:
    for values in frames:
        print("  " + "  ".join(f"{k}={v:g}" for k, v in values.items()))
        end = time.monotonic() + hold_s
        while True:
            driver.render(values)
            if time.monotonic() >= end:
                break
            time.sleep(min(driver.update_interval_s, max(0.0, end - time.monotonic())))


def cmd_send(args: argparse.Namespace) -> int:
    driver = open_display()
    values = {"cpu_temp": args.cpu_temp, "cpu_fan": args.cpu_fan, "gpu_temp": args.gpu_temp}
    try:
        _run(driver, [values], args.seconds)
    finally:
        driver.close()
    return 0


def cmd_test_pattern(args: argparse.Namespace) -> int:
    driver = open_display()
    frames: list[dict[str, float]] = [
        {"cpu_temp": 12, "cpu_fan": 3456, "gpu_temp": 78},
        {"cpu_temp": 0, "cpu_fan": 0, "gpu_temp": 0},
        {"cpu_temp": 99, "cpu_fan": 9999, "gpu_temp": 99},
        *(
            {"cpu_temp": d * 11.0, "cpu_fan": d * 1111.0, "gpu_temp": d * 11.0}
            for d in range(1, 10)
        ),
    ]
    try:
        _run(driver, frames, args.hold)
    finally:
        driver.close()
    return 0


def make_provider(fake: bool) -> SensorProvider:
    if fake:
        return FakeProvider()
    from caseview.sensors.lhm import LhmProvider

    return LhmProvider()


def cmd_sensors(args: argparse.Namespace) -> int:
    provider = make_provider(args.fake)
    try:
        readings = provider.read()
    finally:
        provider.close()
    for r in readings:
        if args.all or r.kind in ("Temperature", "Fan"):
            val = "-" if r.value is None else f"{r.value:.1f}"
            print(f"[{r.hardware_type}] {r.hardware} / {r.kind}: {r.name} = {val}")
    cfg = config_mod.load()
    picked = pick_values(readings, {k: v.sensor for k, v in cfg.slots.items()})
    print("\nDisplay would show:", picked or "nothing (no readable sensors)")
    if "cpu_temp" not in picked:
        print("CPU sensors unreadable: run as administrator (PawnIO driver required).")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = config_mod.load()
    if args.stop_dtcontrol:
        cfg.on_conflict = OnConflict.STOP
    log_path = logs.setup(args.verbose)
    if args.no_rgb:
        cfg.rgb.enabled = False
    rgb = RgbOutput(cfg.rgb) if cfg.rgb.enabled else None
    if rgb:
        rgb.start()  # animated effects need their own ~30 fps loop
    engine = Engine(
        make_provider(args.fake), cfg, open_display, rgb=rgb, metrics=SystemMetrics(power=cfg.power)
    )
    print(f"CaseView running (log: {log_path}). Ctrl+C to stop.")

    # State changes are already logged to the console; only print live values here.
    def show(status: Status) -> None:
        if status.state is State.RUNNING and args.verbose:
            shown = "  ".join(f"{k}={v:.0f}" for k, v in status.values.items())
            if status.rgb:
                shown += f"  rgb={format_color(status.rgb)}"
            print(f"  {shown}", flush=True)

    stop = threading.Event()
    try:
        engine.run(stop, show)
    except KeyboardInterrupt:
        stop.set()
    return 0


def cmd_rgb(args: argparse.Namespace) -> int:
    from caseview.ui.rgb_window import main as rgb_main

    logs.setup()
    return rgb_main(fake_sensors=args.fake)


def cmd_tray(args: argparse.Namespace) -> int:
    from caseview.ui.tray import main as tray_main

    return tray_main(fake_sensors=args.fake)


def cmd_config(args: argparse.Namespace) -> int:
    path = config_mod.default_config_path()
    if args.action == "path":
        print(path)
    elif args.action == "slot":
        if args.slot not in ("cpu_temp", "cpu_fan", "gpu_temp") or not args.source:
            sources = "\n".join(f"  {k:12} {v}" for k, v in DISPLAY_SOURCES.items())
            print(
                "usage: caseview config slot <cpu_temp|cpu_fan|gpu_temp> <source>\n"
                "sources: auto, a sensor name from `caseview sensors`, or one of:\n" + sources,
                file=sys.stderr,
            )
            return 2
        cfg = config_mod.load(path)
        cfg.slots[args.slot] = config_mod.SlotConfig(args.source)
        config_mod.save(cfg, path)
        shown = DISPLAY_SOURCES.get(args.source, args.source)
        print(f"{args.slot} now shows {shown} (restart the tray to apply)")
    elif args.action == "init":
        if path.exists() and not args.overwrite:
            print(f"{path} already exists (use --overwrite)", file=sys.stderr)
            return 1
        slots = {"cpu_temp": "auto", "cpu_fan": "auto", "gpu_temp": "auto"}
        cfg = config_mod.Config(slots={k: config_mod.SlotConfig(v) for k, v in slots.items()})
        print(f"wrote {config_mod.save(cfg, path)}")
    else:
        print(f"# {path}{'' if path.exists() else ' (not created yet, showing defaults)'}")
        print(config_mod.load(path).to_toml())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="caseview", description="Drive PC case displays.")
    p.add_argument("--version", action="version", version=f"caseview {__version__}")
    p.add_argument("--force", action="store_true", help="send/test even if DT Control is running")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-devices", help="list HID devices and mark supported displays")

    s = sub.add_parser("send", help="show fixed values on the display")
    s.add_argument("--cpu-temp", type=float, default=0)
    s.add_argument("--cpu-fan", type=float, default=0)
    s.add_argument("--gpu-temp", type=float, default=0)
    s.add_argument("--seconds", type=float, default=10)

    t = sub.add_parser("test-pattern", help="cycle through check values")
    t.add_argument("--hold", type=float, default=3, help="seconds per frame")

    se = sub.add_parser("sensors", help="list sensor readings and the default slot picks")
    se.add_argument("--all", action="store_true", help="include load/clock/power sensors")
    se.add_argument("--fake", action="store_true", help="use simulated sensors")

    r = sub.add_parser("run", help="show live sensor values on the display")
    r.add_argument("--fake", action="store_true", help="use simulated sensors")
    r.add_argument("-v", "--verbose", action="store_true", help="print each update")
    r.add_argument(
        "--stop-dtcontrol", action="store_true", help="close DT Control instead of waiting for it"
    )
    r.add_argument("--no-rgb", action="store_true", help="leave case lighting alone")

    tr = sub.add_parser("tray", help="run in the system tray (display + lighting, all day)")
    tr.add_argument("--fake", action="store_true", help="use simulated sensors")

    g = sub.add_parser("rgb", help="open the lighting window (color wheel, zones, effects)")
    g.add_argument("--fake", action="store_true", help="use simulated sensors")

    c = sub.add_parser("config", help="show or create the settings file")
    c.add_argument("action", choices=["show", "path", "init", "slot"], nargs="?", default="show")
    c.add_argument("slot", nargs="?", help="with `slot`: cpu_temp, cpu_fan or gpu_temp")
    c.add_argument("source", nargs="?", help="with `slot`: what that readout shows")
    c.add_argument("--overwrite", action="store_true")

    args = p.parse_args(argv)
    # `run` handles conflicts itself (waits or stops); one-shot commands just refuse.
    if args.cmd in ("send", "test-pattern") and not args.force:
        found = conflicts.running_conflicts()
        if found:
            names = ", ".join(conflicts.describe(f) for f in found)
            print(
                f"{names} is running and will overwrite the display.\n"
                "Exit it from the tray icon first, or pass --force.",
                file=sys.stderr,
            )
            return 2

    handlers = {
        "list-devices": cmd_list_devices,
        "send": cmd_send,
        "test-pattern": cmd_test_pattern,
        "sensors": cmd_sensors,
        "run": cmd_run,
        "config": cmd_config,
        "rgb": cmd_rgb,
        "tray": cmd_tray,
    }
    try:
        return handlers[args.cmd](args)
    except (HidError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
