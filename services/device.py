"""Device state machine: boot, lock, sleep, charge, shutdown and reboot.

All timing is driven by ``update(dt)``. No method sleeps or waits; a transition
always leaves the machine in a valid state so input and the render loop can
keep running. The kernel observes :attr:`state` and reacts by changing scenes,
while this service owns the blackout fade and the boot step progress.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable

LOG = logging.getLogger("phoneos.device")

BOOT_SECONDS = 2.5
SHUTDOWN_SECONDS = 1.5
SLEEP_FADE_SECONDS = 0.4


class DeviceState(Enum):
    BOOT = "boot"
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    SLEEP = "sleep"
    CHARGING = "charging"
    SHUTDOWN = "shutdown"
    REBOOT = "reboot"


@dataclass(slots=True)
class BootStep:
    label: str
    action: Callable[[], object] | None = None
    duration: float = 0.8
    status: str = "pending"


StateListener = Callable[[DeviceState, DeviceState], None]


class DeviceService:
    """Interruptible power/boot state machine."""

    def __init__(
        self,
        settings,
        boot_actions: list[tuple[str, Callable[[], object]]] | None = None,
    ) -> None:
        self._settings = settings
        self._listeners: list[StateListener] = []
        self._state = DeviceState.LOCKED
        self._previous = DeviceState.LOCKED

        self.screen_alpha = 0.0
        self._fade_target = 0.0
        self._fade_speed = 1.0 / SLEEP_FADE_SECONDS

        self.boot_elapsed = 0.0
        self.boot_steps: list[BootStep] = []
        self._boot_actions = list(boot_actions or [])
        self._reset_boot_steps()

        self.shutdown_elapsed = 0.0
        self.auto_reboot = False
        self.reboot_target = False

    # -- listeners ------------------------------------------------------------
    def subscribe(self, callback: StateListener) -> None:
        if callable(callback) and callback not in self._listeners:
            self._listeners.append(callback)

    def unsubscribe(self, callback: StateListener) -> None:
        self._listeners = [item for item in self._listeners if item is not callback]

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def previous_state(self) -> DeviceState:
        return self._previous

    @property
    def is_screen_on(self) -> bool:
        return self._state not in (
            DeviceState.SLEEP,
            DeviceState.CHARGING,
            DeviceState.SHUTDOWN,
            DeviceState.REBOOT,
        )

    @property
    def is_off(self) -> bool:
        return self._state is DeviceState.SHUTDOWN and self.shutdown_elapsed >= SHUTDOWN_SECONDS

    @property
    def boot_progress(self) -> float:
        return max(0.0, min(1.0, self.boot_elapsed / BOOT_SECONDS))

    @property
    def boot_step_index(self) -> int:
        elapsed = 0.0
        for index, step in enumerate(self.boot_steps):
            elapsed += step.duration
            if self.boot_elapsed < elapsed:
                return index
        return max(0, len(self.boot_steps) - 1)

    @property
    def shutdown_progress(self) -> float:
        return max(0.0, min(1.0, self.shutdown_elapsed / SHUTDOWN_SECONDS))

    # -- transitions ----------------------------------------------------------
    def _set_state(self, state: DeviceState) -> None:
        if state is self._state:
            return
        old = self._state
        self._previous = old
        self._state = state
        for callback in list(self._listeners):
            try:
                callback(old, state)
            except Exception:  # noqa: BLE001 - a listener must not deadlock power
                LOG.exception("device state listener failed")

    def begin_boot(self) -> bool:
        if self._state is DeviceState.BOOT:
            return False
        self.boot_elapsed = 0.0
        self.shutdown_elapsed = 0.0
        self.screen_alpha = 0.0
        self._fade_target = 0.0
        self._reset_boot_steps()
        self._set_state(DeviceState.BOOT)
        return True

    def _reset_boot_steps(self) -> None:
        actions = self._boot_actions or [
            ("Services", None),
            ("Applications", None),
            ("Storage", None),
        ]
        total = sum(max(0.3, 0.8) for _ in actions)
        scale = BOOT_SECONDS / max(0.3, total)
        self.boot_steps = [
            BootStep(label=label, action=action, duration=0.8 * scale)
            for label, action in actions
        ]

    def lock(self) -> bool:
        if self._state in (DeviceState.UNLOCKED, DeviceState.CHARGING, DeviceState.BOOT):
            self._fade_target = 0.0
            self._set_state(DeviceState.LOCKED)
            return True
        return False

    def unlock(self) -> bool:
        if self._state in (DeviceState.LOCKED, DeviceState.CHARGING, DeviceState.BOOT):
            self._fade_target = 0.0
            self._set_state(DeviceState.UNLOCKED)
            return True
        return False

    def sleep(self, *, force: bool = False) -> bool:
        if self._state in (DeviceState.SLEEP, DeviceState.SHUTDOWN, DeviceState.REBOOT):
            return False
        if self._state is DeviceState.BOOT and not force:
            return False
        self._fade_target = 1.0
        self._fade_speed = 1.0 / SLEEP_FADE_SECONDS
        self._set_state(DeviceState.SLEEP)
        return True

    def force_sleep(self) -> bool:
        """Battery-empty transition; allowed from boot so 0% never sticks."""
        return self.sleep(force=True)

    def wake(self) -> bool:
        if self._state in (DeviceState.SLEEP, DeviceState.CHARGING):
            self._fade_target = 0.0
            self._fade_speed = 1.0 / SLEEP_FADE_SECONDS
            self._set_state(DeviceState.LOCKED)
            return True
        if self._state is DeviceState.SHUTDOWN and self.is_off:
            return self.begin_boot()
        return False

    def power_button(self) -> bool:
        if self._state is DeviceState.SHUTDOWN:
            return self.begin_boot()
        if self._state in (DeviceState.SLEEP, DeviceState.CHARGING):
            return self.wake()
        if self._state in (DeviceState.LOCKED, DeviceState.UNLOCKED):
            return self.sleep()
        return False

    def note_charging(self, charging: bool) -> None:
        if charging:
            # CHARGING is the screen-off charging state; a locked device stays
            # locked but visibly shows the charging bolt.
            if self._state is DeviceState.SLEEP:
                self._set_state(DeviceState.CHARGING)
        else:
            if self._state is DeviceState.CHARGING:
                self._set_state(DeviceState.LOCKED if self._fade_target < 0.5 else DeviceState.SLEEP)

    def request_shutdown(self, auto_reboot: bool | None = None) -> bool:
        if self._state in (DeviceState.SHUTDOWN, DeviceState.REBOOT):
            return False
        self.auto_reboot = (
            bool(self._settings.get_bool("simulation.auto_reboot", False))
            if auto_reboot is None
            else bool(auto_reboot)
        )
        self.shutdown_elapsed = 0.0
        self.reboot_target = False
        self._fade_target = 1.0
        self._fade_speed = 1.0 / SHUTDOWN_SECONDS
        self._set_state(DeviceState.SHUTDOWN)
        return True

    def request_reboot(self) -> bool:
        if self._state in (DeviceState.REBOOT, DeviceState.SHUTDOWN):
            return False
        self.shutdown_elapsed = 0.0
        self.reboot_target = True
        self._fade_target = 1.0
        self._fade_speed = 1.0 / SHUTDOWN_SECONDS
        self._set_state(DeviceState.REBOOT)
        return True

    # -- update ---------------------------------------------------------------
    def update(self, dt: float) -> None:
        dt = max(0.0, float(dt))
        if dt <= 0.0:
            return

        if self._state is DeviceState.BOOT:
            self.boot_elapsed = min(BOOT_SECONDS, self.boot_elapsed + dt)
            self._advance_boot_steps()
            if self.boot_elapsed >= BOOT_SECONDS:
                self._complete_boot_steps()
                self.screen_alpha = 0.0
                self._fade_target = 0.0
                self._set_state(DeviceState.LOCKED)
        elif self._state in (DeviceState.SHUTDOWN, DeviceState.REBOOT):
            self.shutdown_elapsed = min(SHUTDOWN_SECONDS, self.shutdown_elapsed + dt)
            if self.shutdown_elapsed >= SHUTDOWN_SECONDS:
                if self._state is DeviceState.REBOOT or self.auto_reboot:
                    self.begin_boot()
                # else stay in SHUTDOWN with a black screen until power wakes it.
        # Fade toward the current target at the current rate. Never blocks.
        if self.screen_alpha < self._fade_target:
            self.screen_alpha = min(self._fade_target, self.screen_alpha + dt * self._fade_speed)
        elif self.screen_alpha > self._fade_target:
            self.screen_alpha = max(self._fade_target, self.screen_alpha - dt * self._fade_speed)

    def _complete_boot_steps(self) -> None:
        for step in self.boot_steps:
            if step.status != "done":
                if step.action is not None:
                    try:
                        step.action()
                    except Exception:  # noqa: BLE001 - boot must continue
                        LOG.exception("boot step %s failed", step.label)
                step.status = "done"

    def _advance_boot_steps(self) -> None:
        active = self.boot_step_index
        for index, step in enumerate(self.boot_steps):
            if index < active:
                if step.status != "done":
                    step.status = "running"
                    if step.action is not None:
                        try:
                            step.action()
                        except Exception:  # noqa: BLE001 - boot must continue
                            LOG.exception("boot step %s failed", step.label)
                    step.status = "done"
            elif index == active:
                if step.status != "done":
                    step.status = "active"
            else:
                step.status = "pending"

    def __repr__(self) -> str:
        return (
            f"DeviceService(state={self._state.value}, boot={self.boot_elapsed:.2f}, "
            f"alpha={self.screen_alpha:.2f})"
        )
