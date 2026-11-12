"""Notification queue, pull-down shade and transient banner.

The shade is a first-class input consumer: the kernel offers every event to
:meth:`NotificationCenter.handle` before the active scene sees it, so a pull
from the top edge works regardless of which screen is showing.

Animation is dt driven; ``shade`` is a 0..1 progress value eased in the draw
pass, and pull gestures write to that same value directly so the panel tracks
the finger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Sequence

import pygame

from core.display import PORTRAIT_SIZE
from core.input import Direction, EventKind, PointerEvent
from core.scene import clamp, ease_in_out_cubic, ease_out_cubic, lerp
from ui.paint import (
    blit_text,
    draw_bell,
    draw_rounded,
    fit_text,
    mix,
    with_alpha,
)

if TYPE_CHECKING:
    from core.clock import DeviceClock
    from core.fx import Effects
    from ui.theme import Palette, Theme

RGB = tuple[int, int, int]

SHADE_HEIGHT = 660
GRAB_HEIGHT = 34
ROW_TOP = 242
DIVIDER_Y = 226
ROW_HEIGHT = 86
ROW_GAP = 10
ROW_X = 22
ROW_WIDTH = 496
MAX_ROWS = 4
BANNER_LIFE = 3.6
BANNER_WIDTH = 496
BANNER_HEIGHT = 84
BANNER_TOP = 66


@dataclass(slots=True)
class Notification:
    """One queued notification."""

    uid: int
    app_id: str
    title: str
    body: str
    timestamp: float
    icon_color: RGB
    read: bool = False
    extra: dict[str, str] = field(default_factory=dict)
    actions: tuple[dict, ...] = field(default_factory=tuple)

    def time_label(self) -> str:
        try:
            return datetime.fromtimestamp(self.timestamp).strftime("%H:%M")
        except (OverflowError, OSError, ValueError):
            return "--:--"


class NotificationCenter:
    """Owns the queue, unread count, shade and banner."""

    def __init__(
        self,
        clock: "DeviceClock",
        *,
        banner_enabled: bool = True,
        fx: "Effects | None" = None,
    ) -> None:
        self._clock = clock
        self._fx = fx
        self._items: list[Notification] = []
        self._next_uid = 1
        self._banner_enabled = banner_enabled
        self._dnd = False
        self._scrim: pygame.Surface | None = None
        self._panel_layer: pygame.Surface | None = None
        self._width, self._height = PORTRAIT_SIZE
        self._shade_height = SHADE_HEIGHT

        self._value = 0.0
        self._target = 0.0
        self._pulling = False
        self._pull_start = 0.0
        self._open_press_y: float | None = None

        self._banner: Notification | None = None
        self._banner_t = 0.0
        self._media = None
        self._on_post: Callable[[str], None] | None = None
        self._quick_panel = None
        self._quick_page = False
        self._action_handler: Callable[[Notification, str, str], None] | None = None
        self._reply_uid: int | None = None
        self._reply_text = ""
        self._action_rects: dict[int, list[tuple[dict, pygame.Rect]]] = {}
        self._dismiss_uid: int | None = None
        self._dismiss_dx = 0.0

    # -- bindings -------------------------------------------------------------
    def set_media_controller(self, media) -> None:
        """Bind the audio service that powers the shade mini-player."""
        self._media = media

    def set_quick_panel(self, panel) -> None:
        self._quick_panel = panel

    def set_action_handler(self, callback: Callable[[Notification, str, str], None] | None) -> None:
        self._action_handler = callback

    @property
    def quick_page(self) -> bool:
        return self._quick_page

    def show_quick(self, visible: bool = True) -> None:
        self._quick_page = bool(visible)
        if visible:
            self.open_shade()

    def set_post_callback(self, callback: Callable[[str], None] | None) -> None:
        """Called with a cue name when a user-facing notification arrives."""
        self._on_post = callback

    def mark_app_read(self, app_id: str) -> None:
        for item in self._items:
            if item.app_id == app_id:
                item.read = True

    # -- viewport -------------------------------------------------------------
    def set_viewport(self, size: tuple[int, int]) -> None:
        """Update shade/banner geometry after orientation or resize."""
        self._width = max(1, int(size[0]))
        self._height = max(1, int(size[1]))
        if self._height < 760:
            self._shade_height = max(280, self._height - 12)
        else:
            self._shade_height = min(SHADE_HEIGHT, self._height - 24)
        self._scrim = None
        self._panel_layer = None

    # -- queue ---------------------------------------------------------------
    @property
    def items(self) -> Sequence[Notification]:
        return tuple(self._items)

    @property
    def unread_count(self) -> int:
        return sum(0 if item.read else 1 for item in self._items)

    @property
    def count(self) -> int:
        return len(self._items)

    def set_dnd(self, enabled: bool) -> None:
        """Silence banners while keeping notifications in the shade."""
        self._dnd = bool(enabled)
        if self._dnd:
            self._banner = None

    @property
    def dnd(self) -> bool:
        return self._dnd

    def post(
        self,
        app_id: str,
        title: str,
        body: str,
        icon_color: RGB,
        *,
        read: bool = False,
        banner: bool = True,
        extra: dict[str, str] | None = None,
        actions: Sequence[dict] | None = None,
    ) -> Notification:
        notification = Notification(
            uid=self._next_uid,
            app_id=str(app_id),
            title=str(title),
            body=str(body),
            timestamp=self._clock.simulated_time.timestamp(),
            icon_color=(int(icon_color[0]), int(icon_color[1]), int(icon_color[2])),
            read=bool(read),
            extra=dict(extra or {}),
            actions=tuple(dict(action) for action in (actions or ()))[:2],
        )
        self._next_uid += 1
        self._items.insert(0, notification)
        if len(self._items) > 40:
            self._items = self._items[:40]
        if banner and self._banner_enabled and not self._dnd and self._value < 0.35:
            self._banner = notification
            self._banner_t = 0.0
        if self._on_post is not None and banner and notification.app_id not in ("system", "clock"):
            try:
                self._on_post("notification")
            except Exception:  # noqa: BLE001 - audio must never break notifications
                pass
        return notification

    def dismiss(self, uid: int) -> None:
        self._items = [item for item in self._items if item.uid != uid]
        if self._banner is not None and self._banner.uid == uid:
            self._banner = None
        if self._reply_uid == uid:
            self._reply_uid = None
            self._reply_text = ""

    def mark_read(self, uid: int) -> None:
        for item in self._items:
            if item.uid == uid:
                item.read = True

    def mark_all_read(self) -> None:
        for item in self._items:
            item.read = True

    def clear(self) -> None:
        self._items.clear()
        self._banner = None

    # -- shade state ---------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._target > 0.5

    @property
    def shade_progress(self) -> float:
        return self._value

    def open_shade(self) -> None:
        self._target = 1.0
        self._pulling = False

    def close_shade(self) -> None:
        self._target = 0.0
        self._pulling = False

    def toggle_shade(self) -> None:
        if self.is_open or self._value > 0.4:
            self.close_shade()
        else:
            self.open_shade()

    def reset_view(self) -> None:
        """Hide the shade and banner instantly (used on scene changes)."""
        self._value = 0.0
        self._target = 0.0
        self._pulling = False
        self._open_press_y = None
        self._banner = None
        self._banner_t = 0.0
        self._quick_page = False
        self._reply_uid = None
        self._reply_text = ""

    def update(self, dt: float) -> None:
        dt = max(0.0, float(dt))
        if self._pulling or self._open_press_y is not None:
            # Gesture owns the value; only ease the residual toward the target.
            if self._open_press_y is not None:
                self._value += (self._target - self._value) * min(1.0, dt * 20.0)
        else:
            self._value += (self._target - self._value) * min(1.0, dt * 16.0)
            if abs(self._target - self._value) < 0.0015:
                self._value = self._target

        if self._value > 0.92 and self._items:
            self.mark_all_read()

        if self._banner is not None:
            self._banner_t += dt
            if self._banner_t >= BANNER_LIFE:
                self._banner = None

    # -- input ---------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        # A pull that started at the top edge must stay with the closed-state
        # handler until it ends, even though it has already opened the panel.
        if self._pulling:
            return self._handle_closed(event)
        if self._value > 0.02 or self._target > 0.02:
            return self._handle_open(event)
        return self._handle_closed(event)

    def _handle_closed(self, event: PointerEvent) -> bool:
        banner_rect = self._banner_rect()

        if event.kind is EventKind.TAP and self._banner is not None:
            if banner_rect.collidepoint(event.pos):
                if self._banner is not None:
                    self.dismiss(self._banner.uid)
                return True

        if event.kind is EventKind.POINTER_DOWN:
            if event.pos[1] <= GRAB_HEIGHT:
                self._pulling = True
                self._pull_start = event.pos[1]
                return True
            return False

        if not self._pulling:
            return False

        if event.kind is EventKind.DRAG:
            delta = max(0.0, event.pos[1] - self._pull_start)
            self._value = clamp(delta / self._shade_height, 0.0, 1.0)
            self._target = self._value
            return True

        if event.kind in (EventKind.DRAG_END, EventKind.POINTER_UP):
            flick = max(0.0, event.velocity[1])
            self._pulling = False
            self._target = 1.0 if (self._value > 0.22 or flick > 420.0) else 0.0
            return True

        if event.kind is EventKind.TAP:
            self._pulling = False
            self._target = 1.0
            return True

        if event.kind is EventKind.SWIPE and event.direction is Direction.DOWN:
            self._pulling = False
            self._target = 1.0
            return True

        return False

    def _handle_open(self, event: PointerEvent) -> bool:
        panel_y = self._panel_y()

        if self._quick_page:
            if event.kind is EventKind.KEY_DOWN and event.key == pygame.K_ESCAPE:
                self._quick_page = False
                return True
            if self._quick_panel is not None and self._quick_panel.handle(event, panel_y):
                return True
            if event.kind is EventKind.TAP and self._quick_button_rect(panel_y).collidepoint(event.pos):
                self._quick_page = False
                return True
            if event.kind is EventKind.SWIPE and event.direction is Direction.UP:
                self._target = 0.0
                return True
            return True

        if event.kind is EventKind.KEY_DOWN:
            if self._reply_uid is not None:
                if event.key == pygame.K_ESCAPE:
                    self._reply_uid = None
                    self._reply_text = ""
                elif event.key == pygame.K_BACKSPACE:
                    self._reply_text = self._reply_text[:-1]
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self._send_reply()
                elif event.text and event.text.isprintable():
                    self._reply_text += event.text[:120]
                return True
            if event.key == pygame.K_ESCAPE:
                self.close_shade()
            elif event.key == pygame.K_DELETE:
                self.clear()
            return True

        if event.kind is EventKind.POINTER_DOWN:
            self._open_press_y = event.pos[1]
            index = self._row_at(event.pos, panel_y)
            visible = self._visible()
            self._dismiss_uid = visible[index].uid if index is not None and index < len(visible) else None
            self._dismiss_dx = 0.0
            return True

        if event.kind is EventKind.DRAG:
            if self._open_press_y is not None:
                dx = event.pos[0] - (self._width / 2)
                if self._dismiss_uid is not None and abs(event.delta[0]) > 2.0 and abs(event.delta[1]) < abs(event.delta[0]):
                    self._dismiss_dx += event.delta[0]
                    return True
                delta = self._open_press_y - event.pos[1]
                if delta > 8.0:
                    self._value = clamp(1.0 - delta / self._shade_height, 0.0, 1.0)
                    self._target = self._value
                elif delta < -46.0 and self._value > 0.86:
                    self._quick_page = True
                    self._open_press_y = None
            return True

        if event.kind in (EventKind.DRAG_END, EventKind.POINTER_UP):
            if self._dismiss_uid is not None and abs(self._dismiss_dx) > 88.0:
                self.dismiss(self._dismiss_uid)
                self._dismiss_uid = None
                self._dismiss_dx = 0.0
                self._open_press_y = None
                return True
            self._dismiss_uid = None
            self._dismiss_dx = 0.0
            if self._value < 0.78:
                self._target = 0.0
            else:
                self._target = 1.0
            self._open_press_y = None
            return True

        if event.kind is EventKind.SWIPE:
            if event.direction is Direction.UP:
                self._target = 0.0
            elif event.direction is Direction.DOWN:
                self._target = 1.0
            self._open_press_y = None
            return True

        if event.kind is EventKind.TAP:
            if self._quick_button_rect(panel_y).collidepoint(event.pos):
                self._quick_page = True
                return True
            clear_rect = self._clear_rect(panel_y)
            if self._items and clear_rect.collidepoint(event.pos):
                self.clear()
                return True
            if self._reply_uid is not None and self._reply_rect(panel_y).collidepoint(event.pos):
                return True
            for uid, actions in self._action_rects.items():
                for action, rect in actions:
                    if rect.collidepoint(event.pos):
                        item = next((entry for entry in self._items if entry.uid == uid), None)
                        if item is None:
                            return True
                        if action.get("reply"):
                            self._reply_uid = uid
                            self._reply_text = ""
                            self.mark_read(uid)
                        else:
                            self._run_action(item, str(action.get("id", "")), "")
                        return True
            if self._media is not None and getattr(self._media, "has_track", False):
                if self._media_play_rect(panel_y).collidepoint(event.pos):
                    self._media.toggle_play()
                    return True
                if self._media_next_rect(panel_y).collidepoint(event.pos):
                    self._media.next_track()
                    return True
            index = self._row_at(event.pos, panel_y)
            if index is not None:
                visible = self._visible()
                if 0 <= index < len(visible):
                    item = visible[index]
                    self.mark_read(item.uid)
                    self.dismiss(item.uid)
                return True
            if event.pos[1] > panel_y + self._shade_height:
                self.close_shade()
            return True

        return True

    # -- layout ---------------------------------------------------------------
    def _panel_y(self) -> int:
        eased = ease_in_out_cubic(clamp(self._value, 0.0, 1.0))
        return int(round(-self._shade_height * (1.0 - eased)))

    def _visible(self) -> list[Notification]:
        rows = max(1, (self._shade_height - 242) // (ROW_HEIGHT + ROW_GAP) + 1)
        return self._items[: min(MAX_ROWS, rows)]

    def _row_rect(self, index: int, panel_y: int) -> pygame.Rect:
        return pygame.Rect(
            ROW_X,
            panel_y + ROW_TOP + index * (ROW_HEIGHT + ROW_GAP),
            max(120, self._width - ROW_X * 2),
            ROW_HEIGHT,
        )

    def _media_rect(self, panel_y: int) -> pygame.Rect:
        return pygame.Rect(24, panel_y + 156, max(120, self._width - 48), 58)

    def _media_play_rect(self, panel_y: int) -> pygame.Rect:
        return pygame.Rect(self._width - 108, panel_y + 166, 36, 36)

    def _media_next_rect(self, panel_y: int) -> pygame.Rect:
        return pygame.Rect(self._width - 62, panel_y + 166, 36, 36)

    def _row_at(self, pos: tuple[float, float], panel_y: int) -> int | None:
        for index in range(len(self._visible())):
            if self._row_rect(index, panel_y).collidepoint(pos):
                return index
        return None

    def _reply_rect(self, panel_y: int) -> pygame.Rect:
        return pygame.Rect(ROW_X + 12, panel_y + ROW_TOP + 44, self._width - ROW_X * 2 - 24, 34)

    def _send_reply(self) -> None:
        if self._reply_uid is None:
            return
        item = next((entry for entry in self._items if entry.uid == self._reply_uid), None)
        text = self._reply_text.strip()
        if item is not None and text:
            self._run_action(item, "reply", text)
        self.dismiss(self._reply_uid)
        self._reply_uid = None
        self._reply_text = ""

    def _run_action(self, item: Notification, action_id: str, text: str) -> None:
        self.mark_read(item.uid)
        if self._action_handler is not None:
            try:
                self._action_handler(item, action_id, text)
                return
            except Exception:  # noqa: BLE001 - actions must not break the shade
                import logging

                logging.getLogger("phoneos.notifications").exception("notification action failed")
        if action_id not in ("reply", "mark_read"):
            self.dismiss(item.uid)

    def _clear_rect(self, panel_y: int) -> pygame.Rect:
        width = min(136, max(88, self._width // 4))
        return pygame.Rect(self._width - 24 - width, panel_y + 74, width, 44)

    def _quick_button_rect(self, panel_y: int) -> pygame.Rect:
        return pygame.Rect(24, panel_y + 20, 104, 30)

    def _banner_rect(self) -> pygame.Rect:
        width = min(BANNER_WIDTH, self._width - 44)
        return pygame.Rect((self._width - width) // 2, BANNER_TOP, width, BANNER_HEIGHT)

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface, theme: "Theme") -> None:
        if self._value > 0.002:
            self._draw_shade(surface, theme)
        self._draw_banner(surface, theme)

    def _draw_shade(self, surface: pygame.Surface, theme: "Theme") -> None:
        pal: "Palette" = theme.palette
        value = clamp(self._value, 0.0, 1.0)
        panel_y = self._panel_y()

        # Blur what is underneath before tinting it, so the shade reads as
        # frosted glass. The FX cache refreshes at ~12 fps, not every frame.
        if self._fx is not None and value > 0.03:
            surface.blit(self._fx.blur(surface, 12, key="shade", min_interval=0.08), (0, 0))

        if self._scrim is None or self._scrim.get_size() != surface.get_size():
            self._scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        self._scrim.fill(with_alpha(pal.scrim, int(165 * value)))
        surface.blit(self._scrim, (0, 0))

        if self._panel_layer is None or self._panel_layer.get_size() != (self._width, self._shade_height):
            self._panel_layer = pygame.Surface((self._width, self._shade_height), pygame.SRCALPHA)
        self._panel_layer.fill((0, 0, 0, 0))
        pygame.draw.rect(
            self._panel_layer,
            (*pal.shade, 214),
            self._panel_layer.get_rect(),
            border_bottom_left_radius=34,
            border_bottom_right_radius=34,
        )
        surface.blit(self._panel_layer, (0, panel_y))
        pygame.draw.line(
            surface,
            pal.stroke_soft,
            (0, panel_y + self._shade_height),
            (self._width, panel_y + self._shade_height),
            1,
        )

        content_alpha = int(255 * clamp(value * 1.6, 0.0, 1.0))

        clock_font = theme.font(54, True)
        blit_text(
            surface,
            clock_font,
            self._clock.time_string(),
            pal.text,
            topleft=(30, panel_y + 62),
            alpha=content_alpha,
        )
        date_rect = blit_text(
            surface,
            theme.font(15),
            self._clock.date_string().upper(),
            pal.text_secondary,
            topleft=(32, panel_y + 128),
            alpha=content_alpha,
            tracking=2.2,
        )
        quick_rect = self._quick_button_rect(panel_y)
        draw_rounded(
            surface,
            quick_rect,
            fill=pal.surface_alt if self._quick_page else pal.accent_soft,
            stroke=pal.accent if self._quick_page else pal.stroke_soft,
            radius=15,
            alpha=content_alpha,
        )
        blit_text(
            surface,
            theme.font(10, True),
            "QUICK" if not self._quick_page else "NOTIFS",
            pal.accent_ink if self._quick_page else pal.text_secondary,
            center=quick_rect.center,
            alpha=content_alpha,
            tracking=1,
        )
        if self._quick_page:
            body = pygame.Rect(24, panel_y + 58, self._width - 48, self._shade_height - 86)
            if self._quick_panel is not None:
                self._quick_panel.draw(surface, body, content_alpha)
            grabber = pygame.Rect(0, panel_y + self._shade_height - 22, 120, 5)
            grabber.centerx = self._width // 2
            pygame.draw.rect(surface, pal.home_indicator, grabber, border_radius=2)
            return

        if self._items:
            blit_text(
                surface,
                theme.font(15, True),
                "Clear all",
                pal.accent_ink,
                center=self._clear_rect(panel_y).center,
                alpha=content_alpha,
            )

        if self._media is not None and getattr(self._media, "has_track", False):
            self._draw_media(surface, theme, panel_y, content_alpha)
        pygame.draw.line(
            surface,
            pal.stroke_soft,
            (24, panel_y + DIVIDER_Y),
            (self._width - 24, panel_y + DIVIDER_Y),
            1,
        )

        visible = self._visible()
        if not visible:
            draw_bell(surface, (self._width // 2, panel_y + 280), pal.text_muted, scale=1.6)
            blit_text(
                surface,
                theme.font(17),
                "No notifications",
                pal.text_muted,
                center=(self._width // 2, panel_y + 348),
                alpha=content_alpha,
            )
        else:
            for index, item in enumerate(visible):
                row = self._row_rect(index, panel_y)
                if item.uid == self._dismiss_uid and self._dismiss_dx:
                    row = row.move(int(self._dismiss_dx), 0)
                self._draw_row(surface, theme, item, row, content_alpha)

            extra = len(self._items) - len(visible)
            if extra > 0:
                blit_text(
                    surface,
                    theme.font(13),
                    f"+{extra} more",
                    pal.text_muted,
                    center=(self._width // 2, panel_y + ROW_TOP + len(visible) * (ROW_HEIGHT + ROW_GAP) + 6),
                    alpha=content_alpha,
                )

        grabber = pygame.Rect(0, panel_y + self._shade_height - 22, 120, 5)
        grabber.centerx = self._width // 2
        pygame.draw.rect(surface, pal.home_indicator, grabber, border_radius=2)

    def _draw_media(self, surface: pygame.Surface, theme: "Theme", panel_y: int, alpha: int) -> None:
        pal = theme.palette
        media = self._media
        rect = self._media_rect(panel_y)
        draw_rounded(surface, rect, fill=pal.surface, stroke=pal.stroke_soft, radius=16, alpha=alpha)
        art = pygame.Rect(rect.x + 10, rect.y + 8, 42, 42)
        color = pal.accent
        draw_rounded(surface, art, fill=mix(pal.surface, color, 0.35), radius=10, alpha=alpha)
        pygame.draw.circle(surface, with_alpha(pal.on_accent, alpha), art.center, 5)
        title_font = theme.font(12, True)
        artist_font = theme.font(9)
        blit_text(
            surface,
            title_font,
            fit_text(title_font, getattr(media, "current_track", None).title if getattr(media, "current_track", None) else "Music", rect.w - 170),
            pal.text,
            topleft=(art.right + 10, rect.y + 10),
            alpha=alpha,
        )
        blit_text(
            surface,
            artist_font,
            "PROCEDURAL · NOW PLAYING",
            pal.text_muted,
            topleft=(art.right + 10, rect.y + 28),
            alpha=alpha,
            tracking=0.8,
        )
        duration = max(0.01, float(getattr(media, "duration", 1.0)))
        progress = max(0.0, min(1.0, float(getattr(media, "position", 0.0)) / duration))
        track = pygame.Rect(art.right + 10, rect.bottom - 14, rect.w - 160, 3)
        pygame.draw.rect(surface, pal.track, track, border_radius=1)
        pygame.draw.rect(surface, pal.accent, pygame.Rect(track.x, track.y, max(1, int(track.w * progress)), track.h), border_radius=1)
        play = self._media_play_rect(panel_y)
        pygame.draw.circle(surface, pal.accent, play.center, 18)
        if getattr(media, "is_playing", False):
            pygame.draw.rect(surface, pal.on_accent, (play.centerx - 5, play.centery - 7, 3, 14), border_radius=1)
            pygame.draw.rect(surface, pal.on_accent, (play.centerx + 2, play.centery - 7, 3, 14), border_radius=1)
        else:
            pygame.draw.polygon(surface, pal.on_accent, [(play.centerx - 4, play.centery - 7), (play.centerx + 7, play.centery), (play.centerx - 4, play.centery + 7)])
        nxt = self._media_next_rect(panel_y)
        pygame.draw.polygon(surface, pal.text_secondary, [(nxt.centerx - 7, nxt.centery - 7), (nxt.centerx + 1, nxt.centery), (nxt.centerx - 7, nxt.centery + 7)])
        pygame.draw.rect(surface, pal.text_secondary, (nxt.centerx + 2, nxt.centery - 7, 3, 14), border_radius=1)

    def _draw_row(
        self,
        surface: pygame.Surface,
        theme: "Theme",
        item: Notification,
        rect: pygame.Rect,
        alpha: int,
    ) -> None:
        pal = theme.palette
        row_alpha = 255 if alpha >= 254 else int(alpha * 0.96)
        draw_rounded(
            surface,
            rect,
            fill=pal.surface,
            stroke=pal.stroke_soft,
            radius=22,
            alpha=row_alpha,
        )

        icon_rect = pygame.Rect(rect.x + 16, rect.y + 16, 44, 44)
        icon_bg = mix(pal.surface, item.icon_color, 0.28)
        draw_rounded(surface, icon_rect, fill=icon_bg, radius=14, alpha=row_alpha)
        dot = mix(pal.surface, item.icon_color, clamp(alpha / 255.0, 0.0, 1.0))
        pygame.draw.circle(surface, dot, (icon_rect.centerx, icon_rect.centery), 8)

        title_font = theme.font(15, True)
        body_font = theme.font(13)
        text_x = icon_rect.right + 14
        text_width = rect.right - text_x - 58
        blit_text(
            surface,
            title_font,
            fit_text(title_font, item.title, text_width),
            pal.text,
            topleft=(text_x, rect.y + 15),
            alpha=alpha,
        )
        blit_text(
            surface,
            body_font,
            fit_text(body_font, item.body, text_width),
            pal.text_secondary,
            topleft=(text_x, rect.y + 34),
            alpha=int(alpha * 0.92),
        )
        self._action_rects.pop(item.uid, None)
        if self._reply_uid == item.uid:
            field = pygame.Rect(text_x, rect.y + 50, rect.w - (text_x - rect.x) - 22, 28)
            draw_rounded(surface, field, fill=pal.surface_alt, stroke=pal.accent, radius=10, alpha=alpha)
            font = theme.font(11)
            label = self._reply_text or "Reply\u2026"
            blit_text(surface, font, fit_text(font, label, field.w - 20), pal.text if self._reply_text else pal.text_muted, midleft=(field.x + 10, field.centery), alpha=alpha)
            if int(self._clock.uptime * 2) % 2 == 0:
                caret_x = min(field.right - 8, field.x + 12 + font.size(self._reply_text)[0])
                pygame.draw.line(surface, pal.accent, (caret_x, field.y + 7), (caret_x, field.bottom - 7), 1)
        elif item.actions:
            buttons = []
            width = max(64, (rect.w - (text_x - rect.x) - 22 - 8 * (len(item.actions) - 1)) // max(1, len(item.actions)))
            y = rect.y + 58
            for index, action in enumerate(item.actions):
                action_rect = pygame.Rect(text_x + index * (width + 8), y, width, 22)
                primary = bool(action.get("primary", index == 0))
                draw_rounded(surface, action_rect, fill=pal.accent_soft if primary else pal.surface_alt, stroke=pal.accent if primary else pal.stroke_soft, radius=10, alpha=alpha)
                blit_text(surface, theme.font(10, True), str(action.get("label", "Action")), pal.accent_ink if primary else pal.text_secondary, center=action_rect.center, alpha=alpha)
                buttons.append((dict(action), action_rect))
            self._action_rects[item.uid] = buttons
        else:
            blit_text(
                surface,
                theme.font(12),
                item.app_id.upper(),
                pal.text_muted,
                topleft=(text_x, rect.y + 62),
                alpha=int(alpha * 0.85),
                tracking=1.2,
            )
        blit_text(
            surface,
            theme.font(12),
            item.time_label(),
            pal.text_muted,
            topleft=(rect.right - 52, rect.y + 17),
            alpha=int(alpha * 0.9),
        )
        if not item.read:
            pygame.draw.circle(
                surface,
                with_alpha(pal.accent, alpha),
                (rect.right - 20, rect.y + 50),
                4,
            )

    def _draw_banner(self, surface: pygame.Surface, theme: "Theme") -> None:
        if self._banner is None or self._value > 0.35:
            return
        item = self._banner
        appear = ease_out_cubic(clamp(self._banner_t / 0.3, 0.0, 1.0))
        leave = clamp((BANNER_LIFE - self._banner_t) / 0.3, 0.0, 1.0)
        progress = min(appear, leave)
        if progress <= 0.01:
            return

        pal = theme.palette
        width = min(BANNER_WIDTH, self._width - 44)
        height = BANNER_HEIGHT
        x = (self._width - width) // 2
        y = int(round(lerp(-height - 12.0, BANNER_TOP, appear)))
        alpha = int(255 * progress)

        rect = pygame.Rect(x, y, width, height)
        shadow = rect.move(0, 10)
        draw_rounded(surface, shadow, fill=pal.shadow, radius=24, alpha=int(alpha * 0.35))
        draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=24, alpha=alpha)

        icon_rect = pygame.Rect(rect.x + 16, rect.y + 20, 44, 44)
        icon_bg = mix(pal.surface_raised, item.icon_color, 0.28)
        draw_rounded(surface, icon_rect, fill=icon_bg, radius=14, alpha=alpha)
        dot = mix(pal.surface_raised, item.icon_color, clamp(alpha / 255.0, 0.0, 1.0))
        pygame.draw.circle(surface, dot, icon_rect.center, 8)

        title_font = theme.font(15, True)
        body_font = theme.font(13)
        text_x = icon_rect.right + 14
        text_width = rect.right - text_x - 20
        blit_text(
            surface,
            title_font,
            fit_text(title_font, item.title, text_width),
            pal.text,
            topleft=(text_x, rect.y + 18),
            alpha=alpha,
        )
        blit_text(
            surface,
            body_font,
            fit_text(body_font, item.body, text_width),
            pal.text_secondary,
            topleft=(text_x, rect.y + 42),
            alpha=alpha,
        )
        blit_text(
            surface,
            theme.font(11),
            f"{item.app_id.upper()}  \u00b7  NOW",
            pal.text_muted,
            topleft=(text_x, rect.y + 62),
            alpha=int(alpha * 0.85),
            tracking=1.0,
        )

    def __repr__(self) -> str:
        return (
            f"NotificationCenter(count={len(self._items)}, unread={self.unread_count}, "
            f"shade={self._value:.2f})"
        )
