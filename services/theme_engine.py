"""Theme packs, custom theme persistence and contrast protection.

A theme is a full twelve-slot colour map. ``ui.theme`` projects that map onto
the richer ``Palette`` roles used by the existing UI, so every screen re-themes
through one source of truth without rewriting paint call sites.
"""

from __future__ import annotations

import colorsys
import logging
from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.storage import Storage

LOG = logging.getLogger("phoneos.themes")
STORE_FILE = "themes.json"
VERSION = 1

SLOTS = (
    "bg",
    "surface",
    "surface_alt",
    "text_primary",
    "text_secondary",
    "accent",
    "accent_contrast",
    "border",
    "success",
    "warning",
    "danger",
    "shadow",
)
SLOT_LABELS = {
    "bg": "Background",
    "surface": "Surface",
    "surface_alt": "Surface alt",
    "text_primary": "Text primary",
    "text_secondary": "Text secondary",
    "accent": "Accent",
    "accent_contrast": "Accent contrast",
    "border": "Border",
    "success": "Success",
    "warning": "Warning",
    "danger": "Danger",
    "shadow": "Shadow",
}

RGB = tuple[int, int, int]


def _h(hex_value: str) -> RGB:
    value = hex_value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def rgb_to_hex(color: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(channel))) for channel in color))


def hex_to_rgb(value: str) -> RGB | None:
    try:
        text = str(value).strip().lstrip("#")
        if len(text) == 3:
            text = "".join(character * 2 for character in text)
        if len(text) != 6:
            return None
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except (TypeError, ValueError):
        return None


def _pack(
    bg: str,
    surface: str,
    surface_alt: str,
    text_primary: str,
    text_secondary: str,
    accent: str,
    accent_contrast: str,
    border: str,
    success: str,
    warning: str,
    danger: str,
    shadow: str,
) -> dict[str, RGB]:
    return {
        "bg": _h(bg),
        "surface": _h(surface),
        "surface_alt": _h(surface_alt),
        "text_primary": _h(text_primary),
        "text_secondary": _h(text_secondary),
        "accent": _h(accent),
        "accent_contrast": _h(accent_contrast),
        "border": _h(border),
        "success": _h(success),
        "warning": _h(warning),
        "danger": _h(danger),
        "shadow": _h(shadow),
    }


THEME_PACKS: dict[str, dict[str, object]] = {
    "light": {
        "label": "Light",
        "accent_name": "cyan",
        "slots": _pack(
            "#f2f5fb", "#ffffff", "#e6ebf4", "#121622", "#546074",
            "#0f6f8f", "#ffffff", "#c7d0df", "#178a62", "#a66a10",
            "#c83e54", "#0c1424",
        ),
    },
    "dark": {
        "label": "Dark",
        "accent_name": "cyan",
        "slots": _pack(
            "#0b101c", "#151b2a", "#1f2739", "#f4f7ff", "#9aa6bd",
            "#5ad8ff", "#081018", "#303a50", "#56dca0", "#ffbc50",
            "#ff6878", "#000000",
        ),
    },
    "midnight": {
        "label": "Midnight Blue",
        "accent_name": "azure",
        "slots": _pack(
            "#050c24", "#0d1939", "#172650", "#eef3ff", "#98a9d6",
            "#7fb2ff", "#071022", "#293b6b", "#5ce0c0", "#ffc861",
            "#ff7185", "#000000",
        ),
    },
    "solarized": {
        "label": "Solarized",
        "accent_name": "amber",
        "slots": _pack(
            "#fdf6e3", "#fffaf0", "#eee8d5", "#586e75", "#657b83",
            "#b58900", "#fffaf0", "#d6cfb8", "#859900", "#cb4b16",
            "#dc322f", "#002b36",
        ),
    },
    "monochrome": {
        "label": "Monochrome",
        "accent_name": "mono",
        "slots": _pack(
            "#111111", "#1b1b1b", "#272727", "#f5f5f5", "#b8b8b8",
            "#e6e6e6", "#111111", "#454545", "#d8d8d8", "#bdbdbd",
            "#efefef", "#000000",
        ),
    },
    "warm_paper": {
        "label": "Warm Paper",
        "accent_name": "paper",
        "slots": _pack(
            "#f3ead8", "#fffaf0", "#e8dcc4", "#3b3228", "#75695a",
            "#a5653b", "#fffaf0", "#d8c8aa", "#587a52", "#b0792e",
            "#b04a3e", "#211d18",
        ),
    },
}

PACK_ORDER = ("light", "dark", "midnight", "solarized", "monochrome", "warm_paper")


