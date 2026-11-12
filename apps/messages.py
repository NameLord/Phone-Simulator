"""Messages: persisted conversation threads with canned delayed replies."""

from __future__ import annotations

from datetime import datetime

import pygame

from apps._kit import ScrollState, draw_scrollbar
from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_chevron, draw_rounded, fit_text, wrap_text

APP_ID = "messages"
APP_NAME = "Messages"
MAX_DRAFT = 240


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.accent, radius=max(8, rect.w // 4))
    bubble = rect.inflate(-int(rect.w * 0.34), -int(rect.h * 0.40))
    bubble.centery -= int(rect.h * 0.03)
    pygame.draw.rect(surface, pal.on_accent, bubble, border_radius=max(5, rect.w // 9))
    pygame.draw.polygon(
        surface,
        pal.on_accent,
        [
            (bubble.x + int(bubble.w * 0.22), bubble.bottom - 2),
            (bubble.x + int(bubble.w * 0.42), bubble.bottom - 2),
            (bubble.x + int(bubble.w * 0.22), bubble.bottom + int(rect.h * 0.10)),
        ],
    )
    for index in range(3):
        pygame.draw.circle(
            surface,
            pal.accent,
            (bubble.centerx - 7 + index * 7, bubble.centery),
            2,
        )


class MessagesApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._active: str | None = None
        self._draft = ""
        self._scroll = 0.0
        self._thread_rects: list[tuple[str, pygame.Rect]] = []
        self._delete_rects: list[tuple[str, pygame.Rect]] = []
        self._delete_confirm: str | None = None
        self._typing_phase = 0.0
        self._list_scroll = ScrollState(step=50)
        self._chat_press_y: float | None = None
        self._chat_start_scroll = 0.0

    # -- geometry -------------------------------------------------------------
    def _pane_split(self) -> tuple[pygame.Rect, pygame.Rect]:
        margin = current_layout.margin
        top = self.content.y + 12
        bottom = self.content.bottom - 12
        if current_layout.is_landscape:
            list_w = int((self.content.w - margin * 2) * 0.36)
            left = pygame.Rect(margin, top, list_w, bottom - top)
            right = pygame.Rect(left.right + 12, top, self.content.w - margin - (left.right + 12), bottom - top)
            return left, right
        full = pygame.Rect(margin, top, self.content.w - margin * 2, bottom - top)
        return full, full

    def _list_area(self) -> pygame.Rect:
        left, right = self._pane_split()
        return left

    def _thread_area(self) -> pygame.Rect:
        _left, right = self._pane_split()
        return right

    def _input_rect(self) -> pygame.Rect:
        area = self._thread_area()
        return pygame.Rect(area.x + 8, area.bottom - 52, area.w - 82, 44)

    def _send_rect(self) -> pygame.Rect:
        area = self._thread_area()
        return pygame.Rect(area.right - 68, area.bottom - 52, 60, 44)

    def _back_rect(self) -> pygame.Rect:
        area = self._thread_area()
        return pygame.Rect(area.x + 8, area.y + 8, 42, 36)

    def _thread_header(self) -> pygame.Rect:
        area = self._thread_area()
        return pygame.Rect(area.x, area.y, area.w, 50)

    def _bubble_area(self) -> pygame.Rect:
        area = self._thread_area()
        return pygame.Rect(area.x + 4, area.y + 56, area.w - 8, area.h - 56 - 60)

    # -- lifecycle ------------------------------------------------------------
    def update(self, dt: float) -> None:
        super().update(dt)
        self._typing_phase += max(0.0, dt)
        telephony = getattr(self.ctx, "telephony", None)
        if telephony is not None and self._active is not None:
            telephony.mark_thread_read(self._active)
            self.ctx.notifications.mark_app_read("messages")

    def on_resize(self) -> None:
        self._list_scroll.reset()
        self._chat_press_y = None

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        telephony = getattr(self.ctx, "telephony", None)
        if telephony is None:
            return False
        if self._delete_confirm is not None:
            return self._handle_delete(event, telephony)
        list_visible = self._active is None or current_layout.is_landscape
        if event.kind is EventKind.WHEEL:
            if list_visible:
                return self._list_scroll.wheel(event)
            self._scroll = max(0.0, min(self._max_scroll(telephony), self._scroll - event.delta[1] * 38.0))
            return True
        if event.kind is EventKind.POINTER_DOWN:
            if list_visible:
                self._list_scroll.pointer(event, self._list_area())
            elif self._bubble_area().collidepoint(event.pos):
                self._chat_press_y = float(event.pos[1])
                self._chat_start_scroll = self._scroll
            return event.kind is EventKind.POINTER_DOWN
        if event.kind is EventKind.DRAG:
            if list_visible:
                self._list_scroll.pointer(event, self._list_area())
            elif self._chat_press_y is not None:
                self._scroll = max(0.0, min(self._max_scroll(telephony), self._chat_start_scroll + (event.pos[1] - self._chat_press_y)))
            return True
        if event.kind in (EventKind.POINTER_UP, EventKind.DRAG_END):
            if list_visible:
                self._list_scroll.pointer(event, self._list_area())
            self._chat_press_y = None
            return False

        if event.kind is EventKind.KEY_DOWN:
            return self._handle_key(event, telephony)

        if event.kind is not EventKind.TAP:
            return False

        if self._active is None or current_layout.is_landscape:
            for contact_id, rect in self._delete_rects:
                if rect.collidepoint(event.pos):
                    self._delete_confirm = contact_id
                    return True
        if self._active is not None:
            if self._back_rect().collidepoint(event.pos):
                self._active = None
                self._draft = ""
                return True
            if self._send_rect().collidepoint(event.pos):
                self._send(telephony)
                return True
            if self._input_rect().collidepoint(event.pos) or self._bubble_area().collidepoint(event.pos):
                return True
        if not current_layout.is_landscape:
            for contact_id, rect in self._thread_rects:
                if rect.collidepoint(event.pos):
                    self._open(contact_id)
                    return True
        else:
            for contact_id, rect in self._thread_rects:
                if rect.collidepoint(event.pos):
                    self._open(contact_id)
                    return True
        return False

    def _open(self, contact_id: str) -> None:
        self._active = contact_id
        self._draft = ""

    def open_payload(self, payload: dict) -> None:
        telephony = getattr(self.ctx, "telephony", None)
        if telephony is None:
            return
        contact_id = str(payload.get("contact_id", ""))
        if not contact_id:
            query = str(payload.get("query", "")).lower()
            contact = next((item for item in telephony.contacts if query and (query in item.name.lower() or query in item.number)), None)
            contact_id = contact.contact_id if contact is not None else ""
        if contact_id:
            self._open(contact_id)
            text = str(payload.get("text", ""))
            if text:
                self._draft = text[:MAX_DRAFT]
        self._scroll = 0.0
        telephony = getattr(self.ctx, "telephony", None)
        if telephony is not None:
            telephony.mark_thread_read(contact_id)
        self.ctx.notifications.mark_app_read("messages")

    def _handle_delete(self, event: PointerEvent, telephony) -> bool:
        if event.kind is EventKind.KEY_DOWN:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._confirm_delete(telephony)
            elif event.key == pygame.K_ESCAPE:
                self._delete_confirm = None
            return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        yes, no = self._delete_buttons()
        if yes.collidepoint(event.pos):
            self._confirm_delete(telephony)
        elif no.collidepoint(event.pos):
            self._delete_confirm = None
        return True

    def _confirm_delete(self, telephony) -> None:
        if self._delete_confirm is not None:
            telephony.delete_thread(self._delete_confirm)
            if self._active == self._delete_confirm:
                self._active = None
        self._delete_confirm = None

    def _delete_buttons(self) -> tuple[pygame.Rect, pygame.Rect]:
        width = min(340, self.content.w - 40)
        left = self.content.centerx - width // 2
        top = self.content.centery - 20
        return (
            pygame.Rect(left + 16, top + 66, (width - 42) // 2, 38),
            pygame.Rect(left + 26 + (width - 42) // 2, top + 66, (width - 42) // 2, 38),
        )

    def _handle_key(self, event: PointerEvent, telephony) -> bool:
        if self._active is None or current_layout.is_landscape:
            if self._list_scroll.key(event):
                return True
        if self._active is None:
            return False
        if event.key == pygame.K_ESCAPE:
            self._active = None
            return True
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._send(telephony)
            return True
        if event.key == pygame.K_BACKSPACE:
            self._draft = self._draft[:-1]
            return True
        if event.key == pygame.K_UP:
            self._scroll = max(0.0, self._scroll - 32.0)
            return True
        if event.key == pygame.K_DOWN:
            self._scroll = min(self._max_scroll(telephony), self._scroll + 32.0)
            return True
        text = event.text or ""
        for character in text:
            if character.isprintable() and character not in "\t\r\n" and len(self._draft) < MAX_DRAFT:
                self._draft += character
        return True

    def _send(self, telephony) -> None:
        if self._active is None or not self._draft.strip():
            return
        if telephony.send_message(self._active, self._draft):
            self._draft = ""
            self._scroll = self._max_scroll(telephony)

    def _max_scroll(self, telephony) -> float:
        if self._active is None:
            return 0.0
        messages = telephony.conversation(self._active)
        width = self._bubble_area().w - 28
        total = sum(self._bubble_height(message.text, width) + 8 for message in messages)
        return max(0.0, total - self._bubble_area().h + 8)

    def _bubble_height(self, text: str, width: int) -> int:
        font = self.font(13)
        lines = wrap_text(font, text, max(40, width - 24))
        return max(48, 28 + len(lines) * 18)

    @staticmethod
    def _stamp(message) -> str:
        try:
            return datetime.fromtimestamp(message.timestamp).strftime("%H:%M")
        except (OverflowError, OSError, ValueError):
            return "--:--"

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        telephony = getattr(self.ctx, "telephony", None)
        if telephony is None:
            blit_text(surface, self.font(13), "Messages unavailable", pal.text_muted, center=self.content.center)
            return
        if current_layout.is_landscape:
            self._draw_list(surface, pal, telephony, self._list_area())
            if self._active is not None:
                self._draw_thread(surface, pal, telephony)
        elif self._active is None:
            self._draw_list(surface, pal, telephony, self._list_area())
        else:
            self._draw_thread(surface, pal, telephony)
        if self._delete_confirm is not None:
            self._draw_delete(surface, pal)

    def _draw_list(self, surface, pal, telephony, area: pygame.Rect) -> None:
        summaries = telephony.thread_summaries()
        self._thread_rects = []
        self._delete_rects = []
        row_h = 64
        self._list_scroll.set_metrics(area.h, len(summaries) * (row_h + 8))
        for index, (contact, preview, stamp) in enumerate(summaries):
            rect = pygame.Rect(area.x, area.y + index * (row_h + 8) - int(self._list_scroll.offset), area.w, row_h)
            if rect.bottom < area.y or rect.y > area.bottom:
                continue
            self._thread_rects.append((contact.contact_id, rect))
            selected = contact.contact_id == self._active
            draw_rounded(
                surface,
                rect,
                fill=pal.surface_raised if selected else pal.surface,
                stroke=pal.accent if selected else pal.stroke_soft,
                radius=14,
            )
            avatar_size = max(28, row_h - 18)
            avatar = pygame.Rect(rect.x + 9, rect.centery - avatar_size // 2, avatar_size, avatar_size)
            pygame.draw.circle(surface, contact.color, avatar.center, avatar_size // 2)
            blit_text(
                surface,
                self.font(13, True),
                contact.name[:1].upper(),
                pal.on_accent,
                center=avatar.center,
            )
            name_font = self.font(13, True)
            blit_text(
                surface,
                name_font,
                fit_text(name_font, contact.name, rect.w - avatar_size - 30),
                pal.text,
                topleft=(avatar.right + 10, rect.y + 10),
            )
            preview_font = self.font(10)
            blit_text(
                surface,
                preview_font,
                fit_text(preview_font, preview, rect.w - avatar_size - 70),
                pal.text_muted,
                topleft=(avatar.right + 10, rect.y + 30),
            )
            unread = telephony.unread_count(contact.contact_id)
            if unread:
                badge = pygame.Rect(rect.right - 48, rect.y + 12, 22, 20)
                draw_rounded(surface, badge, fill=pal.accent, radius=9)
                blit_text(surface, self.font(9, True), str(min(99, unread)), pal.on_accent, center=badge.center)
            delete = pygame.Rect(rect.right - 30, rect.bottom - 28, 20, 20)
            pygame.draw.line(surface, pal.danger, (delete.x + 4, delete.y + 4), (delete.right - 4, delete.bottom - 4), 2)
            pygame.draw.line(surface, pal.danger, (delete.right - 4, delete.y + 4), (delete.x + 4, delete.bottom - 4), 2)
            self._delete_rects.append((contact.contact_id, delete))
        draw_scrollbar(surface, area, self._list_scroll.offset, len(summaries) * (row_h + 8), pal)

    def _draw_thread(self, surface, pal, telephony) -> None:
        contact = telephony.contact(self._active or "")
        if contact is None:
            self._active = None
            return
        area = self._thread_area()
        if not current_layout.is_landscape:
            back = self._back_rect()
            draw_rounded(surface, back, fill=pal.surface, stroke=pal.stroke_soft, radius=10)
            draw_chevron(surface, back.center, pal.text, size=6, direction="left")
        blit_text(
            surface,
            self.font(14, True),
            contact.name,
            pal.text,
            topleft=(area.x + 56 if not current_layout.is_landscape else area.x + 10, area.y + 10),
        )
        blit_text(
            surface,
            self.font(10),
            contact.number,
            pal.text_muted,
            topleft=(area.x + 56 if not current_layout.is_landscape else area.x + 10, area.y + 30),
        )
        messages = telephony.conversation(contact.contact_id)
        bubble_area = self._bubble_area()
        max_scroll = self._max_scroll(telephony)
        self._scroll = max(0.0, min(self._scroll, max_scroll))
        y = bubble_area.bottom - 4 + self._scroll
        # Draw from newest backwards, then flip positions by walking upward.
        width_limit = bubble_area.w - 28
        for message in reversed(messages):
            height = self._bubble_height(message.text, width_limit)
            y -= height + 8
            if y + height < bubble_area.y or y > bubble_area.bottom:
                continue
            font = self.font(13)
            lines = wrap_text(font, message.text, max(40, width_limit - 24))
            text_w = max(font.size(line)[0] for line in lines) if lines else 0
            bubble_w = min(bubble_area.w - 20, max(70, text_w + 24))
            mine = message.sender == "me"
            x = bubble_area.right - bubble_w if mine else bubble_area.x
            bubble = pygame.Rect(x, y, bubble_w, height)
            draw_rounded(
                surface,
                bubble,
                fill=pal.accent if mine else pal.surface_raised,
                stroke=pal.accent_ink if mine else pal.stroke_soft,
                radius=14,
            )
            color = pal.on_accent if mine else pal.text
            text_y = bubble.y + 7
            for line in lines:
                blit_text(surface, font, line, color, topleft=(bubble.x + 12, text_y))
                text_y += 18
            blit_text(
                surface,
                self.font(7),
                self._stamp(message),
                pal.on_accent if mine else pal.text_muted,
                topleft=(bubble.x + 12, bubble.bottom - 12),
                alpha=180,
            )

        if telephony.is_typing(contact.contact_id):
            dots = int(self._typing_phase * 3.0) % 3 + 1
            typing = pygame.Rect(bubble_area.x, max(bubble_area.y, y - 32), 86, 26)
            draw_rounded(surface, typing, fill=pal.surface_raised, radius=13)
            for dot in range(dots):
                pygame.draw.circle(surface, pal.text_muted, (typing.x + 16 + dot * 9, typing.centery), 3)

        input_rect = self._input_rect()
        draw_rounded(surface, input_rect, fill=pal.surface, stroke=pal.stroke_soft, radius=12)
        blit_text(
            surface,
            self.font(12),
            fit_text(self.font(12), self._draft or "Type a message\u2026", input_rect.w - 20),
            pal.text if self._draft else pal.text_muted,
            midleft=(input_rect.x + 10, input_rect.centery),
        )
        send = self._send_rect()
        active = bool(self._draft.strip())
        draw_rounded(surface, send, fill=pal.accent if active else pal.surface_alt, radius=12)
        blit_text(
            surface,
            self.font(11, True),
            "SEND",
            pal.on_accent if active else pal.text_muted,
            center=send.center,
            tracking=1.0,
        )

    def _draw_delete(self, surface, pal) -> None:
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill((0, 0, 0, 175))
        surface.blit(scrim, (0, 0))
        width = min(340, self.content.w - 40)
        panel = pygame.Rect(self.content.centerx - width // 2, self.content.centery - 70, width, 150)
        draw_rounded(surface, panel, fill=pal.surface_raised, stroke=pal.stroke, radius=20)
        blit_text(surface, self.font(14, True), "Delete thread?", pal.text, center=(panel.centerx, panel.y + 34))
        blit_text(surface, self.font(9), "All messages in this conversation will be removed.", pal.text_muted, center=(panel.centerx, panel.y + 54))
        yes, no = self._delete_buttons()
        pygame.draw.rect(surface, pal.danger, yes, border_radius=10)
        blit_text(surface, self.font(10, True), "DELETE", pal.on_accent, center=yes.center)
        pygame.draw.rect(surface, pal.surface_alt, no, border_radius=10)
        blit_text(surface, self.font(10, True), "CANCEL", pal.text, center=no.center)

    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)
