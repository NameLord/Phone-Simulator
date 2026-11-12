"""Kernel: window lifecycle, fixed timestep loop, scene machine, debug overlay.

Timing model
------------
Rendering runs as fast as the platform allows (capped by ``target_fps``), while
simulation always advances in fixed 1/60 s steps. The accumulator is bounded
by ``MAX_STEPS_PER_FRAME`` and frame dt is capped by ``MAX_FRAME_DT`` so a slow
frame can never spiral into an unbounded catch-up loop.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import pygame

from apps.base_app import APP_CHROME_HEIGHT
from core.clock import DeviceClock
from core.display import Display, PORTRAIT_SIZE
from core.fx import Effects
from core.input import EventKind, InputManager
from core.registry import Registry, ServiceName, get_registry
from services.app_manager import AppManager
from services.audio import AudioService
from services.backup import BackupService
from services.clipboard import ClipboardService
from services.device import DeviceService, DeviceState
from services.home_state import HomeState
from services.logger import install as install_device_log
from services.network import NetworkService
from services.notifications import NotificationCenter
from services.photo_library import PhotoLibrary
from services.plugin_loader import PluginLoader
from services.power import PowerService
from services.profiles import ProfileService
from services.profiler import Profiler
from services.quick_settings import QuickSettingsService
from services.share import ShareService
from services.schedules import ScheduleService
from services.settings import DEFAULT_PATH, SettingsService
from services.sim_events import SimulationEvents
from services.storage import Storage
from services.telephony import TelephonyService
from services.theme_engine import ThemeEngine
from services.toast import ToastService
from ui import layout as layout_state
from ui.alarm_overlay import AlarmOverlay
from ui.call_overlay import CallOverlay
from ui.power_menu import BatteryAlert, PowerMenu
from ui.status_bar import StatusBar
from ui.theme import Theme

if TYPE_CHECKING:
    from core.scene import Scene

FIXED_DT = 1.0 / 60.0
MAX_FRAME_DT = 0.25
MAX_STEPS_PER_FRAME = 5

SceneFactory = Callable[["Kernel"], "Scene"]


@dataclass
class KernelConfig:
    """Boot configuration, provided to the registry as ``config``."""

    title: str = "Phone Simulator"
    virtual_size: tuple[int, int] = PORTRAIT_SIZE
    scale: float | None = None
    target_fps: int = 60
    debug_overlay: bool = False
    fixed_dt: float = FIXED_DT


class Kernel:
    """Owns every subsystem and the main loop."""

    def __init__(self, config: KernelConfig | None = None) -> None:
        self.config = config or KernelConfig()
        self.registry: Registry = get_registry()

        pygame.init()
        pygame.key.set_repeat(0)
        install_device_log()

        # Settings no longer depend on the display, so the window can be built
        # with the user's requested mode/orientation on the very first frame.
        self.settings = SettingsService()
        stored_w = self.settings.get_int("display.window_width", 0)
        stored_h = self.settings.get_int("display.window_height", 0)
        window_size = (stored_w, stored_h) if stored_w > 0 and stored_h > 0 else None
        self.display = Display(
            title=self.config.title,
            virtual_size=self.config.virtual_size,
            scale=self.config.scale,
            window_mode=self.settings.get_str("display.window_mode", "windowed"),
            scaling_mode=self.settings.get_str("display.scaling", "fit"),
            orientation=self.settings.get_str("display.orientation", "portrait"),
            window_size=window_size,
            device_frame=self.settings.get_bool("display.device_frame", False),
            vsync=self.settings.get_bool("display.vsync", False),
        )
        layout_state.configure(self.display.virtual_size)
        # Battery drain is owned by services/power.py; the clock still owns
        # simulated time, CPU load and uptime.
        self.device_clock = DeviceClock(manage_battery=False)
        self.device_clock.time_scale = float(self.settings.get_int("simulation.clock_speed", 1))
        self.input = InputManager(self.display)

        # -- system layer ---------------------------------------------------
        self.storage = Storage()
        self.clipboard = ClipboardService(self.storage)
        self.toast = ToastService()
        self.network = NetworkService(self.settings)
        self.profiles = ProfileService(self.storage)
        self.theme_engine = ThemeEngine(self.settings, self.storage)
        self.theme = Theme(self.display, self.settings, self.theme_engine)
        self.fx = Effects(self.settings, self.display.virtual_size)
        self.fx.set_palette(self.theme.palette, self.theme.is_dark)
        self.notifications = NotificationCenter(self.device_clock, fx=self.fx)
        self.status_bar = StatusBar()
        self.profiler = Profiler()
        self.audio = AudioService(self.settings, self.storage, self.profiler)
        self.photo_library = PhotoLibrary(self.storage)
        self.quick_settings = QuickSettingsService(
            self.settings,
            self.storage,
            self.theme,
            notifications=self.notifications,
            audio=self.audio,
        )
        self.notifications.set_media_controller(self.audio)
        self.notifications.set_post_callback(self.audio.play_ui)
        self.notifications.set_action_handler(self._on_notification_action)
        from ui.quick_settings import QuickSettingsPanel

        self.quick_panel = QuickSettingsPanel(self.quick_settings, self.theme)
        self.notifications.set_quick_panel(self.quick_panel)
        self.telephony = TelephonyService(
            settings=self.settings,
            storage=self.storage,
            clock=self.device_clock,
            notifications=self.notifications,
            theme=self.theme,
        )
        self.schedules = ScheduleService(
            settings=self.settings,
            storage=self.storage,
            clock=self.device_clock,
            notifications=self.notifications,
            theme=self.theme,
        )
        self.app_manager = AppManager(
            settings=self.settings,
            storage=self.storage,
            theme=self.theme,
            clock=self.device_clock,
            notifications=self.notifications,
            size=self.display.virtual_size,
            chrome_height=APP_CHROME_HEIGHT,
            telephony=self.telephony,
            schedules=self.schedules,
            audio=self.audio,
            photos=self.photo_library,
            profiler=self.profiler,
            clipboard=self.clipboard,
            toast=self.toast,
            network=self.network,
        )
        self.share = ShareService(self.app_manager)
        self.backup = BackupService(self.storage)
        self.backup.on_restore = self.reload_data
        self.plugins = PluginLoader(self.storage, self.app_manager)
        self.plugins.load_all()
        self.home_state = HomeState(self.storage, self.settings)
        self.app_manager.bind_home_state(self.home_state)
        self.power = PowerService(
            settings=self.settings,
            clock=self.device_clock,
            notifications=self.notifications,
            theme=self.theme,
            app_draw=lambda: self.app_manager.active_power_draw,
        )
        self.device = DeviceService(
            self.settings,
            boot_actions=[
                ("Services", self._boot_services),
                ("Applications", self._boot_apps),
                ("Storage", self._boot_storage),
            ],
        )
        self.power.bind_device(self.device)
        self.telephony.bind_device(self.device)
        self.sim_events = SimulationEvents(
            settings=self.settings,
            clock=self.device_clock,
            notifications=self.notifications,
            theme=self.theme,
            apps=self.app_manager,
            power=self.power,
            device=self.device,
        )

        self.registry.set(ServiceName.KERNEL, self)
        self.registry.set(ServiceName.CONFIG, self.config)
        self.registry.set(ServiceName.DISPLAY, self.display)
        self.registry.set(ServiceName.INPUT, self.input)
        self.registry.set(ServiceName.CLOCK, self.device_clock)
        self.registry.set(ServiceName.SETTINGS, self.settings)
        self.registry.set(ServiceName.THEME, self.theme)
        self.registry.set(ServiceName.THEME_ENGINE, self.theme_engine)
        self.registry.set(ServiceName.HOME_STATE, self.home_state)
        self.registry.set(ServiceName.QUICK_SETTINGS, self.quick_settings)
        self.registry.set(ServiceName.NETWORK, self.network)
        self.registry.set(ServiceName.CLIPBOARD, self.clipboard)
        self.registry.set(ServiceName.TOAST, self.toast)
        self.registry.set(ServiceName.SHARE, self.share)
        self.registry.set(ServiceName.BACKUP, self.backup)
        self.registry.set(ServiceName.PROFILES, self.profiles)
        self.registry.set(ServiceName.PLUGINS, self.plugins)
        self.registry.set(ServiceName.NOTIFICATIONS, self.notifications)
        self.registry.set(ServiceName.STATUS_BAR, self.status_bar)
        self.registry.set(ServiceName.STORAGE, self.storage)
        self.registry.set(ServiceName.APP_MANAGER, self.app_manager)
        self.registry.set(ServiceName.FX, self.fx)
        self.registry.set(ServiceName.PROFILER, self.profiler)
        self.registry.set(ServiceName.POWER, self.power)
        self.registry.set(ServiceName.DEVICE, self.device)
        self.registry.set(ServiceName.TELEPHONY, self.telephony)
        self.registry.set(ServiceName.SIM_EVENTS, self.sim_events)
        self.registry.set(ServiceName.SCHEDULES, self.schedules)
        self.registry.set(ServiceName.AUDIO, self.audio)
        self.registry.set(ServiceName.PHOTOS, self.photo_library)

        self.display.subscribe_resize(self._on_virtual_resize)
        self.notifications.set_viewport(self.display.virtual_size)
        self.device.subscribe(self._on_device_state)
        self.power.set_screen_on(self.device.is_screen_on)

        # System overlays sit above scenes but below the sleep blackout.
        self.power_menu = PowerMenu(
            on_shutdown=self.device.request_shutdown,
            on_restart=self.device.request_reboot,
        )
        self.battery_alert = BatteryAlert(self.power)
        self.call_overlay = CallOverlay(self.telephony, self.theme)
        self.alarm_overlay = AlarmOverlay(self.schedules, self.theme)

        # Settings changes take effect immediately, including factory reset.
        self.settings.subscribe(self._on_setting_changed)
        self._initial_settings_sync = True
        self._on_setting_changed("*", None)
        self._initial_settings_sync = False
        self._last_device_state = self.device.state
        self._power_pointer = False

        self._target_fps = self._refresh_target()
        self._focus_lost = False
        self._scenes: dict[str, SceneFactory] = {}
        self._current: "Scene | None" = None
        self._scene_name = "none"
        self._running = False
        self._shutdown = False

        self._accumulator = 0.0
        self._frame_dt = self.config.fixed_dt
        self._fps = float(self.config.target_fps)
        self._elapsed = 0.0
        self.debug_overlay = bool(self.config.debug_overlay)

        self._fade_alpha = 0.0
        self._fade_duration = 0.25
        self._fade_state = "idle"
        self._pending_scene: str | None = None
        self._qa_return: str | None = None
        self._qa_app: str | None = None
        self._spotlight_return: str = "home"
        self._pending_settings_highlight: str | None = None
        self._fade_surface = pygame.Surface(self.display.virtual_size)
        self._fade_surface.fill((0, 0, 0))
        self._dim_surface = pygame.Surface(self.display.virtual_size)
        self._dim_surface.fill((0, 0, 0))
        self._sleep_surface = pygame.Surface(self.display.virtual_size)
        self._sleep_surface.fill((0, 0, 0))
        self._pointer_layer = pygame.Surface((64, 64), pygame.SRCALPHA)

    # -- boot init steps -----------------------------------------------------
    def _boot_services(self) -> None:
        self.settings.flush()
        _ = self.theme.palette

    def _boot_apps(self) -> None:
        _ = self.app_manager.installed_manifests()

    def _boot_storage(self) -> None:
        self.storage.root.mkdir(parents=True, exist_ok=True)
        _ = self.storage.exists("settings.json")

    def reload_data(self) -> None:
        """Reload every data-backed service after a profile switch/restore."""
        self.app_manager.close_all()
        self.settings.flush()
        self.storage.root = DEFAULT_PATH.parent
        self.settings.switch_path(DEFAULT_PATH)
        self.display.set_orientation(self.settings.get_str("display.orientation", "portrait"))
        self.display.set_scaling_mode(self.settings.get_str("display.scaling", "fit"))
        self.display.set_device_frame(self.settings.get_bool("display.device_frame", False))
        self.display.set_vsync(self.settings.get_bool("display.vsync", False))
        self.theme_engine.reload()
        self.quick_settings.load()
        for reloader in (
            lambda: self.telephony._load_contacts(),
            lambda: self.telephony._load_messages(),
            lambda: self.telephony._load_calls(),
            lambda: self.schedules._load(),
            lambda: self.photo_library.reload(),
        ):
            try:
                reloader()
            except Exception:  # noqa: BLE001
                logging.getLogger("phoneos.kernel").exception("data reload step failed")
        self.app_manager.reload_installed()
        self.home_state.load()
        self.app_manager.bind_home_state(self.home_state)
        self.clipboard.reload()
        self.plugins._disabled = set(self.plugins._load_state())
        self.plugins.reload()
        self.notifications.clear()
        self.theme._cache_key = None
        self.notifications.post(
            "system",
            "Data reloaded",
            "Profile settings and user data are active.",
            self.theme.palette.success,
            banner=False,
        )

    # -- scene management ---------------------------------------------------
    def register_scene(self, name: str, factory: SceneFactory) -> None:
        if not name:
            raise ValueError("scene name must be a non-empty string")
        if not callable(factory):
            raise TypeError("scene factory must be callable")
        self._scenes[name] = factory

    @property
    def scene_name(self) -> str:
        return self._scene_name

    @property
    def current_scene(self) -> "Scene | None":
        return self._current

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def fixed_dt(self) -> float:
        return self.config.fixed_dt

    def switch_scene(self, name: str, fade: float = 0.25) -> None:
        """Transition to a registered scene, optionally through a fade."""
        if self.settings.get_bool("accessibility.reduce_motion", False):
            fade = 0.0
        if name not in self._scenes:
            raise KeyError(
                f"scene {name!r} is not registered (known: {', '.join(sorted(self._scenes)) or 'none'})"
            )
        if self._fade_state == "out":
            # Already fading toward a different scene; ignore the new request.
            return
        if fade <= 0.0:
            self._activate(name)
            self._fade_alpha = 0.0
            self._fade_state = "idle"
            self._pending_scene = None
            return
        if self._current is not None and name == self._scene_name:
            return
        self._pending_scene = name
        self._fade_state = "out"
        self._fade_duration = max(0.05, float(fade))

    def _activate(self, name: str) -> None:
        factory = self._scenes.get(name)
        if factory is None:
            raise KeyError(f"scene {name!r} is not registered")
        self.notifications.reset_view()
        if self._current is not None:
            self._current.on_exit()
        previous_name = self._scene_name
        scene = factory(self)
        self._current = scene
        self._scene_name = name
        scene.on_enter()
        if name == "home" and previous_name == "lock":
            self.audio.play_ui("unlock")
        # Keep the power state in sync after the scene exists, so the state
        # listener can never request a competing scene transition.
        if name == "lock":
            self.device.lock()
        elif name in ("home", "app", "settings", "qa"):
            self.device.unlock()
        # Returning from the F2 self-test restores the app that was running.
        if name == "app" and self._qa_app is not None:
            app_id = self._qa_app
            self._qa_app = None
            self.app_manager.switch_to(app_id)

    def _update_transition(self, dt: float) -> None:
        if self._fade_state == "idle":
            return
        step = dt / self._fade_duration * 255.0
        if self._fade_state == "out":
            self._fade_alpha = min(255.0, self._fade_alpha + step)
            if self._fade_alpha >= 255.0:
                pending = self._pending_scene
                self._pending_scene = None
                self._fade_state = "in"
                if pending is not None:
                    self._activate(pending)
        else:
            self._fade_alpha = max(0.0, self._fade_alpha - step)
            if self._fade_alpha <= 0.0:
                self._fade_state = "idle"
                self._fade_alpha = 0.0

    # -- main loop ----------------------------------------------------------
    def _refresh_target(self) -> int:
        value = self.settings.get_int("display.refresh_rate", self.config.target_fps)
        return max(30, min(120, int(value)))

    def _target_for_frame(self) -> int:
        return 15 if self._focus_lost else self._target_fps

    @staticmethod
    def _precise_wait(seconds: float) -> None:
        """Sleep most of the remainder, then busy-wait the final 1.2 ms."""
        if seconds <= 0.0:
            return
        deadline = time.perf_counter() + seconds
        tail = 0.0012
        if seconds > tail:
            time.sleep(seconds - tail)
        while time.perf_counter() < deadline:
            pass

    def run(self, initial_scene: str, frame_limit: int | None = None) -> None:
        """Boot into ``initial_scene`` and run until the window closes.

        ``frame_limit`` exists for smoke tests: it stops the loop after N frames.
        """
        self._activate(initial_scene)
        self._running = True
        frames = 0
        last = time.perf_counter()
        try:
            while self._running:
                frame_started = time.perf_counter()
                raw_dt = max(0.0, frame_started - last)
                last = frame_started
                frame_dt = min(raw_dt, MAX_FRAME_DT)
                self._frame_dt = frame_dt
                if raw_dt > 0.0:
                    self._fps = self._fps * 0.9 + (1.0 / raw_dt) * 0.1

                self._process_events()
                if not self._running:
                    break

                cpu_started = time.perf_counter()
                if self._focus_lost:
                    # Battery/simulation time is frozen while alt-tabbed;
                    # settings autosave still runs so pending writes land.
                    self.settings.update(raw_dt)
                    self._accumulator = 0.0
                else:
                    self._accumulator += frame_dt
                    steps = 0
                    while (
                        self._accumulator >= self.config.fixed_dt
                        and steps < MAX_STEPS_PER_FRAME
                    ):
                        self._step(self.config.fixed_dt)
                        self._accumulator -= self.config.fixed_dt
                        steps += 1
                    if steps >= MAX_STEPS_PER_FRAME:
                        # Too far behind: drop the backlog instead of spiralling.
                        self._accumulator = 0.0

                self._render()
                cpu_ms = (time.perf_counter() - cpu_started) * 1000.0
                self.profiler.record_frame(raw_dt * 1000.0, cpu_ms)
                frames += 1
                if frames == 8:
                    # Drop warm-up: font/surface caches, the first wallpaper
                    # and the window's first vsync often produce one long
                    # frame. The overlay reports steady state instead.
                    self.profiler.reset()

                target = self._target_for_frame()
                interval = 1.0 / max(1, target)
                render_finished = time.perf_counter()
                remaining = interval - (render_finished - frame_started)
                if remaining > 0.0:
                    self._precise_wait(remaining)
                if frame_limit is not None and frames >= frame_limit:
                    self._running = False
        finally:
            self.shutdown()

    def stop(self) -> None:
        self._running = False

    def _on_setting_changed(self, key: str, value: object) -> None:
        """Push settings that affect the device and window into live services."""
        if key == "*" and hasattr(self, "audio") and not getattr(self, "_initial_settings_sync", False):
            self.audio.stop_music(clear_state=True)
        if key in ("display.power_saver", "*"):
            self.device_clock.drain_multiplier = 0.5 if self.theme.power_saver else 1.0
        if key in ("display.window_mode", "*"):
            self.display.set_window_mode(self.settings.get_str("display.window_mode", "windowed"))
        if key in ("display.scaling", "*"):
            self.display.set_scaling_mode(self.settings.get_str("display.scaling", "fit"))
        if key in ("display.orientation", "*"):
            self.display.set_orientation(self.settings.get_str("display.orientation", "portrait"))
        if key in ("display.device_frame", "*"):
            self.display.set_device_frame(self.settings.get_bool("display.device_frame", False))
        if key in ("display.vsync", "*"):
            self.display.set_vsync(self.settings.get_bool("display.vsync", False))
        if key in ("display.refresh_rate", "*"):
            self._target_fps = self._refresh_target()
        if key in ("accessibility.large_touch", "*"):
            from ui import widgets as widget_controls

            widget_controls.set_touch_expand(8 if self.settings.get_bool("accessibility.large_touch", False) else 0)
        if key in ("simulation.clock_speed", "*"):
            self.device_clock.time_scale = float(self.settings.get_int("simulation.clock_speed", 1))
        if key in ("display.window_width", "display.window_height"):
            # Settings > Display applies the two fields through one explicit
            # call; avoid a second mode switch when the pair is written.
            width = self.settings.get_int("display.window_width", 0)
            height = self.settings.get_int("display.window_height", 0)
            if width > 0 and height > 0:
                self.display.set_window_size((width, height))

    def _on_notification_action(self, item, action_id: str, text: str) -> None:
        if item.app_id == "messages":
            contact_id = str(item.extra.get("contact_id", ""))
            if action_id == "reply" and text and contact_id:
                self.telephony.send_message(contact_id, text)
                self.notifications.post(
                    "messages",
                    "Reply sent",
                    f"To {item.title}",
                    self.theme.palette.success,
                    banner=False,
                )
            elif action_id == "mark_read" and contact_id:
                self.telephony.mark_thread_read(contact_id)
            return
        if item.app_id == "clock":
            if action_id == "snooze":
                self.schedules.snooze()
            elif action_id in ("dismiss", "mark_read"):
                self.schedules.dismiss()

    def _on_virtual_resize(self, old: tuple[int, int], new: tuple[int, int]) -> None:
        """Fan one display rotation out to every cached surface."""
        layout_state.configure(new)
        self.input.cancel()
        self.fx.resize(new)
        self.app_manager.resize(new)
        self.notifications.set_viewport(new)
        self._fade_surface = pygame.Surface(new)
        self._fade_surface.fill((0, 0, 0))
        self._dim_surface = pygame.Surface(new)
        self._dim_surface.fill((0, 0, 0))
        self._sleep_surface = pygame.Surface(new)
        self._sleep_surface.fill((0, 0, 0))
        scene = self._current
        if scene is not None:
            scene.on_canvas_resize(old, new)

    def _on_device_state(self, old: DeviceState, new: DeviceState) -> None:
        """React to power transitions without blocking the main loop."""
        self.power.set_screen_on(self.device.is_screen_on)
        if new in (DeviceState.SLEEP, DeviceState.SHUTDOWN, DeviceState.REBOOT):
            self.power_menu.close()
            self.input.cancel()
        if new is DeviceState.BOOT:
            if self._scene_name != "boot":
                self.switch_scene("boot", fade=0.0)
        elif new is DeviceState.LOCKED and old in (
            DeviceState.BOOT,
            DeviceState.SLEEP,
            DeviceState.CHARGING,
        ):
            target = "lock"
            if old is DeviceState.BOOT and not self.settings.get_bool("system.onboarded", True) and "onboarding" in self._scenes:
                target = "onboarding"
            if self._scene_name != target and self._pending_scene != target:
                self.switch_scene(target, fade=0.30 if old is DeviceState.BOOT else 0.22)

    def _set_focus(self, focused: bool) -> None:
        if focused == (not self._focus_lost):
            return
        self._focus_lost = not focused
        if self._focus_lost:
            self._accumulator = 0.0
            self.input.cancel()

    def _process_events(self) -> None:
        focus_lost = getattr(pygame, "WINDOWFOCUSLOST", None)
        focus_gained = getattr(pygame, "WINDOWFOCUSGAINED", None)
        minimized = getattr(pygame, "WINDOWMINIMIZED", None)
        restored = getattr(pygame, "WINDOWRESTORED", None)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self._running = False
            elif event.type == pygame.VIDEORESIZE:
                # Drag-resize and Settings > Resolution share one entry point.
                if self.display.window_mode == "windowed":
                    self.display.set_window_size((event.w, event.h))
                    final_w, final_h = self.display.windowed_size
                    self.settings.set("display.window_width", final_w)
                    self.settings.set("display.window_height", final_h)
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_F1:
                self.debug_overlay = not self.debug_overlay
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_SLASH and self._scene_name == "home":
                self.open_spotlight()
            elif (
                event.type == pygame.KEYDOWN
                and event.key == pygame.K_F2
                and "qa" in self._scenes
            ):
                self.toggle_qa()
            elif (
                event.type == pygame.KEYDOWN
                and event.key == pygame.K_q
                and (event.mod & pygame.KMOD_CTRL)
            ):
                self._running = False
            elif (focus_lost is not None and event.type == focus_lost) or (
                minimized is not None and event.type == minimized
            ):
                self._set_focus(False)
            elif (focus_gained is not None and event.type == focus_gained) or (
                restored is not None and event.type == restored
            ):
                self._set_focus(True)
            else:
                self.input.handle_event(event)

    def open_spotlight(self) -> None:
        if self._fade_state != "idle" or "search" not in self._scenes:
            return
        self._spotlight_return = self._scene_name if self._scene_name != "search" else "home"
        self.switch_scene("search", fade=0.12)

    def toggle_qa(self) -> None:
        """F2: show the self-test scene, preserving any running app."""
        if self._fade_state != "idle":
            return
        if self._scene_name == "qa":
            self.close_qa()
            return
        self._qa_return = self._scene_name
        self._qa_app = self.app_manager.active_id
        self.switch_scene("qa", fade=0.12)

    def close_qa(self) -> None:
        target = self._qa_return or "home"
        self._qa_return = None
        if target != "app":
            self._qa_app = None
        self.switch_scene(target, fade=0.12)

    def _step(self, dt: float) -> None:
        self._elapsed += dt
        self.settings.update(dt)
        self._target_fps = self._refresh_target()
        self.device.update(dt)
        self.audio.update(dt)
        active_call = self.telephony.active_call
        self.audio.sync_alerts(
            bool(active_call is not None and active_call.is_ringing),
            bool(self.schedules.active_event and self.schedules.active_event.get("kind") == "alarm"),
        )
        self.power.set_screen_on(self.device.is_screen_on)
        self.fx.update(dt, self.theme.palette, self.theme.is_dark)
        self.device_clock.update(dt)
        self.power.update(dt)
        self.telephony.update(dt)
        self.schedules.update(dt)
        if self.schedules.has_active_event and self.device.state in (DeviceState.SLEEP, DeviceState.CHARGING):
            self.device.wake()
        self.sim_events.update(dt)
        if self.call_overlay.is_open:
            self.notifications.close_shade()
        self.call_overlay.update(dt)
        self.alarm_overlay.update(dt)
        self.power_menu.update(dt)
        self.input.update(dt)
        self.notifications.update(dt)
        self.toast.update(dt)
        self.backup.update(dt)
        self.status_bar.update(dt)

        for event in self.input.drain():
            if event.kind is EventKind.TAP:
                self.audio.play_ui("click")
            elif event.kind is EventKind.SWIPE:
                self.audio.play_ui("swipe")
            if self._handle_system_input(event):
                continue
            # The notification shade owns the top edge and, while expanded,
            # every event, so scenes never act on touches they cannot see.
            if self.notifications.handle(event):
                continue
            scene = self._current
            if scene is None:
                break
            scene.handle(event)
            if self._current is not scene:
                break

        if self._current is not None:
            self._current.update(dt)

        self._update_transition(dt)

    def _handle_system_input(self, event) -> bool:
        """Overlays, screen-off wake and status-bar buttons before scenes."""
        if self.device.state in (DeviceState.SLEEP, DeviceState.CHARGING):
            if event.kind in (EventKind.TAP, EventKind.KEY_DOWN):
                self.device.wake()
            return True
        if self.device.state is DeviceState.SHUTDOWN:
            if event.kind in (EventKind.TAP, EventKind.KEY_DOWN):
                self.device.power_button()
            return True
        if self.device.state is DeviceState.REBOOT:
            return True

        if self.alarm_overlay.is_open:
            return self.alarm_overlay.handle(event)
        if self.power_menu.is_open:
            return self.power_menu.handle(event)
        if self.call_overlay.is_open:
            return self.call_overlay.handle(event)
        if self.battery_alert.is_open:
            return self.battery_alert.handle(event)
        if self.toast.handle(event):
            return True

        if event.kind is EventKind.LONG_PRESS:
            if self.status_bar.hit_test(event.pos, layout_state.layout.width) == "power":
                self.power_menu.open()
                return True
            return False
        if event.kind in (
            EventKind.TAP,
            EventKind.DOUBLE_TAP,
            EventKind.POINTER_DOWN,
            EventKind.POINTER_UP,
        ):
            action = self.status_bar.hit_test(event.pos, layout_state.layout.width)
            if action is None:
                return False
            if event.kind is EventKind.POINTER_DOWN:
                self._power_pointer = True
                return True
            if event.kind is EventKind.POINTER_UP:
                self._power_pointer = False
                return True
            self._power_pointer = False
            if action == "power":
                self.device.power_button()
            elif action == "battery":
                self.power.toggle_charging()
            else:
                direction = 1 if action == "vol_up" else -1
                volume = max(0.0, min(1.0, self.settings.get_float("sound.master_volume", 0.6) + direction * 0.1))
                self.settings.set("sound.master_volume", round(volume, 2))
                self.notifications.post(
                    "system",
                    "Volume",
                    f"{volume * 100:.0f}%",
                    self.theme.palette.accent,
                    banner=True,
                )
            return True
        return False

    # -- rendering ----------------------------------------------------------
    def _render(self) -> None:
        canvas = self.display.canvas
        canvas.fill((0, 0, 0))
        scene = self._current
        scene_started = time.perf_counter()
        if scene is not None:
            scene.draw(canvas)
            if scene.shows_status_bar:
                self.status_bar.draw(
                    canvas,
                    self.theme,
                    self.device_clock,
                    self.notifications.unread_count,
                    self.network,
                )
        else:
            canvas.fill(self.theme.palette.bg_bottom)

        # Shade + transient banner sit above scene chrome but below overlays.
        self.notifications.draw(canvas, self.theme)
        self.call_overlay.draw(canvas)
        self.power_menu.draw(canvas, self.theme)
        self.battery_alert.draw(canvas, self.theme)
        self.alarm_overlay.draw(canvas)
        self.toast.draw(canvas, self.theme)
        if self.settings.get_bool("quick.flashlight", False):
            self._draw_flashlight(canvas)
        self.profiler.record_scene(
            self._scene_name,
            (time.perf_counter() - scene_started) * 1000.0,
        )

        # numpy displacement smear instead of a hard cut. The amount peaks at
        # the activation boundary, so the scene swap happens while the image is
        # maximally sheared and is therefore hidden.
        if self._fade_state != "idle" and not self.settings.get_bool("accessibility.reduce_motion", False):
            if self._fade_state == "out":
                amount = self._fade_alpha / 255.0
            else:
                amount = 1.0 - self._fade_alpha / 255.0
            self.fx.displace(canvas, amount * 0.9, phase=self._elapsed)
            dip = int(50.0 * amount)
            if dip > 1:
                self._fade_surface.set_alpha(dip)
                canvas.blit(self._fade_surface, (0, 0))

        # CRT is deliberately after the transition and before brightness so the
        # scanlines feel like part of the panel. Off by default.
        self.fx.apply_crt(canvas)

        if self.input.keyboard_mode:
            self._draw_pointer(canvas)

        # Brightness (and the extra 15% saved by power saver) dims the whole
        # finished frame, exactly like a backlight.
        factor = self.theme.brightness_factor * self.power.emergency_dim_factor
        if factor < 0.999:
            self._dim_surface.set_alpha(int(round((1.0 - factor) * 255.0)))
            canvas.blit(self._dim_surface, (0, 0))

        if self.debug_overlay and self.device.screen_alpha < 0.98:
            self._draw_debug(canvas)

        # Screen-off fade is last: nothing, not even the debug overlay or an
        # incoming call, is visible through a sleeping panel.
        if self.device.screen_alpha > 0.001:
            self._sleep_surface.set_alpha(int(round(self.device.screen_alpha * 255.0)))
            canvas.blit(self._sleep_surface, (0, 0))

        self.display.present()

    def _draw_flashlight(self, canvas: pygame.Surface) -> None:
        """A warm rear-LED bloom, drawn from primitives (no assets)."""
        width, height = canvas.get_size()
        layer = pygame.Surface((width, height), pygame.SRCALPHA)
        glow = pygame.Surface((240, 240), pygame.SRCALPHA)
        for radius, alpha in ((118, 18), (92, 26), (64, 34), (38, 46)):
            pygame.draw.circle(glow, (255, 236, 186, alpha), (120, 120), radius)
        layer.blit(glow, glow.get_rect(center=(width // 2, 12)))
        canvas.blit(layer, (0, 0))

    def _draw_pointer(self, canvas: pygame.Surface) -> None:
        layer = self._pointer_layer
        layer.fill((0, 0, 0, 0))
        center = (32, 32)
        pygame.draw.circle(layer, (255, 255, 255, 60), center, 16, 1)
        pygame.draw.circle(layer, (255, 255, 255, 170), center, 5, 1)
        pygame.draw.circle(layer, (255, 255, 255, 120), center, 1)
        x, y = self.input.pointer
        canvas.blit(layer, layer.get_rect(center=(int(round(x)), int(round(y)))))

    def _draw_debug(self, surface: pygame.Surface) -> None:
        clock = self.device_clock
        compact = layout_state.layout.compact
        font = self.display.mono_font(14 if compact else 17)
        small = self.display.mono_font(12 if compact else 14)
        line_h = 15 if compact else 20

        rows: list[tuple[str, str]] = [
            ("fps", f"{self._fps:6.1f} / {self._target_for_frame()}"),
            ("display", f"{self.display.window_mode}/{self.display.scaling_mode}"),
            ("canvas", f"{self.display.virtual_size[0]}x{self.display.virtual_size[1]} {self.display.orientation}"),
            ("window", f"{self.display.window_size[0]}x{self.display.window_size[1]} vsync={int(self.display.vsync_active)}"),
            ("focus", "PAUSED @15" if self._focus_lost else "active"),
            ("frame", f"{self._frame_dt * 1000.0:6.2f} ms"),
            ("dt", f"{self.config.fixed_dt * 1000.0:6.2f} ms"),
            ("acc", f"{self._accumulator * 1000.0:6.2f} ms"),
            ("scene", self._scene_name),
            ("battery", f"{clock.battery_percent:5.1f}%  {clock.power_draw_ma:4.0f} mA"),
            ("cpu", f"{clock.cpu_load * 100.0:5.1f}%"),
            ("device", clock.seconds_string()),
            ("uptime", clock.uptime_string()),
            ("theme", f"{self.theme.mode}/{self.theme.accent_name}"),
            ("bright", f"{self.theme.brightness_factor * 100.0:5.1f}%"),
            ("notif", f"{self.notifications.unread_count} unread"),
            ("avg", f"{self.profiler.avg_frame_ms:6.2f} ms"),
            ("worst", f"{self.profiler.worst_frame_ms:6.2f} ms"),
            ("cpu avg", f"{self.profiler.avg_cpu_ms:6.2f} ms"),
            ("worst cpu", f"{self.profiler.worst_cpu_ms:6.2f} ms"),
            ("wall", self.fx.wallpaper_name),
        ]
        rows.extend(self.fx.cost_rows())
        for name, average, count in self.profiler.effect_rows(4):
            rows.append((f"fx {name[:8]}", f"{average:6.2f} ms x{count}"))
        for scene_name, average, count in self.profiler.scene_breakdown(3):
            rows.append((f"sc {scene_name[:8]}", f"{average:6.2f} ms x{count}"))

        label_width = max(font.size(label)[0] for label, _ in rows) + 14
        value_width = max(font.size(value)[0] for _, value in rows)
        width = label_width + value_width + 30
        height = 28 + len(rows) * line_h + 8

        panel = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.rect(panel, (3, 8, 14, 210), panel.get_rect(), border_radius=12)
        pygame.draw.rect(panel, (72, 232, 208, 90), panel.get_rect(), width=1, border_radius=12)
        panel.blit(small.render("debug overlay  [F1]", True, (110, 226, 210)), (15, 7))

        y = 28
        for label, value in rows:
            panel.blit(font.render(label, True, (116, 134, 158)), (15, y))
            panel.blit(font.render(value, True, (232, 244, 255)), (15 + label_width, y))
            y += line_h

        surface.blit(panel, (14, 14))

    # -- teardown -----------------------------------------------------------
    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._running = False
        if self._current is not None:
            self._current.on_exit()
            self._current = None
        self.schedules.shutdown()
        self.audio.shutdown()
        self.app_manager.shutdown()
        self.settings.flush()
        pygame.quit()

    def __repr__(self) -> str:
        return (
            f"Kernel(scene={self._scene_name}, fps={self._fps:.1f}, "
            f"running={self._running})"
        )
