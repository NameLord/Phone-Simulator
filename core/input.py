"""Mouse-as-touch input abstraction.

Raw pygame events are converted to virtual coordinates and fed through a small
gesture state machine that emits :class:`PointerEvent` objects:

    tap         press + release, <= 450 ms, movement <= 10 virtual px
    long_press  held >= 450 ms without leaving the 10 px slop
    drag        movement past the slop, released too slowly / too briefly to swipe
    swipe       >= 40 virtual px released within 300 ms, with velocity + direction
    double_tap  two taps within 260 ms inside a 44 px radius

Every event is queued for :meth:`InputManager.drain` (the kernel feeds these to
the active scene) and mirrored to any callbacks registered with
:meth:`InputManager.subscribe`.

Keyboard fallback: arrows / WASD move a virtual pointer, Shift accelerates it,
Space or Enter synthesise a press/release at that pointer (so taps, long presses
and drags all work without a mouse). Every other key is forwarded as KEY_DOWN /
KEY_UP for scene shortcuts.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Deque, Sequence

import pygame

from core.display import Display


class EventKind(Enum):
    POINTER_DOWN = "pointer_down"
    POINTER_UP = "pointer_up"
    TAP = "tap"
    DOUBLE_TAP = "double_tap"
    LONG_PRESS = "long_press"
    DRAG_START = "drag_start"
    DRAG = "drag"
    DRAG_END = "drag_end"
    SWIPE = "swipe"
    WHEEL = "wheel"
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"


class Direction(str, Enum):
    NONE = "none"
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


@dataclass(slots=True)
class PointerEvent:
    """A decoded input event in virtual coordinates."""

    kind: EventKind
    pos: tuple[float, float] = (0.0, 0.0)
    start_pos: tuple[float, float] = (0.0, 0.0)
    delta: tuple[float, float] = (0.0, 0.0)
    distance: float = 0.0
    duration: float = 0.0
    velocity: tuple[float, float] = (0.0, 0.0)
    speed: float = 0.0
    direction: Direction = Direction.NONE
    button: int = 1
    key: int | None = None
    mods: int = 0
    text: str = ""
    synthetic: bool = False
    handled: bool = False

    def consume(self) -> None:
        """Mark the event as handled by the receiving scene."""
        self.handled = True

    def __str__(self) -> str:
        return (
            f"<{self.kind.value} pos=({self.pos[0]:.1f},{self.pos[1]:.1f}) "
            f"dir={self.direction.value} dur={self.duration * 1000.0:.0f}ms "
            f"speed={self.speed:.0f}px/s>"
        )


Listener = Callable[[PointerEvent], None]


class InputManager:
    """Gesture decoding for mouse and keyboard, expressed in virtual pixels."""

    _MOVE_VECTORS: dict[int, tuple[float, float]] = {
        pygame.K_LEFT: (-1.0, 0.0),
        pygame.K_a: (-1.0, 0.0),
        pygame.K_RIGHT: (1.0, 0.0),
        pygame.K_d: (1.0, 0.0),
        pygame.K_UP: (0.0, -1.0),
        pygame.K_w: (0.0, -1.0),
        pygame.K_DOWN: (0.0, 1.0),
        pygame.K_s: (0.0, 1.0),
    }
    _ACTIVATE_KEYS = (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER)

    def __init__(
        self,
        display: Display,
        *,
        long_press_ms: float = 450.0,
        tap_slop: float = 10.0,
        swipe_min_distance: float = 40.0,
        swipe_max_ms: float = 300.0,
        double_tap_ms: float = 260.0,
        double_tap_slop: float = 44.0,
        key_speed: float = 520.0,
        key_speed_fast: float = 1500.0,
    ) -> None:
        self._display = display
        self._long_press_s = max(0.0, long_press_ms) / 1000.0
        self._tap_slop = max(0.0, tap_slop)
        self._swipe_min = max(0.0, swipe_min_distance)
        self._swipe_max = max(0.0, swipe_max_ms) / 1000.0
        self._double_tap_s = max(0.0, double_tap_ms) / 1000.0
        self._double_tap_slop = max(0.0, double_tap_slop)
        self._key_speed = max(1.0, key_speed)
        self._key_speed_fast = max(self._key_speed, key_speed_fast)

        self._now = 0.0
        self._queue: list[PointerEvent] = []
        self._listeners: list[tuple[EventKind | None, Listener]] = []

        self._pressed = False
        self._synthetic = False
        self._press_pos: tuple[float, float] = (0.0, 0.0)
        self._press_t = 0.0
        self._press_button = 1
        self._dragging = False
        self._long_fired = False
        self._samples: Deque[tuple[float, float, float]] = deque(maxlen=16)

        self._pointer: tuple[float, float] = (
            display.virtual_size[0] * 0.5,
            display.virtual_size[1] * 0.5,
        )
        self._held_keys: set[int] = set()
        self._mods = 0
        self._keyboard_mode = False
        # Form-style UIs (settings, dialogs) set this so arrows/Tab/Enter are
        # delivered to widgets instead of driving the virtual mouse pointer.
        self.ui_capture_keys = False

        self._last_tap_t = -1e9
        self._last_tap_pos: tuple[float, float] = (0.0, 0.0)

    # -- public state -------------------------------------------------------
    @property
    def pointer(self) -> tuple[float, float]:
        """Current pointer position in virtual coordinates."""
        return self._pointer

    @property
    def keyboard_mode(self) -> bool:
        """True while the virtual pointer is keyboard driven."""
        return self._keyboard_mode

    @property
    def pressed(self) -> bool:
        return self._pressed

    @property
    def dragging(self) -> bool:
        return self._dragging

    @property
    def long_press_threshold(self) -> float:
        return self._long_press_s

    # -- subscriptions ------------------------------------------------------
    def subscribe(self, kind: EventKind | None, callback: Listener) -> None:
        """Register ``callback`` for ``kind`` (``None`` receives every event)."""
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._listeners.append((kind, callback))

    def add_listener(self, callback: Listener) -> None:
        self.subscribe(None, callback)

    def unsubscribe(self, callback: Listener) -> None:
        self._listeners = [(kind, cb) for kind, cb in self._listeners if cb is not callback]

    def clear_listeners(self) -> None:
        self._listeners.clear()

    def drain(self) -> list[PointerEvent]:
        """Return and clear the queued events for this frame."""
        if not self._queue:
            return []
        events = self._queue
        self._queue = []
        return events

    # -- event ingestion ----------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        """Translate a raw pygame event into the gesture state machine."""
        etype = event.type

        if etype == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = self._display.clamp_virtual(self._display.to_virtual(event.pos))
            self._keyboard_mode = False
            self._press(pos, synthetic=False, button=1)

        elif etype == pygame.MOUSEBUTTONUP and event.button == 1:
            pos = self._display.clamp_virtual(self._display.to_virtual(event.pos))
            self._release(pos)

        elif etype == pygame.MOUSEMOTION:
            pos = self._display.clamp_virtual(self._display.to_virtual(event.pos))
            if self._keyboard_mode:
                self._keyboard_mode = False
            self._move(pos)

        elif etype == pygame.MOUSEWHEEL:
            # Wheel deltas are normalised to "notches": apps decide the pixel
            # step, matching how drag and arrow scrolling are implemented.
            self._emit(
                PointerEvent(
                    kind=EventKind.WHEEL,
                    pos=self._pointer,
                    delta=(float(event.x), float(event.y)),
                )
            )

        elif etype == pygame.KEYDOWN:
            self._on_key_down(event)

        elif etype == pygame.KEYUP:
            self._on_key_up(event)

    def update(self, dt: float) -> None:
        """Advance timers; call once per fixed step, after event ingestion."""
        dt = max(0.0, float(dt))
        self._now += dt
        self._mods = pygame.key.get_mods()

        if self._keyboard_mode and self._held_keys:
            dx = 0.0
            dy = 0.0
            for key in self._held_keys:
                vector = self._MOVE_VECTORS.get(key)
                if vector is not None:
                    dx += vector[0]
                    dy += vector[1]
            if dx or dy:
                length = math.hypot(dx, dy)
                speed = self._key_speed_fast if self._mods & pygame.KMOD_SHIFT else self._key_speed
                target = (
                    self._pointer[0] + dx / length * speed * dt,
                    self._pointer[1] + dy / length * speed * dt,
                )
                self._move(self._display.clamp_virtual(target))

        self._check_long_press()

        if self._last_tap_t > -1e8 and (self._now - self._last_tap_t) > self._double_tap_s:
            self._last_tap_t = -1e9

    def cancel(self) -> None:
        """Abort any in-flight gesture (window focus lost, app paused)."""
        self._pressed = False
        self._synthetic = False
        self._dragging = False
        self._long_fired = False
        self._held_keys.clear()
        self._samples.clear()

    # -- gesture internals --------------------------------------------------
    def _on_key_down(self, event: pygame.event.Event) -> None:
        key = int(event.key)
        if self.ui_capture_keys:
            self._emit(
                PointerEvent(
                    kind=EventKind.KEY_DOWN,
                    pos=self._pointer,
                    key=key,
                    mods=int(event.mod),
                    text=getattr(event, "unicode", ""),
                )
            )
            return
        if key in self._MOVE_VECTORS:
            self._keyboard_mode = True
            self._held_keys.add(key)
            return
        if key in self._ACTIVATE_KEYS:
            self._keyboard_mode = True
            if not self._pressed:
                self._press(self._pointer, synthetic=True, button=1)
            return
        self._emit(
            PointerEvent(
                kind=EventKind.KEY_DOWN,
                pos=self._pointer,
                key=key,
                mods=int(event.mod),
                text=getattr(event, "unicode", ""),
            )
        )

    def _on_key_up(self, event: pygame.event.Event) -> None:
        key = int(event.key)
        if self.ui_capture_keys:
            self._emit(
                PointerEvent(
                    kind=EventKind.KEY_UP,
                    pos=self._pointer,
                    key=key,
                    mods=int(event.mod),
                )
            )
            return
        if key in self._MOVE_VECTORS:
            self._held_keys.discard(key)
            return
        if key in self._ACTIVATE_KEYS:
            if self._pressed and self._synthetic:
                self._release(self._pointer)
            return
        self._emit(
            PointerEvent(
                kind=EventKind.KEY_UP,
                pos=self._pointer,
                key=key,
                mods=int(event.mod),
            )
        )

    def _press(self, pos: tuple[float, float], *, synthetic: bool, button: int) -> None:
        if self._pressed:
            return
        pos = self._display.clamp_virtual(pos)
        self._pointer = pos
        self._pressed = True
        self._synthetic = synthetic
        self._press_button = button
        self._press_pos = pos
        self._press_t = self._now
        self._dragging = False
        self._long_fired = False
        self._samples.clear()
        self._samples.append((self._now, pos[0], pos[1]))
        self._emit(
            PointerEvent(
                kind=EventKind.POINTER_DOWN,
                pos=pos,
                start_pos=pos,
                button=button,
                synthetic=synthetic,
            )
        )

    def _move(self, pos: tuple[float, float]) -> None:
        pos = self._display.clamp_virtual(pos)
        previous = self._pointer
        if pos == previous:
            return
        self._pointer = pos
        if not self._pressed:
            return

        dx = pos[0] - previous[0]
        dy = pos[1] - previous[1]
        self._samples.append((self._now, pos[0], pos[1]))

        distance = self._distance(pos, self._press_pos)
        if not self._dragging and distance > self._tap_slop:
            self._dragging = True
            self._emit(
                PointerEvent(
                    kind=EventKind.DRAG_START,
                    pos=pos,
                    start_pos=self._press_pos,
                    delta=(dx, dy),
                    distance=distance,
                    duration=self._now - self._press_t,
                    button=self._press_button,
                    synthetic=self._synthetic,
                )
            )
        if self._dragging:
            velocity = self._velocity()
            self._emit(
                PointerEvent(
                    kind=EventKind.DRAG,
                    pos=pos,
                    start_pos=self._press_pos,
                    delta=(dx, dy),
                    distance=distance,
                    duration=self._now - self._press_t,
                    velocity=velocity,
                    speed=math.hypot(*velocity),
                    button=self._press_button,
                    synthetic=self._synthetic,
                )
            )

    def _release(self, pos: tuple[float, float]) -> None:
        if not self._pressed:
            return
        pos = self._display.clamp_virtual(pos)
        self._pointer = pos
        self._samples.append((self._now, pos[0], pos[1]))

        duration = max(0.0, self._now - self._press_t)
        distance = self._distance(pos, self._press_pos)
        velocity = self._velocity()
        speed = math.hypot(*velocity)
        button = self._press_button

        self._emit(
            PointerEvent(
                kind=EventKind.POINTER_UP,
                pos=pos,
                start_pos=self._press_pos,
                distance=distance,
                duration=duration,
                velocity=velocity,
                speed=speed,
                button=button,
                synthetic=self._synthetic,
            )
        )

        if self._long_fired:
            self._reset_gesture()
            return

        if self._dragging or distance > self._tap_slop:
            direction = self._direction(pos[0] - self._press_pos[0], pos[1] - self._press_pos[1])
            if distance >= self._swipe_min and duration <= self._swipe_max and speed >= 1.0:
                self._emit(
                    PointerEvent(
                        kind=EventKind.SWIPE,
                        pos=pos,
                        start_pos=self._press_pos,
                        delta=(pos[0] - self._press_pos[0], pos[1] - self._press_pos[1]),
                        distance=distance,
                        duration=duration,
                        velocity=velocity,
                        speed=speed,
                        direction=direction,
                        button=button,
                        synthetic=self._synthetic,
                    )
                )
            else:
                self._emit(
                    PointerEvent(
                        kind=EventKind.DRAG_END,
                        pos=pos,
                        start_pos=self._press_pos,
                        delta=(pos[0] - self._press_pos[0], pos[1] - self._press_pos[1]),
                        distance=distance,
                        duration=duration,
                        velocity=velocity,
                        speed=speed,
                        direction=direction,
                        button=button,
                        synthetic=self._synthetic,
                    )
                )
            self._reset_gesture()
            return

        self._register_tap(pos, duration, button)
        self._reset_gesture()

    def _register_tap(self, pos: tuple[float, float], duration: float, button: int) -> None:
        within_time = (self._now - self._last_tap_t) <= self._double_tap_s
        within_space = self._distance(pos, self._last_tap_pos) <= self._double_tap_slop
        if within_time and within_space:
            self._last_tap_t = -1e9
            self._emit(
                PointerEvent(
                    kind=EventKind.DOUBLE_TAP,
                    pos=pos,
                    start_pos=pos,
                    duration=duration,
                    button=button,
                    synthetic=self._synthetic,
                )
            )
            return
        self._last_tap_t = self._now
        self._last_tap_pos = pos
        self._emit(
            PointerEvent(
                kind=EventKind.TAP,
                pos=pos,
                start_pos=pos,
                duration=duration,
                button=button,
                synthetic=self._synthetic,
            )
        )

    def _check_long_press(self) -> None:
        if not self._pressed or self._long_fired or self._dragging:
            return
        duration = self._now - self._press_t
        if duration < self._long_press_s:
            return
        self._long_fired = True
        velocity = self._velocity()
        self._emit(
            PointerEvent(
                kind=EventKind.LONG_PRESS,
                pos=self._pointer,
                start_pos=self._press_pos,
                distance=self._distance(self._pointer, self._press_pos),
                duration=duration,
                velocity=velocity,
                speed=math.hypot(*velocity),
                button=self._press_button,
                synthetic=self._synthetic,
            )
        )

    def _reset_gesture(self) -> None:
        self._pressed = False
        self._synthetic = False
        self._dragging = False
        self._long_fired = False
        self._samples.clear()

    def _velocity(self) -> tuple[float, float]:
        if len(self._samples) < 2:
            return (0.0, 0.0)
        newest_t, newest_x, newest_y = self._samples[-1]
        oldest_t, oldest_x, oldest_y = self._samples[0]
        for sample in self._samples:
            if newest_t - sample[0] >= 0.06:
                oldest_t, oldest_x, oldest_y = sample
                break
        elapsed = newest_t - oldest_t
        if elapsed <= 1e-4:
            return (0.0, 0.0)
        return ((newest_x - oldest_x) / elapsed, (newest_y - oldest_y) / elapsed)

    @staticmethod
    def _distance(a: Sequence[float], b: Sequence[float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    @staticmethod
    def _direction(dx: float, dy: float) -> Direction:
        if dx == 0.0 and dy == 0.0:
            return Direction.NONE
        if abs(dx) >= abs(dy):
            return Direction.RIGHT if dx > 0 else Direction.LEFT
        return Direction.DOWN if dy > 0 else Direction.UP

    def _emit(self, event: PointerEvent) -> None:
        self._queue.append(event)
        if not self._listeners:
            return
        for kind, callback in tuple(self._listeners):
            if kind is None or kind is event.kind:
                callback(event)

    def __repr__(self) -> str:
        return (
            f"InputManager(pointer=({self._pointer[0]:.0f},{self._pointer[1]:.0f}), "
            f"pressed={self._pressed}, dragging={self._dragging})"
        )
