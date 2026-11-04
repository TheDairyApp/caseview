# Roadmap

## Done

- [x] Crystal X V331 display driver, including the fan-readout firmware fix
- [x] Live sensors via LibreHardwareMonitor (lean, cached, read-only)
- [x] Settings file, reconnect after sleep/unplug, vendor-app hand-off
- [x] Case RGB through our own OpenRGB SDK client: effects, zones, temperature steps, profiles
- [x] Tray app, start with Windows (elevated logon task)
- [x] Dashboard widget, taskbar stats bar, themes
- [x] Phone access on the local network (PIN-protected)
- [x] Power and current readings; rotating case display pages

## Next

- [ ] **Installer:** one signed Windows installer (Nuitka build + Inno Setup), PawnIO prompt, auto-update check
- [ ] **Low-RAM background service:** a C# service for sensors, display, lighting and the phone API; Qt windows start on demand. Target ~35–45 MB. See [low-ram-plan.md](low-ram-plan.md)
- [ ] **More case displays:** community drivers via the `caseview.drivers` entry point ([guide](adding-a-display.md))
- [ ] **Smart-plug power:** read real wall power from energy-monitoring plugs on the local network
- [ ] **Localization:** UI translations
- [ ] **Phone page:** QR code in the tray menu to open it without typing the address

## Ideas

- Custom stats-bar items and order
- Lighting reacting to GPU temperature or load
- Per-application display pages, e.g. a "gaming" page while a game runs

Have a request? Open a [feature request](../../../issues/new/choose).
