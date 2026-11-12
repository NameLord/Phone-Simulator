"""Phone: contacts, numeric dialer and recent calls."""

from __future__ import annotations

import math

import pygame

from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, fit_text

APP_ID = "phone"
APP_NAME = "Phone"
TABS = ("keypad", "contacts", "recents")
TAB_LABELS = {"keypad": "KEYPAD", "contacts": "CONTACTS", "recents": "RECENTS"}
KEY_HINTS = {
    "2": "ABC",
    "3": "DEF",
    "4": "GHI",
    "5": "JKL",
    "6": "MNO",
    "7": "PQRS",
    "8": "TUV",
    "9": "WXYZ",
    "0": "+",
}


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.success, radius=max(8, rect.w // 4))
    center = rect.center
    width = max(8, int(rect.w * 0.30))
    body = pygame.Rect(0, 0, width, max(10, int(rect.h * 0.44)))
    body.center = (center[0], center[1] + int(rect.h * 0.03))
    pygame.draw.rect(surface, pal.on_accent, body, border_radius=max(3, width // 3))
    pygame.draw.arc(
        surface,
        pal.on_accent,
        pygame.Rect(body.centerx - width, body.top - int(rect.h * 0.2), width * 2, int(rect.h * 0.35)),
        0,
        math.pi,
        max(2, int(rect.w * 0.06)),
    )


class PhoneApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._tab = "keypad"
        self._digits = ""
        self._selected_contact: str | None = None
        self._tab_rects: dict[str, pygame.Rect] = {}
        self._contact_rects: list[tuple[str, pygame.Rect]] = []
        self._recent_rects: list[tuple[int, pygame.Rect]] = []
        self._keys = self._build_keys()

    # -- geometry -------------------------------------------------------------
    def _header_rect(self) -> pygame.Rect:
        return pygame.Rect(
            current_layout.margin,
            self.content.y + 12,
            self.content.w - current_layout.margin * 2,
            44,
        )

    def _tab_rects_for(self) -> dict[str, pygame.Rect]:
        header = self._header_rect()
        width = header.w // len(TABS)
        return {
            key: pygame.Rect(header.x + index * width, header.y, width - 6, header.h)
            for index, key in enumerate(TABS)
        }

    def _body_rect(self) -> pygame.Rect:
        header = self._header_rect()
        return pygame.Rect(
            current_layout.margin,
            header.bottom + 12,
            self.content.w - current_layout.margin * 2,
            self.content.bottom - 16 - (header.bottom + 12),
        )

    def _dial_rect(self) -> pygame.Rect:
        body = self._body_rect()
        if current_layout.is_landscape:
            return pygame.Rect(body.x, body.y, int(body.w * 0.46), 62)
        return pygame.Rect(body.x, body.y, body.w, 62)

    def _key_area(self) -> pygame.Rect:
        body = self._body_rect()
        dial = self._dial_rect()
        available_bottom = body.bottom - 62
        if current_layout.is_landscape:
            return pygame.Rect(body.x, dial.bottom + 12, int(body.w * 0.46), available_bottom - (dial.bottom + 12))
        return pygame.Rect(body.x, dial.bottom + 12, body.w, available_bottom - (dial.bottom + 12))

    def _action_area(self) -> pygame.Rect:
        body = self._body_rect()
        dial = self._dial_rect()
        top = self._key_area().bottom + 8
        width = int(body.w * 0.46) if current_layout.is_landscape else body.w
        return pygame.Rect(body.x, top, width, max(40, body.bottom - top))

    def _build_keys(self) -> list[tuple[str, pygame.Rect]]:
        area = self._key_area()
        labels = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#")
        gap = 8
        width = (area.w - gap * 2) / 3
        height = (area.h - gap * 3) / 4
        rows = [labels[index : index + 3] for index in range(0, 9, 3)] + [("*", "0", "#")]
        keys: list[tuple[str, pygame.Rect]] = []
        for row_index, row in enumerate(rows):
            for column, label in enumerate(row):
                keys.append(
                    (
                        label,
                        pygame.Rect(
                            int(area.x + column * (width + gap)),
                            int(area.y + row_index * (height + gap)),
                            int(width),
                            int(height),
                        ),
                    )
                )
        return keys

    def _call_rect(self) -> pygame.Rect:
        area = self._action_area()
        return pygame.Rect(area.x, area.y, max(80, area.w - 64), area.h)

    def _back_rect(self) -> pygame.Rect:
        area = self._action_area()
        return pygame.Rect(area.right - 56, area.y, 56, area.h)

    def _list_area(self) -> pygame.Rect:
        body = self._body_rect()
        if current_layout.is_landscape:
            return pygame.Rect(
                body.x + int(body.w * 0.50),
                body.y,
                int(body.w * 0.50),
                body.h,
            )
        return body

    def _list_rows(self, count: int) -> list[pygame.Rect]:
        area = self._list_area()
        height = min(64, max(40, (area.h - 8) // max(1, count)))
        return [
            pygame.Rect(area.x, area.y + index * (height + 8), area.w, height)
            for index in range(count)
        ]

    def _visible_count(self, total: int) -> int:
        """How many list rows fit without spilling out of the app body."""
        area = self._list_area()
        return max(1, min(total, (area.h + 8) // 48))

    def on_resize(self) -> None:
        self._keys = self._build_keys()

    # -- input ----------------------------------------------------------------
    def open_payload(self, payload: dict) -> None:
        telephony = getattr(self.ctx, "telephony", None)
        contact_id = str(payload.get("contact_id", ""))
        if telephony is not None and contact_id and telephony.contact(contact_id) is not None:
            self._selected_contact = contact_id

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.KEY_DOWN:
            return self._handle_key(event)
        if event.kind is not EventKind.TAP:
            return False
        self._tab_rects = self._tab_rects_for()
        for key, rect in self._tab_rects.items():
            if rect.collidepoint(event.pos):
                self._tab = key
                self._digits = ""
                self._keys = self._build_keys()
                return True
        if self._tab == "keypad":
            for label, rect in self._keys:
                if rect.collidepoint(event.pos):
                    if label == "\u232b":
                        self._digits = self._digits[:-1]
                    else:
                        if len(self._digits) < 18:
                            self._digits += label
                    return True
            if self._back_rect().collidepoint(event.pos):
                self._digits = self._digits[:-1]
                return True
            if self._call_rect().collidepoint(event.pos):
                telephony = getattr(self.ctx, "telephony", None)
                if telephony is not None and self._digits:
                    telephony.place_call(self._digits)
                return True
            return False
        if self._tab == "contacts":
            for contact_id, rect in self._contact_rects:
                if rect.collidepoint(event.pos):
                    contact = self.ctx.telephony.contact(contact_id) if self.ctx.telephony else None
                    if contact is not None:
                        self._digits = contact.number
                        self._selected_contact = contact_id
                        self._tab = "keypad"
                        self._keys = self._build_keys()
                    return True
            return False
        for index, rect in self._recent_rects:
            if rect.collidepoint(event.pos):
                telephony = self.ctx.telephony
                if telephony is not None and 0 <= index < len(telephony.recent_calls):
                    call = telephony.recent_calls[index]
                    telephony.place_call(call.number, call.name)
                return True
        return False

    def _handle_key(self, event: PointerEvent) -> bool:
        key = event.key
        text = event.text or ""
        if text and text in "0123456789*#+":
            if len(self._digits) < 18:
                self._digits += text
            return True
        if key == pygame.K_BACKSPACE:
            self._digits = self._digits[:-1]
            return True
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self._digits:
                telephony = getattr(self.ctx, "telephony", None)
                if telephony is not None:
                    telephony.place_call(self._digits)
            return True
        return False

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        telephony = getattr(self.ctx, "telephony", None)
        self._draw_tabs(surface, pal)
        if self._tab == "keypad":
            self._draw_dialer(surface, pal)
            if current_layout.is_landscape and telephony is not None:
                self._draw_contacts(surface, pal, telephony, compact=True)
        elif self._tab == "contacts":
            if telephony is not None:
                self._draw_contacts(surface, pal, telephony)
        else:
            self._draw_recents(surface, pal, telephony)

    def _draw_tabs(self, surface, pal) -> None:
        self._tab_rects = self._tab_rects_for()
        for key, rect in self._tab_rects.items():
            active = key == self._tab
            draw_rounded(
                surface,
                rect,
                fill=pal.accent_soft if active else pal.surface,
                stroke=pal.accent if active else pal.stroke_soft,
                radius=12,
            )
            blit_text(
                surface,
                self.font(10, True),
                TAB_LABELS[key],
                pal.accent_ink if active else pal.text_muted,
                center=rect.center,
                tracking=1.0,
            )

    def _draw_dialer(self, surface, pal) -> None:
        self._keys = self._build_keys()
        dial = self._dial_rect()
        draw_rounded(surface, dial, fill=pal.surface, stroke=pal.stroke_soft, radius=18)
        text = self._digits or "Enter a number"
        color = pal.text if self._digits else pal.text_muted
        font = self.mono(20, True)
        blit_text(surface, font, fit_text(font, text, dial.w - 36), color, center=dial.center)
        for label, rect in self._keys:
            draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke_soft, radius=18)
            hint = KEY_HINTS.get(label, "")
            if hint:
                blit_text(
                    surface,
                    self.font(22, True),
                    label,
                    pal.text,
                    center=(rect.centerx, rect.centery - 9),
                )
                blit_text(
                    surface,
                    self.font(8, True),
                    hint,
                    pal.text_muted,
                    center=(rect.centerx, rect.centery + 15),
                    tracking=1.4,
                )
            else:
                blit_text(surface, self.font(22, True), label, pal.text, center=rect.center)
        back = self._back_rect()
        draw_rounded(surface, back, fill=pal.surface_alt, stroke=pal.stroke_soft, radius=14)
        blit_text(surface, self.font(14, True), "DEL", pal.text_secondary, center=back.center)
        call = self._call_rect()
        draw_rounded(surface, call, fill=pal.success, radius=18)
        blit_text(surface, self.font(13, True), "CALL", pal.on_accent, center=call.center, tracking=1.2)

    def _draw_contacts(self, surface, pal, telephony, compact: bool = False) -> None:
        area = self._list_area() if not compact else self._list_area()
        contacts = telephony.contacts
        self._contact_rects = []
        if compact:
            header = area.y
            blit_text(surface, self.font(10, True), "CONTACTS", pal.text_muted, topleft=(area.x, header))
            area = pygame.Rect(area.x, header + 24, area.w, area.h - 24)
        contacts = contacts[: self._visible_count(len(contacts))]
        rows = self._list_rows(len(contacts))
        for (row, contact) in zip(rows, contacts):
            self._contact_rects.append((contact.contact_id, row))
            draw_rounded(surface, row, fill=pal.surface, stroke=pal.stroke_soft, radius=14)
            avatar = pygame.Rect(row.x + 8, row.y + 8, row.h - 16, row.h - 16)
            pygame.draw.circle(surface, contact.color, avatar.center, max(8, avatar.w // 2))
            blit_text(
                surface,
                self.font(12, True),
                contact.name[:1].upper(),
                pal.on_accent,
                center=avatar.center,
            )
            name_font = self.font(13, True)
            blit_text(
                surface,
                name_font,
                fit_text(name_font, contact.name, row.w - 70),
                pal.text,
                midleft=(avatar.right + 10, row.centery - 7),
            )
            blit_text(
                surface,
                self.font(10),
                contact.number,
                pal.text_muted,
                midleft=(avatar.right + 10, row.centery + 8),
            )

    def _draw_recents(self, surface, pal, telephony) -> None:
        if telephony is None:
            return
        area = self._list_area()
        records = telephony.recent_calls[: self._visible_count(len(telephony.recent_calls))]
        self._recent_rects = []
        if not records:
            blit_text(
                surface,
                self.font(13),
                "No recent calls",
                pal.text_muted,
                center=area.center,
            )
            return
        for index, (row, record) in enumerate(zip(self._list_rows(len(records)), records)):
            self._recent_rects.append((index, row))
            draw_rounded(surface, row, fill=pal.surface, stroke=pal.stroke_soft, radius=14)
            direction_color = pal.danger if record.direction == "missed" else pal.success
            pygame.draw.circle(surface, direction_color, (row.x + 18, row.centery), 5)
            name_font = self.font(13, True)
            blit_text(
                surface,
                name_font,
                fit_text(name_font, record.name, row.w - 100),
                pal.text,
                midleft=(row.x + 34, row.centery - 8),
            )
            blit_text(
                surface,
                self.font(10),
                f"{record.direction} \u00b7 {int(record.duration)}s",
                pal.text_muted,
                midleft=(row.x + 34, row.centery + 9),
            )

    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)
