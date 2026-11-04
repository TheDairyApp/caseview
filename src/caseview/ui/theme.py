"""Customizable theme: card-based layout, rounded corners, accent color, optional glass blur.

Stored in %APPDATA%/CaseView/theme.toml. "Glass" (Windows 11 acrylic blur behind the
window) is opt-in because translucent windows repaint more slowly; without it the
window uses solid colors derived from the tint.
"""

from __future__ import annotations

import ctypes
import sys
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

import tomli_w
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from caseview.core.config import default_config_path
from caseview.rgb.effects import Color


@dataclass(frozen=True)
class Theme:
    name: str = "Graphite"
    dark: bool = True
    accent: Color = (10, 132, 255)
    tint: Color = (28, 29, 34)  # window background
    glass: int = 70  # card opacity % when blur is on
    radius: int = 12  # corner radius px
    blur: bool = False  # Windows 11 acrylic backdrop (slower)
    font_size: int = 10


PRESETS: dict[str, Theme] = {
    t.name: t
    for t in (
        Theme(),
        Theme("Light", dark=False, accent=(0, 110, 230), tint=(243, 244, 247)),
        Theme("Aurora", accent=(40, 200, 160), tint=(18, 30, 36)),
        Theme("Sunset", accent=(255, 122, 69), tint=(36, 26, 30)),
        Theme("Violet", accent=(170, 110, 255), tint=(24, 22, 32)),
    )
}


def theme_path() -> Path:
    return default_config_path().parent / "theme.toml"


def load_theme(path: Path | None = None) -> Theme:
    """The saved preset, or the default if none/unknown/broken."""
    path = path or theme_path()
    try:
        name = tomllib.loads(path.read_text(encoding="utf-8")).get("theme", {}).get("name")
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return Theme()  # a missing or broken theme file must never stop the app
    return PRESETS.get(str(name), Theme())


def save_theme(theme: Theme, path: Path | None = None) -> Path:
    path = path or theme_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps({"theme": {"name": theme.name}}), encoding="utf-8")
    return path


# ------------------------------------------------------------------- colors


@dataclass(frozen=True)
class Palette:
    bg: QColor
    card: QColor
    field: QColor
    hover: QColor
    border: QColor
    text: QColor
    muted: QColor
    accent: QColor
    on_accent: QColor


def _mix(a: Color, b: Color, t: float) -> Color:
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def palette_for(t: Theme) -> Palette:
    white, black = (255, 255, 255), (0, 0, 0)
    lift = white if t.dark else black
    glass = t.glass / 100 if t.blur else 1.0

    def c(rgb: Color, alpha: float = 1.0) -> QColor:
        q = QColor(*rgb)
        q.setAlphaF(max(0.0, min(1.0, alpha)))
        return q

    card = _mix(t.tint, white, 0.06) if t.dark else white
    return Palette(
        bg=c(t.tint, 0.55 if t.blur else 1.0),
        card=c(card, glass),
        field=c(_mix(t.tint, white, 0.11) if t.dark else _mix(t.tint, black, 0.035), glass),
        hover=c(_mix(t.tint, white, 0.16) if t.dark else _mix(t.tint, black, 0.07), glass),
        border=c(lift, 0.09 if t.dark else 0.10),
        text=c((240, 240, 243) if t.dark else (29, 29, 31)),
        muted=c((150, 152, 160) if t.dark else (110, 112, 120)),
        accent=c(t.accent),
        on_accent=c(white),
    )


def _css(q: QColor) -> str:
    return f"rgba({q.red()}, {q.green()}, {q.blue()}, {q.alphaF():.3f})"


def _arrow_svg(color: QColor) -> str:
    """Write a small chevron in `color` to the cache folder and return its path for QSS."""
    hexc = f"{color.red():02X}{color.green():02X}{color.blue():02X}"
    path = default_config_path().parent / "cache" / f"chevron-{hexc}.svg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6" viewBox="0 0 10 6">'
            f'<path d="M1 1l4 4 4-4" fill="none" stroke="#{hexc}" stroke-width="1.6" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>',
            encoding="utf-8",
        )
    return path.as_posix()


def stylesheet(t: Theme) -> str:
    p = palette_for(t)
    r, rs = t.radius, max(min(t.radius, 10), 4)
    bg, card, field, hover = _css(p.bg), _css(p.card), _css(p.field), _css(p.hover)
    border, text, muted, accent = _css(p.border), _css(p.text), _css(p.muted), _css(p.accent)
    accent_soft = _css(QColor(p.accent.red(), p.accent.green(), p.accent.blue(), 60))
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
     font-size: {t.font_size}pt; color: {text}; outline: none; }}
