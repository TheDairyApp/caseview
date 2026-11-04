# Configuration reference

CaseView keeps its settings in `%APPDATA%\CaseView\`:

| File | Contents |
|---|---|
| `config.toml` | All settings below |
| `theme.toml` | Selected UI theme |
| `profiles\*.toml` | Saved lighting profiles |
| `logs\caseview.log` | Rotating log (1 MB × 3) |

`caseview config show` prints the effective settings, and `caseview config init` writes a starter file. Invalid values are rejected with a clear message rather than ignored.

## `[display]`

| Key | Default | Meaning |
|---|---|---|
| `on_conflict` | `"wait"` | Vendor display app running: `"wait"` until it exits, or `"stop"` it (needs admin) |
| `reconnect_interval_s` | `3.0` | Retry delay after the display is unplugged or a write fails |
| `sensor_interval_s` | `2.0` | How often hardware sensors are read (0.5–30). Lower = more CPU |
| `page_seconds` | `5.0` | How long each display page shows (1–600) |

## `[[pages]]`

Each page names what the three readouts show. Readout keys follow the labels printed on the Crystal X: `cpu_temp` (2 digits), `cpu_fan` (4 digits), `gpu_temp` (2 digits).

```toml
[[pages]]
name = "GPU"
enabled = true
cpu_temp = "gpu_load"
cpu_fan = "gpu_clock"
gpu_temp = "gpu_vram_pct"
```

Sources:

| Key | Value | Key | Value |
|---|---|---|---|
| `cpu_temp` | CPU temperature °C | `cpu_clock` | CPU clock MHz (fastest core) |
| `gpu_temp` | GPU temperature °C | `gpu_clock` | GPU core clock MHz |
| `gpu_hotspot` | GPU hot spot °C | `gpu_mem_clock` | GPU memory clock MHz |
| `cpu_fan` | CPU fan rpm | `cpu_power` / `gpu_power` | Watts (measured) |
| `cpu_load` / `gpu_load` | Usage % | `total_power` / `wall_power` | Watts (estimated) |
| `ram_pct` | RAM used % | `cpu_amps` / `gpu_amps` / `total_amps` | Current at 12 V |
| `gpu_vram_pct` | VRAM used % | `wall_amps` | Wall current (estimated) |
| `disk_pct` | Disk C: used % | | |

## `[slots.<readout>]`

Which **hardware sensor** feeds the `cpu_temp`, `cpu_fan` and `gpu_temp` values. `"auto"` picks CPU Package, the motherboard's CPU fan header, and GPU Core.

```toml
[slots.cpu_fan]
sensor = "Fan #2"          # a name from `caseview sensors`, or the full "hardware/Kind/Name" key
```

## `[power]`

| Key | Default | Meaning |
|---|---|---|
| `mains_voltage` | `230` | Wall voltage (120 in North America) |
| `psu_efficiency` | `0.90` | Used for the wall-power estimate |
| `other_watts` | `50` | Unmeasured parts: board, RAM, drives, fans, RGB |

## `[rgb]`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Control lighting through OpenRGB |
| `device` | `""` | Part of the OpenRGB device name; empty = first device |
| `effect` | `"temperature"` | `static`, `temperature`, `breathing`, `color_cycle`, `rainbow`, `off` |
| `brightness` | `100` | 0–100 |
| `speed` | `5` | 1–10, animated effects |
| `color` | `"#0078FF"` | Static / breathing color |
| `zone_colors` | `{}` | Static per-zone colors, e.g. `{ "ARGB header" = "#00FFAA" }` |
| `source` | `"cpu_temp"` | Which value drives the temperature effect and alert |
| `temp_stops` | blue → red, 40–90 °C | `[[temperature, "#RRGGBB"], …]` |
| `temp_blend` | `true` | Blend between steps, or one solid color per band |
| `alert_temp` | `90` | Flash `alert_color` at or above this; 0 = off |

## `[ui]`

| Key | Default | Meaning |
|---|---|---|
| `stats_bar` | `true` | Show the taskbar stats bar |
| `stats_bar_pos` | unset | Remembered `[x, y]` position |
| `web_enabled` | `false` | Phone access on the local network |
| `web_port` | `8765` | Phone access port |
| `web_pin` | generated | 6-digit PIN |
