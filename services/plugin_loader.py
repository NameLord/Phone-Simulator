"""Scan ./plugins for third-party apps and load them defensively."""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.app_manager import AppManifest, AppManager
    from services.storage import Storage

LOG = logging.getLogger("phoneos.plugins")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = PROJECT_ROOT / "plugins"
STATE_FILE = "plugins.json"


@dataclass
class PluginInfo:
    app_id: str
    name: str
    path: str
    enabled: bool = True
    error: str = ""
    manifest: Any = None
    module: Any = None


class PluginLoader:
    def __init__(self, storage: "Storage", apps: "AppManager") -> None:
        self._storage = storage
        self._apps = apps
        self.plugins: list[PluginInfo] = []
        self.last_error = ""
        self._disabled = set(self._load_state())

    def _load_state(self) -> list[str]:
        payload = self._storage.read_json(STATE_FILE, {})
        rows = payload.get("disabled", []) if isinstance(payload, dict) else []
        return [str(item) for item in rows] if isinstance(rows, list) else []

    def _save_state(self) -> None:
        self._storage.write_json(STATE_FILE, {"disabled": sorted(self._disabled)})

    def _icon_wrapper(self, draw_icon, fallback_name: str):
        def paint(surface, rect, theme) -> None:
            if callable(draw_icon):
                draw_icon(surface, rect, theme)
                return
            from ui.paint import draw_rounded

            pal = theme.palette
            draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.accent, radius=14)
            font = theme.font(max(10, rect.h // 3), True)
            label = (fallback_name or "P")[:1].upper()
            image = font.render(label, True, pal.accent_ink)
            surface.blit(image, image.get_rect(center=rect.center))

        return paint

    def _make_manifest(self, app_id: str, data: dict, module) -> "AppManifest | None":
        from apps.base_app import App
        from services.app_manager import AppManifest

        app_class = None
        for _name, value in vars(module).items():
            if inspect.isclass(value) and issubclass(value, App) and value is not App:
                if getattr(value, "app_id", "") == app_id or app_class is None:
                    app_class = value
        if app_class is None:
            raise ValueError("no App subclass exported")
        if not getattr(app_class, "app_id", ""):
            app_class.app_id = app_id
        if not getattr(app_class, "name", ""):
            app_class.name = str(data.get("name", app_id.title()))
        draw_icon = getattr(module, "draw_icon", None)
        return AppManifest(
            app_id=app_id,
            name=str(data.get("name", app_id.title())),
            description=str(data.get("description", "Third-party plugin app.")),
            category=str(data.get("category", "Plugins")),
            factory=app_class,
            icon=self._icon_wrapper(draw_icon, str(data.get("name", app_id))),
            preinstalled=False,
            power_draw=float(data.get("power_draw", 40.0)),
        )

    def scan(self) -> list[PluginInfo]:
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        found: list[PluginInfo] = []
        for index, path in enumerate(sorted(PLUGINS_DIR.glob("*.py"))):
            if path.name.startswith("_"):
                continue
            module_name = f"phoneos_plugin_{path.stem}_{index}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    raise ImportError("could not create import spec")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                data = getattr(module, "MANIFEST", None)
                if not isinstance(data, dict):
                    raise ValueError("missing MANIFEST dict")
                app_id = str(data.get("app_id", path.stem))
                manifest = self._make_manifest(app_id, data, module)
                if manifest is None:
                    raise ValueError("could not build manifest")
                info = PluginInfo(
                    app_id=app_id,
                    name=manifest.name,
                    path=str(path),
                    enabled=app_id not in self._disabled,
                    manifest=manifest,
                    module=module,
                )
                found.append(info)
            except Exception as exc:  # noqa: BLE001 - broken plugins are disabled
                LOG.exception("plugin %s failed to load", path.name)
                self.last_error = f"{path.name}: {exc}"
                found.append(
                    PluginInfo(
                        app_id=f"broken:{path.stem}",
                        name=path.stem.title(),
                        path=str(path),
                        enabled=False,
                        error=str(exc),
                    )
                )
        self.plugins = found
        return found

    def load_all(self) -> list[PluginInfo]:
        for info in self.plugins:
            if info.manifest is not None:
                self._apps.unregister_manifest(info.app_id)
        for info in self.scan():
            if info.manifest is None:
                continue
            try:
                self._apps.register_manifest(info.manifest, install=True)
            except Exception as exc:  # noqa: BLE001
                info.error = str(exc)
                LOG.exception("plugin %s could not register", info.app_id)
            if not info.enabled:
                self._apps.set_installed(info.app_id, False)
        return self.plugins

    def reload(self) -> list[PluginInfo]:
        LOG.info("reloading plugins")
        return self.load_all()

    def set_enabled(self, app_id: str, enabled: bool) -> bool:
        info = next((item for item in self.plugins if item.app_id == app_id), None)
        if info is None or info.manifest is None:
            return False
        info.enabled = bool(enabled)
        if enabled:
            self._disabled.discard(app_id)
            self._apps.set_installed(app_id, True)
        else:
            self._disabled.add(app_id)
            self._apps.set_installed(app_id, False)
        self._save_state()
        return True

    def status(self) -> str:
        if self.last_error:
            return self.last_error
        broken = sum(1 for info in self.plugins if info.error)
        return f"{len(self.plugins)} plugins, {broken} errors"


__all__ = ["PLUGINS_DIR", "PluginInfo", "PluginLoader"]
