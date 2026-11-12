"""Contacts: searchable address book with favorites and full CRUD."""

from __future__ import annotations

from collections import defaultdict

import pygame

from apps._kit import ScrollState, TextField, draw_button, draw_card, draw_scrollbar
from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, fit_text, wrap_text

APP_ID = "contacts"
APP_NAME = "Contacts"


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=max(8, rect.w // 4))
    center = (rect.centerx - 2, rect.centery - 4)
    pygame.draw.circle(surface, pal.accent, center, max(8, rect.w // 7))
    pygame.draw.circle(surface, pal.bg_top, (center[0], center[1] - 2), max(3, rect.w // 20))
    pygame.draw.arc(
        surface,
        pal.bg_top,
        pygame.Rect(center[0] - rect.w // 9, center[1] + 2, rect.w // 4, rect.w // 5),
        0,
        3.14159,
        max(2, rect.w // 24),
    )
    pygame.draw.line(surface, pal.accent, (center[0] + rect.w // 8, rect.centery + 10), (rect.right - 8, rect.centery + 18), 2)


class ContactsApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._query = ""
        self._scroll = ScrollState(step=52)
        self._rows: list[tuple[int, pygame.Rect, str, bool]] = []
        self._selected: str | None = None
        self._editing = False
        self._fields = {
            "name": TextField("", max_length=80, placeholder="Name"),
            "number": TextField("", max_length=40, placeholder="Phone number"),
            "email": TextField("", max_length=120, placeholder="Email"),
            "note": TextField("", max_length=400, multiline=True, placeholder="Note"),
        }
        self._focus = "name"
        self._draft_favorite = False
        self._delete_confirm = False
        self._blink = 0.0
        self._toast = ""
        self._toast_t = 0.0

    # -- lifecycle ------------------------------------------------------------
    def update(self, dt: float) -> None:
        super().update(dt)
        self._blink = (self._blink + dt) % 1.06
        if self._toast_t > 0.0:
            self._toast_t = max(0.0, self._toast_t - dt)
            if self._toast_t == 0.0:
                self._toast = ""

    def on_resize(self) -> None:
        self._scroll.reset()

    @property
    def telephony(self):
        return getattr(self.ctx, "telephony", None)

    def _flash(self, text: str) -> None:
        self._toast = text
        self._toast_t = 1.7

    # -- geometry -------------------------------------------------------------
    def _search_rect(self) -> pygame.Rect:
        return pygame.Rect(current_layout.margin, self.content.y + 8, self.content.w - current_layout.margin * 2 - 104, 38)

    def _add_rect(self) -> pygame.Rect:
        search = self._search_rect()
        return pygame.Rect(search.right + 8, search.y, 96, 38)

    def _list_area(self) -> pygame.Rect:
        return pygame.Rect(
            current_layout.margin,
            self.content.y + 56,
            self.content.w - current_layout.margin * 2,
            self.content.h - 72,
        )

    def _editor_rects(self) -> dict[str, pygame.Rect]:
        width = min(430, self.content.w - 36)
        left = self.content.centerx - width // 2
        top = self.content.y + 10
        return {
            "name": pygame.Rect(left + 18, top + 44, width - 36, 36),
            "number": pygame.Rect(left + 18, top + 100, width - 36, 36),
            "email": pygame.Rect(left + 18, top + 156, width - 36, 36),
            "note": pygame.Rect(left + 18, top + 212, width - 36, 62),
            "favorite": pygame.Rect(left + 18, top + 286, 128, 32),
            "save": pygame.Rect(left + 18, top + 330, (width - 48) // 2, 38),
            "delete": pygame.Rect(left + 28 + (width - 48) // 2, top + 330, (width - 48) // 2, 38),
            "back": pygame.Rect(left + 18, top + 366, width - 36, 32),
        }

    def _delete_buttons(self) -> tuple[pygame.Rect, pygame.Rect]:
        width = min(340, self.content.w - 40)
        left = self.content.centerx - width // 2
        top = self.content.centery - 20
        return (
            pygame.Rect(left + 16, top + 70, (width - 42) // 2, 38),
            pygame.Rect(left + 26 + (width - 42) // 2, top + 70, (width - 42) // 2, 38),
        )

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if self._delete_confirm:
            return self._handle_delete(event)
        if self._editing:
            return self._handle_editor(event)
        if event.kind is EventKind.WHEEL:
            return self._scroll.wheel(event)
        if event.kind is EventKind.POINTER_DOWN:
            self._scroll.pointer(event, self._list_area())
        if event.kind in (EventKind.DRAG, EventKind.DRAG_END, EventKind.POINTER_UP):
            self._scroll.pointer(event, self._list_area())
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_UP:
                self._scroll.offset -= self._scroll.step
                self._scroll.clamp()
                return True
            if event.key == pygame.K_DOWN:
                self._scroll.offset += self._scroll.step
                self._scroll.clamp()
                return True
            if event.key == pygame.K_BACKSPACE and self._query:
                self._query = self._query[:-1]
                return True
            if event.key == pygame.K_RETURN and self._rows:
                self._open_editor(self._rows[0][2])
                return True
            text = event.text or ""
            for character in text:
                if character.isprintable() and character not in "\t\r\n":
                    self._query += character
                    self._scroll.offset = 0.0
            return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        if self._add_rect().collidepoint(event.pos):
            self._open_editor(None)
            return True
        for _index, rect, contact_id, _favorite in self._rows:
            if rect.collidepoint(event.pos):
                self._open_editor(contact_id)
                return True
        return False

    def _open_editor(self, contact_id: str | None) -> None:
        telephony = self.telephony
        if telephony is None:
            return
        self._selected = contact_id
        self._editing = True
        self._focus = "name"
        self._draft_favorite = False
        if contact_id is None:
            for field in self._fields.values():
                field.clear()
        else:
            contact = telephony.contact(contact_id)
            if contact is None:
                self._editing = False
                return
            self._fields["name"].set(contact.name)
            self._fields["number"].set(contact.number)
            self._fields["email"].set(contact.email)
            self._fields["note"].set(contact.note)
            self._draft_favorite = contact.favorite

    def _handle_editor(self, event: PointerEvent) -> bool:
        rects = self._editor_rects()
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self._editing = False
                return True
            if event.key == pygame.K_TAB:
                order = ("name", "number", "email", "note")
                self._focus = order[(order.index(self._focus) + 1) % len(order)]
                return True
            self._fields[self._focus].handle(event)
            return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        for key in ("name", "number", "email", "note"):
            if rects[key].collidepoint(event.pos):
                self._focus = key
                return True
        if rects["favorite"].collidepoint(event.pos):
            self._draft_favorite = not self._draft_favorite
            return True
        if rects["save"].collidepoint(event.pos):
            self._save()
        elif rects["delete"].collidepoint(event.pos):
            if self._selected is not None:
                self._delete_confirm = True
            else:
                self._editing = False
        elif rects["back"].collidepoint(event.pos):
            self._editing = False
        return True

    def _save(self) -> None:
        telephony = self.telephony
        if telephony is None:
            return
        name = self._fields["name"].value.strip()
        number = self._fields["number"].value.strip()
        if not name or not number:
            self._flash("Name and number are required")
            return
        if self._selected is None:
            contact = telephony.add_contact(
                name,
                number,
                self._fields["email"].value,
                self._fields["note"].value,
            )
            if contact is not None and self._draft_favorite:
                telephony.update_contact(contact.contact_id, favorite=True)
            self._flash("Contact added")
        else:
            telephony.update_contact(
                self._selected,
                name=name,
                number=number,
                email=self._fields["email"].value,
                note=self._fields["note"].value,
                favorite=self._draft_favorite,
            )
            self._flash("Contact saved")
        self._editing = False

    def _handle_delete(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.KEY_DOWN:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._delete()
            elif event.key == pygame.K_ESCAPE:
                self._delete_confirm = False
            return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        yes, no = self._delete_buttons()
        if yes.collidepoint(event.pos):
            self._delete()
        elif no.collidepoint(event.pos):
            self._delete_confirm = False
        return True

    def _delete(self) -> None:
        self._delete_confirm = False
        if self._selected is not None and self.telephony is not None:
            self.telephony.delete_contact(self._selected)
            self._flash("Contact deleted")
        self._editing = False
        self._selected = None

    # -- data / list ----------------------------------------------------------
    def _sections(self) -> list[tuple[str, list[object]]]:
        telephony = self.telephony
        if telephony is None:
            return []
        contacts = telephony.search_contacts(self._query)
        favorites = [contact for contact in contacts if contact.favorite]
        rest = [contact for contact in contacts if not contact.favorite]
        grouped: dict[str, list[object]] = defaultdict(list)
        for contact in rest:
            grouped[contact.name[:1].upper() if contact.name else "#"].append(contact)
        sections: list[tuple[str, list[object]]] = []
        if favorites:
            sections.append(("FAVORITES", sorted(favorites, key=lambda item: item.name.casefold())))
        for letter in sorted(grouped):
            sections.append((letter, sorted(grouped[letter], key=lambda item: item.name.casefold())))
        return sections

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        if self._editing:
            self._draw_editor(surface, pal)
        else:
            self._draw_list(surface, pal)
        if self._delete_confirm:
            self._draw_delete(surface, pal)
        if self._toast:
            blit_text(surface, self.font(9, True), self._toast, pal.accent_ink, center=(self.content.centerx, self.content.bottom - 10))

    def _draw_list(self, surface, pal) -> None:
        search = self._search_rect()
        draw_rounded(surface, search, fill=pal.surface, stroke=pal.stroke_soft, radius=12)
        blit_text(
            surface,
            self.font(11),
            fit_text(self.font(11), self._query or "Search name, number or email", search.w - 20),
            pal.text if self._query else pal.text_muted,
            midleft=(search.x + 12, search.centery),
        )
        draw_button(surface, self._add_rect(), "+ ADD", self.font(9, True), pal, fill=pal.accent, color=pal.on_accent, stroke=None)
        area = self._list_area()
        sections = self._sections()
        row_h = 50
        header_h = 22
        content_h = sum(header_h + len(rows) * (row_h + 5) for _title, rows in sections)
        self._scroll.set_metrics(area.h, content_h)
        self._rows = []
        y = area.y - int(self._scroll.offset)
        surface.set_clip(area)
        if not sections:
            surface.set_clip(None)
            blit_text(surface, self.font(12), "No contacts found", pal.text_muted, center=(area.centerx, area.centery))
            return
        for title, contacts in sections:
            if y + header_h > area.y and y < area.bottom:
                blit_text(surface, self.font(9, True), title, pal.accent_ink if title == "FAVORITES" else pal.text_muted, topleft=(area.x + 4, y + 4), tracking=1.4)
            y += header_h
            for contact in contacts:
                row = pygame.Rect(area.x, y, area.w, row_h)
                if row.bottom > area.y and row.y < area.bottom:
                    draw_card(surface, row, pal, radius=12)
                    avatar = pygame.Rect(row.x + 8, row.y + 7, 36, 36)
                    pygame.draw.circle(surface, contact.color, avatar.center, 18)
                    blit_text(surface, self.font(13, True), contact.name[:1].upper(), pal.on_accent, center=avatar.center)
                    name_font = self.font(12, True)
                    blit_text(surface, name_font, fit_text(name_font, contact.name, row.w - 100), pal.text, midleft=(avatar.right + 10, row.centery - 7))
                    blit_text(surface, self.font(9), contact.number, pal.text_muted, midleft=(avatar.right + 10, row.centery + 9))
                    if contact.favorite:
                        pygame.draw.polygon(
                            surface,
                            pal.warning,
                            [
                                (row.right - 20, row.centery - 7),
                                (row.right - 16, row.centery + 1),
                                (row.right - 24, row.centery + 4),
                                (row.right - 20, row.centery + 8),
                                (row.right - 16, row.centery + 4),
                            ],
                        )
                    self._rows.append((0, row, contact.contact_id, contact.favorite))
                y += row_h + 5
        surface.set_clip(None)
        draw_scrollbar(surface, area, self._scroll.offset, content_h, pal)

    def _draw_editor(self, surface, pal) -> None:
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill((0, 0, 0, 165))
        surface.blit(scrim, (0, 0))
        rects = self._editor_rects()
        panel = pygame.Rect(rects["name"].x - 18, self.content.y, rects["name"].w + 36, min(414, self.content.h - 8))
        draw_rounded(surface, panel, fill=pal.surface_raised, stroke=pal.stroke, radius=22)
        blit_text(surface, self.font(16, True), "EDIT CONTACT" if self._selected else "NEW CONTACT", pal.text, topleft=(panel.x + 18, panel.y + 14))
        labels = {"name": "NAME", "number": "PHONE", "email": "EMAIL", "note": "NOTE"}
        for key in ("name", "number", "email", "note"):
            blit_text(surface, self.font(8, True), labels[key], pal.text_muted, topleft=(rects[key].x, rects[key].y - 14), tracking=1.2)
            self._fields[key].draw(surface, rects[key], self.font(11), pal, focused=self._focus == key, blink=self._blink)
        draw_button(
            surface,
            rects["favorite"],
            "FAVORITE" if self._draft_favorite else "ADD FAVORITE",
            self.font(8, True),
            pal,
            fill=pal.accent_soft if self._draft_favorite else pal.surface,
            color=pal.accent_ink if self._draft_favorite else pal.text_muted,
            radius=10,
        )
        draw_button(surface, rects["save"], "SAVE", self.font(11, True), pal, fill=pal.accent, color=pal.on_accent, stroke=None)
        draw_button(
            surface,
            rects["delete"],
            "DELETE" if self._selected else "CANCEL",
            self.font(11, True),
            pal,
            fill=pal.surface_alt,
            color=pal.danger if self._selected else pal.text_muted,
        )
        draw_button(surface, rects["back"], "BACK", self.font(9, True), pal, fill=pal.surface, color=pal.text_muted)

    def _draw_delete(self, surface, pal) -> None:
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill((0, 0, 0, 175))
        surface.blit(scrim, (0, 0))
        width = min(340, self.content.w - 40)
        panel = pygame.Rect(self.content.centerx - width // 2, self.content.centery - 70, width, 150)
        draw_rounded(surface, panel, fill=pal.surface_raised, stroke=pal.stroke, radius=20)
        blit_text(surface, self.font(14, True), "Delete contact?", pal.text, center=(panel.centerx, panel.y + 34))
        blit_text(surface, self.font(9), "The conversation thread will also be removed.", pal.text_muted, center=(panel.centerx, panel.y + 54))
        yes, no = self._delete_buttons()
        draw_button(surface, yes, "DELETE", self.font(10, True), pal, fill=pal.danger, color=pal.on_accent, stroke=None)
        draw_button(surface, no, "CANCEL", self.font(10, True), pal, fill=pal.surface_alt)

    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)


__all__ = ["ContactsApp", "draw_icon"]
