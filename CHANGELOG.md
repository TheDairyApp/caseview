# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Lock the case display on one page (tray menu, Case display window, phone); `display.locked_page`

## [0.1.0] - 2026-11-04

First public preview.

### Added
- Crystal X V331 case display driver (USB HID `5131:2007`), with a workaround for the fan-readout firmware bug and 2-digit clamping
- Rotating **case display pages** (default Power / CPU / GPU / Temp), editable on desktop and phone
- Read-only sensors via LibreHardwareMonitor (lean, cached, 2 s refresh) plus psutil metrics
- Power and current: measured CPU/GPU watts, 12 V current, estimated PC and wall power/current
- Case RGB via our own OpenRGB SDK client: static, per-zone, temperature steps, breathing, color cycle, rainbow, overheat alert, profiles
- Tray app with dashboard widget, taskbar stats bar, lighting window and case display window
- Themes: Graphite, Light, Aurora, Sunset, Violet
- Optional phone access on the local network, PIN-protected
- Start with Windows (elevated logon task); vendor-app detection with wait/stop policies
- CLI: `tray`, `rgb`, `run`, `sensors`, `list-devices`, `config`, `send`, `test-pattern`

[Unreleased]: https://github.com/OWNER/caseview/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OWNER/caseview/releases/tag/v0.1.0
