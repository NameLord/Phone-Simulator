"""Quick settings model: editable tiles bound to real device state."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from services.settings import SettingsService
    from services.storage import Storage
    from ui.theme import Theme

LOG = logging.getLogger("phoneos.quick")
STORE_FILE = "quick.json"
VERSION = 1


@dataclass(frozen=True, slots=True)
class TileDef:
    tile_id: str
    label: str
    subtitle: str
    kind: str = "toggle"
    permanent: bool = False


TILE_DEFS: tuple[TileDef, ...] = (
    TileDef("wifi", "Wi-Fi", "Connected to HomeNet"),
    TileDef("bluetooth", "Bluetooth", "No devices"),
    TileDef("dnd", "Do Not Disturb", "Banners silenced"),
    TileDef("battery_saver", "Battery Saver", "Reduces background drain"),
    TileDef("flashlight", "Flashlight", "Rear LED"),
    TileDef("airplane", "Airplane Mode", "Radios off"),
    TileDef("brightness", "Brightness", "Screen level", "slider", True),
    TileDef("volume", "Volume", "Media level", "slider", True),
    TileDef("rotation", "Rotation Lock", "Keep current orientation"),
    TileDef("dark_mode", "Dark Theme", "Switch appearance"),
    TileDef("recording", "Screen Recording", "Private to this device"),
    TileDef("hotspot", "Hotspot", "Share connection"),
)
TILE_MAP = {tile.tile_id: tile for tile in TILE_DEFS}
DEFAULT_TILES = (
    "wifi",
    "bluetooth",
    "dnd",
    "battery_saver",
    "flashlight",
    "airplane",
    "brightness",
    "volume",
)


class QuickSettingsService:
    def __init__(self, settings: "SettingsService", storage: "Storage", theme: "Theme", notifications=None, audio=None) -> None:
        self._settings = settings
        self._storage = storage
        self._theme = theme
        self._notifications = notifications
        self._audio = audio
        self._order: list[str] = []
        self.load()

    def load(self) -> None:
        payload = self._storage.read_json(STORE_FILE, {})
        order = payload.get("tiles", DEFAULT_TILES) if isinstance(payload, dict) else DEFAULT_TILES
        cleaned = [str(tile) for tile in order if str(tile) in TILE_MAP] if isinstance(order, list) else list(DEFAULT_TILES)
        if not cleaned:
            cleaned = list(DEFAULT_TILES)
        # Never duplicate and always keep brightness/volume available.
        seen: set[str] = set()
        self._order = []
        for tile_id in cleaned:
            if tile_id not in seen:
                seen.add(tile_id)
                self._order.append(tile_id)
        for required in ("brightness", "volume"):
            if required not in self._order:
                self._order.append(required)
        self._apply_dnd()

    def _save(self) -> None:
        self._storage.write_json(STORE_FILE, {"version": VERSION, "tiles": list(self._order)})

    def _apply_dnd(self) -> None:
        if self._notifications is not None and hasattr(self._notifications, "set_dnd"):
            self._notifications.set_dnd(self._settings.get_bool("quick.dnd", False))

    # -- catalog -------------------------------------------------------------
    @property
    def all_tiles(self) -> tuple[TileDef, ...]:
        return TILE_DEFS

    def tiles(self) -> list[TileDef]:
        return [TILE_MAP[tile_id] for tile_id in self._order if tile_id in TILE_MAP]

    def enabled(self, tile_id: str) -> bool:
        if tile_id == "wifi":
            return self._settings.get_bool("quick.wifi", True) and not self._settings.get_bool("quick.airplane", False)
        if tile_id == "bluetooth":
            return self._settings.get_bool("quick.bluetooth", False) and not self._settings.get_bool("quick.airplane", False)
        if tile_id == "dnd":
            return self._settings.get_bool("quick.dnd", False)
        if tile_id == "battery_saver":
            return self._settings.get_bool("display.power_saver", False)
        if tile_id == "flashlight":
            return self._settings.get_bool("quick.flashlight", False)
        if tile_id == "airplane":
            return self._settings.get_bool("quick.airplane", False)
        if tile_id == "rotation":
            return self._settings.get_bool("display.rotation_locked", False)
        if tile_id == "dark_mode":
            return self._theme.is_dark
        if tile_id == "recording":
            return self._settings.get_bool("quick.screen_recording", False)
        if tile_id == "hotspot":
            return self._settings.get_bool("quick.hotspot", False)
        return False

    def value(self, tile_id: str) -> float:
        if tile_id == "brightness":
            return float(self._theme.brightness)
        if tile_id == "volume":
            return float(self._settings.get_float("sound.master_volume", 0.6))
        return 1.0 if self.enabled(tile_id) else 0.0

    def subtitle(self, tile_id: str) -> str:
        if tile_id == "brightness":
            return f"{int(round(self.value('brightness') * 100))}%"
        if tile_id == "volume":
            return f"{int(round(self.value('volume') * 100))}%"
        if tile_id == "wifi":
            return "Off" if not self.enabled("wifi") else ("Airplane mode" if self._settings.get_bool("quick.airplane", False) else self._settings.get_str("quick.wifi_network", "HomeNet"))
        if tile_id == "bluetooth":
            device = self._settings.get_str("quick.bluetooth_device", "")
            return device if self.enabled("bluetooth") and device else ("On" if self.enabled("bluetooth") else "Off")
        if tile_id == "airplane":
            return "On" if self.enabled("airplane") else "Off"
        if tile_id == "battery_saver":
            return "On" if self.enabled("battery_saver") else "Off"
        if tile_id == "flashlight":
            return "On" if self.enabled("flashlight") else "Off"
        if tile_id == "rotation":
            return "Locked" if self.enabled("rotation") else "Auto"
        if tile_id == "dark_mode":
            return "Dark" if self._theme.is_dark else "Light"
        if tile_id == "recording":
            return "Recording" if self.enabled("recording") else "Off"
        tile = TILE_MAP.get(tile_id)
        return tile.subtitle if tile else ""

    def activate(self, tile_id: str) -> bool:
        if tile_id == "brightness" or tile_id == "volume":
            return False
        if tile_id == "wifi":
            new_value = not self._settings.get_bool("quick.wifi", True)
            self._settings.set("quick.wifi", new_value)
        elif tile_id == "bluetooth":
            self._settings.set("quick.bluetooth", not self._settings.get_bool("quick.bluetooth", False))
        elif tile_id == "dnd":
            self._settings.set("quick.dnd", not self._settings.get_bool("quick.dnd", False))
            self._apply_dnd()
        elif tile_id == "battery_saver":
            self._settings.set("display.power_saver", not self._settings.get_bool("display.power_saver", False))
        elif tile_id == "flashlight":
            self._settings.set("quick.flashlight", not self._settings.get_bool("quick.flashlight", False))
        elif tile_id == "airplane":
            enabled = not self._settings.get_bool("quick.airplane", False)
            self._settings.set("quick.airplane", enabled)
            if enabled:
                self._settings.set("quick.wifi", False)
                self._settings.set("quick.bluetooth", False)
        elif tile_id == "rotation":
            self._settings.set("display.rotation_locked", not self._settings.get_bool("display.rotation_locked", False))
        elif tile_id == "dark_mode":
            self._theme.toggle_mode()
        elif tile_id == "recording":
            self._settings.set("quick.screen_recording", not self._settings.get_bool("quick.screen_recording", False))
        elif tile_id == "hotspot":
            self._settings.set("quick.hotspot", not self._settings.get_bool("quick.hotspot", False))
        else:
            return False
        return True

    def set_value(self, tile_id: str, value: float) -> bool:
        value = max(0.0, min(1.0, float(value)))
        if tile_id == "brightness":
            return self._settings.set("display.brightness", max(0.15, value))
        if tile_id == "volume":
            return self._settings.set("sound.master_volume", value)
        return False

    # -- editing -------------------------------------------------------------
    def can_remove(self, tile_id: str) -> bool:
        tile = TILE_MAP.get(tile_id)
        return bool(tile and not tile.permanent and tile_id in self._order)

    def remove(self, tile_id: str) -> bool:
        if not self.can_remove(tile_id):
            return False
        self._order.remove(tile_id)
        self._save()
        return True

    def add(self, tile_id: str) -> bool:
        if tile_id not in TILE_MAP or tile_id in self._order:
            return False
        self._order.append(tile_id)
        self._save()
        return True

    def move(self, tile_id: str, index: int) -> bool:
        if tile_id not in self._order:
            return False
        self._order.remove(tile_id)
        self._order.insert(max(0, min(len(self._order), int(index))), tile_id)
        self._save()
        return True

    def reset(self) -> None:
        self._order = list(DEFAULT_TILES)
        self._save()
