"""JSON-backed settings store with dot-notation keys and debounced autosave.

Design notes
------------
* The on-disk shape is nested (``{"display": {"brightness": 0.9}}``) while the
  public API is flat (``"display.brightness"``).
* Writes are debounced: the first change starts a 2 s timer, further changes
  keep it running, and :meth:`update` flushes when it expires. The kernel also
  calls :meth:`flush` on quit.
* A corrupt or unreadable file never raises. It is quarantined next to the
  original and replaced with defaults, and the failure is logged.
* Every value is validated against :data:`SCHEMA`; invalid writes are rejected
  so a bad slider value can never poison the file.
"""

from __future__ import annotations

import json
import logging
import os
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger("phoneos.settings")

AUTOSAVE_DELAY = 2.0
_MISSING = object()
_INVALID = object()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = PROJECT_ROOT / "data" / "settings.json"

DEFAULTS: dict[str, Any] = {
    "display": {
        "brightness": 0.9,
        "accent": "cyan",
        "mode": "dark",
        "font_scale": 1.0,
        "power_saver": False,
        "wallpaper": "flow",
        "fx_crt": False,
        "window_mode": "windowed",
        "scaling": "fit",
        "orientation": "portrait",
        "device_frame": False,
        "refresh_rate": 60,
        "vsync": False,
        "window_width": 0,
        "window_height": 0,
        "theme_pack": "auto",
        "wallpaper_speed": 1.0,
        "wallpaper_density": 1.0,
        "wallpaper_palette": "theme",
        "wallpaper_color1": "#5adcff",
        "wallpaper_color2": "#7f78ff",
        "wallpaper_seed": 137,
        "wallpaper_random_unlock": False,
        "wallpaper_power_saver_disable": True,
        "rotation_locked": False,
    },
    "sound": {
        "master_volume": 0.6,
        "muted": False,
    },
    "simulation": {
        "event_rate": "normal",
        "calls_per_hour": 0.25,
        "notification_rate": 6,
        "drain_multiplier": 1.0,
        "clock_speed": 1,
        "dnd": False,
        "dnd_start": 22,
        "dnd_end": 7,
        "auto_reboot": False,
    },
    "security": {
        "pin": "0000",
        "pin_enabled": True,
        "failed_attempts": 0,
    },
    "device": {
        "name": "Nova One",
    },
    "weather": {
        "city": "Lisbon",
    },
    "quick": {
        "wifi": True,
        "bluetooth": False,
        "airplane": False,
        "flashlight": False,
        "screen_recording": False,
        "dnd": False,
        "hotspot": False,
        "wifi_network": "HomeNet",
        "bluetooth_device": "",
    },
    "accessibility": {
        "reduce_motion": False,
        "high_contrast": False,
        "large_touch": False,
        "colorblind_accents": False,
    },
    "system": {
        "first_boot": 0.0,
        "last_boot": 0.0,
        "onboarded": True,
    },
}


@dataclass(frozen=True, slots=True)
class Field:
    """Validation rule for one settings key."""

    kind: type
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] | None = None
    length: int | None = None


SCHEMA: dict[str, Field] = {
    "display.brightness": Field(float, 0.15, 1.0),
    "display.accent": Field(str),
    "display.mode": Field(str, choices=("dark", "light")),
    "display.font_scale": Field(float, 0.85, 1.35),
    "display.power_saver": Field(bool),
    "display.wallpaper": Field(str, choices=("flow", "plasma", "constellation", "aurora", "ocean", "forest", "grid", "rings", "sunburst", "synthwave", "mountains")),
    "display.fx_crt": Field(bool),
    "display.window_mode": Field(str, choices=("windowed", "borderless", "fullscreen")),
    "display.scaling": Field(str, choices=("fit", "stretch", "integer", "fill")),
    "display.orientation": Field(str, choices=("portrait", "landscape", "auto")),
    "display.device_frame": Field(bool),
    "display.refresh_rate": Field(int, 30, 120),
    "display.vsync": Field(bool),
    "display.window_width": Field(int, 0, 2160),
    "display.window_height": Field(int, 0, 4800),
    "display.theme_pack": Field(str),
    "display.wallpaper_speed": Field(float, 0.1, 3.0),
    "display.wallpaper_density": Field(float, 0.1, 2.0),
    "display.wallpaper_palette": Field(str, choices=("theme", "custom")),
    "display.wallpaper_color1": Field(str),
    "display.wallpaper_color2": Field(str),
    "display.wallpaper_seed": Field(int, 0, 999999),
    "display.wallpaper_random_unlock": Field(bool),
    "display.wallpaper_power_saver_disable": Field(bool),
    "display.rotation_locked": Field(bool),
    "sound.master_volume": Field(float, 0.0, 1.0),
    "sound.muted": Field(bool),
    "simulation.event_rate": Field(str, choices=("off", "low", "normal", "high")),
    "simulation.calls_per_hour": Field(float, 0.0, 6.0),
    "simulation.notification_rate": Field(int, 0, 60),
    "simulation.drain_multiplier": Field(float, 0.25, 4.0),
    "simulation.clock_speed": Field(int, 1, 60),
    "simulation.dnd": Field(bool),
    "simulation.dnd_start": Field(int, 0, 23),
    "simulation.dnd_end": Field(int, 0, 23),
    "simulation.auto_reboot": Field(bool),
    "security.pin": Field(str, length=4),
    "security.pin_enabled": Field(bool),
    "security.failed_attempts": Field(int, 0, 999),
    "device.name": Field(str),
    "weather.city": Field(str, choices=("Lisbon", "Tokyo", "New York", "Sydney", "Reykjavik")),
    "quick.wifi": Field(bool),
    "quick.bluetooth": Field(bool),
    "quick.airplane": Field(bool),
    "quick.flashlight": Field(bool),
    "quick.screen_recording": Field(bool),
    "quick.dnd": Field(bool),
    "quick.hotspot": Field(bool),
    "quick.wifi_network": Field(str, choices=("HomeNet", "Cafe Guest", "PhoneOS Lab", "Neighbour 5G")),
    "quick.bluetooth_device": Field(str, choices=("", "AirPods Pro", "WH-1000XM5", "Car Audio", "Desk Speaker")),
    "accessibility.reduce_motion": Field(bool),
    "accessibility.high_contrast": Field(bool),
    "accessibility.large_touch": Field(bool),
    "accessibility.colorblind_accents": Field(bool),
    "system.first_boot": Field(float),
    "system.last_boot": Field(float),
    "system.onboarded": Field(bool),
}


