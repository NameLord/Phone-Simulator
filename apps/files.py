"""Files: a bounded virtual filesystem backed by ``data/fs/``.

Every mutation goes through :class:`services.storage.Storage`, which confines
paths to the sandbox and writes text files atomically. The UI exposes list and
tree views, create/rename/delete, size indicators and a simple text editor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pygame

from apps._kit import ScrollState, TextField, draw_button, draw_scrollbar
from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_chevron, draw_rounded, fit_text

APP_ID = "files"
APP_NAME = "Files"
QUOTA_BYTES = 64 * 1024 * 1024


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=max(8, rect.w // 4))
    folder = pygame.Rect(
        rect.x + int(rect.w * 0.18),
        rect.y + int(rect.h * 0.30),
        int(rect.w * 0.64),
        int(rect.h * 0.38),
    )
    tab = pygame.Rect(folder.x, folder.y - max(3, rect.h // 12), int(folder.w * 0.42), max(5, rect.h // 8))
    pygame.draw.rect(surface, pal.warning, tab, border_radius=3)
    pygame.draw.rect(surface, pal.warning, folder, border_radius=4)
    pygame.draw.line(
        surface,
        pal.on_accent,
        (folder.x + 8, folder.centery),
        (folder.right - 8, folder.centery),
        max(1, rect.h // 30),
    )


@dataclass(slots=True)
class Entry:
    name: str
    path: str
    is_dir: bool
    size: int
    modified: float
    depth: int = 0


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


class FilesApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._cwd = ""
        self._view = "list"
        self._entries: list[Entry] = []
        self._selected = 0
        self._scroll = ScrollState(step=46)
        self._row_rects: list[tuple[int, pygame.Rect]] = []
        self._expanded: set[str] = {""}
        self._modal: str | None = None  # new_folder / new_file / rename / confirm / editor
        self._name = TextField("", max_length=48, placeholder="Name")
        self._editor = TextField("", max_length=20000, multiline=True, placeholder="Empty file")
        self._editing_path = ""
        self._confirm_delete = ""
        self._blink = 0.0
        self._status = ""
        self._status_t = 0.0
        self._used = 0
        self._count = 0

    # -- lifecycle ------------------------------------------------------------
    def on_launch(self) -> None:
        self._seed()
        self._refresh()

    def on_pause(self) -> None:
        self._save_editor_if_needed()

    def on_close(self) -> None:
        self._save_editor_if_needed()

    def update(self, dt: float) -> None:
        super().update(dt)
        self._blink = (self._blink + dt) % 1.06
        if self._status_t > 0.0:
            self._status_t = max(0.0, self._status_t - dt)
            if self._status_t == 0.0:
                self._status = ""

    def on_resize(self) -> None:
        self._scroll.reset()

    # -- storage --------------------------------------------------------------
    def _seed(self) -> None:
        try:
            self.ctx.storage.fs_mkdir("")
            if not self.ctx.storage.fs_list(""):
                self.ctx.storage.fs_mkdir("Documents")
                self.ctx.storage.fs_mkdir("Photos")
                self.ctx.storage.fs_mkdir("Downloads")
                self.ctx.storage.fs_write_text(
                    "Documents/welcome.txt",
                    "Welcome to Files.\n\nEverything here lives in data/fs/.\n"
                    "Create, rename, edit and delete files. Writes are atomic.\n",
                )
                self.ctx.storage.fs_write_text(
                    "Downloads/storage-note.txt",
                    "This simulated phone has a 64 MB user quota. Keep an eye on the "
                    "usage bar at the bottom of the screen.\n",
                )
        except OSError:
            pass

    def _refresh(self) -> None:
        raw = self.ctx.storage.fs_list(self._cwd)
        self._entries = [
            Entry(
                name=str(item["name"]),
                path=str(item["path"]),
                is_dir=bool(item["dir"]),
                size=int(item["size"]),
                modified=float(item["modified"]),
            )
            for item in raw
        ]
        self._used, self._count = self.ctx.storage.fs_usage()
        self._selected = max(0, min(self._selected, len(self._entries) - 1))
        self._scroll.reset()

    def _tree_entries(self) -> list[Entry]:
        result: list[Entry] = []
        self._walk_tree("", 0, result)
        return result

    def _walk_tree(self, relative: str, depth: int, result: list[Entry]) -> None:
        for item in self.ctx.storage.fs_list(relative):
            entry = Entry(
                name=str(item["name"]),
                path=str(item["path"]),
                is_dir=bool(item["dir"]),
                size=int(item["size"]),
                modified=float(item["modified"]),
                depth=depth,
            )
            result.append(entry)
            if entry.is_dir and entry.path in self._expanded:
                self._walk_tree(entry.path, depth + 1, result)

    def _visible_entries(self) -> list[Entry]:
        return self._tree_entries() if self._view == "tree" else self._entries

    def _toast(self, text: str) -> None:
        self._status = text
        self._status_t = 1.6

    def _save_editor_if_needed(self) -> None:
        if self._modal == "editor" and self._editing_path:
            self.ctx.storage.fs_write_text(self._editing_path, self._editor.value)

    # -- geometry -------------------------------------------------------------
    def _path_bar(self) -> pygame.Rect:
        return pygame.Rect(
            current_layout.margin,
            self.content.y + 8,
            self.content.w - current_layout.margin * 2,
            34,
        )

    def _toolbar_rects(self) -> dict[str, pygame.Rect]:
        bar = self._path_bar()
        margin = current_layout.margin
        width = self.content.w - margin * 2
        labels = ("view", "folder", "file", "rename", "delete", "share")
        button_w = (width - 20) // 6
        return {
            key: pygame.Rect(margin + index * (button_w + 4), bar.bottom + 7, button_w, 32)
            for index, key in enumerate(labels)
        }

    def _list_area(self) -> pygame.Rect:
        toolbar = next(iter(self._toolbar_rects().values()))
        return pygame.Rect(
            current_layout.margin,
            toolbar.bottom + 8,
            self.content.w - current_layout.margin * 2,
            max(80, self.content.bottom - toolbar.bottom - 34),
        )

    def _row_height(self) -> int:
        return 44

    def _modal_panel(self) -> pygame.Rect:
        width = min(390, self.content.w - 36)
        if self._modal == "editor":
            return pygame.Rect(current_layout.margin, self.content.y + 8, self.content.w - current_layout.margin * 2, self.content.h - 16)
        height = 210 if self._modal != "confirm" else 150
        return pygame.Rect(self.content.centerx - width // 2, self.content.centery - height // 2, width, height)

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if self._modal is not None:
            return self._handle_modal(event)
        if event.kind is EventKind.WHEEL:
            return self._scroll.wheel(event)
        if event.kind is EventKind.POINTER_DOWN:
            self._scroll.pointer(event, self._list_area())
        if event.kind in (EventKind.DRAG, EventKind.DRAG_END, EventKind.POINTER_UP):
            self._scroll.pointer(event, self._list_area())
        if event.kind is EventKind.KEY_DOWN:
            entries = self._visible_entries()
            if event.key in (pygame.K_UP, pygame.K_DOWN) and entries:
                self._selected = max(0, min(len(entries) - 1, self._selected + (-1 if event.key == pygame.K_UP else 1)))
                self._ensure_selected_visible()
                return True
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._open_selected()
                return True
            if event.key == pygame.K_BACKSPACE:
                self._go_up()
                return True
            if self._scroll.key(event):
                return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        if self._back_rect().collidepoint(event.pos):
            self._go_up()
            return True
        toolbar = self._toolbar_rects()
        if toolbar["view"].collidepoint(event.pos):
            self._view = "tree" if self._view == "list" else "list"
            self._scroll.reset()
            self._selected = 0
            return True
        if toolbar["folder"].collidepoint(event.pos):
            self._name.clear()
            self._modal = "new_folder"
            return True
        if toolbar["file"].collidepoint(event.pos):
            self._name.clear()
            self._modal = "new_file"
            return True
        if toolbar["rename"].collidepoint(event.pos):
            entry = self._selected_entry()
            if entry is not None:
                self._name.set(entry.name)
                self._modal = "rename"
            return True
        if toolbar["delete"].collidepoint(event.pos):
            entry = self._selected_entry()
            if entry is not None:
                self._confirm_delete = entry.path
                self._modal = "confirm"
            return True
        if toolbar["share"].collidepoint(event.pos):
            entry = self._selected_entry()
            if entry is not None and not entry.is_dir:
                self.ctx.share({"type": "file", "title": entry.name, "path": entry.path, "text": entry.name})
            else:
                self._toast("Select a file to share")
            return True
        for index, rect in self._row_rects:
            if rect.collidepoint(event.pos):
                entries = self._visible_entries()
                if 0 <= index < len(entries):
                    self._selected = index
                    entry = entries[index]
                    if entry.is_dir:
                        if self._view == "tree":
                            if entry.path in self._expanded:
                                self._expanded.remove(entry.path)
                            else:
                                self._expanded.add(entry.path)
                        self._cwd = entry.path
                        self._refresh()
                    else:
                        self._open_file(entry)
                return True
        return False

    def _back_rect(self) -> pygame.Rect:
        bar = self._path_bar()
        return pygame.Rect(bar.x, bar.y + 2, 30, 30)

    def _selected_entry(self) -> Entry | None:
        entries = self._visible_entries()
        if not entries:
            return None
        return entries[max(0, min(self._selected, len(entries) - 1))]

    def _open_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if entry.is_dir:
            self._cwd = entry.path
            self._refresh()
        else:
            self._open_file(entry)

    def _go_up(self) -> None:
        if self._cwd:
            self._cwd = self._cwd.rsplit("/", 1)[0] if "/" in self._cwd else ""
            self._refresh()

    def _open_file(self, entry: Entry) -> None:
        try:
            text = self.ctx.storage.fs_read_text(entry.path)
        except (OSError, UnicodeDecodeError):
            self._toast("Cannot open this file")
            return
        self._editing_path = entry.path
        self._editor.set(text)
        self._modal = "editor"

    def _ensure_selected_visible(self) -> None:
        area = self._list_area()
        row_h = self._row_height()
        top = self._selected * row_h
        bottom = top + row_h
        if top < self._scroll.offset:
            self._scroll.offset = top
        elif bottom > self._scroll.offset + area.h:
            self._scroll.offset = bottom - area.h
        self._scroll.clamp()

    def _handle_modal(self, event: PointerEvent) -> bool:
        if self._modal == "confirm":
            if event.kind is EventKind.KEY_DOWN:
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self._delete_confirmed()
                elif event.key == pygame.K_ESCAPE:
                    self._modal = None
                return True
            if event.kind is not EventKind.TAP:
                return event.kind is EventKind.POINTER_DOWN
            yes, no = self._modal_buttons()
            if yes.collidepoint(event.pos):
                self._delete_confirmed()
            elif no.collidepoint(event.pos):
                self._modal = None
            return True
        if self._modal == "editor":
            if event.kind is EventKind.KEY_DOWN:
                if event.key == pygame.K_ESCAPE:
                    self._save_editor()
                elif event.key == pygame.K_s and (event.mods & pygame.KMOD_CTRL):
                    self._save_editor()
                else:
                    self._editor.handle(event)
                return True
            if event.kind is not EventKind.TAP:
                return event.kind is EventKind.POINTER_DOWN
            buttons = self._editor_buttons()
            if buttons["save"].collidepoint(event.pos):
                self._save_editor()
            elif buttons["back"].collidepoint(event.pos):
                self._save_editor()
            return True
        # new folder / new file / rename
        if event.kind is EventKind.KEY_DOWN:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._commit_name()
            elif event.key == pygame.K_ESCAPE:
                self._modal = None
            else:
                self._name.handle(event)
            return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        save, cancel = self._modal_buttons()
        if save.collidepoint(event.pos):
            self._commit_name()
        elif cancel.collidepoint(event.pos):
            self._modal = None
        return True

    def _modal_buttons(self) -> tuple[pygame.Rect, pygame.Rect]:
        panel = self._modal_panel()
        width = (panel.w - 42) // 2
        return (
            pygame.Rect(panel.x + 16, panel.bottom - 54, width, 38),
            pygame.Rect(panel.x + 26 + width, panel.bottom - 54, width, 38),
        )

    def _editor_buttons(self) -> dict[str, pygame.Rect]:
        panel = self._modal_panel()
        return {
            "save": pygame.Rect(panel.right - 150, panel.y + 8, 66, 34),
            "back": pygame.Rect(panel.right - 78, panel.y + 8, 66, 34),
        }

    def _commit_name(self) -> None:
        name = self._name.value.strip().replace("/", "").replace("\\", "")
        if not name or name in (".", ".."):
            self._toast("Enter a valid name")
            return
        base = self._cwd
        relative = f"{base}/{name}".strip("/") if base else name
        try:
            if self._modal == "new_folder":
                self.ctx.storage.fs_mkdir(relative)
                self._toast("Folder created")
            elif self._modal == "new_file":
                if not name.lower().endswith(".txt"):
                    name += ".txt"
                    relative = f"{base}/{name}".strip("/") if base else name
                self.ctx.storage.fs_write_text(relative, "")
                self._toast("File created")
            elif self._modal == "rename":
                entry = self._selected_entry()
                if entry is not None and not self.ctx.storage.fs_rename(entry.path, name):
                    self._toast("Rename failed")
                    return
                self._toast("Renamed")
        except OSError:
            self._toast("Operation failed")
            return
        self._modal = None
        self._refresh()

    def _delete_confirmed(self) -> None:
        path = self._confirm_delete
        snapshot = self.ctx.storage.fs_snapshot(path)
        ok = self.ctx.storage.fs_delete(path)
        self._modal = None
        self._confirm_delete = ""
        self._refresh()
        self._toast("Deleted" if ok else "Delete failed")
        if ok and snapshot is not None:
            self.ctx.undo_toast("File deleted", lambda: self._restore_snapshot(snapshot))

    def _restore_snapshot(self, snapshot: dict) -> None:
        if self.ctx.storage.fs_restore(snapshot):
            self._refresh()
            self._toast("Restored")

    def open_payload(self, payload: dict) -> None:
        path = str(payload.get("path", ""))
        if not path:
            return
        entry = Entry(name=path.rsplit("/", 1)[-1], path=path, is_dir=False, size=0, modified=0.0)
        self._open_file(entry)

    def clipboard_copy(self) -> bool:
        if self._modal != "editor" or not self.ctx.clipboard:
            return False
        return self.ctx.clipboard.copy(self._editor.value)

    def clipboard_paste(self) -> bool:
        if self._modal != "editor" or not self.ctx.clipboard:
            return False
        text = self.ctx.clipboard.paste()
        if not text:
            return False
        self._editor.set(self._editor.value + text)
        return True

    def _save_editor(self) -> None:
        if self._editing_path and self.ctx.storage.fs_write_text(self._editing_path, self._editor.value):
            self._toast("Saved")
        self._modal = None
        self._editing_path = ""
        self._refresh()

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        self._draw_path(surface, pal)
        self._draw_toolbar(surface, pal)
        self._draw_list(surface, pal)
        self._draw_usage(surface, pal)
        if self._status:
            blit_text(surface, self.font(10, True), self._status, pal.accent_ink, center=(self.content.centerx, self.content.bottom - 8))
        if self._modal is not None:
            self._draw_modal(surface, pal)

    def _draw_path(self, surface, pal) -> None:
        bar = self._path_bar()
        draw_rounded(surface, bar, fill=pal.surface, stroke=pal.stroke_soft, radius=10)
        back = self._back_rect()
        draw_chevron(surface, back.center, pal.text if self._cwd else pal.text_muted, size=5, direction="left")
        path = f"/{self._cwd}" if self._cwd else "/"
        blit_text(surface, self.font(11, True), fit_text(self.font(11, True), path, bar.w - 50), pal.text, midleft=(bar.x + 38, bar.centery))
        blit_text(
            surface,
            self.font(8),
            "TREE" if self._view == "tree" else "LIST",
            pal.text_muted,
            midright=(bar.right - 12, bar.centery),
            tracking=1.0,
        )

    def _draw_toolbar(self, surface, pal) -> None:
        labels = {
            "view": "TREE" if self._view == "list" else "LIST",
            "folder": "+ FOLDER",
            "file": "+ FILE",
            "rename": "RENAME",
            "delete": "DELETE",
            "share": "SHARE",
        }
        for key, rect in self._toolbar_rects().items():
            danger = key == "delete"
            draw_button(
                surface,
                rect,
                labels[key],
                self.font(8, True),
                pal,
                fill=pal.surface_alt if not danger else pal.surface,
                color=pal.danger if danger else pal.text_secondary,
                stroke=pal.stroke_soft,
                radius=8,
            )

    def _draw_list(self, surface, pal) -> None:
        area = self._list_area()
        entries = self._visible_entries()
        row_h = self._row_height()
        self._scroll.set_metrics(area.h, len(entries) * row_h)
        self._row_rects = []
        if not entries:
            blit_text(surface, self.font(11), "This folder is empty", pal.text_muted, center=(area.centerx, area.centery))
        for index, entry in enumerate(entries):
            y = area.y + index * row_h - int(self._scroll.offset)
            row = pygame.Rect(area.x, y, area.w, row_h - 3)
            if row.bottom < area.y or row.y > area.bottom:
                continue
            selected = index == self._selected
            if selected:
                draw_rounded(surface, row, fill=pal.accent_soft, stroke=pal.accent, radius=8)
            else:
                pygame.draw.line(surface, pal.stroke_soft, (row.x, row.bottom), (row.right, row.bottom), 1)
            indent = entry.depth * 16 if self._view == "tree" else 0
            icon = pygame.Rect(row.x + 8 + indent, row.centery - 10, 20, 20)
            if entry.is_dir:
                pygame.draw.rect(surface, pal.warning, icon, border_radius=4)
                if self._view == "tree":
                    draw_chevron(
                        surface,
                        (icon.centerx, icon.centery),
                        pal.on_accent,
                        size=3,
                        thickness=1,
                        direction="down" if entry.path in self._expanded else "right",
                    )
            else:
                draw_rounded(surface, icon, fill=pal.surface_raised, stroke=pal.text_muted, radius=3)
                pygame.draw.line(surface, pal.text_muted, (icon.x + 5, icon.y + 7), (icon.right - 5, icon.y + 7), 1)
                pygame.draw.line(surface, pal.text_muted, (icon.x + 5, icon.y + 11), (icon.right - 6, icon.y + 11), 1)
            name_font = self.font(11, True)
            blit_text(
                surface,
                name_font,
                fit_text(name_font, entry.name, row.w - 150 - indent),
                pal.text,
                midleft=(icon.right + 8, row.centery),
            )
            meta = "FOLDER" if entry.is_dir else f"{human_size(entry.size)}  \u00b7  {time.strftime('%b %d', time.localtime(entry.modified))}"
            blit_text(surface, self.font(8), meta, pal.text_muted, midright=(row.right - 8, row.centery))
            self._row_rects.append((index, row))
        draw_scrollbar(surface, area, self._scroll.offset, len(entries) * row_h, pal)

    def _draw_usage(self, surface, pal) -> None:
        area = self._list_area()
        bar = pygame.Rect(current_layout.margin, self.content.bottom - 20, self.content.w - current_layout.margin * 2, 5)
        pygame.draw.rect(surface, pal.track, bar, border_radius=2)
        fraction = min(1.0, self._used / QUOTA_BYTES)
        if fraction > 0:
            pygame.draw.rect(surface, pal.accent if fraction < 0.8 else pal.warning, pygame.Rect(bar.x, bar.y, max(2, int(bar.w * fraction)), bar.h), border_radius=2)
        blit_text(
            surface,
            self.font(8),
            f"{human_size(self._used)} of {human_size(QUOTA_BYTES)}  \u00b7  {self._count} files",
            pal.text_muted,
            midright=(bar.right, bar.y - 10),
        )

    def _draw_modal(self, surface, pal) -> None:
        if self._modal == "editor":
            self._draw_editor(surface, pal)
            return
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill((0, 0, 0, 175))
        surface.blit(scrim, (0, 0))
        panel = self._modal_panel()
        draw_rounded(surface, panel, fill=pal.surface_raised, stroke=pal.stroke, radius=18)
        if self._modal == "confirm":
            blit_text(surface, self.font(15, True), "Delete this item?", pal.text, center=(panel.centerx, panel.y + 32))
            blit_text(
                surface,
                self.font(9),
                f"/{self._confirm_delete}",
                pal.text_muted,
                center=(panel.centerx, panel.y + 54),
            )
            yes, no = self._modal_buttons()
            draw_button(surface, yes, "DELETE", self.font(11, True), pal, fill=pal.danger, color=pal.on_accent, stroke=None)
            draw_button(surface, no, "CANCEL", self.font(11, True), pal, fill=pal.surface_alt)
            return
        titles = {"new_folder": "New folder", "new_file": "New text file", "rename": "Rename"}
        blit_text(surface, self.font(16, True), titles.get(self._modal or "", "Name"), pal.text, topleft=(panel.x + 18, panel.y + 16))
        blit_text(surface, self.font(9), f"in /{self._cwd}" if self._cwd else "in /", pal.text_muted, topleft=(panel.x + 18, panel.y + 38))
        field = pygame.Rect(panel.x + 18, panel.y + 66, panel.w - 36, 40)
        self._name.draw(surface, field, self.font(12), pal, blink=self._blink)
        save, cancel = self._modal_buttons()
        draw_button(surface, save, "CREATE" if self._modal != "rename" else "RENAME", self.font(10, True), pal, fill=pal.accent, color=pal.on_accent, stroke=None)
        draw_button(surface, cancel, "CANCEL", self.font(10, True), pal, fill=pal.surface_alt)

    def _draw_editor(self, surface, pal) -> None:
        panel = self._modal_panel()
        draw_rounded(surface, panel, fill=pal.surface_raised, stroke=pal.stroke, radius=16)
        blit_text(surface, self.font(12, True), f"/{self._editing_path}", pal.text, topleft=(panel.x + 14, panel.y + 16))
        buttons = self._editor_buttons()
        draw_button(surface, buttons["save"], "SAVE", self.font(9, True), pal, fill=pal.accent, color=pal.on_accent, stroke=None, radius=8)
        draw_button(surface, buttons["back"], "BACK", self.font(9, True), pal, fill=pal.surface_alt, radius=8)
        area = pygame.Rect(panel.x + 14, panel.y + 52, panel.w - 28, panel.h - 66)
        draw_rounded(surface, area, fill=pal.surface, stroke=pal.stroke_soft, radius=10)
        font = self.font(12)
        lines = self._editor.value.split("\n")
        visible = max(1, (area.h - 16) // 20)
        caret_line = self._editor.value[: self._editor.caret].count("\n")
        start = max(0, min(max(0, len(lines) - visible), caret_line - visible + 2))
        y = area.y + 8
        for line in lines[start:]:
            blit_text(surface, font, line[:120], pal.text, topleft=(area.x + 10, y))
            y += 20
        if not self._editor.value:
            blit_text(surface, font, "Empty file \u2014 start typing", pal.text_muted, topleft=(area.x + 10, area.y + 8))

    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)


__all__ = ["FilesApp", "draw_icon", "human_size"]
