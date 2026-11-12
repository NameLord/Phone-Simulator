"""Simulated device time, battery drain and CPU load.

No pygame dependency: the model is deterministic for a given seed, which makes
it testable and keeps the debug overlay honest.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime

SECONDS_PER_HOUR = 3600.0


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


@dataclass(frozen=True)
class BatteryModel:
    """Linear-ish battery model expressed in milliamps."""

    capacity_mah: float = 4300.0
    screen_ma: float = 112.0
    idle_ma: float = 38.0
    load_ma: float = 540.0
    charge_ma: float = 900.0

    def drain_ma(self, load: float) -> float:
        return self.screen_ma + self.idle_ma + self.load_ma * _clamp(load, 0.0, 1.0)

    def percent_per_hour(self, load: float) -> float:
        return self.drain_ma(load) / self.capacity_mah * 100.0


class DeviceClock:
    """Wall clock + battery + synthetic CPU load for the virtual device."""

    def __init__(
        self,
        *,
        start: datetime | None = None,
        battery: float = 82.0,
        time_scale: float = 1.0,
        seed: int = 0x5EED,
        model: BatteryModel | None = None,
        manage_battery: bool = True,
    ) -> None:
        self.model = model or BatteryModel()
        # The kernel lets services/power.py own battery drain (app draw,
        # brightness, charging, history). Direct DeviceClock users keep the
        # original self-contained model.
        self.manage_battery = bool(manage_battery)
        self._rng = random.Random(seed)
        self._time_scale = max(0.0, float(time_scale))
        self._epoch = (start or datetime.now().replace(microsecond=0)).timestamp()
        self._battery = _clamp(float(battery), 0.0, 100.0)
        self._load = 0.18
        self._load_target = 0.26
        self._retarget_in = 0.9
        self._uptime = 0.0
        self._charging = False
        # Set by the kernel from the "power saver" setting (0.5 = half drain).
        self._drain_multiplier = 1.0

    # -- simulation ---------------------------------------------------------
    def update(self, dt: float) -> None:
        """Advance simulated time and the power model by ``dt`` real seconds."""
        dt = max(0.0, float(dt))
        if dt == 0.0:
            return

        self._uptime += dt
        simulated = dt * self._time_scale
        self._epoch += simulated

        self._retarget_in -= dt
        if self._retarget_in <= 0.0:
            self._load_target = _clamp(0.06 + self._rng.random() * 0.50, 0.0, 1.0)
            if self._rng.random() < 0.18:
                self._load_target = _clamp(self._load_target + 0.35, 0.0, 1.0)
            self._retarget_in = self._rng.uniform(0.6, 3.4)
        self._load += (self._load_target - self._load) * min(1.0, dt * 2.4)
        self._load = _clamp(self._load, 0.0, 1.0)

        if self.manage_battery:
            hours = simulated / SECONDS_PER_HOUR
            if self._charging:
                net_ma = self.model.charge_ma - self.model.drain_ma(self._load) * self._drain_multiplier
                self._battery += net_ma / self.model.capacity_mah * 100.0 * hours
            else:
                self._battery -= self.model.percent_per_hour(self._load) * self._drain_multiplier * hours
            self._battery = _clamp(self._battery, 0.0, 100.0)

    def skip(self, seconds: float) -> None:
        """Advance the device state in one jump (used by tests and tools)."""
        self.update(max(0.0, float(seconds)))

    # -- state --------------------------------------------------------------
    @property
    def simulated_time(self) -> datetime:
        return datetime.fromtimestamp(self._epoch)

    @property
    def battery_percent(self) -> float:
        return self._battery

    @battery_percent.setter
    def battery_percent(self, value: float) -> None:
        self._battery = _clamp(float(value), 0.0, 100.0)

    @property
    def battery_level(self) -> int:
        return int(round(self._battery))

    @property
    def is_low_battery(self) -> bool:
        return self._battery <= 15.0

    @property
    def cpu_load(self) -> float:
        return self._load

    @property
    def cpu_percent(self) -> int:
        return int(round(self._load * 100.0))

    @property
    def power_draw_ma(self) -> float:
        return self.model.drain_ma(self._load) * self._drain_multiplier

    @property
    def drain_percent_per_hour(self) -> float:
        return self.model.percent_per_hour(self._load) * self._drain_multiplier

    @property
    def drain_multiplier(self) -> float:
        return self._drain_multiplier

    @drain_multiplier.setter
    def drain_multiplier(self, value: float) -> None:
        self._drain_multiplier = _clamp(float(value), 0.05, 3.0)

    @property
    def uptime(self) -> float:
        return self._uptime

    @property
    def time_scale(self) -> float:
        return self._time_scale

    @time_scale.setter
    def time_scale(self, value: float) -> None:
        self._time_scale = max(0.0, float(value))

    @property
    def charging(self) -> bool:
        return self._charging

    @charging.setter
    def charging(self, value: bool) -> None:
        self._charging = bool(value)

    # -- formatting ---------------------------------------------------------
    def time_string(self) -> str:
        return self.simulated_time.strftime("%H:%M")

    def seconds_string(self) -> str:
        return self.simulated_time.strftime("%H:%M:%S")

    def date_string(self) -> str:
        now = self.simulated_time
        return f"{now.strftime('%A, %B')} {now.day}"

    def uptime_string(self) -> str:
        total = int(self._uptime)
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def __repr__(self) -> str:
        return (
            f"DeviceClock(time={self.seconds_string()}, "
            f"battery={self._battery:.1f}%, cpu={self._load * 100:.0f}%)"
        )
