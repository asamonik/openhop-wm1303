"""Resolve OpenHop configuration paths for the WM1303 overlay.

Install and upgrade physically migrate legacy directories to
``/etc/openhop_repeater``. The read fallback remains for code launched directly
against an older, not-yet-migrated device. A returned legacy path is the existing
file's location; callers writing it do not implicitly migrate that file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

__all__ = [
    "CONFIG_DIR",
    "LEGACY_CONFIG_DIR",
    "resolve_config_path",
    "config_dir",
    "legacy_config_dir",
]

#: Canonical (OpenHop) config directory. Used for all future writes.
CONFIG_DIR: Path = Path("/etc/openhop_repeater")

#: Legacy config directory (pre-v2.6.0). Kept as a read-fallback only.
LEGACY_CONFIG_DIR: Path = Path("/etc/pymc_repeater")


def resolve_config_path(name: Union[str, Path]) -> Path:
    """Return the best available path for a config file named ``name``.

    Resolution order:

    1. ``/etc/openhop_repeater/<name>`` if that file exists (canonical location).
    2. ``/etc/pymc_repeater/<name>`` if only the legacy file exists
       (backwards compatibility for devices upgraded from v2.5.x).
    3. ``/etc/openhop_repeater/<name>`` as the default when neither exists
       (future writes always go to the canonical location).

    Existing legacy files remain readable until the installer migrates them.
    When neither file exists, new files use the canonical location.

    Parameters
    ----------
    name : str or pathlib.Path
        Basename of the config file (e.g. ``"wm1303_ui.json"``,
        ``"config.yaml"``, ``"version"``). Sub-paths are supported.

    Returns
    -------
    pathlib.Path
        Resolved absolute path to the config file.

    Examples
    --------
    >>> resolve_config_path("wm1303_ui.json")  # doctest: +SKIP
    PosixPath('/etc/openhop_repeater/wm1303_ui.json')

    >>> resolve_config_path("version").read_text().strip()  # doctest: +SKIP
    '2.6.2'
    """
    rel = Path(name)
    openhop_path = CONFIG_DIR / rel
    if openhop_path.exists():
        return openhop_path
    legacy_path = LEGACY_CONFIG_DIR / rel
    if legacy_path.exists():
        return legacy_path
    # Neither exists: default to the canonical location so future writes land
    # in /etc/openhop_repeater/ rather than perpetuating the legacy layout.
    return openhop_path


def config_dir() -> Path:
    """Return the canonical config directory (``/etc/openhop_repeater``).

    Prefer this over hardcoding the path.
    """
    return CONFIG_DIR


def legacy_config_dir() -> Path:
    """Return the legacy config directory (``/etc/pymc_repeater``).

    Kept for compatibility with callers inspecting older devices.
    New installations write to the canonical directory.
    """
    return LEGACY_CONFIG_DIR
