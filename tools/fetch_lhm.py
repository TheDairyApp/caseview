"""Download LibreHardwareMonitorLib (MPL-2.0) from NuGet into vendor/lhm/.

Run: python -I tools/fetch_lhm.py
The DLLs are not committed; CI and the installer build run this script.
"""

from __future__ import annotations

import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

# LibreHardwareMonitorLib 0.9.6 and its .NET Framework 4.7.2 dependencies.
# System.Management ships with .NET Framework itself.
PACKAGES = {
    "librehardwaremonitorlib": "0.9.6",
    "hidsharp": "2.6.4",
    "diskinfotoolkit": "1.1.2",
    "ramspdtoolkit-ndd": "1.4.2",
    "system.memory": "4.6.3",
    "system.buffers": "4.6.1",
    "system.numerics.vectors": "4.6.1",
    "system.runtime.compilerservices.unsafe": "6.1.2",
    "system.threading.accesscontrol": "10.0.3",
}
TFM_PREFERENCE = ["net472", "net471", "net47", "net462", "net461", "netstandard2.0"]
DEST = Path(__file__).resolve().parent.parent / "vendor" / "lhm"


def fetch(pkg: str, version: str) -> None:
    url = f"https://api.nuget.org/v3-flatcontainer/{pkg}/{version}/{pkg}.{version}.nupkg"
    data = urllib.request.urlopen(url, timeout=60).read()
    print(f"{pkg} {version}: {len(data)} bytes sha256={hashlib.sha256(data).hexdigest()}")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        for name in names:
            if name.split("/")[-1].lower() in {"license", "license.txt", "license.md"}:
                (DEST / f"{pkg}.LICENSE.txt").write_bytes(z.read(name))
        # Newer packages put Windows builds under runtimes/win-x64/lib/<tfm>/.
        for root in ("runtimes/win-x64/lib/", "lib/"):
            tfms = {n[len(root) :].split("/")[0] for n in names if n.startswith(root)}
            tfm = next((t for t in TFM_PREFERENCE if t in tfms), None)
            if tfm:
                break
        else:
            raise SystemExit(f"{pkg}: no usable target framework")
        for name in names:
            if name.startswith(f"{root}{tfm}/") and name.lower().endswith(".dll"):
                (DEST / name.split("/")[-1]).write_bytes(z.read(name))
                print(f"  -> {name.split('/')[-1]} ({root}{tfm})")


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    for pkg, version in PACKAGES.items():
        fetch(pkg, version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
