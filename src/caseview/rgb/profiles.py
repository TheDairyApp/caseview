"""Named lighting setups, one TOML file each in %APPDATA%/CaseView/profiles."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import tomli_w

from caseview.core.config import ConfigError, default_config_path, rgb_from_dict, rgb_to_dict
from caseview.rgb.effects import RgbSettings

_SAFE_NAME = re.compile(r"^[\w \-]{1,40}$")


def profile_dir() -> Path:
    return default_config_path().parent / "profiles"


def _path(name: str, root: Path | None) -> Path:
    if not _SAFE_NAME.fullmatch(name.strip()):
        raise ConfigError("profile names may use letters, digits, spaces, - and _ (max 40)")
    return (root or profile_dir()) / f"{name.strip()}.toml"


def list_profiles(root: Path | None = None) -> list[str]:
    folder = root or profile_dir()
    return sorted(p.stem for p in folder.glob("*.toml")) if folder.exists() else []


def save_profile(name: str, settings: RgbSettings, root: Path | None = None) -> Path:
    path = _path(name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps({"rgb": rgb_to_dict(settings)}), encoding="utf-8")
    return path


def load_profile(name: str, root: Path | None = None) -> RgbSettings:
    path = _path(name, root)
    if not path.exists():
        raise ConfigError(f"no profile named {name!r}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"profile {name!r} is not valid TOML: {e}") from e
    return rgb_from_dict(data.get("rgb", {}))


def delete_profile(name: str, root: Path | None = None) -> None:
    _path(name, root).unlink(missing_ok=True)