def luminance(color: RGB) -> float:
    channels = []
    for value in color:
        channel = max(0.0, min(1.0, value / 255.0))
        channels.append(channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(first: RGB, second: RGB) -> float:
    a = luminance(first)
    b = luminance(second)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


class ThemeEngine:
    """Built-in packs plus persisted custom themes."""

    def __init__(self, settings, storage: "Storage | None" = None) -> None:
        self._settings = settings
        self._storage = storage
        self._customs: dict[str, dict[str, RGB]] = {}
        self.revision = 0
        self.reload()

    # -- persistence ----------------------------------------------------------
    def reload(self) -> None:
        self._customs = {}
        if self._storage is None:
            return
        payload = self._storage.read_json(STORE_FILE, {})
        themes = payload.get("themes", {}) if isinstance(payload, dict) else {}
        if not isinstance(themes, dict):
            return
        for name, slots in themes.items():
            cleaned = self._sanitize_slots(slots)
            if cleaned is not None:
                self._customs[str(name)[:32]] = cleaned
        self.revision += 1

    def _save(self) -> None:
        if self._storage is None:
            return
        self._storage.write_json(
            STORE_FILE,
            {
                "version": VERSION,
                "themes": {
                    name: {slot: rgb_to_hex(color) for slot, color in slots.items()}
                    for name, slots in self._customs.items()
                },
            },
        )

    @staticmethod
    def _sanitize_slots(raw) -> dict[str, RGB] | None:
        if not isinstance(raw, dict):
            return None
        result: dict[str, RGB] = {}
        for slot in SLOTS:
            value = raw.get(slot)
            if isinstance(value, str):
                color = hex_to_rgb(value)
            elif isinstance(value, (list, tuple)) and len(value) >= 3:
                try:
                    color = (int(value[0]), int(value[1]), int(value[2]))
                except (TypeError, ValueError):
                    color = None
            else:
                color = None
            if color is None:
                return None
            result[slot] = tuple(max(0, min(255, channel)) for channel in color)
        return result

    # -- public ---------------------------------------------------------------
    def builtins(self) -> list[tuple[str, str, dict[str, RGB]]]:
        return [
            (key, str(THEME_PACKS[key]["label"]), deepcopy(THEME_PACKS[key]["slots"]))
            for key in PACK_ORDER
        ]

    def customs(self) -> list[tuple[str, str, dict[str, RGB]]]:
        return [
            (f"custom:{name}", name, deepcopy(slots))
            for name, slots in sorted(self._customs.items())
        ]

    def all_themes(self) -> list[tuple[str, str, dict[str, RGB]]]:
        return self.builtins() + self.customs()

    def resolve_key(self) -> str:
        key = self._settings.get_str("display.theme_pack", "auto")
        if key == "auto":
            return "light" if self._settings.get_str("display.mode", "dark") == "light" else "dark"
        if key in THEME_PACKS:
            return key
        if key.startswith("custom:"):
            name = key.split(":", 1)[1]
            if name in self._customs:
                return key
        return "dark"

    def slots(self, key: str | None = None) -> dict[str, RGB]:
        resolved = self.resolve_key() if key is None else key
        if resolved in THEME_PACKS:
            return deepcopy(THEME_PACKS[resolved]["slots"])  # type: ignore[return-value]
        if resolved.startswith("custom:"):
            name = resolved.split(":", 1)[1]
            if name in self._customs:
                return deepcopy(self._customs[name])
        return deepcopy(THEME_PACKS["dark"]["slots"])  # type: ignore[return-value]

    def label_for(self, key: str) -> str:
        if key in THEME_PACKS:
            return str(THEME_PACKS[key]["label"])
        if key.startswith("custom:"):
            return key.split(":", 1)[1]
        return "Auto" if key == "auto" else key

    def apply(self, key: str) -> bool:
        if key == "auto" or key in THEME_PACKS or key.startswith("custom:"):
            self._settings.set("display.theme_pack", key)
            if key in THEME_PACKS:
                accent = str(THEME_PACKS[key].get("accent_name", "cyan"))
                self._settings.set("display.accent", accent)
            self.revision += 1
            return True
        return False

    def save_custom(self, name: str, slots: dict[str, RGB]) -> str | None:
        clean = " ".join(str(name).split())[:32]
        cleaned = self._sanitize_slots({slot: rgb_to_hex(color) for slot, color in slots.items()})
        if not clean or cleaned is None:
            return None
        self._customs[clean] = cleaned
        self._save()
        self.revision += 1
        return f"custom:{clean}"

    def delete_custom(self, key: str) -> bool:
        if not key.startswith("custom:"):
            return False
        name = key.split(":", 1)[1]
        if name not in self._customs:
            return False
        del self._customs[name]
        self._save()
        self.revision += 1
        if self._settings.get_str("display.theme_pack", "auto") == key:
            self.apply("dark")
        return True

    def auto_fix(self, slots: dict[str, RGB]) -> dict[str, RGB]:
        fixed = deepcopy(slots)
        bg = fixed["bg"]
        text = fixed["text_primary"]
        if contrast_ratio(text, bg) >= 4.5:
            return fixed
        white = (255, 255, 255)
        black = (0, 0, 0)
        fixed["text_primary"] = white if contrast_ratio(white, bg) >= contrast_ratio(black, bg) else black
        # Keep secondary text readable relative to the corrected primary.
        fixed["text_secondary"] = (
            (int(fixed["text_primary"][0] * 0.72), int(fixed["text_primary"][1] * 0.72), int(fixed["text_primary"][2] * 0.72))
        )
        return fixed

    @staticmethod
    def contrast(slots: dict[str, RGB]) -> float:
        return contrast_ratio(slots.get("text_primary", (255, 255, 255)), slots.get("bg", (0, 0, 0)))

    @staticmethod
    def hsv_color(hue: float, saturation: float, value: float) -> RGB:
        red, green, blue = colorsys.hsv_to_rgb(hue % 1.0, max(0.0, min(1.0, saturation)), max(0.0, min(1.0, value)))
        return (int(red * 255), int(green * 255), int(blue * 255))
