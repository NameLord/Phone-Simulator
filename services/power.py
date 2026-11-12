"""Battery, charging and screen-power simulation.

The kernel creates this service with the active-app power-draw callback. It
owns the battery percentage in :class:`core.clock.DeviceClock`, so all existing
widgets (status bar, lock screen, settings) keep reading the same source of
truth while drain becomes app-aware.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from core.clock import SECONDS_PER_HOUR

if TYPE_CHECKING:
    from core.clock import DeviceClock
    from services.device import DeviceService
    from services.notifications import NotificationCenter
    from services.settings import SettingsService
    from ui.theme import Theme

CAPACITY_MAH = 4300.0
SYSTEM_MA = 22.0
SCREEN_MA = 105.0
CHARGE_MA = 920.0
SAMPLE_SECONDS = 2.0

LOW_THRESHOLD = 15.0
CRITICAL_THRESHOLD = 5.0


@dataclass(slots=True)
class BatterySample:
    percent: float
    charging: bool
    uptime: float


class PowerService:
    """Rolling battery model with app draw, charging and threshold alerts."""

    def __init__(
        self,
        settings: "SettingsService",
        clock: "DeviceClock",
        notifications: "NotificationCenter",
        theme: "Theme",
        app_draw: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._notifications = notifications
        self._theme = theme
        self._app_draw = app_draw or (lambda: 0.0)
        self._device: "DeviceService | None" = None

        self._charging = False
        self._screen_on = True
        self._sample_timer = 0.0
        self._low_warned = False
        self._critical_dismissed = False
        self._last_percent = clock.battery_percent
        self.history: deque[BatterySample] = deque(maxlen=120)
        self._record_sample()

    # -- bindings -------------------------------------------------------------
    def bind_device(self, device: "DeviceService") -> None:
        self._device = device

    def set_screen_on(self, enabled: bool) -> None:
        self._screen_on = bool(enabled)

    # -- state ---------------------------------------------------------------
    @property
    def charging(self) -> bool:
        return self._charging

    @property
    def percent(self) -> float:
        return self._clock.battery_percent

    @property
    def is_low(self) -> bool:
        return self.percent <= LOW_THRESHOLD

    @property
    def is_critical(self) -> bool:
        return self.percent <= CRITICAL_THRESHOLD

    @property
    def critical_alert(self) -> bool:
        return self.is_critical and not self._critical_dismissed

    @property
    def emergency_dim_factor(self) -> float:
        if self.percent <= CRITICAL_THRESHOLD:
            return 0.62
        if self.percent <= LOW_THRESHOLD:
            return 0.86
        return 1.0

    @property
    def power_draw_ma(self) -> float:
        return self._current_draw_ma()

    @property
    def percent_per_hour(self) -> float:
        if self._charging:
            return max(0.0, (CHARGE_MA - self._current_draw_ma()) / CAPACITY_MAH * 100.0)
        return self._current_draw_ma() / CAPACITY_MAH * 100.0

    def history_points(self) -> list[tuple[float, float]]:
        """Return ``(index, percent)`` points for the settings graph."""
        count = len(self.history)
        return [
            (index / max(1, count - 1), sample.percent)
            for index, sample in enumerate(self.history)
        ]

    # -- charging -------------------------------------------------------------
    def plug_in(self) -> None:
        if self._charging:
            return
        self._charging = True
        self._clock.charging = True
        self._critical_dismissed = True
        self._notifications.post(
            "power",
            "Charging",
            f"Connected. {self.percent:.0f}% and climbing.",
            self._theme.palette.success,
            banner=True,
        )
        if self._device is not None:
            self._device.note_charging(True)
        self._record_sample()

    def unplug(self) -> None:
        if not self._charging:
            return
        self._charging = False
        self._clock.charging = False
        self._notifications.post(
            "power",
            "Unplugged",
            "Running on battery.",
            self._theme.palette.warning,
            banner=False,
        )
        if self._device is not None:
            self._device.note_charging(False)
        self._record_sample()

    def toggle_charging(self) -> bool:
        if self._charging:
            self.unplug()
        else:
            self.plug_in()
        return self._charging

    def acknowledge_critical(self) -> None:
        self._critical_dismissed = True

    # -- simulation -----------------------------------------------------------
    def _current_draw_ma(self) -> float:
        brightness = max(0.15, min(1.0, self._settings.get_float("display.brightness", 0.9)))
        power_saver = self._settings.get_bool("display.power_saver", False)
        screen_factor = 1.0 if self._screen_on else 0.06
        draw = SYSTEM_MA + (SCREEN_MA * brightness + self._app_draw()) * screen_factor
        if power_saver:
            draw *= 0.58
        multiplier = max(0.25, min(4.0, self._settings.get_float("simulation.drain_multiplier", 1.0)))
        return max(1.0, draw * multiplier)

    def update(self, dt: float) -> None:
        dt = max(0.0, float(dt))
        if dt <= 0.0:
            return
        percent = self.percent
        hours = dt / SECONDS_PER_HOUR
        if self._charging:
            net_ma = CHARGE_MA - self._current_draw_ma()
            percent += net_ma / CAPACITY_MAH * 100.0 * hours
            if percent >= 100.0:
                percent = 100.0
        else:
            percent -= self._current_draw_ma() / CAPACITY_MAH * 100.0 * hours
        percent = max(0.0, min(100.0, percent))
        self._clock.battery_percent = percent
        self._clock.charging = self._charging

        self._sample_timer += dt
        if self._sample_timer >= SAMPLE_SECONDS:
            self._sample_timer = 0.0
            self._record_sample()

        self._thresholds(percent)

    def _thresholds(self, percent: float) -> None:
        if percent > LOW_THRESHOLD + 1.0:
            self._low_warned = False
            self._critical_dismissed = False
        if percent <= LOW_THRESHOLD and not self._low_warned:
            self._low_warned = True
            self._notifications.post(
                "power",
                "Battery low",
                f"{percent:.0f}% remaining. Plug in or enable power saver.",
                self._theme.palette.warning,
                banner=True,
            )
        if percent <= 0.0 and self._device is not None and not self._charging:
            self._critical_dismissed = True
            self._device.force_sleep()

    def _record_sample(self) -> None:
        self.history.append(
            BatterySample(
                percent=self.percent,
                charging=self._charging,
                uptime=self._clock.uptime,
            )
        )

    def __repr__(self) -> str:
        return (
            f"PowerService(percent={self.percent:.1f}, charging={self._charging}, "
            f"draw={self._current_draw_ma():.0f}mA)"
        )
