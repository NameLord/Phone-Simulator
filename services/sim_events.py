"""Ambient simulation: app notifications, storage warnings and DND.

The point is not random noise for its own sake; it is the small stream of
system events a real phone produces. Events are posted through
:class:`services.notifications.NotificationCenter`, never drawn directly.
"""

from __future__ import annotations

import logging
import os
import random
from typing import TYPE_CHECKING, Callable

from services.device import DeviceState

if TYPE_CHECKING:
    from core.clock import DeviceClock
    from services.app_manager import AppManager
    from services.device import DeviceService
    from services.notifications import NotificationCenter
    from services.power import PowerService
    from services.settings import SettingsService
    from ui.theme import Theme

LOG = logging.getLogger("phoneos.sim_events")

EVENT_MULTIPLIERS = {"off": 0.0, "low": 0.5, "normal": 1.0, "high": 2.5}

# Ambient copy is deliberately mundane.
APP_EVENTS: dict[str, tuple[str, str]] = {
    "store": ("App Store", "3 apps updated in the background"),
    "browser": ("NovaNet", "Traffic ahead on your usual route"),
    "music": ("Music", "Your evening mix is ready"),
    "notes": ("Notes", "2 notes synced to local storage"),
    "calculator": ("Calculator", "Tip: long expressions support parentheses"),
    "phone": ("Phone", "Voicemail is almost full"),
    "messages": ("Messages", "A conversation is waiting for you"),
    "weather": ("Weather", "Rain expected in 1 hour"),
    "maps": ("Maps", "Traffic is heavier than usual"),
    "system": ("Storage", "Storage almost full \u00b7 92% used"),
}


def dnd_active(settings: "SettingsService", clock: "DeviceClock") -> bool:
    """True when the current simulated hour falls in the DND window."""
    if not settings.get_bool("simulation.dnd", False):
        return False
    start = settings.get_int("simulation.dnd_start", 22) % 24
    end = settings.get_int("simulation.dnd_end", 7) % 24
    hour = clock.simulated_time.hour
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


class SimulationEvents:
    """Timer-driven ambient notifications."""

    def __init__(
        self,
        settings: "SettingsService",
        clock: "DeviceClock",
        notifications: "NotificationCenter",
        theme: "Theme",
        apps: "AppManager",
        power: "PowerService",
        device: "DeviceService",
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._notifications = notifications
        self._theme = theme
        self._apps = apps
        self._power = power
        self._device = device
        self._rng = random.Random(0x51E9)
        self._rate_key = (self.event_rate, self._settings.get_int("simulation.notification_rate", 6))
        self._timer = self._next_interval()
        self.last_event = ""

    # -- state ---------------------------------------------------------------
    @property
    def dnd(self) -> bool:
        return dnd_active(self._settings, self._clock)

    @property
    def event_rate(self) -> str:
        rate = self._settings.get_str("simulation.event_rate", "normal")
        return rate if rate in EVENT_MULTIPLIERS else "normal"

    def _next_interval(self) -> float:
        rate = self._settings.get_int("simulation.notification_rate", 6)
        multiplier = EVENT_MULTIPLIERS.get(self.event_rate, 1.0)
        effective = rate * multiplier
        if effective <= 0.0:
            return float("inf")
        mean = 3600.0 / effective
        return max(25.0, self._rng.expovariate(1.0 / mean))

    def _can_emit(self) -> bool:
        if self._device.state in (DeviceState.BOOT, DeviceState.SHUTDOWN, DeviceState.REBOOT):
            return False
        if self.dnd:
            return False
        return EVENT_MULTIPLIERS.get(self.event_rate, 0.0) > 0.0

    # -- ambient events ------------------------------------------------------
    def _ambient_source(self) -> tuple[str, str, str, tuple[int, int, int]]:
        installed = [manifest.app_id for manifest in self._apps.installed_manifests()]
        # Weather/Maps are system surfaces that do not ship a full app, but
        # their notifications are part of the simulated city.
        candidates = installed + ["weather", "maps", "system"]
        app_id = self._rng.choice(candidates) if candidates else "system"
        title, body = APP_EVENTS.get(app_id, ("PhoneOS", "Background activity completed"))
        palette = self._theme.palette
        color = {
            "store": palette.accent,
            "browser": palette.accent,
            "music": palette.violet if hasattr(palette, "violet") else palette.accent,
            "system": palette.warning,
            "weather": palette.accent,
            "maps": palette.success,
        }.get(app_id, palette.text_secondary)
        return app_id, title, body, color

    def force_event(self) -> bool:
        """Settings/QA hook: emit one ambient event immediately."""
        if not self._can_emit():
            return False
        app_id, title, body, color = self._ambient_source()
        self._notifications.post(app_id, title, body, color, banner=True)
        self.last_event = f"{app_id}: {title}"
        self._timer = self._next_interval()
        return True

    def _storage_warning(self) -> None:
        root = getattr(self._apps, "storage_root", None)
        size = 0
        if root is not None and os.path.isdir(root):
            for name in os.listdir(root):
                try:
                    size += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        detail = "Storage almost full \u00b7 local data is growing." if size > 256_000 else "Storage almost full \u00b7 92% used."
        self._notifications.post(
            "system",
            "Storage",
            detail,
            self._theme.palette.warning,
            banner=True,
        )
        self.last_event = "storage warning"

    def update(self, dt: float) -> None:
        dt = max(0.0, float(dt))
        if dt <= 0.0:
            return
        rate_key = (self.event_rate, self._settings.get_int("simulation.notification_rate", 6))
        if rate_key != self._rate_key:
            self._rate_key = rate_key
            self._timer = self._next_interval()
        if not self._can_emit():
            return
        self._timer -= dt
        if self._timer > 0.0:
            return
        self._timer = self._next_interval()
        if self._power.is_low and self._rng.random() < 0.35:
            return  # the power service owns low-battery alerts
        if self._rng.random() < 0.10:
            self._storage_warning()
            return
        self.force_event()

    def __repr__(self) -> str:
        return f"SimulationEvents(rate={self.event_rate}, dnd={self.dnd}, last={self.last_event!r})"
