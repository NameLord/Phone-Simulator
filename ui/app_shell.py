"""App host scene: common chrome, clipping, navigation and the Recents carousel.

The host is the only UI that talks to the AppManager for lifecycle; apps stay
inside their own surface and receive events in virtual coordinates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import pygame

from apps.base_app import APP_CHROME_HEIGHT
from core.input import Direction, EventKind, PointerEvent
from core.scene import Scene, ease_out_cubic
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_chevron, draw_rounded, with_alpha
from ui.share_sheet import ShareSheet

if TYPE_CHECKING:
    from apps.base_app import App
    from services.app_manager import AppManager
    from ui.theme import Palette

STATUS_HEIGHT = 54


def clamp_ratio(value: float) -> float:
    return max(0.12, min(0.88, float(value)))



class RecentsOverlay:
    """Horizontal card carousel over running apps."""

    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size
        self._card_width = 196
        self._card_height = 336
        self._card_gap = 18
        self._card_y = 372
        self._measure()
        self.is_open = False
        self._apps: tuple["App", ...] = ()
        self._index = 0
        self._offset = 0.0
        self._target = 0.0
        self._opening = 0.0
        self._dragging = False
        self._drag_mode: str | None = None
        self._drag_start = (0.0, 0.0)
        self._drag_base = 0.0
        self._vertical_offset = 0.0
        self._press_index: int | None = None
        self._ignore_next_swipe = False
        self._thumb_timer = 0.0
        self._thumbs: dict[str, pygame.Surface] = {}
        self._scrim: pygame.Surface | None = None

    # -- lifecycle ------------------------------------------------------------
    def open(self, manager: "AppManager") -> None:
        self._apps = manager.recents_apps()
        if not self._apps:
            return
        self.is_open = True
        self._opening = 0.0
        self._index = 0
        self._offset = 0.0
        self._target = 0.0
        self._dragging = False
        self._drag_mode = None
        self._vertical_offset = 0.0
        self._press_index = None
        self._ignore_next_swipe = False
        self._thumb_timer = 0.0
        self._thumbs = {}
        for app in self._apps:
            self._thumbs[app.app_id] = self._thumbnail(app.surface)

    def close(self) -> None:
        self.is_open = False
        self._dragging = False
        self._thumbs.clear()

    def update(self, dt: float, manager: "AppManager") -> None:
        if not self.is_open:
            return
        self._opening = min(1.0, self._opening + max(0.0, dt) / 0.22)
        if not self._dragging:
            self._offset += (self._target - self._offset) * min(1.0, max(0.0, dt) * 15.0)
            if abs(self._target - self._offset) < 0.5:
                self._offset = self._target

        # Paused apps do not update, but their snapshots are still refreshed
        # twice a second so the carousel always shows their latest frame.
        self._thumb_timer += max(0.0, dt)
        if self._thumb_timer >= 0.5:
            self._thumb_timer = 0.0
            for app in self._apps:
                if manager.active_id != app.app_id:
                    self._thumbs[app.app_id] = self._thumbnail(app.surface)

    # -- layout ---------------------------------------------------------------
    def resize(self, size: tuple[int, int]) -> None:
        self.size = (int(size[0]), int(size[1]))
        self._measure()
        self._thumbs.clear()
        for app in self._apps:
            self._thumbs[app.app_id] = self._thumbnail(app.surface)

    def _measure(self) -> None:
        width, height = self.size
        if current_layout.is_landscape:
            self._card_width = int(min(236, max(150, width * 0.20)))
            self._card_height = int(min(330, max(180, height * 0.63)))
            self._card_y = int((height - self._card_height) * 0.52)
        else:
            self._card_width = int(min(196, max(140, width * 0.36)))
            self._card_height = int(min(336, max(220, height * 0.30)))
            self._card_y = int((height - self._card_height) * 0.44)
        self._card_gap = 16

    def _card_rect(self, index: int) -> pygame.Rect:
        origin = (self.size[0] - self._card_width) / 2.0 + self._offset
        x = origin + index * (self._card_width + self._card_gap)
        return pygame.Rect(int(round(x)), self._card_y, self._card_width, self._card_height)

    def _target_for(self, index: int) -> float:
        return -index * (self._card_width + self._card_gap)

    def _select(self, index: int) -> None:
        self._index = max(0, min(len(self._apps) - 1, index))
        self._target = self._target_for(self._index)

    def _thumbnail(self, source: pygame.Surface) -> pygame.Surface:
        width = max(64, self._card_width - 24)
        height = max(90, int(self._card_height * 0.65))
        sw, sh = source.get_size()
        scale = max(width / sw, height / sh)
        scaled = pygame.transform.smoothscale(source, (max(1, int(sw * scale)), max(1, int(sh * scale))))
        result = pygame.Surface((width, height))
        result.blit(scaled, ((width - scaled.get_width()) // 2, (height - scaled.get_height()) // 2))
        return result

    # -- input ----------------------------------------------------------------
    def handle(
        self,
        event: PointerEvent,
        manager: "AppManager",
        on_empty: Callable[[], None],
        on_split: Callable[[str], bool] | None = None,
    ) -> bool:
        if not self.is_open:
            return False

        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self.close()
            elif event.key in (pygame.K_LEFT, pygame.K_a):
                self._select(self._index - 1)
            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                self._select(self._index + 1)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self._activate(manager, on_empty)
            return True

        if event.kind is EventKind.SWIPE:
            if self._ignore_next_swipe:
                self._ignore_next_swipe = False
                return True
            target = self._press_index if self._press_index is not None else self._index
            if event.direction is Direction.UP:
                self._kill_app(target, manager, on_empty)
            elif event.direction is Direction.DOWN:
                self.close()
            elif event.direction is Direction.LEFT:
                self._select(self._index + 1)
            elif event.direction is Direction.RIGHT:
                self._select(self._index - 1)
            self._press_index = None
            return True

        if event.kind is EventKind.POINTER_DOWN:
            self._dragging = True
            self._drag_mode = None
            self._drag_start = event.pos
            self._drag_base = self._offset
            self._vertical_offset = 0.0
            self._press_index = self._card_at(event.pos)
            self._ignore_next_swipe = False
            return True

        if event.kind is EventKind.DRAG:
            if self._dragging:
                dx = event.pos[0] - self._drag_start[0]
                dy = event.pos[1] - self._drag_start[1]
                if self._drag_mode is None and (abs(dx) > 10.0 or abs(dy) > 10.0):
                    self._drag_mode = "vertical" if abs(dy) > abs(dx) else "horizontal"
                if self._drag_mode == "horizontal":
                    self._offset = self._drag_base + dx
                elif self._drag_mode == "vertical":
                    self._vertical_offset = dy
            return True

        if event.kind in (EventKind.POINTER_UP, EventKind.DRAG_END):
            if self._dragging:
                mode = self._drag_mode
                self._dragging = False
                acted = False
                if mode == "vertical":
                    if self._vertical_offset < -70.0 and self._press_index is not None:
                        app = self._apps[self._press_index] if 0 <= self._press_index < len(self._apps) else None
                        if app is not None and event.pos[1] <= self.size[1] * 0.32 and on_split is not None and on_split(app.app_id):
                            self.close()
                        else:
                            self._kill_app(self._press_index, manager, on_empty)
                        acted = True
                    elif self._vertical_offset > 70.0:
                        self.close()
                        acted = True
                    self._vertical_offset = 0.0
                elif mode == "horizontal":
                    nearest = round(-self._offset / (self._card_width + self._card_gap))
                    self._select(nearest)
                    acted = True
                # If a short vertical flick did not cross the drag threshold,
                # leave the press index so the following SWIPE can handle it.
                if acted:
                    self._ignore_next_swipe = True
                self._drag_mode = None
            return True

        if event.kind is EventKind.TAP:
            for index, app in enumerate(self._apps):
                rect = self._card_rect(index)
                close_rect = pygame.Rect(rect.right - 38, rect.y + 10, 28, 28)
                if close_rect.collidepoint(event.pos):
                    self._close_app(app.app_id, manager, on_empty)
                    return True
                if rect.collidepoint(event.pos):
                    if index == self._index:
                        self._activate(manager, on_empty)
                    else:
                        self._select(index)
                    self._press_index = None
                    return True
            # Tap outside closes the overlay.
            self.close()
            return True

        return True

    def _card_at(self, pos: tuple[float, float]) -> int | None:
        for index in range(len(self._apps)):
            if self._card_rect(index).collidepoint(pos):
                return index
        return None

    def _kill_app(
        self, index: int, manager: "AppManager", on_empty: Callable[[], None]
    ) -> None:
        if 0 <= index < len(self._apps):
            self._close_app(self._apps[index].app_id, manager, on_empty)

    def _activate(self, manager: "AppManager", on_empty: Callable[[], None]) -> None:
        if not self._apps:
            self.close()
            on_empty()
            return
        app = self._apps[self._index]
        if manager.switch_to(app.app_id):
            self.close()
        else:
            on_empty()

    def _close_app(
        self, app_id: str, manager: "AppManager", on_empty: Callable[[], None]
    ) -> None:
        was_active = manager.active_id == app_id
        manager.close(app_id)
        self._apps = manager.recents_apps()
        self._thumbs.pop(app_id, None)
        if not self._apps:
            self.close()
            on_empty()
            return
        if was_active:
            manager.switch_to(self._apps[0].app_id)
        self._select(0)
        self._offset = self._target
        self._vertical_offset = 0.0
        self._drag_mode = None
        self._press_index = None

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface, theme, manager: "AppManager") -> None:
        if not self.is_open:
            return
        pal = theme.palette
        alpha = 255 if self._opening >= 0.999 else int(220 * ease_out_cubic(self._opening))

        if self._scrim is None or self._scrim.get_size() != surface.get_size():
            self._scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        self._scrim.fill(with_alpha(pal.scrim, alpha))
        surface.blit(self._scrim, (0, 0))

        first_card = self._card_rect(0)
        blit_text(
            surface,
            theme.font(11),
            "RECENT APPS",
            pal.text_muted,
            center=(self.size[0] // 2, max(64, first_card.y - 96)),
            alpha=alpha,
            tracking=3.0,
        )

        for index, app in enumerate(self._apps):
            rect = self._card_rect(index)
            if index == self._press_index and self._drag_mode == "vertical":
                rect = rect.move(0, int(self._vertical_offset * 0.65))
            selected = index == self._index
            killing = (
                index == self._press_index
                and self._drag_mode == "vertical"
                and self._vertical_offset < -40.0
            )
            draw_rounded(
                surface,
                rect.move(0, 12),
                fill=pal.shadow,
                radius=26,
                alpha=int(alpha * 0.45),
            )
            draw_rounded(
                surface,
                rect,
                fill=pal.surface_raised if selected else pal.surface,
                stroke=pal.danger if killing else (pal.accent if selected else pal.stroke_soft),
                width=2 if selected or killing else 1,
                radius=26,
                alpha=alpha,
            )
            if killing:
                blit_text(
                    surface,
                    theme.font(10, True),
                    "RELEASE TO CLOSE",
                    pal.danger,
                    center=(rect.centerx, rect.y + 30),
                    alpha=alpha,
                    tracking=1.4,
                )

            thumb = self._thumbs.get(app.app_id)
            thumb_rect = pygame.Rect(rect.x + 12, rect.y + 12, max(40, rect.w - 24), max(60, int(rect.h * 0.64)))
            if thumb is not None:
                scaled = pygame.transform.smoothscale(thumb, thumb_rect.size)
                surface.blit(scaled, thumb_rect.topleft)
                pygame.draw.rect(surface, pal.stroke_soft, thumb_rect, 1, border_radius=14)

            icon_rect = pygame.Rect(rect.x + 14, rect.bottom - 54, 26, 26)
            app.icon_draw(surface, icon_rect)
            name_font = theme.font(13, True)
            blit_text(
                surface,
                name_font,
                app.name,
                pal.text,
                midleft=(icon_rect.right + 10, icon_rect.centery - 7),
            )
            state = "running" if manager.active_id == app.app_id else "paused"
            blit_text(
                surface,
                theme.font(9),
                state.upper(),
                pal.accent_ink if selected else pal.text_muted,
                midleft=(icon_rect.right + 10, icon_rect.centery + 10),
                tracking=1.2,
            )

            close_rect = pygame.Rect(rect.right - 38, rect.y + 10, 28, 28)
            pygame.draw.circle(surface, with_alpha(pal.scrim, 110), close_rect.center, 14)
            pygame.draw.line(
                surface,
                pal.text,
                (close_rect.centerx - 5, close_rect.centery - 5),
                (close_rect.centerx + 5, close_rect.centery + 5),
                2,
            )
            pygame.draw.line(
                surface,
                pal.text,
                (close_rect.centerx + 5, close_rect.centery - 5),
                (close_rect.centerx - 5, close_rect.centery + 5),
                2,
            )

        blit_text(
            surface,
            theme.font(10),
            "drag to browse \u00b7 swipe up to close an app \u00b7 swipe down to dismiss",
            pal.text_muted,
            center=(self.size[0] // 2, min(self.size[1] - 32, first_card.bottom + 44)),
            alpha=alpha,
        )


class AppScene(Scene):
    """Host for every running application."""

    name = "app"

    def __init__(self, kernel) -> None:
        super().__init__(kernel)
        self.manager = self.registry.require("app_manager")
        self.overlay = RecentsOverlay(self.size)
        self.share_service = self.registry.get("share")
        self.share_sheet = ShareSheet(self.share_service, self.manager, self.theme) if self.share_service is not None else None
        if self.share_sheet is not None:
            self.manager.set_share_callback(self.share_sheet.open)
        self._leaving = False
        self.split_app_id: str | None = None
        self.split_ratio = 0.5
        self._split_dragging = False
        self._focus_pane = "bottom"
        self._divider_rect = pygame.Rect(0, 0, 0, 0)

    def on_canvas_resize(self, old: tuple[int, int], new: tuple[int, int]) -> None:
        self.overlay.resize(new)
        if self._split_apps() is not None:
            self._apply_split_viewports()

    def on_enter(self) -> None:
        super().on_enter()
        self.input.cancel()
        self.input.ui_capture_keys = True
        self._leaving = False
        self.overlay.close()
        if self.share_sheet is not None:
            self.share_sheet.close()

    def on_exit(self) -> None:
        super().on_exit()
        self.input.ui_capture_keys = False
        self._end_split()
        self.manager.pause_active()

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if self.share_sheet is not None and self.share_sheet.is_open:
            return self.share_sheet.handle(event)
        if self.overlay.is_open:
            return self.overlay.handle(event, self.manager, self._on_recents_empty, self.start_split)
        if event.kind is EventKind.KEY_DOWN and (event.mods & pygame.KMOD_CTRL) and event.key in (pygame.K_c, pygame.K_v):
            app = self.manager.active_app
            if app is not None:
                action = app.clipboard_copy if event.key == pygame.K_c else app.clipboard_paste
                if action():
                    return True
        split = self._split_apps()
        if split is not None and event.kind is EventKind.KEY_DOWN and event.key == pygame.K_ESCAPE:
            self._end_split()
            return True
        if split is not None:
            return self._handle_split(event, split)

        if event.kind is EventKind.KEY_DOWN and event.key == pygame.K_ESCAPE:
            self._leave()
            return True

        if event.kind is EventKind.TAP:
            if self._back_rect().collidepoint(event.pos):
                self._leave()
                return True
            if self._recents_rect().collidepoint(event.pos):
                self.overlay.open(self.manager)
                return True

        app = self.manager.active_app
        if app is None:
            return False
        return app.handle(event)

    # -- split view -----------------------------------------------------------
    def split_rects(self) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect]:
        """Return (top/left pane, bottom/right pane, divider)."""
        width, height = self.size
        gap = 10
        if current_layout.is_landscape:
            available = width - gap
            split = int(available * clamp_ratio(self.split_ratio))
            first = pygame.Rect(0, 0, split, height)
            second = pygame.Rect(split + gap, 0, width - split - gap, height)
            divider = pygame.Rect(split, 0, gap, height)
        else:
            available = height - gap
            split = int(available * clamp_ratio(self.split_ratio))
            first = pygame.Rect(0, 0, width, split)
            second = pygame.Rect(0, split + gap, width, height - split - gap)
            divider = pygame.Rect(0, split, width, gap)
        return first, second, divider

    def _split_apps(self) -> tuple["App", "App"] | None:
        if self.split_app_id is None:
            return None
        top = self.manager.recents_apps()
        focused = self.manager.active_app
        if focused is None:
            return None
        other = next((app for app in top if app.app_id == self.split_app_id), None)
        if other is None or other is focused:
            return None
        return other, focused

    def start_split(self, app_id: str) -> bool:
        active = self.manager.active_app
        if active is None or app_id == active.app_id:
            return False
        target = next((app for app in self.manager.recents_apps() if app.app_id == app_id), None)
        if target is None:
            return False
        self.split_app_id = app_id
        self.split_ratio = 0.5
        self._focus_pane = "bottom"
        self.overlay.close()
        self._apply_split_viewports()
        self.notifications.post("system", "Split view", "Drag the divider to resize · Esc exits.", self.theme.palette.accent, banner=False)
        return True

    def _apply_split_viewports(self) -> None:
        split = self._split_apps()
        if split is None:
            return
        first, second, _divider = self.split_rects()
        split[0].set_viewport(first)
        split[1].set_viewport(second)

    def _end_split(self) -> None:
        if self.split_app_id is None:
            return
        for app in self.manager.recents_apps():
            app.set_viewport(None)
        self.split_app_id = None
        self._split_dragging = False

    def _handle_split(self, event: PointerEvent, split) -> bool:
        first, second, divider = self.split_rects()
        self._divider_rect = divider
        if event.kind is EventKind.POINTER_DOWN:
            if divider.collidepoint(event.pos):
                self._split_dragging = True
                return True
            if first.collidepoint(event.pos):
                self._focus_pane = "top"
            elif second.collidepoint(event.pos):
                self._focus_pane = "bottom"
            else:
                return True
        if event.kind is EventKind.DRAG and self._split_dragging:
            if current_layout.is_landscape:
                self.split_ratio = event.pos[0] / max(1, self.size[0])
            else:
                self.split_ratio = event.pos[1] / max(1, self.size[1])
            if self.split_ratio <= 0.12 or self.split_ratio >= 0.88:
                self._end_split()
            else:
                self._apply_split_viewports()
            return True
        if event.kind in (EventKind.POINTER_UP, EventKind.DRAG_END):
            self._split_dragging = False
            return True
        pane = first if self._focus_pane == "top" else second
        app = split[0] if self._focus_pane == "top" else split[1]
        if event.kind is EventKind.KEY_DOWN:
            return app.handle(event)
        if not pane.collidepoint(event.pos):
            return True
        local = event
        local.pos = (event.pos[0] - pane.x, event.pos[1] - pane.y)
        local.start_pos = (event.start_pos[0] - pane.x, event.start_pos[1] - pane.y)
        return app.handle(local)

    @property
    def notifications(self):
        return self.registry.require("notifications")

    def _leave(self) -> None:
        if self._leaving:
            return
        self._leaving = True
        self.manager.background_active()
        self.go("home", fade=0.16)

    def _on_recents_empty(self) -> None:
        self.go("home", fade=0.16)

    # -- frame ----------------------------------------------------------------
    def update(self, dt: float) -> None:
        super().update(dt)
        self.overlay.update(dt, self.manager)

        split = self._split_apps()
        if split is not None:
            for app in split:
                app.update(dt)
                if app.ctx.close_requested:
                    app.ctx.close_requested = False
                    self._end_split()
            return

        app = self.manager.active_app
        if app is None:
            if not self._leaving and not self.overlay.is_open:
                self._leaving = True
                self.go("home", fade=0.16)
            return

        app.update(dt)
        if app.ctx.close_requested:
            app.ctx.close_requested = False
            self.manager.close(app.app_id)
            self._leaving = True
            self.go("home", fade=0.16)

    def draw(self, surface: pygame.Surface) -> None:
        pal: "Palette" = self.theme.palette
        split = self._split_apps()
        surface.fill(pal.bg_bottom)
        if split is not None:
            first, second, divider = self.split_rects()
            self._divider_rect = divider
            for pane, app, focused in ((first, split[0], self._focus_pane == "top"), (second, split[1], self._focus_pane == "bottom")):
                self._draw_pane(surface, pal, pane, app, focused)
            draw_rounded(surface, divider.inflate(-4, -4) if current_layout.is_landscape else divider.inflate(-4, -4), fill=pal.surface_raised, radius=5)
            pygame.draw.circle(surface, pal.accent, divider.center, 3)
        else:
            app = self.manager.active_app
            if app is not None:
                app.surface.fill(pal.bg_bottom)
                previous_clip = app.surface.get_clip()
                app.surface.set_clip(app.content)
                app.draw(app.surface)
                app.surface.set_clip(previous_clip)
                surface.blit(app.surface, (0, 0))
                self._draw_chrome(surface, pal, app.name)
            else:
                blit_text(
                    surface,
                    self.font(14),
                    "No running app",
                    pal.text_muted,
                    center=(current_layout.center_x, current_layout.center[1]),
                )

        self.overlay.draw(surface, self.theme, self.manager)
        if self.share_sheet is not None:
            self.share_sheet.draw(surface)

    def _draw_pane(self, surface, pal, pane: pygame.Rect, app: "App", focused: bool) -> None:
        app.surface.fill(pal.bg_bottom)
        clip = app.surface.get_clip()
        app.surface.set_clip(app.content)
        app.draw(app.surface)
        app.surface.set_clip(clip)
        surface.blit(app.surface, pane.topleft)
        header = pygame.Rect(pane.x, pane.y, pane.w, 44)
        draw_rounded(surface, header, fill=pal.surface, radius=12, alpha=240)
        pygame.draw.line(surface, pal.accent if focused else pal.stroke_soft, (pane.x, header.bottom), (pane.right, header.bottom), 2 if focused else 1)
        blit_text(surface, self.font(12, True), app.name, pal.text, midleft=(pane.x + 14, header.centery))
        blit_text(surface, self.font(8, True), "FOCUSED" if focused else "PAUSED INPUT", pal.accent_ink if focused else pal.text_muted, midright=(pane.right - 14, header.centery), tracking=1)
        pygame.draw.rect(surface, pal.stroke_soft, pane, 1)

    # -- chrome ---------------------------------------------------------------
    @staticmethod
    def _back_rect() -> pygame.Rect:
        return pygame.Rect(current_layout.margin - 4, current_layout.status_bar_height + 10, 44, 44)

    @staticmethod
    def _recents_rect() -> pygame.Rect:
        return pygame.Rect(current_layout.width - current_layout.margin - 44, current_layout.status_bar_height + 10, 48, 44)

    def _draw_chrome(self, surface: pygame.Surface, pal: "Palette", name: str) -> None:
        bar = pygame.Rect(0, STATUS_HEIGHT, self.size[0], APP_CHROME_HEIGHT - STATUS_HEIGHT)
        pygame.draw.rect(surface, pal.surface, bar)
        pygame.draw.line(
            surface,
            pal.stroke_soft,
            (0, APP_CHROME_HEIGHT - 1),
            (self.size[0], APP_CHROME_HEIGHT - 1),
            1,
        )

        back = self._back_rect()
        pygame.draw.circle(surface, pal.surface_raised, back.center, 21)
        pygame.draw.circle(surface, pal.stroke_soft, back.center, 21, 1)
        draw_chevron(surface, (back.centerx + 1, back.centery), pal.text, size=7, thickness=2, direction="left")

        blit_text(
            surface,
            self.font(16, True),
            name,
            pal.text,
            midleft=(back.right + 16, bar.centery),
        )

        recents = self._recents_rect()
        outer = pygame.Rect(recents.centerx - 10, recents.centery - 9, 17, 17)
        inner = pygame.Rect(outer.x + 5, outer.y + 5, 17, 17)
        draw_rounded(surface, outer, fill=None, stroke=pal.text_secondary, width=2, radius=4)
        draw_rounded(surface, inner, fill=pal.surface, stroke=pal.text, width=2, radius=4)
