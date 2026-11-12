"""Clock: world clocks, alarms, timer and stopwatch.

Alarms and the countdown live in ``services/schedules.py`` so they continue to
work while another app is foregrounded. World-clock selection and stopwatch
laps/elapsed are persisted by this app in ``clock.json``.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta

import pygame

from apps._kit import ScrollState, TextField, draw_button, draw_card, draw_scrollbar, format_duration, format_timer
from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, fit_text

APP_ID = "clock"
APP_NAME = "Clock"
STORE_FILE = "clock.json"
TABS = ("clock", "alarm", "timer", "stopwatch")
TAB_LABELS = {"clock": "CLOCK", "alarm": "ALARM", "timer": "TIMER", "stopwatch": "STOPWATCH"}

# Fixed UTC offsets. The simulator intentionally does not depend on a timezone
# database; these cover the five weather cities plus common places.
WORLD_CITIES: dict[str, float] = {
    "London": 0.0,
    "Lisbon": 0.0,
    "Reykjavik": 0.0,
    "Paris": 1.0,
    "Berlin": 1.0,
    "Cairo": 2.0,
    "Nairobi": 3.0,
    "Dubai": 4.0,
    "Karachi": 5.0,
    "Delhi": 5.5,
    "Bangkok": 7.0,
    "Singapore": 8.0,
    "Tokyo": 9.0,
    "Sydney": 10.0,
    "Auckland": 12.0,
    "New York": -5.0,
    "Chicago": -6.0,
    "Denver": -7.0,
    "Los Angeles": -8.0,
    "Anchorage": -9.0,
    "Honolulu": -10.0,
}
DEFAULT_CITIES = ["Tokyo", "New York", "London"]
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
QUICK_TIMERS = (60, 300, 600, 1500)


def world_time(now: datetime, city: str) -> datetime:
    offset = WORLD_CITIES.get(city, 0.0)
    return now + timedelta(hours=offset)


def utc_label(offset: float) -> str:
    sign = "+" if offset >= 0 else "-"
    value = abs(offset)
    whole = int(value)
    half = int(round((value - whole) * 60))
    return f"UTC{sign}{whole:02d}" + (f":{half:02d}" if half else "")


class ClockApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._tab = "clock"
        self._cities: list[str] = list(DEFAULT_CITIES)
        self._add_city_open = False
        self._city_scroll = ScrollState(step=44)
        self._world_scroll = ScrollState(step=64)
        self._city_rects: list[tuple[str, pygame.Rect]] = []
        self._remove_rects: list[tuple[str, pygame.Rect]] = []

        self._alarm_rows: list[tuple[int, pygame.Rect]] = []
        self._alarm_toggle_rects: list[tuple[int, pygame.Rect]] = []
        self._alarm_editor: int | None = None
        self._editor_open = False
        self._editor_hour = 7
        self._editor_minute = 0
        self._editor_days: set[int] = set()
        self._editor_label = TextField("Alarm", max_length=28, placeholder="Label")
        self._alarm_scroll = ScrollState(step=54)

        self._timer_size = 300
        self._switch_timer = 0.0
        self._laps_scroll = ScrollState(step=38)
        self._sw_accum = 0.0
        self._sw_started: float | None = None
        self._sw_laps: list[float] = []
        self._blink = 0.0

    # -- lifecycle ------------------------------------------------------------
    def on_launch(self) -> None:
        self._load()
        self._sync_timer_duration()

    def on_pause(self) -> None:
        self._save()

    def on_close(self) -> None:
        self._save()

    def update(self, dt: float) -> None:
        super().update(dt)
        self._blink = (self._blink + dt) % 1.06
        self._clock_tick(dt)

    def on_resize(self) -> None:
        self._city_scroll.reset()
        self._alarm_scroll.reset()
        self._laps_scroll.reset()

    # -- persistence ----------------------------------------------------------
    def _load(self) -> None:
        payload = self.ctx.storage.read_json(STORE_FILE, {})
        if not isinstance(payload, dict):
            payload = {}
        cities = payload.get("cities", DEFAULT_CITIES)
        if isinstance(cities, list):
            self._cities = [str(city) for city in cities if isinstance(city, str) and city in WORLD_CITIES]
        if not self._cities:
            self._cities = list(DEFAULT_CITIES)
        try:
            self._sw_accum = max(0.0, float(payload.get("stopwatch_accum", 0.0)))
        except (TypeError, ValueError):
            self._sw_accum = 0.0
        started = payload.get("stopwatch_started")
        self._sw_started = float(started) if isinstance(started, (int, float)) else None
        laps = payload.get("laps", [])
        self._sw_laps = [float(value) for value in laps if isinstance(value, (int, float))] if isinstance(laps, list) else []
        try:
            self._timer_size = max(1, int(payload.get("timer_size", 300)))
        except (TypeError, ValueError):
            self._timer_size = 300

    def _save(self) -> None:
        self.ctx.storage.write_json(
            STORE_FILE,
            {
                "cities": list(self._cities),
                "timer_size": int(self._timer_size),
                "stopwatch_accum": float(self._sw_accum),
                "stopwatch_started": self._sw_started,
                "laps": list(self._sw_laps),
            },
        )

    # -- schedules ------------------------------------------------------------
    @property
    def schedules(self):
        return getattr(self.ctx, "schedules", None)

    def _sync_timer_duration(self) -> None:
        schedules = self.schedules
        if schedules is None:
            return
        timer = schedules.timer
        if not timer.get("running") and float(timer.get("remaining", 0.0)) <= 0.01:
            schedules.set_timer(self._timer_size)

    # -- tabs / input ---------------------------------------------------------
    def _tab_rects(self) -> dict[str, pygame.Rect]:
        margin = current_layout.margin
        top = self.content.y + 8
        width = (self.content.w - margin * 2 - 12) // len(TABS)
        return {
            key: pygame.Rect(margin + index * (width + 4), top, width, 34)
            for index, key in enumerate(TABS)
        }

    def handle(self, event: PointerEvent) -> bool:
        if self._editor_open:
            return self._handle_editor(event)
        if self._add_city_open:
            return self._handle_city_picker(event)
        if event.kind is EventKind.WHEEL:
            scroll = self._current_scroll()
            return scroll.wheel(event) if scroll is not None else False
        if event.kind is EventKind.POINTER_DOWN:
            scroll = self._current_scroll()
            if scroll is not None:
                scroll.pointer(event, self._scroll_area())
        if event.kind in (EventKind.DRAG, EventKind.DRAG_END, EventKind.POINTER_UP):
            scroll = self._current_scroll()
            if scroll is not None:
                scroll.pointer(event, self._scroll_area())
        if event.kind is EventKind.KEY_DOWN:
            if event.key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4):
                self._tab = TABS[int(event.text or "1") - 1]
                return True
            scroll = self._current_scroll()
            if scroll is not None and scroll.key(event):
                return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        for key, rect in self._tab_rects().items():
            if rect.collidepoint(event.pos):
                self._tab = key
                return True
        if self._tab == "clock":
            return self._handle_clock_tap(event)
        if self._tab == "alarm":
            return self._handle_alarm_tap(event)
        if self._tab == "timer":
            return self._handle_timer_tap(event)
        return self._handle_stopwatch_tap(event)

    def _current_scroll(self) -> ScrollState | None:
        if self._tab == "clock":
            return self._world_scroll
        if self._tab == "alarm":
            return self._alarm_scroll
        if self._tab == "stopwatch":
            return self._laps_scroll
        return None

    def _scroll_area(self) -> pygame.Rect:
        if self._tab == "clock":
            return self._world_list_area()
        if self._tab == "alarm":
            return self._alarm_list_area()
        return self._laps_area()

    # -- clock tab ------------------------------------------------------------
    def _clock_area(self) -> pygame.Rect:
        return pygame.Rect(
            current_layout.margin,
            self.content.y + 52,
            self.content.w - current_layout.margin * 2,
            self.content.h - 70,
        )

    def _add_city_rect(self) -> pygame.Rect:
        area = self._clock_area()
        return pygame.Rect(area.right - 94, area.y, 94, 34)

    def _world_list_area(self) -> pygame.Rect:
        area = self._clock_area()
        return pygame.Rect(area.x, area.y + 110, area.w, max(40, area.h - 116))

    def _handle_clock_tap(self, event: PointerEvent) -> bool:
        if self._add_city_rect().collidepoint(event.pos):
            self._add_city_open = True
            self._city_scroll.reset()
            return True
        for city, rect in self._remove_rects:
            if rect.collidepoint(event.pos):
                self._cities = [item for item in self._cities if item != city]
                self._save()
                return True
        return False

    def _handle_city_picker(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self._add_city_open = False
                return True
            return self._city_scroll.key(event)
        if event.kind is EventKind.WHEEL:
            return self._city_scroll.wheel(event)
        if event.kind in (EventKind.POINTER_DOWN, EventKind.DRAG, EventKind.DRAG_END, EventKind.POINTER_UP):
            return self._city_scroll.pointer(event, self._city_picker_area())
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        if not self._city_picker_rect().collidepoint(event.pos):
            self._add_city_open = False
            return True
        for city, rect in self._city_rects:
            if rect.collidepoint(event.pos):
                if city not in self._cities:
                    self._cities.append(city)
                    self._save()
                self._add_city_open = False
                return True
        return True

    def _city_picker_rect(self) -> pygame.Rect:
        width = min(360, self.content.w - 36)
        height = min(self.content.h - 60, 620)
        return pygame.Rect(
            (self.content.centerx - width // 2),
            self.content.y + 56,
            width,
            height,
        )

    def _city_picker_area(self) -> pygame.Rect:
        rect = self._city_picker_rect()
        return pygame.Rect(rect.x + 10, rect.y + 58, rect.w - 20, rect.h - 68)

    # -- alarm tab ------------------------------------------------------------
    def _alarm_list_area(self) -> pygame.Rect:
        return pygame.Rect(
            current_layout.margin,
            self.content.y + 58,
            self.content.w - current_layout.margin * 2,
            self.content.h - 116,
        )

    def _add_alarm_rect(self) -> pygame.Rect:
        return pygame.Rect(
            current_layout.margin,
            self.content.bottom - 48,
            self.content.w - current_layout.margin * 2,
            38,
        )

    def _handle_alarm_tap(self, event: PointerEvent) -> bool:
        if self._add_alarm_rect().collidepoint(event.pos):
            self._open_alarm_editor(None)
            return True
        for alarm_id, rect in self._alarm_toggle_rects:
            if rect.collidepoint(event.pos):
                if self.schedules is not None:
                    self.schedules.toggle_alarm(alarm_id)
                return True
        for alarm_id, rect in self._alarm_rows:
            if rect.collidepoint(event.pos):
                self._open_alarm_editor(alarm_id)
                return True
        return False

    def _open_alarm_editor(self, alarm_id: int | None) -> None:
        self._alarm_editor = alarm_id
        self._editor_open = True
        self._editor_days = set()
        if alarm_id is None:
            self._editor_hour = 7
            self._editor_minute = 0
            self._editor_label.set("Alarm")
        else:
            alarm = self.schedules.alarm(alarm_id) if self.schedules is not None else None
            if alarm is None:
                self._alarm_editor = None
                self._editor_open = False
                return
            self._editor_hour = alarm.hour
            self._editor_minute = alarm.minute
            self._editor_days = set(alarm.days)
            self._editor_label.set(alarm.label)

    def _editor_rects(self) -> dict[str, pygame.Rect]:
        width = min(430, self.content.w - 36)
        left = self.content.centerx - width // 2
        top = self.content.y + 34
        button = 38
        return {
            "hour_minus": pygame.Rect(left + 24, top + 102, button, button),
            "hour_plus": pygame.Rect(left + 68, top + 102, button, button),
            "minute_minus": pygame.Rect(left + width - 106, top + 102, button, button),
            "minute_plus": pygame.Rect(left + width - 62, top + 102, button, button),
            "label": pygame.Rect(left + 20, top + 158, width - 40, 44),
            "days": pygame.Rect(left + 20, top + 210, width - 40, 32),
            "save": pygame.Rect(left + 20, top + 258, (width - 52) // 2, 42),
            "delete": pygame.Rect(left + 32 + (width - 52) // 2, top + 258, (width - 52) // 2, 42),
            "back": pygame.Rect(left + 20, top + 310, width - 40, 36),
        }

    def _handle_editor(self, event: PointerEvent) -> bool:
        rects = self._editor_rects()
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self._alarm_editor = None
                self._editor_open = False
                return True
            self._editor_label.handle(event)
            return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        if rects["hour_minus"].collidepoint(event.pos):
            self._editor_hour = (self._editor_hour - 1) % 24
        elif rects["hour_plus"].collidepoint(event.pos):
            self._editor_hour = (self._editor_hour + 1) % 24
        elif rects["minute_minus"].collidepoint(event.pos):
            self._editor_minute = (self._editor_minute - 5) % 60
        elif rects["minute_plus"].collidepoint(event.pos):
            self._editor_minute = (self._editor_minute + 5) % 60
        elif rects["save"].collidepoint(event.pos):
            self._save_alarm()
        elif rects["delete"].collidepoint(event.pos):
            if self._alarm_editor is not None and self.schedules is not None:
                self.schedules.delete_alarm(self._alarm_editor)
            self._alarm_editor = None
            self._editor_open = False
        elif rects["back"].collidepoint(event.pos):
            self._alarm_editor = None
            self._editor_open = False
        else:
            for index in range(7):
                day_rect = self._weekday_rect(index)
                if day_rect.collidepoint(event.pos):
                    if index in self._editor_days:
                        self._editor_days.remove(index)
                    else:
                        self._editor_days.add(index)
                    break
        return True

    def _save_alarm(self) -> None:
        if self.schedules is None:
            self._alarm_editor = None
            self._editor_open = False
            return
        days = sorted(self._editor_days)
        if self._alarm_editor is None:
            self.schedules.add_alarm(
                self._editor_hour,
                self._editor_minute,
                self._editor_label.value,
                days,
            )
        else:
            self.schedules.update_alarm(
                self._alarm_editor,
                hour=self._editor_hour,
                minute=self._editor_minute,
                label=self._editor_label.value,
                days=days,
            )
        self._alarm_editor = None
        self._editor_open = False

    def _weekday_rect(self, index: int) -> pygame.Rect:
        rect = self._editor_rects()["days"]
        width = (rect.w - 18) // 7
        return pygame.Rect(rect.x + index * (width + 3), rect.y, width, rect.h)

    # -- timer tab ------------------------------------------------------------
    def _timer_radius(self) -> int:
        area = self._clock_area()
        return min(138, int(area.w * 0.32), int(area.h * 0.26))

    def _timer_buttons(self) -> dict[str, pygame.Rect]:
        area = self._clock_area()
        radius = self._timer_radius()
        top = area.y + 30 + radius * 2 + 100
        width = min(120, (area.w - 24) // 3)
        return {
            "start": pygame.Rect(area.centerx - width - 8, top, width, 42),
            "reset": pygame.Rect(area.centerx + 8, top, width, 42),
        }

    def _quick_rect(self, index: int) -> pygame.Rect:
        area = self._clock_area()
        radius = self._timer_radius()
        width = (area.w - 18) // 4
        return pygame.Rect(area.x + index * (width + 6), area.y + 30 + radius * 2 + 54, width, 34)

    def _handle_timer_tap(self, event: PointerEvent) -> bool:
        schedules = self.schedules
        if schedules is None:
            return False
        buttons = self._timer_buttons()
        if buttons["start"].collidepoint(event.pos):
            timer = schedules.timer
            if timer.get("running"):
                schedules.pause_timer()
            else:
                schedules.start_timer()
            return True
        if buttons["reset"].collidepoint(event.pos):
            schedules.reset_timer()
            self._timer_size = max(1, int(float(schedules.timer.get("duration", 300))))
            return True
        for index, seconds in enumerate(QUICK_TIMERS):
            if self._quick_rect(index).collidepoint(event.pos):
                self._timer_size = seconds
                schedules.set_timer(seconds)
                return True
        return False

    # -- stopwatch tab --------------------------------------------------------
    def _laps_area(self) -> pygame.Rect:
        area = self._clock_area()
        return pygame.Rect(area.x, area.y + 236, area.w, max(40, area.h - 244))

    def _stopwatch_buttons(self) -> dict[str, pygame.Rect]:
        area = self._clock_area()
        top = area.y + 170
        width = min(126, (area.w - 20) // 3)
        return {
            "toggle": pygame.Rect(area.centerx - width - 8, top, width, 42),
            "lap": pygame.Rect(area.centerx + 8, top, width, 42),
            "reset": pygame.Rect(area.x, area.y + 58, 96, 32),
        }

    def _stopwatch_elapsed(self) -> float:
        elapsed = self._sw_accum
        if self._sw_started is not None:
            elapsed += max(0.0, self.ctx.clock.simulated_time.timestamp() - self._sw_started)
        return elapsed

    def _handle_stopwatch_tap(self, event: PointerEvent) -> bool:
        buttons = self._stopwatch_buttons()
        if buttons["toggle"].collidepoint(event.pos):
            self._toggle_stopwatch()
            return True
        if buttons["lap"].collidepoint(event.pos):
            if self._sw_started is not None:
                self._sw_laps.insert(0, self._stopwatch_elapsed())
                self._save()
            return True
        if buttons["reset"].collidepoint(event.pos):
            self._sw_accum = 0.0
            self._sw_started = None
            self._sw_laps.clear()
            self._save()
            return True
        return False

    def _toggle_stopwatch(self) -> None:
        now_ts = self.ctx.clock.simulated_time.timestamp()
        if self._sw_started is None:
            self._sw_started = now_ts
        else:
            self._sw_accum += max(0.0, now_ts - self._sw_started)
            self._sw_started = None
        self._save()

    # -- timers update --------------------------------------------------------
    def _clock_tick(self, dt: float) -> None:
        del dt
        # Stopwatch is computed from simulated timestamps; nothing to advance.
        # Keep a tiny live animation phase for the timer dial.
        self._switch_timer = (self._switch_timer + 0.03) % 1.0

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        for key, rect in self._tab_rects().items():
            active = key == self._tab
            draw_rounded(
                surface,
                rect,
                fill=pal.accent_soft if active else pal.surface,
                stroke=pal.accent if active else pal.stroke_soft,
                radius=10,
            )
            blit_text(
                surface,
                self.font(9, True),
                TAB_LABELS[key],
                pal.accent_ink if active else pal.text_muted,
                center=rect.center,
                tracking=0.8,
            )
        if self._editor_open:
            self._draw_editor(surface, pal)
        elif self._add_city_open:
            self._draw_city_picker(surface, pal)
        elif self._tab == "clock":
            self._draw_clock(surface, pal)
        elif self._tab == "alarm":
            self._draw_alarms(surface, pal)
        elif self._tab == "timer":
            self._draw_timer(surface, pal)
        else:
            self._draw_stopwatch(surface, pal)

    def _draw_clock(self, surface, pal) -> None:
        area = self._clock_area()
        now = self.ctx.clock.simulated_time
        local = now
        blit_text(surface, self.font(10, True), "LOCAL TIME", pal.text_muted, topleft=(area.x, area.y + 4), tracking=1.6)
        blit_text(surface, self.mono(46, True), local.strftime("%H:%M"), pal.text, topleft=(area.x, area.y + 22))
        blit_text(surface, self.font(11), local.strftime("%A, %B %d"), pal.text_secondary, topleft=(area.x, area.y + 72))
        draw_button(
            surface,
            self._add_city_rect(),
            "+ CITY",
            self.font(10, True),
            pal,
            fill=pal.accent,
            color=pal.on_accent,
            stroke=None,
        )
        list_area = self._world_list_area()
        self._world_scroll.set_metrics(list_area.h, len(self._cities) * 64)
        self._remove_rects = []
        for index, city in enumerate(self._cities):
            card = pygame.Rect(list_area.x, list_area.y + index * 64 - int(self._world_scroll.offset), list_area.w, 56)
            if card.bottom < list_area.y or card.y > list_area.bottom:
                continue
            draw_card(surface, card, pal)
            city_now = world_time(now, city)
            blit_text(surface, self.font(13, True), city, pal.text, midleft=(card.x + 14, card.centery - 8))
            blit_text(
                surface,
                self.font(9),
                f"{utc_label(WORLD_CITIES.get(city, 0.0))}  \u00b7  {city_now.strftime('%a')}",
                pal.text_muted,
                midleft=(card.x + 14, card.centery + 11),
            )
            blit_text(
                surface,
                self.mono(21, True),
                city_now.strftime("%H:%M"),
                pal.accent_ink,
                midright=(card.right - 42, card.centery),
            )
            remove = pygame.Rect(card.right - 32, card.centery - 12, 24, 24)
            pygame.draw.circle(surface, pal.surface_alt, remove.center, 12)
            pygame.draw.line(surface, pal.text_muted, (remove.centerx - 4, remove.centery - 4), (remove.centerx + 4, remove.centery + 4), 2)
            pygame.draw.line(surface, pal.text_muted, (remove.centerx + 4, remove.centery - 4), (remove.centerx - 4, remove.centery + 4), 2)
            self._remove_rects.append((city, remove))
        draw_scrollbar(surface, list_area, self._world_scroll.offset, len(self._cities) * 64, pal)

    def _draw_city_picker(self, surface, pal) -> None:
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill((0, 0, 0, 180))
        surface.blit(scrim, (0, 0))
        rect = self._city_picker_rect()
        draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=20)
        blit_text(surface, self.font(17, True), "Add a city", pal.text, topleft=(rect.x + 18, rect.y + 16))
        blit_text(surface, self.font(9), "UTC offsets are fixed in this simulation", pal.text_muted, topleft=(rect.x + 18, rect.y + 38))
        area = self._city_picker_area()
        available = [city for city in WORLD_CITIES if city not in self._cities]
        row_h = 40
        self._city_scroll.set_metrics(area.h, len(available) * (row_h + 4))
        self._city_rects = []
        for index, city in enumerate(available):
            row = pygame.Rect(area.x, area.y + index * (row_h + 4) - int(self._city_scroll.offset), area.w, row_h)
            if row.bottom < area.y or row.y > area.bottom:
                continue
            draw_card(surface, row, pal)
            blit_text(surface, self.font(12, True), city, pal.text, midleft=(row.x + 12, row.centery))
            blit_text(
                surface,
                self.font(10),
                utc_label(WORLD_CITIES[city]),
                pal.text_muted,
                midright=(row.right - 14, row.centery),
            )
            self._city_rects.append((city, row))
        draw_scrollbar(surface, area, self._city_scroll.offset, len(available) * (row_h + 4), pal)

    def _draw_alarms(self, surface, pal) -> None:
        schedules = self.schedules
        area = self._alarm_list_area()
        alarms = schedules.alarms() if schedules is not None else ()
        self._alarm_rows = []
        self._alarm_toggle_rects = []
        if not alarms:
            blit_text(surface, self.font(13), "No alarms yet", pal.text_muted, center=(area.centerx, area.y + 90))
        row_h = 66
        self._alarm_scroll.set_metrics(area.h, len(alarms) * (row_h + 8))
        for index, alarm in enumerate(alarms):
            row = pygame.Rect(area.x, area.y + index * (row_h + 8) - int(self._alarm_scroll.offset), area.w, row_h)
            if row.bottom < area.y or row.y > area.bottom:
                continue
            draw_card(surface, row, pal)
            toggle = pygame.Rect(row.x + 12, row.centery - 13, 30, 26)
            pygame.draw.rect(
                surface,
                pal.accent if alarm.enabled else pal.switch_off,
                toggle,
                border_radius=13,
            )
            knob_x = toggle.right - 13 if alarm.enabled else toggle.x + 13
            pygame.draw.circle(surface, pal.on_accent if alarm.enabled else pal.text_muted, (knob_x, toggle.centery), 10)
            blit_text(
                surface,
                self.mono(26, True),
                f"{alarm.hour:02d}:{alarm.minute:02d}",
                pal.text if alarm.enabled else pal.text_muted,
                midleft=(row.x + 56, row.centery - 7),
            )
            blit_text(
                surface,
                self.font(10),
                f"{alarm.label}  \u00b7  {alarm.day_text()}",
                pal.text_muted,
                midleft=(row.x + 56, row.centery + 14),
            )
            self._alarm_rows.append((alarm.alarm_id, row))
            self._alarm_toggle_rects.append((alarm.alarm_id, toggle))
        draw_scrollbar(surface, area, self._alarm_scroll.offset, len(alarms) * (row_h + 8), pal)
        draw_button(
            surface,
            self._add_alarm_rect(),
            "+ NEW ALARM",
            self.font(11, True),
            pal,
            fill=pal.accent,
            color=pal.on_accent,
            stroke=None,
        )

    def _draw_editor(self, surface, pal) -> None:
        rects = self._editor_rects()
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill((0, 0, 0, 150))
        surface.blit(scrim, (0, 0))
        panel = pygame.Rect(rects["label"].x - 18, self.content.y + 14, rects["label"].w + 36, 380)
        draw_rounded(surface, panel, fill=pal.surface, stroke=pal.stroke_soft, radius=20)
        blit_text(
            surface,
            self.font(11, True),
            "EDIT ALARM" if self._alarm_editor is not None else "NEW ALARM",
            pal.text_muted,
            topleft=(panel.x + 20, panel.y + 16),
            tracking=1.8,
        )
        blit_text(
            surface,
            self.mono(42, True),
            f"{self._editor_hour:02d}:{self._editor_minute:02d}",
            pal.text,
            center=(panel.centerx, panel.y + 66),
        )
        blit_text(surface, self.font(8, True), "HOUR", pal.text_muted, center=(rects["hour_minus"].centerx + 21, rects["hour_minus"].y - 12), tracking=1.2)
        blit_text(surface, self.font(8, True), "MIN", pal.text_muted, center=(rects["minute_minus"].centerx + 21, rects["minute_minus"].y - 12), tracking=1.2)
        for key in ("hour_minus", "hour_plus", "minute_minus", "minute_plus"):
            label = "-" if "minus" in key else "+"
            draw_button(surface, rects[key], label, self.font(16, True), pal, fill=pal.surface_raised)
        self._editor_label.draw(surface, rects["label"], self.font(13), pal, blink=self._blink)
        for index, day in enumerate(WEEKDAYS):
            day_rect = self._weekday_rect(index)
            active = index in self._editor_days
            draw_rounded(
                surface,
                day_rect,
                fill=pal.accent if active else pal.surface,
                stroke=pal.accent if active else pal.stroke_soft,
                radius=8,
            )
            blit_text(
                surface,
                self.font(8, True),
                day,
                pal.on_accent if active else pal.text_muted,
                center=day_rect.center,
            )
        draw_button(surface, rects["save"], "SAVE", self.font(12, True), pal, fill=pal.accent, color=pal.on_accent, stroke=None)
        draw_button(
            surface,
            rects["delete"],
            "DELETE" if self._alarm_editor is not None else "CANCEL",
            self.font(12, True),
            pal,
            fill=pal.surface_alt,
            color=pal.danger if self._alarm_editor is not None else pal.text_muted,
        )
        draw_button(surface, rects["back"], "BACK", self.font(10, True), pal, fill=pal.surface, color=pal.text_muted)

    def _draw_timer(self, surface, pal) -> None:
        area = self._clock_area()
        schedules = self.schedules
        timer = schedules.timer if schedules is not None else {"remaining": float(self._timer_size), "running": False, "duration": self._timer_size}
        remaining = max(0.0, float(timer.get("remaining", 0.0)))
        running = bool(timer.get("running", False))
        blit_text(surface, self.font(10, True), "COUNTDOWN", pal.text_muted, center=(area.centerx, area.y + 10), tracking=2.0)
        radius = self._timer_radius()
        center = (area.centerx, area.y + 30 + radius)
        pygame.draw.circle(surface, pal.surface, center, radius)
        pygame.draw.circle(surface, pal.stroke_soft, center, radius, 1)
        fraction = 1.0 if float(timer.get("duration", 1)) <= 0 else remaining / float(timer.get("duration", 1))
        pygame.draw.arc(
            surface,
            pal.accent,
            pygame.Rect(center[0] - radius, center[1] - radius, radius * 2, radius * 2),
            -math.pi / 2,
            -math.pi / 2 + math.tau * max(0.0, min(1.0, fraction)),
            max(4, radius // 16),
        )
        blit_text(surface, self.mono(max(24, radius // 2), True), format_timer(remaining), pal.text, center=center)
        blit_text(
            surface,
            self.font(10),
            "RUNNING" if running else "READY",
            pal.accent_ink if running else pal.text_muted,
            center=(center[0], center[1] + radius // 2 + 12),
            tracking=1.4,
        )
        for index, seconds in enumerate(QUICK_TIMERS):
            label = f"{seconds // 60} MIN" if seconds >= 60 else f"{seconds} SEC"
            selected = int(timer.get("duration", 0)) == seconds
            draw_button(surface, self._quick_rect(index), label, self.font(9, True), pal, fill=pal.accent_soft if selected else pal.surface)
        buttons = self._timer_buttons()
        draw_button(
            surface,
            buttons["start"],
            "PAUSE" if running else "START",
            self.font(13, True),
            pal,
            fill=pal.success if not running else pal.warning,
            color=pal.on_accent,
            stroke=None,
        )
        draw_button(surface, buttons["reset"], "RESET", self.font(13, True), pal, fill=pal.surface_alt)
        blit_text(
            surface,
            self.font(9),
            "The timer keeps running in the background and fires an overlay.",
            pal.text_muted,
            center=(area.centerx, buttons["start"].bottom + 22),
        )

    def _draw_stopwatch(self, surface, pal) -> None:
        area = self._clock_area()
        elapsed = self._stopwatch_elapsed()
        blit_text(surface, self.font(10, True), "STOPWATCH", pal.text_muted, center=(area.centerx, area.y + 10), tracking=2.0)
        blit_text(surface, self.mono(48, True), format_duration(elapsed, hundredths=True), pal.text, center=(area.centerx, area.y + 74))
        buttons = self._stopwatch_buttons()
        draw_button(
            surface,
            buttons["toggle"],
            "STOP" if self._sw_started is not None else "START",
            self.font(13, True),
            pal,
            fill=pal.success if self._sw_started is None else pal.warning,
            color=pal.on_accent,
            stroke=None,
        )
        draw_button(surface, buttons["lap"], "LAP", self.font(13, True), pal, fill=pal.surface_alt)
        draw_button(surface, buttons["reset"], "RESET", self.font(9, True), pal, fill=pal.surface, color=pal.text_muted)
        laps_area = self._laps_area()
        blit_text(surface, self.font(9, True), "LAPS", pal.text_muted, topleft=(laps_area.x, laps_area.y - 18), tracking=1.4)
        self._laps_scroll.set_metrics(laps_area.h, len(self._sw_laps) * 34)
        for index, lap in enumerate(self._sw_laps):
            row = pygame.Rect(laps_area.x, laps_area.y + index * 34 - int(self._laps_scroll.offset), laps_area.w, 30)
            if row.bottom < laps_area.y or row.y > laps_area.bottom:
                continue
            draw_rounded(surface, row, fill=pal.surface, radius=8)
            blit_text(surface, self.font(10, True), f"LAP {len(self._sw_laps) - index}", pal.text_secondary, midleft=(row.x + 10, row.centery))
            blit_text(surface, self.mono(12, True), format_duration(lap, hundredths=True), pal.text, midright=(row.right - 10, row.centery))
        draw_scrollbar(surface, laps_area, self._laps_scroll.offset, len(self._sw_laps) * 34, pal)

    # -- icon -----------------------------------------------------------------
    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=max(8, rect.w // 4))
    center = rect.center
    radius = max(8, int(rect.w * 0.30))
    pygame.draw.circle(surface, pal.accent, center, radius, max(2, rect.w // 22))
    for angle in (0, 1, 2, 3):
        point = (
            center[0] + int(math.cos(angle * math.pi / 2) * radius * 0.82),
            center[1] + int(math.sin(angle * math.pi / 2) * radius * 0.82),
        )
        pygame.draw.circle(surface, pal.accent, point, max(2, rect.w // 35))
    pygame.draw.line(surface, pal.text, center, (center[0], center[1] - radius // 2), max(2, rect.w // 26))
    pygame.draw.line(surface, pal.text, center, (center[0] + radius // 3, center[1] + radius // 5), max(2, rect.w // 30))


__all__ = ["ClockApp", "draw_icon", "WORLD_CITIES", "world_time", "utc_label"]
