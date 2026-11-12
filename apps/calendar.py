"""Calendar: month grid, day agenda and persisted timed events."""

from __future__ import annotations

import calendar as pycalendar
import time
from datetime import date, datetime

import pygame

from apps._kit import ScrollState, TextField, draw_button, draw_card, draw_scrollbar
from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_chevron, draw_rounded, fit_text, wrap_text

APP_ID = "calendar"
APP_NAME = "Calendar"
STORE_FILE = "calendar.json"


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=max(8, rect.w // 4))
    paper = rect.inflate(-int(rect.w * 0.38), -int(rect.h * 0.34))
    paper.centery += int(rect.h * 0.05)
    draw_rounded(surface, paper, fill=pal.surface, stroke=pal.text_muted, radius=4)
    pygame.draw.rect(surface, pal.danger, pygame.Rect(paper.x, paper.y, paper.w, max(4, rect.h // 12)), border_radius=3)
    grid_x = paper.x + max(3, paper.w // 7)
    for row in range(2):
        for column in range(3):
            pygame.draw.circle(
                surface,
                pal.accent if row == 0 and column == 1 else pal.text_muted,
                (grid_x + column * max(4, paper.w // 5), paper.y + max(8, paper.h // 3) + row * max(5, paper.h // 5)),
                max(1, rect.w // 30),
            )


class CalendarApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._events: list[dict[str, object]] = []
        self._next_id = 1
        self._month = self.ctx.clock.simulated_time.date().replace(day=1)
        self._selected = self.ctx.clock.simulated_time.date()
        self._day_scroll = ScrollState(step=52)
        self._event_rects: list[tuple[int, pygame.Rect]] = []
        self._editor_id: int | None = None
        self._editor_open = False
        self._title = TextField("", max_length=64, placeholder="Event title")
        self._note = TextField("", max_length=240, multiline=True, placeholder="Note (optional)")
        self._editor_hour = 9
        self._editor_minute = 0
        self._editor_focus = "title"
        self._confirm_delete: int | None = None
        self._blink = 0.0
        self._status = ""
        self._status_t = 0.0

    # -- lifecycle ------------------------------------------------------------
    def on_launch(self) -> None:
        self._load()
        now = self.ctx.clock.simulated_time.date()
        self._month = now.replace(day=1)
        self._selected = now

    def on_pause(self) -> None:
        self._save()

    def on_close(self) -> None:
        self._save()

    def update(self, dt: float) -> None:
        super().update(dt)
        self._blink = (self._blink + dt) % 1.06
        if self._status_t > 0.0:
            self._status_t = max(0.0, self._status_t - dt)
            if self._status_t == 0.0:
                self._status = ""

    def open_payload(self, payload: dict) -> None:
        title = str(payload.get("title", ""))
        match = next((event for event in self._events if title and str(event.get("title", "")).lower() == title.lower()), None)
        if match is None:
            return
        try:
            day = datetime.strptime(str(match.get("date", "")), "%Y-%m-%d").date()
        except ValueError:
            return
        self._selected = day
        self._month = day.replace(day=1)
        self._open_editor(int(match.get("id", 0)))

    def on_resize(self) -> None:
        self._day_scroll.reset()

    # -- persistence ----------------------------------------------------------
    def _load(self) -> None:
        payload = self.ctx.storage.read_json(STORE_FILE, {})
        if not isinstance(payload, dict):
            payload = {}
        events = payload.get("events", [])
        cleaned: list[dict[str, object]] = []
        if isinstance(events, list):
            for item in events:
                if not isinstance(item, dict):
                    continue
                try:
                    event_id = int(item.get("id", 0))
                    event_date = datetime.strptime(str(item.get("date", "")), "%Y-%m-%d").date()
                    event_time = datetime.strptime(str(item.get("time", "")), "%H:%M").time()
                except (TypeError, ValueError):
                    continue
                if event_id <= 0:
                    continue
                cleaned.append(
                    {
                        "id": event_id,
                        "date": event_date.isoformat(),
                        "time": event_time.strftime("%H:%M"),
                        "title": str(item.get("title", "Event"))[:64],
                        "note": str(item.get("note", ""))[:240],
                    }
                )
        self._events = cleaned
        self._next_id = max(
            int(payload.get("next_id", 1)) if isinstance(payload.get("next_id"), (int, float)) else 1,
            max((int(event["id"]) for event in cleaned), default=0) + 1,
        )

    def _save(self) -> None:
        self.ctx.storage.write_json(
            STORE_FILE,
            {"next_id": self._next_id, "events": sorted(self._events, key=lambda item: (str(item["date"]), str(item["time"])))},
        )

    # -- data helpers ---------------------------------------------------------
    def _day_events(self, day: date) -> list[dict[str, object]]:
        return sorted(
            (event for event in self._events if event.get("date") == day.isoformat()),
            key=lambda event: str(event.get("time", "")),
        )

    def _has_events(self, day: date) -> bool:
        return any(event.get("date") == day.isoformat() for event in self._events)

    def _editor_event(self) -> dict[str, object] | None:
        return next((event for event in self._events if int(event["id"]) == self._editor_id), None)

    def _toast(self, text: str) -> None:
        self._status = text
        self._status_t = 1.8

    # -- geometry -------------------------------------------------------------
    def _header_rect(self) -> pygame.Rect:
        return pygame.Rect(
            current_layout.margin,
            self.content.y + 6,
            self.content.w - current_layout.margin * 2,
            42,
        )

    def _month_panel(self) -> pygame.Rect:
        header = self._header_rect()
        if current_layout.is_landscape:
            width = int((self.content.w - current_layout.margin * 2) * 0.54)
            return pygame.Rect(current_layout.margin, header.bottom + 8, width, self.content.h - 60)
        return pygame.Rect(
            current_layout.margin,
            header.bottom + 8,
            self.content.w - current_layout.margin * 2,
            int(self.content.h * 0.57),
        )

    def _day_panel(self) -> pygame.Rect:
        month = self._month_panel()
        if current_layout.is_landscape:
            return pygame.Rect(month.right + 12, month.y, self.content.right - current_layout.margin - month.right - 12, month.h)
        return pygame.Rect(month.x, month.bottom + 10, month.w, max(100, self.content.bottom - month.bottom - 14))

    def _prev_rect(self) -> pygame.Rect:
        header = self._header_rect()
        return pygame.Rect(header.x, header.y + 4, 36, 34)

    def _next_rect(self) -> pygame.Rect:
        header = self._header_rect()
        return pygame.Rect(header.right - 36, header.y + 4, 36, 34)

    def _add_rect(self) -> pygame.Rect:
        day = self._day_panel()
        return pygame.Rect(day.x, day.bottom - 42, day.w, 38)

    def _grid_area(self) -> pygame.Rect:
        month = self._month_panel()
        return pygame.Rect(month.x, month.y + 32, month.w, month.h - 38)

    def _cell_rect(self, index: int) -> pygame.Rect:
        area = self._grid_area()
        column = index % 7
        row = index // 7
        width = area.w / 7.0
        height = area.h / 6.0
        return pygame.Rect(
            int(area.x + column * width),
            int(area.y + row * height),
            int(width),
            int(height),
        )

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if self._confirm_delete is not None:
            return self._handle_confirm(event)
        if self._editor_open:
            return self._handle_editor(event)
        if event.kind is EventKind.WHEEL:
            return self._day_scroll.wheel(event)
        if event.kind is EventKind.POINTER_DOWN:
            self._day_scroll.pointer(event, self._day_events_area())
        if event.kind in (EventKind.DRAG, EventKind.DRAG_END, EventKind.POINTER_UP):
            self._day_scroll.pointer(event, self._day_events_area())
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_LEFT:
                self._selected = self._selected.fromordinal(max(1, self._selected.toordinal() - 1))
                self._month = self._selected.replace(day=1)
                return True
            if event.key == pygame.K_RIGHT:
                self._selected = self._selected.fromordinal(self._selected.toordinal() + 1)
                self._month = self._selected.replace(day=1)
                return True
            if self._day_scroll.key(event):
                return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        if self._prev_rect().collidepoint(event.pos):
            self._shift_month(-1)
            return True
        if self._next_rect().collidepoint(event.pos):
            self._shift_month(1)
            return True
        if self._add_rect().collidepoint(event.pos):
            self._open_editor(None)
            return True
        for event_id, rect in self._event_rects:
            if rect.collidepoint(event.pos):
                self._open_editor(event_id)
                return True
        month = self._month_panel()
        if month.collidepoint(event.pos):
            for index in range(42):
                cell = self._cell_rect(index)
                if not cell.collidepoint(event.pos):
                    continue
                day = self._cell_day(index)
                if day is not None:
                    self._selected = day
                    self._day_scroll.reset()
                return True
        return False

    def _shift_month(self, direction: int) -> None:
        year = self._month.year
        month = self._month.month + direction
        if month < 1:
            year, month = year - 1, 12
        elif month > 12:
            year, month = year + 1, 1
        self._month = date(year, month, 1)
        self._selected = self._month
        self._day_scroll.reset()

    def _cell_day(self, index: int) -> date | None:
        first_weekday = (self._month.weekday() + 1) % 7  # Monday=0 -> Sunday=0
        days_in_month = pycalendar.monthrange(self._month.year, self._month.month)[1]
        day_number = index - first_weekday + 1
        if 1 <= day_number <= days_in_month:
            return date(self._month.year, self._month.month, day_number)
        return None

    def _day_events_area(self) -> pygame.Rect:
        panel = self._day_panel()
        return pygame.Rect(panel.x, panel.y + 42, panel.w, max(40, panel.h - 92))

    # -- editor ---------------------------------------------------------------
    def _open_editor(self, event_id: int | None) -> None:
        self._editor_id = event_id
        self._editor_open = True
        self._editor_focus = "title"
        self._confirm_delete = None
        if event_id is None:
            now = self.ctx.clock.simulated_time
            self._title.set("")
            self._note.set("")
            self._editor_hour = min(23, now.hour + 1)
            self._editor_minute = 0
        else:
            event = next((item for item in self._events if int(item["id"]) == event_id), None)
            if event is None:
                self._editor_id = None
                return
            self._title.set(str(event.get("title", "")))
            self._note.set(str(event.get("note", "")))
            try:
                hour, minute = str(event.get("time", "09:00")).split(":")
                self._editor_hour, self._editor_minute = int(hour), int(minute)
            except (TypeError, ValueError):
                self._editor_hour, self._editor_minute = 9, 0

    def _editor_rects(self) -> dict[str, pygame.Rect]:
        width = min(430, self.content.w - 36)
        left = self.content.centerx - width // 2
        top = self.content.y + 14
        return {
            "title": pygame.Rect(left + 18, top + 54, width - 36, 40),
            "hour_minus": pygame.Rect(left + 24, top + 154, 38, 36),
            "hour_plus": pygame.Rect(left + 68, top + 154, 38, 36),
            "minute_minus": pygame.Rect(left + width - 106, top + 154, 38, 36),
            "minute_plus": pygame.Rect(left + width - 62, top + 154, 38, 36),
            "note": pygame.Rect(left + 18, top + 204, width - 36, 70),
            "save": pygame.Rect(left + 18, top + 292, (width - 46) // 2, 40),
            "delete": pygame.Rect(left + 28 + (width - 46) // 2, top + 292, (width - 46) // 2, 40),
            "back": pygame.Rect(left + 18, top + 344, width - 36, 34),
        }

    def _handle_editor(self, event: PointerEvent) -> bool:
        rects = self._editor_rects()
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self._editor_id = None
                self._editor_open = False
                return True
            if event.key == pygame.K_TAB:
                self._editor_focus = "note" if self._editor_focus == "title" else "title"
                return True
            field = self._note if self._editor_focus == "note" else self._title
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self._editor_focus == "title":
                self._editor_focus = "note"
                return True
            field.handle(event)
            return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        if rects["title"].collidepoint(event.pos):
            self._editor_focus = "title"
            return True
        if rects["note"].collidepoint(event.pos):
            self._editor_focus = "note"
            return True
        if rects["hour_minus"].collidepoint(event.pos):
            self._editor_hour = (self._editor_hour - 1) % 24
        elif rects["hour_plus"].collidepoint(event.pos):
            self._editor_hour = (self._editor_hour + 1) % 24
        elif rects["minute_minus"].collidepoint(event.pos):
            self._editor_minute = (self._editor_minute - 5) % 60
        elif rects["minute_plus"].collidepoint(event.pos):
            self._editor_minute = (self._editor_minute + 5) % 60
        elif rects["save"].collidepoint(event.pos):
            self._save_event()
        elif rects["delete"].collidepoint(event.pos):
            if self._editor_id is not None:
                self._confirm_delete = self._editor_id
            else:
                self._editor_id = None
                self._editor_open = False
        elif rects["back"].collidepoint(event.pos):
            self._editor_id = None
            self._editor_open = False
        return True

    def _save_event(self) -> None:
        title = self._title.value.strip()
        if not title:
            self._toast("Add a title")
            return
        payload = {
            "date": self._selected.isoformat(),
            "time": f"{self._editor_hour:02d}:{self._editor_minute:02d}",
            "title": title[:64],
            "note": self._note.value.strip()[:240],
        }
        if self._editor_id is None:
            record = {"id": self._next_id, **payload}
            self._next_id += 1
            self._events.append(record)
            self._editor_id = int(record["id"])
        else:
            event = next((item for item in self._events if int(item["id"]) == self._editor_id), None)
            if event is None:
                self._editor_id = None
                self._editor_open = False
                return
            event.update(payload)
        self._save()
        self._editor_id = None
        self._editor_open = False

    def _handle_confirm(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.KEY_DOWN:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._delete_confirmed()
            elif event.key == pygame.K_ESCAPE:
                self._confirm_delete = None
            return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        yes, no = self._confirm_rects()
        if yes.collidepoint(event.pos):
            self._delete_confirmed()
        elif no.collidepoint(event.pos):
            self._confirm_delete = None
        return True

    def _delete_confirmed(self) -> None:
        event_id = self._confirm_delete
        self._confirm_delete = None
        if event_id is None:
            return
        self._events = [event for event in self._events if int(event["id"]) != event_id]
        self._save()
        self._editor_id = None
        self._editor_open = False
        self._toast("Event deleted")

    def _confirm_rects(self) -> tuple[pygame.Rect, pygame.Rect]:
        width = min(340, self.content.w - 40)
        left = self.content.centerx - width // 2
        top = self.content.centery - 50
        return (
            pygame.Rect(left + 16, top + 76, (width - 42) // 2, 40),
            pygame.Rect(left + 26 + (width - 42) // 2, top + 76, (width - 42) // 2, 40),
        )

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        self._draw_header(surface, pal)
        self._draw_month(surface, pal)
        self._draw_day(surface, pal)
        if self._status:
            draw_rounded(
                surface,
                pygame.Rect(self.content.centerx - 100, self.content.bottom - 34, 200, 26),
                fill=pal.surface_raised,
                stroke=pal.stroke_soft,
                radius=13,
            )
            blit_text(surface, self.font(10, True), self._status, pal.text, center=(self.content.centerx, self.content.bottom - 21))
        if self._editor_open:
            self._draw_editor(surface, pal)
        if self._confirm_delete is not None:
            self._draw_confirm(surface, pal)

    def _draw_header(self, surface, pal) -> None:
        header = self._header_rect()
        for rect in (self._prev_rect(), self._next_rect()):
            draw_rounded(surface, rect, fill=pal.surface, stroke=pal.stroke_soft, radius=10)
        draw_chevron(surface, self._prev_rect().center, pal.text, size=5, direction="left")
        draw_chevron(surface, self._next_rect().center, pal.text, size=5, direction="right")
        blit_text(
            surface,
            self.font(20, True),
            self._month.strftime("%B %Y"),
            pal.text,
            center=(header.centerx, header.centery),
        )

    def _draw_month(self, surface, pal) -> None:
        month = self._month_panel()
        draw_rounded(surface, month, fill=pal.surface, stroke=pal.stroke_soft, radius=18)
        for index, label in enumerate(("S", "M", "T", "W", "T", "F", "S")):
            blit_text(
                surface,
                self.font(9, True),
                label,
                pal.text_muted,
                center=(self._cell_rect(index).centerx, month.y + 16),
            )
        today = self.ctx.clock.simulated_time.date()
        for index in range(42):
            cell = self._cell_rect(index)
            day = self._cell_day(index)
            if day is None:
                continue
            selected = day == self._selected
            if selected:
                draw_rounded(surface, cell.inflate(-4, -4), fill=pal.accent, radius=10)
            elif day == today:
                pygame.draw.rect(surface, pal.accent, cell.inflate(-5, -5), 1, border_radius=10)
            color = pal.on_accent if selected else (pal.accent_ink if day == today else pal.text)
            blit_text(
                surface,
                self.font(12, True if selected or day == today else False),
                str(day.day),
                color,
                center=(cell.centerx, cell.centery - (4 if self._has_events(day) else 0)),
            )
            if self._has_events(day):
                pygame.draw.circle(
                    surface,
                    pal.on_accent if selected else pal.warning,
                    (cell.centerx, cell.centery + 13),
                    2,
                )

    def _draw_day(self, surface, pal) -> None:
        panel = self._day_panel()
        if not current_layout.is_landscape:
            draw_rounded(surface, panel, fill=pal.surface_alt, radius=14)
        title = self._selected.strftime("%A, %b %d")
        blit_text(surface, self.font(13, True), title, pal.text, topleft=(panel.x + 12, panel.y + 10))
        count = len(self._day_events(self._selected))
        blit_text(
            surface,
            self.font(9),
            f"{count} event{'s' if count != 1 else ''}",
            pal.text_muted,
            topleft=(panel.x + 14, panel.y + 30),
        )
        self._draw_add_button(surface, pal)
        area = self._day_events_area()
        events = self._day_events(self._selected)
        self._event_rects = []
        row_h = 58
        self._day_scroll.set_metrics(area.h, len(events) * (row_h + 6))
        if not events:
            blit_text(
                surface,
                self.font(11),
                "Nothing scheduled",
                pal.text_muted,
                center=(area.centerx, area.centery),
            )
        for index, event in enumerate(events):
            row = pygame.Rect(area.x, area.y + index * (row_h + 6) - int(self._day_scroll.offset), area.w, row_h)
            if row.bottom < area.y or row.y > area.bottom:
                continue
            draw_card(surface, row, pal)
            blit_text(
                surface,
                self.mono(14, True),
                str(event.get("time", "--:--")),
                pal.accent_ink,
                midleft=(row.x + 12, row.centery),
            )
            title_font = self.font(12, True)
            blit_text(
                surface,
                title_font,
                fit_text(title_font, str(event.get("title", "Event")), row.w - 94),
                pal.text,
                topleft=(row.x + 62, row.y + 10),
            )
            note_font = self.font(9)
            note = str(event.get("note", "")) or "No note"
            blit_text(
                surface,
                note_font,
                fit_text(note_font, note, row.w - 94),
                pal.text_muted,
                topleft=(row.x + 62, row.y + 30),
            )
            self._event_rects.append((int(event["id"]), row))
        draw_scrollbar(surface, area, self._day_scroll.offset, len(events) * (row_h + 6), pal)

    def _draw_add_button(self, surface, pal) -> None:
        rect = self._add_rect()
        draw_button(surface, rect, "+ ADD EVENT", self.font(10, True), pal, fill=pal.accent, color=pal.on_accent, stroke=None)

    def _draw_editor(self, surface, pal) -> None:
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill((0, 0, 0, 185))
        surface.blit(scrim, (0, 0))
        rects = self._editor_rects()
        panel = pygame.Rect(rects["title"].x - 18, rects["title"].y - 40, rects["title"].w + 36, 378)
        draw_rounded(surface, panel, fill=pal.surface_raised, stroke=pal.stroke, radius=22)
        blit_text(surface, self.font(16, True), "EDIT EVENT" if self._editor_id is not None else "NEW EVENT", pal.text, topleft=(panel.x + 20, panel.y + 14))
        blit_text(surface, self.font(9), self._selected.strftime("%A, %B %d"), pal.text_muted, topleft=(panel.x + 20, panel.y + 34))
        blit_text(surface, self.font(8, True), "TITLE", pal.text_muted, topleft=(rects["title"].x, rects["title"].y - 14), tracking=1.4)
        self._title.draw(surface, rects["title"], self.font(13), pal, focused=self._editor_focus == "title", blink=self._blink)
        blit_text(surface, self.font(8, True), "TIME", pal.text_muted, topleft=(rects["hour_minus"].x, rects["hour_minus"].y - 30), tracking=1.4)
        blit_text(
            surface,
            self.mono(28, True),
            f"{self._editor_hour:02d}:{self._editor_minute:02d}",
            pal.text,
            center=(panel.centerx, rects["hour_minus"].y - 28),
        )
        blit_text(surface, self.font(8, True), "HOUR", pal.text_muted, center=(rects["hour_minus"].centerx + 21, rects["hour_minus"].y - 10), tracking=1.0)
        blit_text(surface, self.font(8, True), "MIN", pal.text_muted, center=(rects["minute_minus"].centerx + 21, rects["minute_minus"].y - 10), tracking=1.0)
        for key in ("hour_minus", "hour_plus", "minute_minus", "minute_plus"):
            draw_button(surface, rects[key], "-" if "minus" in key else "+", self.font(15, True), pal)
        blit_text(surface, self.font(8, True), "NOTE", pal.text_muted, topleft=(rects["note"].x, rects["note"].y - 14), tracking=1.4)
        self._note.draw(surface, rects["note"], self.font(11), pal, focused=self._editor_focus == "note", blink=self._blink)
        draw_button(surface, rects["save"], "SAVE", self.font(12, True), pal, fill=pal.accent, color=pal.on_accent, stroke=None)
        draw_button(
            surface,
            rects["delete"],
            "DELETE" if self._editor_id is not None else "CANCEL",
            self.font(12, True),
            pal,
            fill=pal.surface_alt,
            color=pal.danger if self._editor_id is not None else pal.text_muted,
        )
        draw_button(surface, rects["back"], "BACK", self.font(10, True), pal, fill=pal.surface, color=pal.text_muted)

    def _draw_confirm(self, surface, pal) -> None:
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill((0, 0, 0, 170))
        surface.blit(scrim, (0, 0))
        width = min(340, self.content.w - 40)
        panel = pygame.Rect(self.content.centerx - width // 2, self.content.centery - 70, width, 150)
        draw_rounded(surface, panel, fill=pal.surface_raised, stroke=pal.stroke, radius=20)
        blit_text(surface, self.font(15, True), "Delete this event?", pal.text, center=(panel.centerx, panel.y + 34))
        blit_text(surface, self.font(10), "This cannot be undone.", pal.text_muted, center=(panel.centerx, panel.y + 55))
        yes, no = self._confirm_rects()
        draw_button(surface, yes, "DELETE", self.font(11, True), pal, fill=pal.danger, color=pal.on_accent, stroke=None)
        draw_button(surface, no, "CANCEL", self.font(11, True), pal, fill=pal.surface_alt)

    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)


__all__ = ["CalendarApp", "draw_icon"]
