# Low-RAM rewrite plan

Goal: the part of CaseView that runs all day uses **about 20–40 MB RAM and close to 0 % CPU**. Windows only cost memory while they are open.

## Where the memory goes today (measured 2026-11-04 on a 20-core Intel desktop with an NVIDIA GPU)

| Piece | Private RAM | Needed all day? |
|---|---|---|
| Python interpreter + our code | ~15 MB | yes, today |
| .NET runtime (pythonnet) + LibreHardwareMonitor | ~40 MB | yes, for sensors |
| Qt (tray, stats bar, windows) | ~45 MB | only the tray icon and stats bar |
| **Total tray + stats bar** | **~100 MB** | |

So two runtimes (Python, .NET) and one big UI toolkit (Qt) are loaded all the time, just to show a tray icon and a strip of numbers.

## Target architecture: one small service + windows on demand

```
caseviewd.exe  (C#, .NET 8, always running, started at logon with admin rights)
  ├─ sensors        LibreHardwareMonitorLib used directly, no Python bridge
  ├─ display        HID 0x40 packets to the Crystal X (port of crystalx_v331.py)
  ├─ lighting       OpenRGB SDK client + effects (port of rgb/*.py)
  ├─ tray icon      raw Win32 Shell_NotifyIcon, no WinForms/WPF
  ├─ stats bar      tiny Win32 layered window drawn with GDI
  └─ local API      the existing JSON API: phone page + desktop windows
        ▲
        │ http://127.0.0.1 (PIN) / home network (phone, PIN)
        │
caseview-ui (current Python + Qt app, started only when you open a window)
  ├─ Lighting window, Dashboard, Settings
  └─ exits when its last window closes → its RAM goes back to 0
```

Why this split:
- **One runtime all day.** LibreHardwareMonitor is a .NET library, so a C# service uses it directly. That removes Python and pythonnet from the always-on part.
- **No UI toolkit all day.** The tray icon and the stats bar are drawn with plain Win32/GDI, a few MB at most.
- **Keep the UI we just built.** The Qt windows already look and work right. They become a client of the service's JSON API, which the phone page already uses, so there is no second UI to keep in sync.
- **The phone page is unchanged**, served by the service.

Expected memory, to be confirmed by the spike in step 0:

| Mode | Expected private RAM |
|---|---|
| Service only (tray + stats bar + display + lighting + phone) | ~25–40 MB |
| Service + Lighting or Dashboard window open | +~60 MB, freed when closed |

Options for later, if 25–40 MB is still too much:
- **.NET Native AOT** compiles the service to native code. It could drop to ~15–25 MB, but LibreHardwareMonitor relies on reflection and WMI, which AOT may not support. The spike tests this.
- **Rust service.** About 5–10 MB, but it can't use LibreHardwareMonitor. We would have to write our own Intel MSR / Super I/O fan chip / NVML readers on top of PawnIO. That's months of work and support for fewer boards, so **not recommended**.

## Spike results (2026-11-04, step 0 done)

`spike/lhm-probe`: a C# console app reading the same lean sensor set every 2 s. Measured the same way as the Python numbers (non-admin, 15 s warm-up, 30 s window).

| Build | RAM (working set) | Private | CPU (one core) |
|---|---|---|---|
| Python today: `caseview run`, sensors + display, no UI | 91 MB | 68 MB | 2.6 % |
| C#, framework-dependent | 62 MB | **32 MB** | 2.0 % |
| C#, trimmed self-contained | 58 MB | **35 MB** | 1.7 % |
| C#, Native AOT | not built | | |

- **Go.** C# halves private memory for the same work (68 → 32 MB) and uses less CPU.
- Adding the Win32 tray icon, stats bar, HID, OpenRGB client and HTTP server should cost a few MB more, so the full service lands at about 35–45 MB private, against today's ~100 MB.
- Native AOT was not built: it needs Visual Studio's C++ build tools, a download of several GB. Since the normal build already meets the target, AOT stays optional.

## Steps

| # | Step | Done when |
|---|---|---|
| 0 | **Spike.** Install the .NET 8 SDK. Build a minimal console app that reads CPU temp, fan and GPU temp with LibreHardwareMonitor every 2 s. Measure RAM and CPU, normal and AOT-trimmed. | Numbers in this doc. Go/no-go on C#, and AOT yes/no |
| 1 | Service skeleton: logon task (admin), single instance, log file, config.toml read/write (same file and format as today) | Starts, reads your existing settings |
| 2 | Port the sensors: lean sensor list, 2 s interval, default slot picks, the same tests ported to xUnit | Same picks as the Python `caseview sensors` |
| 3 | Port the display: HID writes, 2-digit clamp, fan tens-digit fix, DT Control wait/stop, reconnect | Display live from the service; DT Control hand-off works |
| 4 | Port the lighting: OpenRGB client, effects (static, temperature steps, breathing, cycle, rainbow, alert), profiles, own 30 fps thread | Lights match the Python version |
| 5 | Local API: the current endpoints plus the ones the desktop windows need (state, settings, history) | Phone page works against the service |
| 6 | Tray icon + stats bar in Win32/GDI; menu opens the UI process | Menu and bar look like today's |
| 7 | Turn the Qt windows into API clients; start `caseview-ui` on demand; it exits when its windows close | Lighting/Dashboard windows work; RAM drops after closing |
| 8 | Packaging: one installer (service exe + UI), PawnIO prompt, signing, auto-update | Clean install on a fresh Windows 11 |

Steps 2–4 port code that already has tests, so each port is checked against the Python behaviour, including the fan-digit firmware model and real-hardware sensor readings.

## Risks

- **AOT and LibreHardwareMonitor:** may not work. Fallback is a normal trimmed .NET 8 build, still about half of today's RAM.
- **Two languages in one repo:** the C# service becomes the source of truth for hardware logic. Python keeps only the UI.
- **Stats bar on the taskbar:** the Win32 version has the same limit as now (a topmost window, not a real taskbar widget).