QWidget#GlassRoot, QDialog {{ background: {bg}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QLabel {{ background: transparent; }}
QLabel#Title {{ font-size: {t.font_size + 5}pt; font-weight: 600; }}
QLabel#CardTitle {{ font-size: {t.font_size + 1}pt; font-weight: 600; }}
QLabel#Big {{ font-size: {t.font_size + 3}pt; font-weight: 600; }}
QLabel#Muted, QLabel#Value {{ color: {muted}; }}
QLabel#Pill {{ border-radius: 11px; padding: 3px 10px; background: {field}; color: {muted}; }}
QLabel#Pill[state="ok"] {{ color: rgb(48, 209, 88); }}
QLabel#Pill[state="warn"] {{ color: rgb(255, 159, 10); }}

QFrame#Card {{ background: {card}; border: 1px solid {border}; border-radius: {r}px; }}
QFrame#Divider {{ background: {border}; max-height: 1px; min-height: 1px; border: none; }}

QPushButton {{ background: {field}; border: 1px solid {border}; border-radius: {rs}px;
    padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ background: {hover}; }}
QPushButton:pressed {{ background: {accent_soft}; }}
QPushButton:disabled {{ color: {muted}; }}
QPushButton#Primary {{ background: {accent}; border: 1px solid {accent}; color: white;
    font-weight: 600; padding: 7px 18px; }}
QPushButton#Primary:hover {{ background: {_css(p.accent.lighter(112))}; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {accent}; padding: 4px 6px; }}
QPushButton#Ghost:hover {{ background: {accent_soft}; }}
QPushButton#Icon {{ background: transparent; border: none; padding: 2px; min-width: 28px;
    min-height: 28px; border-radius: 14px; color: {muted}; font-size: {t.font_size + 4}pt; }}
QPushButton#Icon:hover {{ background: {hover}; color: {text}; }}

QComboBox, QLineEdit {{
    background: {field}; border: 1px solid {border}; border-radius: {rs}px;
    padding: 5px 10px; min-height: 18px; selection-background-color: {accent}; }}

QComboBox:hover, QLineEdit:hover {{ background: {hover}; }}
QLineEdit:disabled {{ color: {muted}; }}
QComboBox:focus, QLineEdit:focus {{
    border: 1px solid {accent}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url("{_arrow_svg(p.muted)}"); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{ background: {_css(p.card.toRgb())}; border: 1px solid {border};
    border-radius: {rs}px; padding: 4px; selection-background-color: {accent_soft};
    selection-color: {text}; }}

QListWidget {{ background: transparent; border: none; }}
QListWidget::item {{ padding: 9px 10px; margin: 1px 0; border-radius: {rs}px; }}
QListWidget::item:hover {{ background: {hover}; }}
QListWidget::item:selected {{ background: {accent_soft}; color: {text}; }}

QSlider {{ min-height: 22px; }}
QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {hover}; }}
QSlider::sub-page:horizontal {{ height: 4px; border-radius: 2px; background: {accent}; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
    background: white; border: 1px solid rgba(0, 0, 0, 0.25); }}

QToolTip {{ background: {_css(p.card.toRgb())}; color: {text}; border: 1px solid {border};
    border-radius: 6px; padding: 4px 8px; }}
QScrollBar:vertical {{ width: 10px; background: transparent; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {hover}; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


# ------------------------------------------------------------- Windows blur

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMSBT_NONE, _DWMSBT_TRANSIENT = 1, 3  # 3 = acrylic


class _Margins(ctypes.Structure):
    _fields_ = [(n, ctypes.c_int) for n in ("left", "right", "top", "bottom")]


def _dwm(widget: QWidget, theme: Theme) -> bool:
    """Dark title bar, rounded corners and (if asked) acrylic. True if blur is active."""
    if sys.platform != "win32":
        return False
    try:
        dwm = ctypes.windll.dwmapi
        hwnd = ctypes.c_void_p(int(widget.winId()))

        def attr(key: int, value: int) -> int:
            v = ctypes.c_int(value)
            return int(dwm.DwmSetWindowAttribute(hwnd, key, ctypes.byref(v), ctypes.sizeof(v)))

        attr(_DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if theme.dark else 0)
        attr(_DWMWA_WINDOW_CORNER_PREFERENCE, 2)  # round
        if not theme.blur:
            attr(_DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_NONE)
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margins(0, 0, 0, 0)))
            return False
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margins(-1, -1, -1, -1)))
        return attr(_DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_TRANSIENT) == 0
    except (AttributeError, OSError):
        return False


def apply_theme(window: QWidget, theme: Theme) -> bool:
    """Style `window` and its children. Returns True if real blur is active."""
    p = palette_for(theme)
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, p.bg.toRgb())
    pal.setColor(QPalette.ColorRole.WindowText, p.text)
    pal.setColor(QPalette.ColorRole.Text, p.text)
    pal.setColor(QPalette.ColorRole.Highlight, p.accent)
    pal.setColor(QPalette.ColorRole.PlaceholderText, p.muted)
    app = QApplication.instance()
    if isinstance(app, QApplication):
        app.setPalette(pal)
    window.setPalette(pal)
    window.setObjectName("GlassRoot")
    window.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    blurred = _dwm(window, theme)
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, blurred)
    window.setStyleSheet(stylesheet(theme if blurred else replace(theme, blur=False)))
    return blurred
