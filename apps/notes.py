"""Notes: list/search/create/edit/delete with persisted storage.

Editing is a small custom text control: caret, arrows (including up/down across
wrapped lines), Home/End, backspace, delete and Enter for a new line. Autosave
is debounced to 0.35 s and flushed on pause/close.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

import pygame

from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.paint import blit_text, draw_rounded, fit_text

APP_ID = "notes"
APP_NAME = "Notes"
STORE_FILE = "notes.json"
AUTOSAVE_DELAY = 0.35


@dataclass(slots=True)
class Note:
    note_id: int
    body: str
    updated: float


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=max(8, rect.w // 4))
    inset = rect.inflate(-int(rect.w * 0.44), -int(rect.h * 0.4))
    line_color = pal.accent
    for index in range(4):
        y = inset.y + index * max(4, inset.h // 3)
        width = inset.w if index < 3 else int(inset.w * 0.7)
        pygame.draw.rect(
            surface,
            line_color if index == 0 else pal.text_muted,
            pygame.Rect(inset.x, y, width, max(2, rect.h // 30)),
            border_radius=1,
        )


class NotesApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._notes: list[Note] = []
        self._next_id = 1
        self._mode = "list"
        self._search = ""
        self._search_caret = 0
        self._selected = 0
        self._list_scroll = 0
        self._editor_id: int | None = None
        self._caret = 0
        self._scroll_line = 0
        self._dirty_timer = 0.0
        self._delete_rects: list[tuple[int, pygame.Rect]] = []
        self._row_rects: list[tuple[int, pygame.Rect]] = []
        self._caret_clock = 0.0
        self._wrap_key: tuple[object, ...] | None = None
        self._wrap_cache: list[tuple[str, int, int]] = []

    # -- lifecycle ------------------------------------------------------------
    def on_launch(self) -> None:
        existed = self.ctx.storage.exists(STORE_FILE)
        self._load()
        if not existed and not self._notes:
            welcome = Note(
                note_id=self._next_id,
                body=(
                    "Welcome to Notes\n"
                    "Create a note with + New, type with the keyboard, and press Done "
                    "to save. Search filters as you type."
                ),
                updated=time.time(),
            )
            self._next_id += 1
            self._notes.append(welcome)
            self._save()

    def on_pause(self) -> None:
        self._flush()

    def on_close(self) -> None:
        self._flush()

    def update(self, dt: float) -> None:
        super().update(dt)
        self._caret_clock = (self._caret_clock + dt) % 1.06
        if self._dirty_timer > 0.0:
            self._dirty_timer = max(0.0, self._dirty_timer - dt)
            if self._dirty_timer == 0.0:
                self._save()

    # -- persistence ----------------------------------------------------------
    def _load(self) -> None:
        payload = self.ctx.storage.read_json(STORE_FILE, {"next_id": 1, "notes": []})
        notes: list[Note] = []
        raw_notes = payload.get("notes", []) if isinstance(payload, dict) else []
        if isinstance(raw_notes, list):
            for item in raw_notes:
                if not isinstance(item, dict):
                    continue
                try:
                    note_id = int(item.get("id", 0))
                    body = str(item.get("body", ""))
                    updated = float(item.get("updated", time.time()))
                except (TypeError, ValueError):
                    continue
                if note_id > 0:
                    notes.append(Note(note_id, body, updated))
        notes.sort(key=lambda note: note.updated, reverse=True)
        self._notes = notes
        highest = max((note.note_id for note in notes), default=0)
        stored_next = payload.get("next_id", 1) if isinstance(payload, dict) else 1
        try:
            self._next_id = max(highest + 1, int(stored_next))
        except (TypeError, ValueError):
            self._next_id = highest + 1

    def _save(self) -> None:
        self._dirty_timer = 0.0
        payload = {
            "next_id": self._next_id,
            "notes": [
                {"id": note.note_id, "body": note.body, "updated": note.updated}
                for note in self._notes
            ],
        }
        self.ctx.storage.write_json(STORE_FILE, payload)

    def _flush(self) -> None:
        if self._dirty_timer > 0.0:
            self._save()

    def _touch(self) -> None:
        self._dirty_timer = AUTOSAVE_DELAY

    # -- data -----------------------------------------------------------------
    def _note_by_id(self, note_id: int | None) -> Note | None:
        if note_id is None:
            return None
        for note in self._notes:
            if note.note_id == note_id:
                return note
        return None

    def _filtered(self) -> list[Note]:
        query = self._search.strip().casefold()
        notes = sorted(self._notes, key=lambda note: note.updated, reverse=True)
        if not query:
            return notes
        return [note for note in notes if query in note.body.casefold()]

    @staticmethod
    def _title(note: Note) -> str:
        for line in note.body.splitlines():
            text = line.strip()
            if text:
                return text[:48]
        return "Untitled"

    @staticmethod
    def _preview(note: Note) -> str:
        lines = [line.strip() for line in note.body.splitlines() if line.strip()]
        if len(lines) <= 1:
            return "No additional text"
        return " ".join(lines[1:])[:120]

    @staticmethod
    def _timestamp(note: Note) -> str:
        try:
            moment = datetime.fromtimestamp(note.updated)
        except (OverflowError, OSError, ValueError):
            return "--"
        if moment.date() == datetime.now().date():
            return moment.strftime("%H:%M")
        return moment.strftime("%b %d")

    # -- navigation -----------------------------------------------------------
    def _new_note(self) -> None:
        now = time.time()
        note = Note(self._next_id, "", now)
        self._next_id += 1
        self._notes.append(note)
        self._touch()
        self._open_editor(note)

    def _open_editor(self, note: Note) -> None:
        self._mode = "editor"
        self._editor_id = note.note_id
        self._caret = len(note.body)
        self._scroll_line = 0
        self._ensure_caret_visible()

    def _close_editor(self) -> None:
        note = self._note_by_id(self._editor_id)
        if note is not None and not note.body.strip():
            self._notes = [item for item in self._notes if item.note_id != note.note_id]
        self._save()
        self._mode = "list"
        self._editor_id = None
        self._selected = min(self._selected, max(0, len(self._filtered()) - 1))

    def _delete_note(self, note_id: int) -> None:
        note = self._note_by_id(note_id)
        if note is None:
            return
        index = self._notes.index(note)
        self._notes = [item for item in self._notes if item.note_id != note_id]
        self._save()
        if self._editor_id == note_id:
            self._mode = "list"
            self._editor_id = None
        self._selected = min(self._selected, max(0, len(self._filtered()) - 1))

        def undo() -> None:
            self._notes.insert(min(index, len(self._notes)), note)
            self._save()

        self.ctx.undo_toast("Note deleted", undo)

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if self._mode == "editor":
            return self._handle_editor(event)
        return self._handle_list(event)

    def _handle_list(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.POINTER_DOWN:
            return True
        if event.kind is EventKind.TAP:
            if self._new_rect().collidepoint(event.pos):
                self._new_note()
                return True
            search_rect = self._search_rect()
            if self._search and pygame.Rect(search_rect.right - 38, search_rect.y + 8, 30, 34).collidepoint(event.pos):
                self._search = ""
                self._search_caret = 0
                self._selected = 0
                return True
            if search_rect.collidepoint(event.pos):
                self._search_caret = len(self._search)
                return True
            for note_id, rect in self._delete_rects:
                if rect.collidepoint(event.pos):
                    self._delete_note(note_id)
                    return True
            for note_id, rect in self._row_rects:
                if rect.collidepoint(event.pos):
                    note = self._note_by_id(note_id)
                    if note is not None:
                        self._open_editor(note)
                    return True
            return False
        if event.kind is not EventKind.KEY_DOWN:
            return False

        filtered = self._filtered()
        if event.key == pygame.K_BACKSPACE:
            if self._search:
                self._search = self._search[: max(0, self._search_caret - 1)] + self._search[self._search_caret :]
                self._search_caret = max(0, self._search_caret - 1)
            return True
        if event.key == pygame.K_LEFT:
            self._search_caret = max(0, self._search_caret - 1)
            return True
        if event.key == pygame.K_RIGHT:
            self._search_caret = min(len(self._search), self._search_caret + 1)
            return True
        if event.key == pygame.K_UP:
            self._selected = max(0, self._selected - 1)
            self._ensure_selection_visible()
            return True
        if event.key == pygame.K_DOWN:
            self._selected = min(max(0, len(filtered) - 1), self._selected + 1)
            self._ensure_selection_visible()
            return True
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if filtered:
                self._open_editor(filtered[min(self._selected, len(filtered) - 1)])
            return True
        if event.key == pygame.K_DELETE:
            if filtered:
                self._delete_note(filtered[min(self._selected, len(filtered) - 1)].note_id)
            return True

        text = event.text or ""
        for character in text:
            if character.isprintable() and character not in "\t\r\n":
                self._search = (
                    self._search[: self._search_caret] + character + self._search[self._search_caret :]
                )
                self._search_caret += 1
        self._selected = 0
        self._list_scroll = 0
        return True

    def clipboard_copy(self) -> bool:
        note = self._note_by_id(self._editor_id)
        if note is None or not self.ctx.clipboard:
            return False
        return self.ctx.clipboard.copy(note.body)

    def clipboard_paste(self) -> bool:
        note = self._note_by_id(self._editor_id)
        text = self.ctx.clipboard.paste() if self.ctx.clipboard else ""
        if note is None or not text:
            return False
        note.body = note.body[: self._caret] + text + note.body[self._caret :]
        self._caret += len(text)
        self._changed(note)
        return True

    def open_payload(self, payload: dict) -> None:
        kind = str(payload.get("type", ""))
        if kind == "note":
            note = self._note_by_id(payload.get("note_id"))
            if note is not None:
                self._open_editor(note)
            return
        if kind in ("text", "note_text"):
            body = str(payload.get("text", payload.get("query", ""))).strip()
            if body:
                note = Note(self._next_id, body, time.time())
                self._next_id += 1
                self._notes.insert(0, note)
                self._save()
                self._open_editor(note)

    def _handle_editor(self, event: PointerEvent) -> bool:
        note = self._note_by_id(self._editor_id)
        if note is None:
            self._mode = "list"
            return True

        if event.kind is EventKind.POINTER_DOWN:
            return True
        if event.kind is EventKind.TAP:
            if self._done_rect().collidepoint(event.pos):
                self._close_editor()
                return True
            if self._delete_button_rect().collidepoint(event.pos):
                self._delete_note(note.note_id)
                return True
            if self._share_button_rect().collidepoint(event.pos):
                self.ctx.share({"type": "text", "title": "Note", "text": note.body})
                return True
            if self._body_rect().collidepoint(event.pos):
                self._caret = self._caret_from_pos(note.body, event.pos)
                self._ensure_caret_visible()
                return True
            return False
        if event.kind is not EventKind.KEY_DOWN:
            return False

        if event.key == pygame.K_BACKSPACE:
            if self._caret > 0:
                note.body = note.body[: self._caret - 1] + note.body[self._caret :]
                self._caret -= 1
                self._changed(note)
            return True
        if event.key == pygame.K_DELETE:
            if self._caret < len(note.body):
                note.body = note.body[: self._caret] + note.body[self._caret + 1 :]
                self._changed(note)
            return True
        if event.key == pygame.K_LEFT:
            self._caret = max(0, self._caret - 1)
            self._ensure_caret_visible()
            return True
        if event.key == pygame.K_RIGHT:
            self._caret = min(len(note.body), self._caret + 1)
            self._ensure_caret_visible()
            return True
        if event.key == pygame.K_UP:
            self._move_vertical(note, -1)
            return True
        if event.key == pygame.K_DOWN:
            self._move_vertical(note, 1)
            return True
        if event.key == pygame.K_HOME:
            self._caret = self._line_range(note)[1]
            self._ensure_caret_visible()
            return True
        if event.key == pygame.K_END:
            self._caret = self._line_range(note)[2]
            self._ensure_caret_visible()
            return True
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            note.body = note.body[: self._caret] + "\n" + note.body[self._caret :]
            self._caret += 1
            self._changed(note)
            return True

        text = event.text or ""
        for character in text:
            if character.isprintable() and character not in "\t\r":
                note.body = note.body[: self._caret] + character + note.body[self._caret :]
                self._caret += 1
                self._changed(note)
        return True

    def _changed(self, note: Note) -> None:
        note.updated = time.time()
        self._touch()
        self._ensure_caret_visible()

    # -- caret helpers --------------------------------------------------------
    def _body_font(self) -> pygame.font.Font:
        return self.font(18)

    def _body_rect(self) -> pygame.Rect:
        margin = max(14, self.content.x + 18)
        top = self.content.y + 76
        return pygame.Rect(
            margin,
            top,
            self.content.w - margin * 2,
            max(80, self.content.bottom - 38 - top),
        )

    def _body_width(self) -> int:
        return self._body_rect().w - 16

    def _wrap_ranges(self, text: str) -> list[tuple[str, int, int]]:
        key = (text, round(self.theme.font_scale, 3), self._body_width())
        if self._wrap_key == key:
            return self._wrap_cache
        font = self._body_font()
        width = self._body_width()
        lines: list[tuple[str, int, int]] = []
        total = len(text)
        start = 0
        while True:
            newline = text.find("\n", start)
            paragraph_end = total if newline == -1 else newline
            line_start = start
            index = start
            last_space = -1
            while index < paragraph_end:
                if text[index] == " ":
                    last_space = index
                if font.size(text[line_start : index + 1])[0] > width and index > line_start:
                    break_at = last_space if last_space > line_start else index
                    lines.append((text[line_start:break_at], line_start, break_at))
                    line_start = (
                        break_at + 1
                        if break_at < paragraph_end and text[break_at] == " "
                        else break_at
                    )
                    last_space = -1
                    index = line_start
                    continue
                index += 1
            lines.append((text[line_start:paragraph_end], line_start, paragraph_end))
            if newline == -1:
                break
            start = newline + 1
        self._wrap_key = key
        self._wrap_cache = lines
        return lines

    def _line_range(self, note: Note) -> tuple[int, int, int]:
        lines = self._wrap_ranges(note.body)
        for line_index, (_text, start, end) in enumerate(lines):
            if start <= self._caret <= end:
                return (line_index, start, end)
        return (max(0, len(lines) - 1), len(note.body), len(note.body))

    def _move_vertical(self, note: Note, direction: int) -> None:
        lines = self._wrap_ranges(note.body)
        line_index, start, _end = self._line_range(note)
        target = line_index + direction
        if target < 0 or target >= len(lines):
            return
        column = self._caret - start
        target_start, target_end = lines[target][1], lines[target][2]
        self._caret = min(target_end, target_start + column)
        self._ensure_caret_visible()

    def _visible_lines(self) -> int:
        return max(1, (self._body_rect().h - 16) // 27)

    def _ensure_caret_visible(self) -> None:
        note = self._note_by_id(self._editor_id)
        if note is None:
            return
        line_index, _start, _end = self._line_range(note)
        visible = self._visible_lines()
        if line_index < self._scroll_line:
            self._scroll_line = line_index
        elif line_index >= self._scroll_line + visible:
            self._scroll_line = line_index - visible + 1
        self._scroll_line = max(0, self._scroll_line)

    def _caret_from_pos(self, text: str, pos: tuple[float, float]) -> int:
        rect = self._body_rect()
        lines = self._wrap_ranges(text)
        line_height = 27
        line_index = max(0, min(len(lines) - 1, self._scroll_line + (int(pos[1]) - rect.y - 10) // line_height))
        _line, start, end = lines[line_index]
        font = self._body_font()
        best = start
        for index in range(start, end + 1):
            width = font.size(text[start:index])[0]
            if rect.x + 8 + width >= pos[0]:
                best = index
                break
            best = index
        return best

    def _ensure_selection_visible(self) -> None:
        top = self.content.y + 100
        visible = max(1, (self.content.bottom - 34 - top) // 94)
        if self._selected < self._list_scroll:
            self._list_scroll = self._selected
        elif self._selected >= self._list_scroll + visible:
            self._list_scroll = self._selected - visible + 1
        self._list_scroll = max(0, self._list_scroll)

    # -- rects ----------------------------------------------------------------
    def _search_rect(self) -> pygame.Rect:
        margin = max(14, self.content.x + 18)
        new_width = 96
        return pygame.Rect(margin, self.content.y + 22, self.content.w - margin * 2 - new_width - 12, 46)

    def _new_rect(self) -> pygame.Rect:
        search = self._search_rect()
        return pygame.Rect(search.right + 12, search.y, 96, 46)

    def _done_rect(self) -> pygame.Rect:
        return pygame.Rect(20, self.content.y + 18, 88, 42)

    def _delete_button_rect(self) -> pygame.Rect:
        return pygame.Rect(self.content.w - 108, self.content.y + 18, 88, 42)

    def _share_button_rect(self) -> pygame.Rect:
        return pygame.Rect(self.content.centerx - 44, self.content.y + 18, 88, 42)

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        surface.fill(self.palette.bg_bottom)
        if self._mode == "editor":
            self._draw_editor(surface)
        else:
            self._draw_list(surface)

    def _draw_list(self, surface: pygame.Surface) -> None:
        pal = self.palette
        search = self._search_rect()
        draw_rounded(surface, search, fill=pal.surface, stroke=pal.stroke_soft, radius=16)
        search_font = self.font(15)
        blit_text(
            surface,
            search_font,
            fit_text(search_font, self._search or "Search notes", search.w - 44),
            pal.text if self._search else pal.text_muted,
            midleft=(search.x + 18, search.centery),
        )
        if self._search:
            clear = pygame.Rect(search.right - 34, search.centery - 12, 24, 24)
            pygame.draw.line(surface, pal.text_muted, (clear.x + 7, clear.y + 7), (clear.right - 7, clear.bottom - 7), 2)
            pygame.draw.line(surface, pal.text_muted, (clear.right - 7, clear.y + 7), (clear.x + 7, clear.bottom - 7), 2)
        if self._caret_clock < 0.55:
            if self._search:
                prefix = self._search[: min(self._search_caret, len(self._search))]
                caret_x = search.x + 18 + search_font.size(prefix)[0] + 1
                pygame.draw.line(surface, pal.accent, (caret_x, search.y + 14), (caret_x, search.bottom - 14), 2)
            else:
                caret_x = search.x + 18 + search_font.size("Search notes")[0] + 1
                pygame.draw.line(surface, pal.text_muted, (caret_x, search.y + 14), (caret_x, search.bottom - 14), 1)

        new_rect = self._new_rect()
        draw_rounded(surface, new_rect, fill=pal.accent, radius=16)
        blit_text(surface, self.font(14, True), "+ New", pal.on_accent, center=new_rect.center)

        filtered = self._filtered()
        self._row_rects = []
        self._delete_rects = []
        margin = 20
        top = self.content.y + 100
        height = 84
        pitch = 94
        visible = max(1, (self.content.bottom - 34 - top) // pitch)

        if not filtered:
            blit_text(
                surface,
                self.font(16),
                "No notes found" if self._search else "No notes yet",
                pal.text_muted,
                center=(self.content.centerx, self.content.y + 210),
            )
            blit_text(
                surface,
                self.font(12),
                "Tap + New to write one.",
                pal.text_muted,
                center=(self.content.centerx, self.content.y + 242),
            )
            return

        self._selected = min(self._selected, len(filtered) - 1)
        self._list_scroll = min(self._list_scroll, max(0, len(filtered) - visible))
        for offset, index in enumerate(range(self._list_scroll, min(len(filtered), self._list_scroll + visible))):
            note = filtered[index]
            rect = pygame.Rect(margin, top + offset * pitch, self.content.w - margin * 2, height)
            self._row_rects.append((note.note_id, rect))
            selected = index == self._selected
            draw_rounded(
                surface,
                rect,
                fill=pal.surface_raised if selected else pal.surface,
                stroke=pal.accent if selected else pal.stroke_soft,
                width=2 if selected else 1,
                radius=18,
            )
            title_font = self.font(17, True)
            blit_text(
                surface,
                title_font,
                fit_text(title_font, self._title(note), rect.w - 170),
                pal.text,
                topleft=(rect.x + 18, rect.y + 14),
            )
            preview_font = self.font(12)
            blit_text(
                surface,
                preview_font,
                fit_text(preview_font, self._preview(note), rect.w - 154),
                pal.text_muted,
                topleft=(rect.x + 18, rect.y + 42),
            )
            blit_text(
                surface,
                self.font(11),
                self._timestamp(note),
                pal.text_muted,
                midright=(rect.right - 18, rect.y + 24),
            )

            delete = pygame.Rect(rect.right - 48, rect.bottom - 38, 34, 26)
            self._delete_rects.append((note.note_id, delete))
            draw_rounded(surface, delete, fill=pal.surface_alt, stroke=pal.danger, radius=9)
            pygame.draw.line(surface, pal.danger, (delete.x + 12, delete.centery - 5), (delete.right - 12, delete.centery + 5), 2)
            pygame.draw.line(surface, pal.danger, (delete.right - 12, delete.centery - 5), (delete.x + 12, delete.centery + 5), 2)

        blit_text(
            surface,
            self.font(11),
            f"{len(filtered)} note{'s' if len(filtered) != 1 else ''} \u00b7 \u2191\u2193 select \u00b7 Enter open",
            pal.text_muted,
            center=(self.content.centerx, self.content.bottom - 18),
        )

    def _draw_editor(self, surface: pygame.Surface) -> None:
        pal = self.palette
        note = self._note_by_id(self._editor_id)
        if note is None:
            return

        done = self._done_rect()
        draw_rounded(surface, done, fill=pal.surface_alt, stroke=pal.stroke_soft, radius=14)
        blit_text(surface, self.font(13, True), "Done", pal.text, center=done.center)

        share = self._share_button_rect()
        draw_rounded(surface, share, fill=pal.accent_soft, stroke=pal.accent, radius=14)
        blit_text(surface, self.font(13, True), "Share", pal.accent_ink, center=share.center)

        delete = self._delete_button_rect()
        draw_rounded(surface, delete, fill=pal.surface_alt, stroke=pal.danger, radius=14)
        blit_text(surface, self.font(13, True), "Delete", pal.danger, center=delete.center)

        blit_text(
            surface,
            self.font(11),
            f"{len(note.body)} characters",
            pal.text_muted,
            center=(self.content.centerx, self.content.y + 38),
            tracking=1.0,
        )

        body = self._body_rect()
        draw_rounded(surface, body, fill=pal.surface, stroke=pal.stroke_soft, radius=20)
        if not note.body:
            blit_text(
                surface,
                self.font(18),
                "Start typing\u2026",
                pal.text_muted,
                topleft=(body.x + 8, body.y + 10),
            )

        lines = self._wrap_ranges(note.body)
        visible = self._visible_lines()
        self._scroll_line = max(0, min(self._scroll_line, max(0, len(lines) - visible)))
        line_height = 27
        font = self._body_font()
        for offset, (text, start, _end) in enumerate(lines[self._scroll_line : self._scroll_line + visible]):
            y = body.y + 10 + offset * line_height
            blit_text(surface, font, text, pal.text, topleft=(body.x + 8, y))
            if self._caret_clock < 0.55:
                caret_line, _ls, _le = self._line_range(note)
                if self._scroll_line + offset == caret_line:
                    prefix_width = font.size(note.body[start : self._caret])[0] if start <= self._caret else 0
                    caret_x = min(body.right - 12, body.x + 8 + prefix_width)
                    pygame.draw.line(surface, pal.accent, (caret_x, y), (caret_x, y + line_height - 6), 2)

        if len(lines) > visible:
            track = pygame.Rect(body.right - 8, body.y + 10, 4, body.h - 20)
            pygame.draw.rect(surface, pal.track, track, border_radius=2)
            thumb_height = max(24, int(track.h * visible / len(lines)))
            progress = self._scroll_line / max(1, len(lines) - visible)
            thumb_y = track.y + int((track.h - thumb_height) * progress)
            pygame.draw.rect(surface, pal.accent, pygame.Rect(track.x, thumb_y, track.w, thumb_height), border_radius=2)

        blit_text(
            surface,
            self.font(11),
            "Enter newline \u00b7 arrows move the caret \u00b7 autosaves",
            pal.text_muted,
            center=(self.content.centerx, self.content.bottom - 14),
        )

    # -- icon -----------------------------------------------------------------
    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)
