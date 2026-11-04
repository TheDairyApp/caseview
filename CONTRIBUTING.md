# Contributing to CaseView

Thanks for helping! Bug reports, display drivers, docs and code are all welcome.

## Ground rules

- **Read-only hardware access.** CaseView reads sensors and writes only to displays and lighting. Pull requests that change fan curves, clocks, voltages or power limits won't be accepted. A test enforces this.
- **Only send known commands to devices.** Display drivers may only send byte patterns observed from the vendor's own software. No guessed or fuzzed commands on users' hardware.
- **No vendor code or assets.** Don't copy code, DLLs, images or logos from vendor apps. Protocol support must come from your own observation (e.g. USB captures).
- **No personal data in issues, logs or tests.** Remove serial numbers, user names and file paths before posting.

## Development setup

```powershell
git clone https://github.com/OWNER/caseview.git
cd caseview
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -I tools/fetch_lhm.py
```

Run the checks before opening a pull request. CI runs the same ones:

```powershell
ruff check .
ruff format --check .
mypy src
pytest
```

UI tests run headless (`QT_QPA_PLATFORM=offscreen`); you don't need the hardware.

## Project layout

| Path | What lives there |
|---|---|
| `src/caseview/drivers/` | Display drivers ([adding a display](docs/adding-a-display.md)) |
| `src/caseview/sensors/` | Sensor providers (LibreHardwareMonitor, fake) |
| `src/caseview/rgb/` | OpenRGB SDK client, effects, profiles |
| `src/caseview/core/` | Engine, config, metrics, phone server, autostart |
| `src/caseview/ui/` | PySide6 windows and widgets |
| `src/caseview/web/` | Phone page (single HTML file, no build step) |
| `tests/` | pytest suite |
| `tools/` | Build helpers |
| `spike/` | Experiments (not shipped) |

## Pull requests

1. Open an issue first for large changes, so we can agree on the approach.
2. Keep each PR focused: one feature or fix.
3. Add or update tests. New display drivers need encoding tests.
4. Update the docs (`README.md`, `docs/`) and `CHANGELOG.md` under **Unreleased**.
5. Use clear commit messages: an imperative summary line, then the why.

## Reporting bugs

Use the **Bug report** issue form. Include:
- CaseView version (`caseview --version`), Windows version
- the relevant part of `%APPDATA%\CaseView\logs\caseview.log` (check it for personal info first)
- `caseview list-devices` and `caseview sensors` output, if it's hardware related

## Code style

- Python 3.12, type hints everywhere (`mypy --strict`), ruff for lint and formatting (line length 100).
- Prefer small, pure functions for logic (effects, encoding, metrics) so they can be unit-tested.
- Comments explain *why*, not *what*.

## License

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