def _lookup(data: dict[str, Any], key: str) -> Any:
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _assign(data: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _merge(defaults: dict[str, Any], loaded: Any) -> dict[str, Any]:
    """Overlay ``loaded`` onto ``defaults``, recursing into mapping nodes."""
    result = deepcopy(defaults)
    if not isinstance(loaded, dict):
        return result
    for key, value in loaded.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


Listener = Callable[[str, Any], None]


class SettingsService:
    """Mutable, validated, autosaving settings for the virtual device."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_PATH
        self._data: dict[str, Any] = deepcopy(DEFAULTS)
        self._listeners: list[Listener] = []
        self._pending = False
        self._cooldown = 0.0
        self._load()

    # -- lifecycle -----------------------------------------------------------
    def switch_path(self, path: Path | str) -> None:
        """Flush, move to another settings file, and reload it."""
        self.flush()
        self.path = Path(path)
        self._data = deepcopy(DEFAULTS)
        self._load()
        self._notify("__reload__", None)

    def _load(self) -> None:
        if not self.path.exists():
            self._data = deepcopy(DEFAULTS)
            stamp = time.time()
            self._data["system"]["first_boot"] = stamp
            self._data["system"]["last_boot"] = stamp
            self._data["system"]["onboarded"] = False
            self.save_now()
            LOG.info("created settings file at %s", self.path)
            return

        try:
            raw_text = self.path.read_text(encoding="utf-8")
            raw = json.loads(raw_text)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            LOG.error("settings file %s is unreadable (%s); restoring defaults", self.path, exc)
            self._quarantine()
            self._data = deepcopy(DEFAULTS)
            stamp = time.time()
            self._data["system"]["first_boot"] = stamp
            self._data["system"]["last_boot"] = stamp
            self.save_now()
            return

        if not isinstance(raw, dict):
            LOG.error("settings file %s does not contain an object; restoring defaults", self.path)
            self._quarantine()
            self._data = deepcopy(DEFAULTS)
            self.save_now()
            return

        merged = _merge(DEFAULTS, raw)
        for key in SCHEMA:
            value = _lookup(merged, key)
            coerced = self._coerce(key, value)
            if coerced is _INVALID:
                fallback = _lookup(DEFAULTS, key)
                LOG.warning("invalid stored value for %s (%r); using default", key, value)
                _assign(merged, key, deepcopy(fallback if fallback is not _MISSING else None))
            else:
                _assign(merged, key, coerced)

        system = merged.get("system")
        if isinstance(system, dict):
            if not system.get("first_boot"):
                system["first_boot"] = time.time()
            system["last_boot"] = time.time()

        self._data = merged
        LOG.info("loaded settings from %s", self.path)

    def _quarantine(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.stem}.corrupt-{stamp}.json")
        try:
            self.path.replace(target)
            LOG.warning("moved corrupt settings to %s", target)
        except OSError as exc:
            LOG.error("could not quarantine %s: %s", self.path, exc)

    def update(self, dt: float) -> None:
        """Advance the autosave debounce; call once per simulation step."""
        if not self._pending:
            return
        self._cooldown += max(0.0, float(dt))
        if self._cooldown >= AUTOSAVE_DELAY:
            self.save_now()

    def save_now(self) -> None:
        """Write immediately (atomic tmp + replace)."""
        payload = deepcopy(self._data)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                # No fsync: settings are tiny and the simulator must not hitch
                # for a disk barrier. os.replace still gives atomic updates.
                handle.flush()
            os.replace(temporary, self.path)
            LOG.debug("settings saved to %s", self.path)
        except OSError as exc:
            LOG.error("could not save settings to %s: %s", self.path, exc)
        finally:
            self._pending = False
            self._cooldown = 0.0
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def flush(self) -> None:
        """Persist any pending change (called on application quit)."""
        if self._pending:
            self.save_now()

    # -- validation ----------------------------------------------------------
    def _coerce(self, key: str, value: Any) -> Any:
        spec = SCHEMA.get(key)
        if spec is None:
            return value
        if spec.kind is bool:
            return value if isinstance(value, bool) else _INVALID
        if spec.kind is int:
            if isinstance(value, bool):
                return _INVALID
            try:
                number = int(value)
            except (TypeError, ValueError):
                return _INVALID
            if isinstance(value, float) and value != number:
                return _INVALID
            if spec.minimum is not None:
                number = max(int(spec.minimum), number)
            if spec.maximum is not None:
                number = min(int(spec.maximum), number)
            return number
        if spec.kind is float:
            if isinstance(value, bool):
                return _INVALID
            try:
                number = float(value)
            except (TypeError, ValueError):
                return _INVALID
            if not (number == number):  # NaN
                return _INVALID
            if spec.minimum is not None:
                number = max(float(spec.minimum), number)
            if spec.maximum is not None:
                number = min(float(spec.maximum), number)
            return round(number, 4)
        if spec.kind is str:
            if not isinstance(value, str):
                return _INVALID
            text = value
            if spec.length is not None and len(text) != spec.length:
                return _INVALID
            if spec.choices is not None and text not in spec.choices:
                return _INVALID
            if key == "security.pin" and not text.isdigit():
                return _INVALID
            if key == "device.name":
                text = " ".join(text.split())[:24]
                if not text:
                    return _INVALID
            return text
        return _INVALID

    # -- reads ---------------------------------------------------------------
    def get(self, key: str, default: Any = _MISSING) -> Any:
        value = _lookup(self._data, key)
        if value is _MISSING:
            if default is not _MISSING:
                return default
            fallback = _lookup(DEFAULTS, key)
            return None if fallback is _MISSING else deepcopy(fallback)
        return deepcopy(value) if isinstance(value, (dict, list)) else value

    def get_float(self, key: str, default: float = 0.0) -> float:
        value = self.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def get_int(self, key: str, default: int = 0) -> int:
        value = self.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, default)
        return bool(value) if isinstance(value, bool) else bool(default)

    def get_str(self, key: str, default: str = "") -> str:
        value = self.get(key, default)
        return value if isinstance(value, str) else str(default)

    # -- writes --------------------------------------------------------------
    def set(self, key: str, value: Any) -> bool:
        """Validate and store ``value``; returns True when the data changed."""
        coerced = self._coerce(key, value)
        if coerced is _INVALID:
            LOG.warning("rejected invalid value for %s: %r", key, value)
            return False
        current = _lookup(self._data, key)
        if current is not _MISSING and current == coerced:
            return False
        _assign(self._data, key, coerced)
        self._pending = True
        self._notify(key, coerced)
        return True

    def toggle(self, key: str) -> bool:
        value = bool(self.get(key, False))
        self.set(key, not value)
        return not value

    def delete(self, key: str) -> bool:
        """Remove a key entirely (used by round-trip tests and migrations)."""
        parts = key.split(".")
        node: Any = self._data
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        if not isinstance(node, dict) or parts[-1] not in node:
            return False
        del node[parts[-1]]
        self._pending = True
        self._notify(key, None)
        return True

    def reset(self) -> None:
        """Factory reset: restore defaults and write them immediately."""
        self._data = deepcopy(DEFAULTS)
        stamp = time.time()
        self._data["system"]["first_boot"] = stamp
        self._data["system"]["last_boot"] = stamp
        self.save_now()
        self._notify("*", None)
        LOG.warning("settings reset to factory defaults")

    # -- change notifications -------------------------------------------------
    def subscribe(self, callback: Listener) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._listeners.append(callback)

    def unsubscribe(self, callback: Listener) -> None:
        self._listeners = [item for item in self._listeners if item is not callback]

    def _notify(self, key: str, value: Any) -> None:
        for callback in list(self._listeners):
            try:
                callback(key, value)
            except Exception:  # noqa: BLE001 - listeners must never break settings
                LOG.exception("settings listener failed for %s", key)

    # -- introspection --------------------------------------------------------
    def as_dict(self) -> dict[str, Any]:
        return deepcopy(self._data)

    @property
    def has_pending_writes(self) -> bool:
        return self._pending

    def __repr__(self) -> str:
        return f"SettingsService(path={self.path}, pending={self._pending})"
