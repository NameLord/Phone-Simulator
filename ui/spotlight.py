"""Global search / Spotlight across apps, settings and user data."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pygame

from core.input import EventKind, PointerEvent
from core.scene import Scene, ease_out_cubic
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, fit_text, make_glow, vertical_gradient

LOG = logging.getLogger("phoneos.spotlight")
HISTORY_FILE = "search_history.json"
MAX_HISTORY = 10
ROW_HEIGHT = 56
HEADER_HEIGHT = 26

GROUPS = ("Apps", "Settings", "Contacts", "Notes", "Calendar", "Files")


@dataclass(slots=True)
class SearchHit:
    source: str
    title: str
    subtitle: str
    action: tuple
    score: int = 0


def _fuzzy_score(query: str, text: str) -> int:
    query = query.lower().strip()
    text = text.lower()
    if not query:
        return 0
    if text.startswith(query):
        return 100
    if query in text:
        return 80 - min(20, text.index(query))
    position = 0
    matched = 0
    for character in text:
        if position < len(query) and character == query[position]:
            position += 1
            matched += 1
    if matched == len(query):
        return 40
    return 0


class SpotlightScene(Scene):
    name = "search"
    shows_status_bar = False

    def __init__(self, kernel) -> None:
        super().__init__(kernel)
        self.query = ""
        self.selected = 0
        self._results: list[SearchHit | None] = []
        self._row_rects: list[tuple[SearchHit, pygame.Rect]] = []
        self._history_rects: list[tuple[str, pygame.Rect]] = []
        self._clear_rect = pygame.Rect(0, 0, 0, 0)
        self._history = self._read_history()
        self._theme_key = None
        self._background = vertical_gradient(self.size, self.theme.palette.bg_top, self.theme.palette.bg_bottom)
        self._glow = make_glow(220, self.theme.palette.accent, power=2.2)
        self._refresh()

    # -- history -------------------------------------------------------------
    def _load_history(self) -> list[str]:
        payload = self.storage.read_json(HISTORY_FILE, {}) if hasattr(self, "storage") else {}
        return []

    @property
    def storage(self):
        return self.registry.get("storage")

    def _read_history(self) -> list[str]:
        payload = self.storage.read_json(HISTORY_FILE, {}) if self.storage is not None else {}
        values = payload.get("recent", []) if isinstance(payload, dict) else []
        return [str(value) for value in values if str(value).strip()][:MAX_HISTORY]

    def _save_history(self, values: list[str]) -> None:
        self._history = values[:MAX_HISTORY]
        if self.storage is not None:
            self.storage.write_json(HISTORY_FILE, {"recent": self._history})

    def _remember(self, query: str) -> None:
        value = " ".join(str(query).split())
        if not value:
            return
        values = [value] + [item for item in self._history if item.lower() != value.lower()]
        self._save_history(values)

    # -- search --------------------------------------------------------------
    def _refresh(self) -> None:
        if not self.query.strip():
            self._results = []
            self._row_rects = []
            return
        query = self.query
        hits: list[SearchHit] = []
        manager = self.registry.require("app_manager")
        for manifest in manager.catalog():
            score = max(_fuzzy_score(query, manifest.name), _fuzzy_score(query, manifest.app_id))
            if score:
                hits.append(SearchHit("Apps", manifest.name, manifest.description, ("app", manifest.app_id), score))
        try:
            from ui.settings_app import SEARCH_INDEX

            seen = set()
            for page_key, label, keywords in SEARCH_INDEX:
                score = max(_fuzzy_score(query, label), _fuzzy_score(query, keywords))
                if score and (page_key, label) not in seen:
                    seen.add((page_key, label))
                    hits.append(SearchHit("Settings", label, page_key.title(), ("settings", page_key), score))
        except Exception:  # noqa: BLE001
            LOG.exception("settings index unavailable")

        telephony = self.registry.get("telephony")
        if telephony is not None:
            for contact in telephony.contacts:
                score = max(_fuzzy_score(query, contact.name), _fuzzy_score(query, contact.number))
                if score:
                    hits.append(SearchHit("Contacts", contact.name, contact.number, ("contact", contact.contact_id), score))

        storage = self.storage
        if storage is not None:
            notes_payload = storage.read_json("notes.json", {"notes": []})
            for note in notes_payload.get("notes", []) if isinstance(notes_payload, dict) else []:
                body = str(note.get("body", ""))
                score = _fuzzy_score(query, body)
                if score:
                    title = body.strip().splitlines()[0] if body.strip() else "Untitled note"
                    hits.append(SearchHit("Notes", title[:42], "Note", ("note", int(note.get("id", 0))), score))
            calendar_payload = storage.read_json("calendar.json", {"events": []})
            for event in calendar_payload.get("events", []) if isinstance(calendar_payload, dict) else []:
                title = str(event.get("title", ""))
                score = _fuzzy_score(query, title)
                if score:
                    hits.append(SearchHit("Calendar", title, str(event.get("date", "")), ("calendar", title), score))
            try:
                fs_root = storage.fs_root
                for path in fs_root.rglob("*"):
                    if not path.is_file():
                        continue
                    relative = str(path.relative_to(fs_root)).replace("\\", "/")
                    score = _fuzzy_score(query, relative)
                    if score:
                        hits.append(SearchHit("Files", path.name, relative, ("file", relative), score))
            except OSError:
                pass

        hits.sort(key=lambda hit: (-hit.score, hit.source, hit.title))
        self._results = []
        for group in GROUPS:
            group_hits = [hit for hit in hits if hit.source == group][:4]
            if not group_hits:
                continue
            self._results.append(None)
            self._results.extend(group_hits)
        self.selected = 0
        self._ensure_selection()

    def _selectable(self, direction: int) -> None:
        if not self._results:
            return
        index = self.selected
        for _ in range(len(self._results) * 2):
            index = (index + direction) % len(self._results)
            if self._results[index] is not None:
                self.selected = index
                return

    def _ensure_selection(self) -> None:
        if not self._results:
            self.selected = 0
            return
        if self.selected >= len(self._results) or self._results[self.selected] is None:
            self.selected = next((i for i, hit in enumerate(self._results) if hit is not None), 0)

    # -- input ---------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self._close()
            elif event.key == pygame.K_UP:
                self._selectable(-1)
            elif event.key == pygame.K_DOWN:
                self._selectable(1)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._activate_selected()
            elif event.key == pygame.K_BACKSPACE:
                self.query = self.query[:-1]
                self._refresh()
            elif event.text and event.text.isprintable():
                self.query += event.text
                if len(self.query) > 48:
                    self.query = self.query[:48]
                self._refresh()
            return True
        if event.kind is EventKind.TAP:
            if self._clear_rect.collidepoint(event.pos):
                self._save_history([])
                return True
            for value, rect in self._history_rects:
                if rect.collidepoint(event.pos):
                    self.query = value
                    self._refresh()
                    return True
            for hit, rect in self._row_rects:
                if rect.collidepoint(event.pos):
                    self._remember(self.query)
                    self._run(hit)
                    return True
            if event.pos[1] > self.size[1] * 0.82:
                self._close()
            return True
        return True

    def _activate_selected(self) -> None:
        if 0 <= self.selected < len(self._results):
            hit = self._results[self.selected]
            if hit is not None:
                self._remember(self.query)
                self._run(hit)

    def _run(self, hit: SearchHit) -> None:
        kind = hit.action[0]
        manager = self.registry.require("app_manager")
        if kind == "app":
            if manager.launch(hit.action[1]) is not None:
                self.go("app", fade=0.14)
            return
        if kind == "settings":
            self.kernel._pending_settings_page = hit.action[1]
            self.go("settings", fade=0.16)
            settings_scene = self.kernel._current
            if settings_scene is not None and hasattr(settings_scene, "highlight"):
                pass
            self.kernel._pending_settings_highlight = self.query
            return
        app_id = {"contact": "phone", "note": "notes", "calendar": "calendar", "file": "files"}.get(kind)
        if app_id is None:
            return
        payload = {"type": kind, "query": self.query}
        if kind == "contact":
            payload["contact_id"] = hit.action[1]
        elif kind == "note":
            payload["note_id"] = hit.action[1]
        elif kind == "calendar":
            payload["title"] = hit.action[1]
        elif kind == "file":
            payload["path"] = hit.action[1]
        if manager.launch(app_id, payload=payload) is not None:
            self.go("app", fade=0.14)

    def _close(self) -> None:
        target = getattr(self.kernel, "_spotlight_return", "home") or "home"
        self.go(target if target != "search" else "home", fade=0.12)

    # -- draw ----------------------------------------------------------------
    def _ensure_theme(self):
        key = self.theme.signature
        if self._theme_key != key:
            self._theme_key = key
            pal = self.theme.palette
            self._background = vertical_gradient(self.size, pal.bg_top, pal.bg_bottom)
            self._glow = make_glow(220, pal.accent, power=2.2)
        return self.theme.palette

    def draw(self, surface: pygame.Surface) -> None:
        pal = self._ensure_theme()
        surface.blit(self._background, (0, 0))
        self._glow.set_alpha(48)
        surface.blit(self._glow, self._glow.get_rect(center=(current_layout.center_x, self.size[1] // 3)))
        width = min(self.size[0] - 36, 560)
        field = pygame.Rect((self.size[0] - width) // 2, 54, width, 62)
        draw_rounded(surface, field, fill=pal.surface_raised, stroke=pal.accent, radius=22, alpha=245)
        pygame.draw.circle(surface, pal.text_muted, (field.x + 30, field.centery), 9, 2)
        pygame.draw.line(surface, pal.text_muted, (field.x + 36, field.centery + 6), (field.x + 44, field.centery + 14), 2)
        text_font = self.font(18)
        label = self.query or "Search apps, settings, contacts, notes, files"
        blit_text(surface, text_font, fit_text(text_font, label, field.w - 100), pal.text if self.query else pal.text_muted, midleft=(field.x + 52, field.centery))
        if self.query:
            pygame.draw.line(surface, pal.accent, (field.x + 54, field.y + 12), (field.x + 54 + text_font.size(self.query)[0] + 2, field.y + 12), 2)

        self._clear_rect = pygame.Rect(0, 0, 0, 0)
        if not self.query:
            blit_text(surface, self.font(11), "RECENT SEARCHES", pal.text_muted, topleft=(field.x + 4, field.bottom + 24), tracking=2)
            self._clear_rect = pygame.Rect(field.right - 54, field.bottom + 18, 54, 24)
            if self._history:
                blit_text(surface, self.font(10, True), "CLEAR", pal.accent_ink, center=self._clear_rect.center)
            y = field.bottom + 54
            self._history_rects = []
            for value in self._history[:10]:
                row = pygame.Rect(field.x, y, field.w, 42)
                draw_rounded(surface, row, fill=pal.surface, radius=14, alpha=210)
                blit_text(surface, self.font(13), value, pal.text_secondary, midleft=(row.x + 16, row.centery))
                self._history_rects.append((value, row))
                y += 46
            if not self._history:
                blit_text(surface, self.font(13), "No recent searches yet", pal.text_muted, center=(field.centerx, field.bottom + 70))
            blit_text(surface, self.font(10), "Esc closes  \u00b7  / opens search", pal.text_muted, center=(field.centerx, self.size[1] - 26))
            return

        self._row_rects = []
        self._history_rects = []
        y = field.bottom + 18
        if not self._results:
            blit_text(surface, self.font(14), "No results", pal.text_muted, center=(field.centerx, y + 60))
        for index, hit in enumerate(self._results):
            if hit is None:
                group = next((self._results[j] for j in range(index + 1, len(self._results)) if self._results[j] is not None), None)
                if y + HEADER_HEIGHT > self.size[1] - 20:
                    break
                blit_text(surface, self.font(10, True), group.source.upper() if group else "", pal.text_muted, topleft=(field.x + 8, y + 6), tracking=2)
                y += HEADER_HEIGHT
                continue
            if y + ROW_HEIGHT > self.size[1] - 20:
                break
            row = pygame.Rect(field.x, y, field.w, ROW_HEIGHT - 6)
            selected = index == self.selected
            draw_rounded(surface, row, fill=pal.accent_soft if selected else pal.surface, stroke=pal.accent if selected else pal.stroke_soft, radius=16, alpha=245)
            icon = pygame.Rect(row.x + 12, row.y + 9, 34, 34)
            draw_rounded(surface, icon, fill=pal.accent if selected else pal.surface_alt, radius=10)
            blit_text(surface, self.font(14, True), hit.title[:1].upper(), pal.on_accent if selected else pal.accent_ink, center=icon.center)
            title_font = self.font(14, True)
            blit_text(surface, title_font, fit_text(title_font, hit.title, row.w - 130), pal.text, topleft=(icon.right + 12, row.y + 8))
            sub_font = self.font(10)
            blit_text(surface, sub_font, fit_text(sub_font, hit.subtitle, row.w - 130), pal.text_muted, topleft=(icon.right + 12, row.y + 30))
            self._row_rects.append((hit, row))
            y += ROW_HEIGHT
        blit_text(surface, self.font(10), "Up/Down select  \u00b7  Enter opens  \u00b7  Esc closes", pal.text_muted, center=(field.centerx, self.size[1] - 20))
