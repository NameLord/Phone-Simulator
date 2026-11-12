"""App manager: manifests, install/uninstall, launch/close and Recents.

The manager is the only place that knows the catalog. Apps never import each
other or the manager; they receive an :class:`AppContext` with an ``apps``
facade. Installed ids persist in settings at ``apps.installed``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import pygame

from apps.app_store import AppStoreApp
from apps.app_store import draw_icon as store_icon
from apps.base_app import App, AppContext
from apps.browser import BrowserApp
from apps.browser import draw_icon as browser_icon
from apps.calculator import CalculatorApp
from apps.calculator import draw_icon as calculator_icon
from apps.calendar import CalendarApp
from apps.calendar import draw_icon as calendar_icon
from apps.camera import CameraApp
from apps.camera import draw_icon as camera_icon
from apps.clock_app import ClockApp
from apps.clock_app import draw_icon as clock_icon
from apps.compass import CompassApp
from apps.compass import draw_icon as compass_icon
from apps.contacts import ContactsApp
from apps.contacts import draw_icon as contacts_icon
from apps.files import FilesApp
from apps.files import draw_icon as files_icon
from apps.games import GamesApp
from apps.games import draw_icon as games_icon
from apps.music import MusicApp
from apps.music import draw_icon as music_icon
from apps.notes import NotesApp
from apps.notes import draw_icon as notes_icon
from apps.messages import MessagesApp
from apps.messages import draw_icon as messages_icon
from apps.phone import PhoneApp
from apps.phone import draw_icon as phone_icon
from apps.photos import PhotosApp
from apps.photos import draw_icon as photos_icon
from apps.recorder import RecorderApp
from apps.recorder import draw_icon as recorder_icon
from apps.video import VideoApp
from apps.video import draw_icon as video_icon
from apps.weather import WeatherApp
from apps.weather import draw_icon as weather_icon

if TYPE_CHECKING:
    from core.clock import DeviceClock
    from services.audio import AudioService
    from services.notifications import NotificationCenter
    from services.photo_library import PhotoLibrary
    from services.profiler import Profiler
    from services.schedules import ScheduleService
    from services.telephony import TelephonyService
    from services.settings import SettingsService
    from services.storage import Storage
    from ui.theme import Theme

LOG = logging.getLogger("phoneos.apps")

SETTING_KEY = "apps.installed"
MAX_RECENTS = 5

IconPainter = Callable[[pygame.Surface, pygame.Rect, "Theme"], None]
AppFactory = Callable[[AppContext], App]


@dataclass(frozen=True, slots=True)
class AppManifest:
    app_id: str
    name: str
    description: str
    category: str
    factory: AppFactory
    icon: IconPainter
    core: bool = False
    preinstalled: bool = False
    system: bool = False
    power_draw: float = 45.0


def _manifest_list() -> tuple[AppManifest, ...]:
    return (
        AppManifest(
            app_id=AppStoreApp.app_id,
            name=AppStoreApp.name,
            description="Install and remove apps. Core titles are protected from removal.",
            category="System",
            factory=AppStoreApp,
            icon=store_icon,
            core=True,
            preinstalled=True,
            power_draw=90.0,
        ),
        AppManifest(
            app_id=CalculatorApp.app_id,
            name=CalculatorApp.name,
            description="Safe expression engine with parentheses, powers, percent and live results.",
            category="Utilities",
            factory=CalculatorApp,
            icon=calculator_icon,
            preinstalled=True,
            power_draw=32.0,
        ),
        AppManifest(
            app_id=NotesApp.app_id,
            name=NotesApp.name,
            description="Write, search and delete notes. Saved to data/notes.json.",
            category="Productivity",
            factory=NotesApp,
            icon=notes_icon,
            preinstalled=True,
            power_draw=38.0,
        ),
        AppManifest(
            app_id=BrowserApp.app_id,
            name=BrowserApp.name,
            description="A fake web with six pages, links and back/forward history.",
            category="Internet",
            factory=BrowserApp,
            icon=browser_icon,
            preinstalled=True,
            power_draw=125.0,
        ),
        AppManifest(
            app_id=MusicApp.app_id,
            name=MusicApp.name,
            description="Procedural album library, playlist, seek, shuffle and background playback.",
            category="Media",
            factory=MusicApp,
            icon=music_icon,
            preinstalled=False,
            power_draw=165.0,
        ),
        AppManifest(
            app_id=PhoneApp.app_id,
            name=PhoneApp.name,
            description="Contacts, numeric dialer and recent calls.",
            category="Communication",
            factory=PhoneApp,
            icon=phone_icon,
            system=True,
            power_draw=72.0,
        ),
        AppManifest(
            app_id=MessagesApp.app_id,
            name=MessagesApp.name,
            description="Conversation threads with delayed replies, persisted locally.",
            category="Communication",
            factory=MessagesApp,
            icon=messages_icon,
            system=True,
            power_draw=58.0,
        ),
        AppManifest(
            app_id=ClockApp.app_id,
            name=ClockApp.name,
            description="World clocks, alarms, timer and stopwatch with a full-screen alarm.",
            category="Utilities",
            factory=ClockApp,
            icon=clock_icon,
            power_draw=40.0,
        ),
        AppManifest(
            app_id=CalendarApp.app_id,
            name=CalendarApp.name,
            description="Month grid and day agenda with persisted timed events.",
            category="Productivity",
            factory=CalendarApp,
            icon=calendar_icon,
            power_draw=42.0,
        ),
        AppManifest(
            app_id=WeatherApp.app_id,
            name=WeatherApp.name,
            description="Seeded procedural forecast, hourly strip and seven-day outlook.",
            category="Utilities",
            factory=WeatherApp,
            icon=weather_icon,
            power_draw=48.0,
        ),
        AppManifest(
            app_id=FilesApp.app_id,
            name=FilesApp.name,
            description="A real sandboxed filesystem tree with an atomic text editor.",
            category="Productivity",
            factory=FilesApp,
            icon=files_icon,
            power_draw=46.0,
        ),
        AppManifest(
            app_id=PhotosApp.app_id,
            name=PhotosApp.name,
            description="Eight procedural photo generators with a zoomable viewer.",
            category="Media",
            factory=PhotosApp,
            icon=photos_icon,
            power_draw=135.0,
        ),
        AppManifest(
            app_id=RecorderApp.app_id,
            name=RecorderApp.name,
            description="Simulated voice memo with live numpy waveform and playback.",
            category="Media",
            factory=RecorderApp,
            icon=recorder_icon,
            power_draw=112.0,
        ),
        AppManifest(
            app_id=CompassApp.app_id,
            name=CompassApp.name,
            description="A drifting compass, level bubble and two-second calibration spin.",
            category="Utilities",
            factory=CompassApp,
            icon=compass_icon,
            power_draw=38.0,
        ),
        AppManifest(
            app_id=VideoApp.app_id,
            name=VideoApp.name,
            description="Three procedural clips with seek, speed and fullscreen controls.",
            category="Media",
            factory=VideoApp,
            icon=video_icon,
            power_draw=190.0,
        ),
        AppManifest(
            app_id=CameraApp.app_id,
            name=CameraApp.name,
            description="Simulated viewfinder with filters and captures sent to Photos.",
            category="Media",
            factory=CameraApp,
            icon=camera_icon,
            power_draw=155.0,
        ),
        AppManifest(
            app_id=ContactsApp.app_id,
            name=ContactsApp.name,
            description="Searchable contacts with favorites, notes and full editing.",
            category="Communication",
            factory=ContactsApp,
            icon=contacts_icon,
            power_draw=44.0,
        ),
        AppManifest(
            app_id=GamesApp.app_id,
            name=GamesApp.name,
            description="A complete 2048 board with undo, best score and resume.",
            category="Games",
            factory=GamesApp,
            icon=games_icon,
            power_draw=48.0,
        ),
    )


class AppManager:
    """Owns installed manifests and running app instances."""

    def __init__(
        self,
        settings: "SettingsService",
        storage: "Storage",
        theme: "Theme",
        clock: "DeviceClock",
        notifications: "NotificationCenter",
        size: tuple[int, int],
        chrome_height: int,
        telephony: "TelephonyService | None" = None,
        schedules: "ScheduleService | None" = None,
        audio: "AudioService | None" = None,
        photos: "PhotoLibrary | None" = None,
        profiler: "Profiler | None" = None,
        clipboard=None,
        toast=None,
        network=None,
    ) -> None:
        self._settings = settings
        self._notifications = notifications
        self._theme = theme
        self._manifests: dict[str, AppManifest] = {
            manifest.app_id: manifest for manifest in _manifest_list()
        }
        self._order: tuple[str, ...] = tuple(self._manifests)
        self._installed: list[str] = []
        self._running: dict[str, App] = {}
        self._recents: list[str] = []
        self._active: str | None = None
        self._persisting = False
        self._home_state = None

        self._context = AppContext(
            settings=settings,
            storage=storage,
            theme=theme,
            clock=clock,
            notifications=notifications,
            apps=self,
            size=size,
            chrome_height=chrome_height,
            telephony=telephony,
            schedules=schedules,
            audio=audio,
            photos=photos,
            profiler=profiler,
            clipboard=clipboard,
            network=network,
            _notify=self._post_notification,
            _toast=(lambda message, label, callback: toast.undo(message, callback)) if toast is not None else None,
        )

        self._installed = self._load_installed()
        self._settings.subscribe(self._on_setting_changed)

    def set_share_callback(self, callback) -> None:
        self._context._share = callback

    def reload_installed(self) -> None:
        """Re-read installed ids after a profile switch or restore."""
        self.close_all()
        self._installed = self._load_installed()
        self._sync_home(initial_dock=False)

    # -- catalog --------------------------------------------------------------
    def catalog(self) -> tuple[AppManifest, ...]:
        return tuple(self._manifests[app_id] for app_id in self._order)

    def manifest(self, app_id: str) -> AppManifest | None:
        return self._manifests.get(app_id)

    def is_installed(self, app_id: str) -> bool:
        return app_id in self._installed

    @property
    def installed_ids(self) -> tuple[str, ...]:
        return tuple(self._installed)

    def installed_manifests(self) -> tuple[AppManifest, ...]:
        return tuple(
            self._manifests[app_id] for app_id in self._installed if app_id in self._manifests
        )

    def system_manifests(self) -> tuple[AppManifest, ...]:
        return tuple(manifest for manifest in self._manifests.values() if manifest.system)

    def bind_home_state(self, home_state) -> None:
        """Keep the launcher layout in step with installed applications."""
        self._home_state = home_state
        self._sync_home(initial_dock=None)

    def _sync_home(self, *, initial_dock=None) -> None:
        if self._home_state is None:
            return
        self._home_state.sync(
            self.installed_ids,
            (manifest.app_id for manifest in self.system_manifests()),
            initial_dock=initial_dock,
        )

    # -- install / uninstall ---------------------------------------------------
    def install(self, app_id: str) -> bool:
        manifest = self._manifests.get(app_id)
        if manifest is None or app_id in self._installed:
            return False
        self._installed.append(app_id)
        self._persist()
        self._sync_home(initial_dock=False)
        LOG.info("installed app %s", app_id)
        return True

    def uninstall(self, app_id: str) -> bool:
        manifest = self._manifests.get(app_id)
        if manifest is None or manifest.core or app_id not in self._installed:
            return False
        if app_id in self._running:
            self.close(app_id)
        self._installed = [item for item in self._installed if item != app_id]
        self._persist()
        self._sync_home(initial_dock=False)
        LOG.info("uninstalled app %s", app_id)
        return True

    # -- external manifests (plugins) -----------------------------------------
    def register_manifest(self, manifest: AppManifest, *, install: bool = True) -> bool:
        app_id = manifest.app_id
        if not app_id:
            return False
        self._manifests[app_id] = manifest
        if app_id not in self._order:
            self._order = tuple(self._order) + (app_id,)
        if install and app_id not in self._installed:
            self._installed.append(app_id)
            self._persist()
            self._sync_home(initial_dock=False)
        LOG.info("registered external app %s", app_id)
        return True

    def unregister_manifest(self, app_id: str) -> bool:
        if app_id not in self._manifests:
            return False
        if app_id in self._running:
            self.close(app_id)
        self._manifests.pop(app_id, None)
        self._order = tuple(item for item in self._order if item != app_id)
        if app_id in self._installed:
            self._installed = [item for item in self._installed if item != app_id]
            self._persist()
            self._sync_home(initial_dock=False)
        LOG.info("unregistered external app %s", app_id)
        return True

    def set_installed(self, app_id: str, installed: bool) -> bool:
        if app_id not in self._manifests:
            return False
        if installed:
            if app_id in self._installed:
                return True
            self._installed.append(app_id)
            self._persist()
            self._sync_home(initial_dock=False)
            return True
        if app_id not in self._installed:
            return True
        if app_id in self._running:
            self.close(app_id)
        self._installed = [item for item in self._installed if item != app_id]
        self._persist()
        self._sync_home(initial_dock=False)
        return True

    # -- lifecycle -------------------------------------------------------------
    def launch(self, app_id: str, payload: dict | None = None) -> App | None:
        manifest = self._manifests.get(app_id)
        if app_id not in self._installed and not (manifest is not None and manifest.system):
            return None
        existing = self._running.get(app_id)
        if existing is not None:
            self.switch_to(app_id)
            if payload is not None:
                try:
                    existing.open_payload(payload)
                except Exception:  # noqa: BLE001
                    LOG.exception("app %s failed to open payload", app_id)
            return existing

        if manifest is None:
            return None
        try:
            app = manifest.factory(self._context)
        except Exception:  # noqa: BLE001 - a broken app must not kill the OS
            LOG.exception("could not instantiate app %s", app_id)
            return None

        if self._active is not None and self._active in self._running:
            self._running[self._active].on_pause()
        self._running[app_id] = app
        self._active = app_id
        self._touch_recent(app_id)
        try:
            app.on_launch()
        except Exception:  # noqa: BLE001
            LOG.exception("app %s failed during launch", app_id)
        if payload is not None:
            try:
                app.open_payload(payload)
            except Exception:  # noqa: BLE001
                LOG.exception("app %s failed to open payload", app_id)
        LOG.info("launched app %s", app_id)
        return app

    def switch_to(self, app_id: str) -> bool:
        if app_id not in self._running:
            return self.launch(app_id) is not None
        if app_id == self._active:
            return True
        if self._active is not None and self._active in self._running:
            self._running[self._active].on_pause()
        self._active = app_id
        self._touch_recent(app_id)
        try:
            self._running[app_id].on_resume()
        except Exception:  # noqa: BLE001
            LOG.exception("app %s failed during resume", app_id)
        return True

    def background_active(self) -> None:
        if self._active is None:
            return
        app = self._running.get(self._active)
        if app is not None:
            try:
                app.on_pause()
            except Exception:  # noqa: BLE001
                LOG.exception("app %s failed during pause", app.app_id)
        self._active = None

    def pause_active(self) -> None:
        self.background_active()

    def close(self, app_id: str) -> bool:
        app = self._running.pop(app_id, None)
        if app is None:
            return False
        try:
            app.on_close()
        except Exception:  # noqa: BLE001
            LOG.exception("app %s failed during close", app_id)
        if self._active == app_id:
            self._active = None
        self._recents = [item for item in self._recents if item != app_id]
        LOG.info("closed app %s", app_id)
        return True

    def close_all(self) -> None:
        for app_id in list(self._running):
            self.close(app_id)

    def shutdown(self) -> None:
        self.close_all()
        self._settings.unsubscribe(self._on_setting_changed)

    # -- resize ---------------------------------------------------------------
    def resize(self, size: tuple[int, int]) -> None:
        """Resize every live app while preserving its state."""
        new_size = (int(size[0]), int(size[1]))
        self._context.size = new_size
        for app in list(self._running.values()):
            try:
                app.resize(new_size)
            except Exception:  # noqa: BLE001 - one broken app must not stop rotation
                LOG.exception("app %s failed to resize", app.app_id)

    # -- state ----------------------------------------------------------------
    @property
    def active_id(self) -> str | None:
        return self._active

    @property
    def active_app(self) -> App | None:
        return self._running.get(self._active) if self._active is not None else None

    def running_apps(self) -> tuple[App, ...]:
        return tuple(self._running[app_id] for app_id in self._recents if app_id in self._running)

    def recents_apps(self) -> tuple[App, ...]:
        return self.running_apps()

    @property
    def recent_ids(self) -> tuple[str, ...]:
        return tuple(self._recents)

    @property
    def active_power_draw(self) -> float:
        if self._active is None:
            return 0.0
        manifest = self._manifests.get(self._active)
        return manifest.power_draw if manifest is not None else 0.0

    # -- internals -------------------------------------------------------------
    def _touch_recent(self, app_id: str) -> None:
        self._recents = [item for item in self._recents if item != app_id]
        self._recents.insert(0, app_id)
        del self._recents[MAX_RECENTS:]
        # Anything pushed out of the five-entry stack is closed so no invisible
        # app keeps holding a surface, audio channel or draft.
        for running_id in [item for item in self._running if item not in self._recents]:
            self.close(running_id)

    def _seed_ids(self) -> list[str]:
        return [
            app_id
            for app_id in self._order
            if (self._manifests[app_id].preinstalled or self._manifests[app_id].core)
            and not self._manifests[app_id].system
        ]

    def _sanitize(self, value: object) -> list[str]:
        ids: list[str] = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item in self._manifests and item not in ids:
                    ids.append(item)
        for app_id in self._seed_ids():
            if self._manifests[app_id].core and app_id not in ids:
                ids.insert(0, app_id)
        if not ids:
            ids = self._seed_ids()
        return ids

    def _load_installed(self) -> list[str]:
        raw = self._settings.get(SETTING_KEY, None)
        if isinstance(raw, list):
            return self._sanitize(raw)
        seeded = self._seed_ids()
        self._install_list(seeded)
        return seeded

    def _persist(self) -> None:
        self._persisting = True
        try:
            self._settings.set(SETTING_KEY, list(self._installed))
        finally:
            self._persisting = False

    def _install_list(self, ids: list[str]) -> None:
        self._installed = list(ids)
        self._persist()

    def _on_setting_changed(self, key: str, value: object) -> None:
        if self._persisting:
            return
        if key == "*":
            self.close_all()
            self._installed = self._seed_ids()
            self._persist()
        elif key == SETTING_KEY:
            self._installed = self._sanitize(value)

    def _post_notification(self, app_id: str, title: str, body: str) -> None:
        self._notifications.post(app_id, title, body, self._theme.palette.accent)

    def __len__(self) -> int:
        return len(self._installed)

    def __repr__(self) -> str:
        return (
            f"AppManager(installed={len(self._installed)}, running={len(self._running)}, "
            f"active={self._active!r})"
        )
