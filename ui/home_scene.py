"""Customisable home screen: pages, folders, drag/drop, icons and widgets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pygame

from core.input import EventKind, PointerEvent
from core.scene import Scene, clamp, ease_out_cubic, pulse
from ui.app_library import AppLibrary
from ui.icon_packs import draw_plate, get_pack, icon_rect as packed_icon_rect
from ui.layout import layout as current_layout
from ui.paint import (
    blit_text,
    draw_home_indicator,
    draw_rounded,
    fit_text,
    make_glow,
    vertical_gradient,
)
from ui.widgets import ContextMenu
from ui.widgets_engine import WidgetEngine
from widgets import WidgetContext


@dataclass(frozen=True, slots=True)
class HomeEntry:
    kind: str
    app_id: str
    name: str
    folder_id: str = ""
    children: tuple[str, ...] = ()


@dataclass(slots=True)
class Ripple:
    x: float
    y: float
    born: float
    tile: int = -1
    life: float = 0.6


class HomeScene(Scene):
    """Launcher driven by the persistent HomeState model."""

    name = "home"

    def __init__(self, kernel) -> None:
        super().__init__(kernel)
        self._home = self.registry.require("home_state")
        self._widget_engine = WidgetEngine()
        self._theme_key: tuple[object, ...] | None = None
        self._background = vertical_gradient(self.size, self.theme.palette.bg_top, self.theme.palette.bg_bottom)
        self._glow = pygame.transform.smoothscale(make_glow(210, self.theme.palette.accent, power=2.4), (760, 620))
        self._tiles: list[pygame.Rect] = []
        self._cols = 4
        self._rows = 5
        self._press_anim: list[float] = []
        self._active: int | None = None
        self._ripples: list[Ripple] = []
        self._page = 0
        self._menu: ContextMenu | None = None
        self._folder_id: str | None = None
        self._folder_rects: list[tuple[str, pygame.Rect]] = []
        self._folder_panel = pygame.Rect(0, 0, 0, 0)
        self._folder_drag: str | None = None
        self._folder_drag_pos = (0.0, 0.0)
        self._rename_id: str | None = None
        self._rename_text = ""
        self._press_entry: HomeEntry | None = None
        self._press_widget: dict | None = None
        self._press_pos = (0.0, 0.0)
        self._drag_entry: HomeEntry | None = None
        self._drag_widget: dict | None = None
        self._drag_pos = (0.0, 0.0)
        self._dragging = False
        self.library = AppLibrary(self)
        self._build_grid()

    # -- theme / geometry ----------------------------------------------------
    def _ensure_theme(self):
        palette = self.theme.palette
        key = self.theme.signature
        if self._theme_key != key:
            self._theme_key = key
            self._background = vertical_gradient(self.size, palette.bg_top, palette.bg_bottom)
            glow = make_glow(210, palette.accent, power=2.4)
            self._glow = pygame.transform.smoothscale(glow, (760, 620))
        return palette

    def _build_grid(self) -> None:
        self._tiles = []
        self._cols, self._rows = self._home.grid(current_layout.is_landscape)
        margin = float(current_layout.margin)
        gap = 18.0 if current_layout.is_landscape else 26.0
        top = current_layout.status_bar_height + (18 if current_layout.is_landscape else 46)
        bottom = current_layout.height - (86 if current_layout.is_landscape else 150)
        tile_width = (self.size[0] - 2.0 * margin - gap * (self._cols - 1)) / self._cols
        tile_height = min(
            102.0 if not current_layout.is_landscape else 96.0,
            (bottom - top - gap * (self._rows - 1)) / self._rows,
        )
        row_pitch = tile_height + gap
        for row in range(self._rows):
            for column in range(self._cols):
                x = margin + column * (tile_width + gap)
                y = top + row * row_pitch
                self._tiles.append(
                    pygame.Rect(
                        int(round(x)),
                        int(round(y)),
                        int(round(tile_width)),
                        int(round(tile_height)),
                    )
                )
        self._press_anim = [0.0] * len(self._tiles)
        self._active = None
        self._home.reflow(self._cols, self._rows)

    def on_canvas_resize(self, old: tuple[int, int], new: tuple[int, int]) -> None:
        self._background = vertical_gradient(self.size, self.theme.palette.bg_top, self.theme.palette.bg_bottom)
        self._theme_key = None
        self._build_grid()
        self._menu = None
        self._folder_id = None

    # -- entries -------------------------------------------------------------
    def _entry_from_item(self, item) -> HomeEntry | None:
        manager = self.registry.require("app_manager")
        if isinstance(item, str):
            if item == "settings":
                return HomeEntry("settings", "settings", "Settings")
            manifest = manager.manifest(item)
            if manifest is None:
                return None
            return HomeEntry("app", manifest.app_id, manifest.name)
        if isinstance(item, dict) and item.get("type") == "folder":
            children = tuple(child for child in item.get("items", []) if manager.manifest(child) is not None)
            return HomeEntry("folder", str(item.get("id", "")), str(item.get("name", "Folder")), str(item.get("id", "")), children)
        return None

    def _page_entries(self, page: int) -> list[HomeEntry]:
        if not (0 <= page < len(self._home.pages)):
            return []
        entries = []
        for item in self._home.pages[page]:
            entry = self._entry_from_item(item)
            if entry is not None:
                entries.append(entry)
        return entries

    def _entries(self) -> list[HomeEntry]:
        entries: list[HomeEntry] = []
        for page in range(len(self._home.pages)):
            entries.extend(self._page_entries(page))
        return entries

    def _dock_entries(self) -> list[HomeEntry]:
        dock = self._home.dock
        if not dock.get("show", True):
            return []
        manager = self.registry.require("app_manager")
        entries = []
        for app_id in dock.get("ids", []):
            manifest = manager.manifest(app_id)
            if manifest is not None:
                entries.append(HomeEntry("app", manifest.app_id, manifest.name))
        return entries

    def _page_widgets(self, page: int | None = None) -> list[dict]:
        return self._home.widgets(self._page if page is None else page)

    def _widget_rects(self, page: int | None = None) -> list[tuple[dict, pygame.Rect]]:
        widgets = self._page_widgets(page)
        return [
            (
                widget,
                WidgetEngine.cell_rect(
                    self._tiles,
                    self._cols,
                    int(widget.get("col", 0)),
                    int(widget.get("row", 0)),
                    int(widget.get("w", 1)),
                    int(widget.get("h", 1)),
                ),
            )
            for widget in widgets
        ]

    # -- paging --------------------------------------------------------------
    def _dock_rects(self) -> list[pygame.Rect]:
        entries = self._dock_entries()
        if not entries:
            return []
        size = 62 if current_layout.is_landscape else 68
        gap = 54
        total = len(entries) * size + (len(entries) - 1) * gap
        start = current_layout.center_x - total // 2
        y = current_layout.height - (112 if current_layout.is_landscape else 152)
        return [pygame.Rect(start + index * (size + gap), y, size, size) for index in range(len(entries))]

    def _page_count(self) -> int:
        return max(1, len(self._home.pages))

    def _entry_for_slot(self, slot: int) -> HomeEntry | None:
        rank = WidgetEngine.entry_rank_for_slot(slot, self._page_widgets(), self._cols, self._rows)
        if rank is None:
            return None
        entries = self._page_entries(self._page)
        return entries[rank] if rank < len(entries) else None

    def _entry_at_pos(self, pos: tuple[float, float]) -> HomeEntry | None:
        slot = self._hit(pos)
        return self._entry_for_slot(slot) if slot is not None else None

    def _dot_centers(self) -> list[tuple[int, int]]:
        count = self._page_count()
        if count <= 1:
            return []
        spacing = 18
        start = current_layout.center_x - (count - 1) * spacing / 2
        y = current_layout.height - 42
        return [(int(round(start + index * spacing)), y) for index in range(count)]

    def _dot_at(self, pos: tuple[float, float]) -> int | None:
        for index, center in enumerate(self._dot_centers()):
            if (pos[0] - center[0]) ** 2 + (pos[1] - center[1]) ** 2 <= 18 ** 2:
                return index
        return None

    def _set_page(self, page: int) -> None:
        self._page = max(0, min(self._page_count() - 1, page))
        self._active = None
        self._press_anim = [0.0] * len(self._tiles)

    # -- opening -------------------------------------------------------------
    def _open_entry(self, entry: HomeEntry) -> None:
        if entry.kind == "settings":
            self.go("settings", fade=0.22)
            return
        if entry.kind == "folder":
            self._folder_id = entry.folder_id
            return
        manager = self.registry.require("app_manager")
        if manager.launch(entry.app_id) is not None:
            self.go("app", fade=0.16)

    def _notify(self, title: str, body: str, color=None) -> None:
        self.notifications.post("system", title, body, color or self.theme.palette.warning, banner=False)

    def _open_menu(self, entry: HomeEntry, pos: tuple[float, float]) -> None:
        manager = self.registry.require("app_manager")

        def launch() -> None:
            self._open_entry(entry)

        def uninstall() -> None:
            if manager.uninstall(entry.app_id):
                self._notify("App removed", f"{entry.name} was uninstalled.", self.theme.palette.warning)
                self._page = min(self._page, self._page_count() - 1)

        def ungroup() -> None:
            self._home.ungroup(entry.folder_id)

        def rename_folder() -> None:
            self._rename_id = entry.folder_id
            self._rename_text = entry.name
            self._menu = None

        def delete_folder() -> None:
            snapshot = self._home.delete_folder(entry.folder_id)
            if snapshot is None:
                return
            if self._folder_id == entry.folder_id:
                self._folder_id = None
            toast = self.registry.get("toast")
            if toast is not None:
                toast.undo("Folder deleted", lambda: self._home.restore_folder(snapshot))

        if entry.kind == "settings":
            options: list[tuple[str, Callable[[], None] | None]] = [
                ("Open Settings", launch),
                ("Edit home screen", lambda: self._open_home_settings()),
                ("System app \u00b7 cannot uninstall", None),
            ]
        elif entry.kind == "folder":
            options = [("Open folder", launch), ("Rename folder", rename_folder), ("Ungroup folder", ungroup), ("Delete folder", delete_folder), ("Cancel", lambda: None)]
        else:
            manifest = manager.manifest(entry.app_id)
            options = [("Open", launch), ("App info", lambda: self._open_app_info(entry))]
            if manifest is not None and not manifest.core and not manifest.system:
                options.append(("Uninstall", uninstall))
            else:
                options.append(("System app \u00b7 cannot uninstall", None))
        options.append(("Cancel", lambda: None))
        self._menu = ContextMenu(pygame.Rect(0, 0, *self.size), entry.name, options)
        self._menu.open(pos)

    def _open_app_info(self, entry: HomeEntry) -> None:
        self.kernel._pending_settings_page = "apps"
        self.kernel._pending_settings_app = entry.app_id
        self.go("settings", fade=0.2)

    def _open_home_settings(self) -> None:
        self.kernel._pending_settings_page = "home"
        self.go("settings", fade=0.2)

    def _open_widget_menu(self, widget: dict, pos: tuple[float, float]) -> None:
        widget_id = str(widget.get("id", ""))

        def remove() -> None:
            self._widget_engine.forget(widget_id)
            self._home.remove_widget(widget_id)

        def resize(delta: int) -> None:
            width = max(1, min(self._cols - int(widget.get("col", 0)), int(widget.get("w", 1)) + delta))
            height = max(1, min(self._rows - int(widget.get("row", 0)), int(widget.get("h", 1)) + delta))
            self._home.update_widget(widget_id, w=width, h=height)

        options = [
            ("Remove widget", remove),
            ("Wider", lambda: resize(1)),
            ("Narrower", lambda: resize(-1)),
            ("Taller", lambda: self._home.update_widget(widget_id, h=min(self._rows, int(widget.get("h", 1)) + 1))),
            ("Shorter", lambda: self._home.update_widget(widget_id, h=max(1, int(widget.get("h", 1)) - 1))),
            ("Cancel", lambda: None),
        ]
        kind = str(widget.get("type", "widget")).title()
        self._menu = ContextMenu(pygame.Rect(0, 0, *self.size), f"{kind} widget", options)
        self._menu.open(pos)

    # -- input ---------------------------------------------------------------
    def update(self, dt: float) -> None:
        super().update(dt)
        blend = min(1.0, dt * 16.0)
        for index, value in enumerate(self._press_anim):
            target = 1.0 if index == self._active else 0.0
            self._press_anim[index] = value + (target - value) * blend
        self._ripples = [ripple for ripple in self._ripples if self.elapsed - ripple.born < ripple.life]
        if self._menu is not None:
            self._menu.update(dt)

    def handle(self, event: PointerEvent) -> bool:
        if self._rename_id is not None:
            if event.kind is EventKind.KEY_DOWN:
                if event.key == pygame.K_ESCAPE:
                    self._rename_id = None
                elif event.key == pygame.K_BACKSPACE:
                    self._rename_text = self._rename_text[:-1]
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self._home.rename_folder(self._rename_id, self._rename_text)
                    self._rename_id = None
                elif event.text and event.text.isprintable():
                    self._rename_text = (self._rename_text + event.text)[:24]
                return True
            if event.kind is EventKind.TAP:
                self._rename_id = None
                return True
            return True
        if self.library.is_open:
            return self.library.handle(event)
        if self._menu is not None and self._menu.is_open:
            self._menu.handle(event)
            return True

        if self._folder_id is not None:
            return self._handle_folder(event)

        if event.kind is EventKind.SWIPE and event.direction.name == "UP":
            dock = self._dock_rect()
            if dock.inflate(30, 30).collidepoint(event.start_pos):
                self.library.open()
                return True
            if event.start_pos[1] > current_layout.height * 0.7:
                self.library.open()
                return True
        if event.kind is EventKind.SWIPE and event.direction.name == "DOWN":
            if abs(event.start_pos[0] - current_layout.center_x) < current_layout.width * 0.32 and event.start_pos[1] > current_layout.status_bar_height + 80:
                self.kernel.open_spotlight()
                return True

        if event.kind is EventKind.KEY_DOWN and event.key == pygame.K_ESCAPE:
            self.go("lock", fade=0.3)
            return True

        if event.kind is EventKind.POINTER_DOWN:
            self._active = self._hit(event.pos)
            self._press_entry = None
            self._press_widget = None
            widget = WidgetEngine.hit(self._page_widgets(), self._tiles, self._cols, event.pos)
            if widget is not None:
                self._press_widget = widget
            self._press_pos = event.pos
            return self._active is not None or self._dock_hit(event.pos) is not None or widget is not None

        if event.kind is EventKind.DRAG:
            if not self._dragging and self._press_pos and (
                abs(event.pos[0] - self._press_pos[0]) + abs(event.pos[1] - self._press_pos[1]) > 14
            ):
                if self._press_widget is not None:
                    self._drag_widget = self._press_widget
                    self._dragging = True
                    self._menu = None
                elif self._press_entry is not None:
                    self._drag_entry = self._press_entry
                    self._dragging = True
                    self._menu = None
            if self._dragging:
                self._drag_pos = event.pos
                if event.pos[0] < 18 and self._page > 0:
                    self._set_page(self._page - 1)
                elif event.pos[0] > self.size[0] - 18 and self._page < self._page_count() - 1:
                    self._set_page(self._page + 1)
                return True
            return False

        if event.kind in (EventKind.DRAG_END, EventKind.POINTER_UP):
            if self._dragging:
                self._finish_drag(event.pos)
            self._dragging = False
            self._drag_entry = None
            self._drag_widget = None
            self._active = None
            self._press_entry = None
            self._press_widget = None
            return True

        if event.kind in (EventKind.TAP, EventKind.DOUBLE_TAP):
            page = self._dot_at(event.pos)
            if page is not None:
                self._set_page(page)
                return True
            docked = self._dock_hit(event.pos)
            if docked is not None:
                self._open_entry(self._dock_entries()[docked])
                return True
            index = self._hit(event.pos)
            if index is None:
                return False
            entry = self._entry_for_slot(index)
            if entry is None:
                return False
            self._add_ripple(event.pos, index)
            self._open_entry(entry)
            return True

        if event.kind is EventKind.LONG_PRESS:
            widget = WidgetEngine.hit(self._page_widgets(), self._tiles, self._cols, event.pos)
            if widget is not None:
                self._open_widget_menu(widget, event.pos)
                return True
            docked = self._dock_hit(event.pos)
            if docked is not None:
                self._open_menu(self._dock_entries()[docked], event.pos)
                return True
            index = self._hit(event.pos)
            if index is None:
                self._open_home_settings()
                return True
            entry = self._entry_for_slot(index)
            if entry is None:
                self._open_home_settings()
                return True
            self._press_entry = entry
            self._press_pos = event.pos
            self._add_ripple(event.pos, index, life=0.9)
            self._open_menu(entry, event.pos)
            return True

        return False

    def _finish_drag(self, pos: tuple[float, float]) -> None:
        if self._drag_widget is not None:
            self._drop_widget(self._drag_widget, pos)
            return
        entry = self._drag_entry
        if entry is None or entry.kind == "folder":
            return
        if self._trash_rect().collidepoint(pos):
            self._trash_entry(entry)
            return
        if self._dock_rect().collidepoint(pos):
            if self._home.add_to_dock(entry.app_id):
                self._notify("Added to dock", entry.name, self.theme.palette.accent)
            return
        target = self._entry_at_pos(pos)
        if target is not None and target != entry:
            if target.kind == "folder":
                self._home.add_to_folder(target.folder_id, entry.app_id)
                return
            if target.kind == "app":
                self._home.make_folder(entry.app_id, target.app_id)
                return
        slot = self._hit(pos)
        if slot is None:
            return
        rank = WidgetEngine.entry_rank_for_slot(slot, self._page_widgets(), self._cols, self._rows)
        if rank is None:
            return
        self._home.move_item(entry.app_id, self._page, rank)

    def _drop_widget(self, widget: dict, pos: tuple[float, float]) -> None:
        if self._trash_rect().collidepoint(pos):
            self._home.remove_widget(str(widget.get("id", "")))
            return
        if not self._tiles:
            return
        pitch_x = self._tiles[1].x - self._tiles[0].x if len(self._tiles) > 1 else self._tiles[0].w
        pitch_y = self._tiles[self._cols].y - self._tiles[0].y if len(self._tiles) > self._cols else self._tiles[0].h
        col = int((pos[0] - self._tiles[0].x) // max(1, pitch_x))
        row = int((pos[1] - self._tiles[0].y) // max(1, pitch_y))
        col = max(0, min(self._cols - int(widget.get("w", 1)), col))
        row = max(0, min(self._rows - int(widget.get("h", 1)), row))
        others = [item for item in self._page_widgets() if item.get("id") != widget.get("id")]
        if self._widget_engine.find_free_rect(others, self._cols, self._rows, int(widget.get("w", 1)), int(widget.get("h", 1))) is None:
            return
        self._home.update_widget(str(widget.get("id", "")), col=col, row=row)

    def _trash_entry(self, entry: HomeEntry) -> None:
        manager = self.registry.require("app_manager")
        if entry.kind == "folder":
            self._home.ungroup(entry.folder_id)
            return
        manifest = manager.manifest(entry.app_id)
        if manifest is None or manifest.core or manifest.system:
            self._notify("Can't remove", f"{entry.name} is a system app.", self.theme.palette.warning)
            return

        def confirm() -> None:
            if manager.uninstall(entry.app_id):
                self._notify("App removed", f"{entry.name} was uninstalled.", self.theme.palette.warning)

        self._menu = ContextMenu(
            pygame.Rect(0, 0, *self.size),
            f"Uninstall {entry.name}?",
            [("Uninstall", confirm), ("Keep app", lambda: None)],
        )
        self._menu.open((self.size[0] // 2, self.size[1] // 2))

    # -- folder --------------------------------------------------------------
    def _folder(self) -> dict | None:
        return self._home.folder(self._folder_id) if self._folder_id else None

    def _handle_folder(self, event: PointerEvent) -> bool:
        folder = self._folder()
        if folder is None:
            self._folder_id = None
            return False
        if event.kind is EventKind.KEY_DOWN and event.key == pygame.K_ESCAPE:
            self._folder_id = None
            return True
        if event.kind is EventKind.POINTER_DOWN:
            for app_id, rect in self._folder_rects:
                if rect.collidepoint(event.pos):
                    self._folder_drag = app_id
                    self._folder_drag_pos = event.pos
                    return True
            return True
        if event.kind is EventKind.DRAG and self._folder_drag is not None:
            self._folder_drag_pos = event.pos
            return True
        if event.kind in (EventKind.DRAG_END, EventKind.POINTER_UP):
            app_id = self._folder_drag
            self._folder_drag = None
            if app_id is not None and not self._folder_panel.inflate(20, 20).collidepoint(event.pos):
                self._home.remove_from_folder(folder["id"], app_id)
                self._folder_id = None
                toast = self.registry.get("toast")
                if toast is not None:
                    toast.show("Removed from folder")
            return True
        if event.kind is EventKind.TAP:
            for app_id, rect in self._folder_rects:
                if rect.collidepoint(event.pos):
                    self._folder_id = None
                    self._open_entry(HomeEntry("app", app_id, ""))
                    return True
            self._folder_id = None
            return True
        if event.kind is EventKind.LONG_PRESS:
            return True
        return False

    # -- hit testing ---------------------------------------------------------
    def _hit(self, pos: tuple[float, float]) -> int | None:
        for index, rect in enumerate(self._tiles):
            if rect.collidepoint(pos):
                return index
        return None

    def _dock_hit(self, pos: tuple[float, float]) -> int | None:
        for index, rect in enumerate(self._dock_rects()):
            if rect.collidepoint(pos):
                return index
        return None

    def _trash_rect(self) -> pygame.Rect:
        return pygame.Rect(current_layout.center_x - 38, current_layout.status_bar_height + 10, 76, 66)

    def _dock_rect(self) -> pygame.Rect:
        rects = self._dock_rects()
        if not rects:
            return pygame.Rect(0, current_layout.height - 160, self.size[0], 110)
        return pygame.Rect(rects[0].x - 22, rects[0].y - 14, rects[-1].right - rects[0].x + 44, rects[0].h + 28)

    def _add_ripple(self, pos: tuple[float, float], index: int, life: float = 0.6) -> None:
        self._ripples.append(Ripple(pos[0], pos[1], self.elapsed, tile=index, life=life))

    # -- drawing -------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        palette = self._ensure_theme()
        fx = self.registry.get("fx")
        if fx is not None:
            fx.draw_wallpaper(surface)
        else:
            surface.blit(self._background, (0, 0))

        glow = self._glow
        glow.set_alpha(int(38 + int(16 * pulse(self.elapsed, 4.2))))
        surface.blit(glow, glow.get_rect(center=(current_layout.center_x, current_layout.status_bar_height + 84)))

        self._page = min(self._page, self._page_count() - 1)
        widgets = self._page_widgets()
        context = WidgetContext(
            theme=self.theme,
            clock=self.device_clock,
            settings=self.settings,
            power=self.registry.get("power"),
            audio=self.registry.get("audio"),
            storage=self.registry.get("storage"),
        )
        self._widget_engine.draw(surface, widgets, self._tiles, self._cols, context)
        for index, rect in enumerate(self._tiles):
            self._draw_tile(surface, rect, index, palette)

        self._draw_dock(surface, palette)
        self._draw_page_dots(surface, palette)
        draw_home_indicator(surface, palette.home_indicator)
        if self._rename_id is not None:
            scrim = pygame.Surface(self.size, pygame.SRCALPHA)
            scrim.fill((0, 0, 0, 150))
            surface.blit(scrim, (0, 0))
            card = pygame.Rect(0, 0, min(360, self.size[0] - 50), 150)
            card.center = (current_layout.center_x, current_layout.height // 2)
            draw_rounded(surface, card, fill=palette.surface, stroke=palette.accent, radius=24)
            blit_text(surface, self.font(16, True), "Rename folder", palette.text, center=(card.centerx, card.y + 34))
            field = pygame.Rect(card.x + 24, card.y + 62, card.w - 48, 40)
            draw_rounded(surface, field, fill=palette.surface_alt, stroke=palette.stroke_soft, radius=12)
            blit_text(surface, self.font(15), self._rename_text or "Folder name", palette.text if self._rename_text else palette.text_muted, midleft=(field.x + 12, field.centery))
            blit_text(surface, self.font(9), "Enter saves · Esc cancels", palette.text_muted, center=(card.centerx, card.bottom - 20))
        if self._folder_id is not None:
            self._draw_folder(surface, palette)
        if self.library.is_open:
            self.library.draw(surface)
        if self._dragging:
            self._draw_drag(surface, palette)
        if self._menu is not None:
            self._menu.draw(surface, self.theme)

    def _draw_dock(self, surface: pygame.Surface, palette) -> None:
        entries = self._dock_entries()
        rects = self._dock_rects()
        if not entries:
            return
        manager = self.registry.require("app_manager")
        pack = get_pack(self._home.icons.get("pack"))
        if rects:
            panel = pygame.Rect(rects[0].x - 20, rects[0].y - 12, rects[-1].right - rects[0].x + 40, rects[0].h + 24)
            draw_rounded(surface, panel, fill=palette.surface_alt, radius=26, alpha=120)
        for entry, rect in zip(entries, rects):
            manifest = manager.manifest(entry.app_id)
            if manifest is None:
                continue
            plate = pygame.Rect(0, 0, int(rect.w * 0.92), int(rect.h * 0.92))
            plate.center = rect.center
            draw_plate(surface, plate, pack, palette, alpha=95)
            inner = pygame.Rect(0, 0, int(rect.w * 0.62), int(rect.h * 0.62))
            inner.center = rect.center
            manifest.icon(surface, inner, self.theme)

    def _draw_tile(self, surface: pygame.Surface, rect: pygame.Rect, slot: int, palette) -> None:
        entry = self._entry_for_slot(slot)
        if entry is None:
            return
        anim = self._press_anim[slot] if slot < len(self._press_anim) else 0.0
        pack = get_pack(self._home.icons.get("pack"))
        scale = float(self._home.icons.get("scale", 1.0))
        plate_size = max(28, int(min(64, rect.h * 0.62) * scale * (1.0 - 0.05 * anim)))
        plate = pygame.Rect(0, 0, plate_size, plate_size)
        plate.center = (rect.centerx, rect.y + max(8, int(rect.h * 0.36)))
        if anim > 0.01:
            halo = pygame.Surface((plate.w + 30, plate.h + 30), pygame.SRCALPHA)
            pygame.draw.rect(halo, (*palette.accent, int(48 * anim)), halo.get_rect(), border_radius=28)
            surface.blit(halo, halo.get_rect(center=plate.center))
        draw_plate(surface, plate, pack, palette)
        inner = packed_icon_rect(plate, pack, 1.0)
        manager = self.registry.require("app_manager")
        if entry.kind == "settings":
            self._draw_settings_icon(surface, inner, palette)
        elif entry.kind == "folder":
            self._draw_folder_icon(surface, plate, entry, palette, manager)
        else:
            manifest = manager.manifest(entry.app_id)
            if manifest is not None:
                manifest.icon(surface, inner, self.theme)

        if self._home.icons.get("labels", True):
            label_font = self.font(11)
            label = entry.name
            blit_text(
                surface,
                label_font,
                fit_text(label_font, label, rect.w + 6),
                palette.text_secondary,
                center=(rect.centerx, rect.bottom - 12),
            )

        for ripple in self._ripples:
            if ripple.tile != slot:
                continue
            progress = (self.elapsed - ripple.born) / ripple.life
            if progress >= 1.0:
                continue
            ring_radius = int(8 + 64 * ease_out_cubic(progress))
            alpha = int(140 * (1.0 - progress))
            layer = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
            pygame.draw.circle(layer, (*palette.accent, alpha), (int(ripple.x - rect.x), int(ripple.y - rect.y)), ring_radius, 2)
            surface.blit(layer, rect.topleft)

    def _draw_settings_icon(self, surface: pygame.Surface, icon_rect: pygame.Rect, palette) -> None:
        draw_rounded(surface, icon_rect, fill=palette.surface_raised, stroke=palette.stroke, radius=16)
        rows = (-11, 0, 11)
        knobs = (-9, 7, -3)
        for offset, knob in zip(rows, knobs):
            y = icon_rect.centery + offset
            pygame.draw.line(surface, palette.text_muted, (icon_rect.x + 14, y), (icon_rect.right - 14, y), 2)
            pygame.draw.circle(surface, palette.accent_ink, (icon_rect.centerx + knob, y), 4)

    def _draw_folder_icon(self, surface, plate, entry: HomeEntry, palette, manager) -> None:
        draw_rounded(surface, plate, fill=palette.surface_raised, stroke=palette.stroke, radius=int(plate.w * 0.28))
        children = entry.children[:4]
        piece = max(10, int(plate.w * 0.32))
        for index, app_id in enumerate(children):
            row, col = divmod(index, 2)
            cell = pygame.Rect(
                plate.x + int(plate.w * 0.17) + col * int(plate.w * 0.34),
                plate.y + int(plate.h * 0.17) + row * int(plate.h * 0.34),
                piece,
                piece,
            )
            manifest = manager.manifest(app_id)
            if manifest is not None:
                manifest.icon(surface, cell, self.theme)

    def _draw_page_dots(self, surface: pygame.Surface, palette) -> None:
        for index, center in enumerate(self._dot_centers()):
            color = palette.text if index == self._page else palette.text_muted
            pygame.draw.circle(surface, color, center, 3)

    def _draw_drag(self, surface, palette) -> None:
        pygame.draw.rect(surface, palette.danger, self._trash_rect(), border_radius=18)
        pygame.draw.rect(surface, palette.on_accent, self._trash_rect(), 2, border_radius=18)
        blit_text(surface, self.font(9, True), "DROP", palette.on_accent, center=(self._trash_rect().centerx, self._trash_rect().centery + 12), tracking=1)
        pygame.draw.line(surface, palette.on_accent, (self._trash_rect().centerx - 12, self._trash_rect().centery - 12), (self._trash_rect().centerx + 12, self._trash_rect().centery - 12), 2)
        pygame.draw.line(surface, palette.on_accent, (self._trash_rect().centerx - 12, self._trash_rect().centery - 12), (self._trash_rect().centerx - 12, self._trash_rect().centery + 2), 2)
        pygame.draw.line(surface, palette.on_accent, (self._trash_rect().centerx + 12, self._trash_rect().centery - 12), (self._trash_rect().centerx + 12, self._trash_rect().centery + 2), 2)
        if self._drag_entry is not None:
            manager = self.registry.require("app_manager")
            size = 58
            rect = pygame.Rect(0, 0, size, size)
            rect.center = (int(self._drag_pos[0]), int(self._drag_pos[1]))
            halo = pygame.Surface((size + 20, size + 20), pygame.SRCALPHA)
            pygame.draw.rect(halo, (*palette.accent, 60), halo.get_rect(), border_radius=22)
            surface.blit(halo, halo.get_rect(center=rect.center))
            if self._drag_entry.kind == "settings":
                self._draw_settings_icon(surface, rect, palette)
            elif self._drag_entry.kind == "folder":
                self._draw_folder_icon(surface, rect, self._drag_entry, palette, manager)
            else:
                manifest = manager.manifest(self._drag_entry.app_id)
                if manifest is not None:
                    manifest.icon(surface, rect, self.theme)
        elif self._drag_widget is not None:
            widget = self._drag_widget
            rect = WidgetEngine.cell_rect(
                self._tiles,
                self._cols,
                0,
                0,
                int(widget.get("w", 2)),
                int(widget.get("h", 1)),
            )
            rect.center = (int(self._drag_pos[0]), int(self._drag_pos[1]))
            draw_rounded(surface, rect, fill=palette.accent_soft, stroke=palette.accent, radius=20, alpha=120)

    def _draw_folder(self, surface, palette) -> None:
        folder = self._folder()
        if folder is None:
            return
        manager = self.registry.require("app_manager")
        scrim = pygame.Surface(self.size, pygame.SRCALPHA)
        scrim.fill((0, 0, 0, 150))
        surface.blit(scrim, (0, 0))
        width = min(self.size[0] - 56, 430)
        panel = pygame.Rect(0, 0, width, 360)
        panel.center = (current_layout.center_x, current_layout.height // 2)
        self._folder_panel = panel
        draw_rounded(surface, panel, fill=palette.surface, stroke=palette.stroke, radius=30, alpha=245)
        title_font = self.font(20, True)
        blit_text(surface, title_font, fit_text(title_font, str(folder.get("name", "Folder")), panel.w - 60), palette.text, center=(panel.centerx, panel.y + 34))
        children = [child for child in folder.get("items", []) if manager.manifest(child) is not None]
        self._folder_rects = []
        index = 0
        for row in range(3):
            for col in range(3):
                if index >= len(children):
                    break
                app_id = children[index]
                manifest = manager.manifest(app_id)
                cell = pygame.Rect(panel.x + 34 + col * 120, panel.y + 72 + row * 92, 64, 64)
                artifact = pygame.Rect(0, 0, 58, 58)
                artifact.center = (cell.centerx, cell.y + 24)
                draw_plate(surface, artifact, get_pack(self._home.icons.get("pack")), palette, alpha=110)
                inner = pygame.Rect(0, 0, 40, 40)
                inner.center = artifact.center
                if manifest is not None:
                    manifest.icon(surface, inner, self.theme)
                    blit_text(surface, self.font(10), fit_text(self.font(10), manifest.name, 100), palette.text_secondary, center=(cell.centerx, cell.bottom + 12))
                self._folder_rects.append((app_id, pygame.Rect(cell.x - 18, cell.y - 8, 100, 92)))
                index += 1
