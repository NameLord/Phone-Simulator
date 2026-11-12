"""Music widget: now-playing track, progress and transport state."""

from __future__ import annotations

import math

import pygame

from ui.paint import blit_text, draw_rounded, fit_text
from widgets.base import BaseWidget, WidgetContext


class MusicWidget(BaseWidget):
    kind = "music"
    label = "Music"

    def draw(self, surface: pygame.Surface, rect: pygame.Rect, ctx: WidgetContext) -> None:
        self.panel(surface, rect, ctx, alpha=165)
        audio = ctx.audio
        if audio is None or not audio.has_track:
            self.title(surface, rect, ctx, "Music", icon_color=ctx.palette.accent)
            blit_text(surface, ctx.font(12, True), "Nothing playing", ctx.palette.text_secondary, topleft=(rect.x + 16, rect.y + 40))
            blit_text(surface, ctx.font(9), "Open Music to start a track", ctx.palette.text_muted, topleft=(rect.x + 16, rect.y + 62))
            return
        track = audio.current_track
        self.title(surface, rect, ctx, "Now playing", icon_color=ctx.palette.accent)
        art = pygame.Rect(rect.x + 14, rect.y + 30, min(rect.h - 44, 64), min(rect.h - 44, 64))
        draw_rounded(surface, art, fill=ctx.palette.accent_soft, radius=14)
        center = art.center
        for index in range(3):
            bar_h = 10 + int(10 * abs(math.sin(ctx.clock.uptime * 2.0 + index)))
            pygame.draw.rect(surface, ctx.palette.accent, (center[0] - 10 + index * 8, center[1] - bar_h // 2, 4, bar_h), border_radius=2)
        text_x = art.right + 12
        title_font = ctx.font(12, True)
        blit_text(surface, title_font, fit_text(title_font, track.title, rect.right - text_x - 14), ctx.palette.text, topleft=(text_x, rect.y + 30))
        artist_font = ctx.font(9)
        blit_text(surface, artist_font, fit_text(artist_font, track.artist, rect.right - text_x - 14), ctx.palette.text_secondary, topleft=(text_x, rect.y + 50))
        fraction = audio.position / max(0.01, audio.duration)
        bar = pygame.Rect(text_x, rect.y + 70, max(20, rect.right - text_x - 18), 4)
        draw_rounded(surface, bar, fill=ctx.palette.track, radius=2)
        draw_rounded(surface, pygame.Rect(bar.x, bar.y, max(2, int(bar.w * fraction)), bar.h), fill=ctx.palette.accent, radius=2)
        if rect.h > 120:
            state = "PLAYING" if audio.is_playing else "PAUSED"
            blit_text(surface, ctx.font(8, True), state, ctx.palette.text_muted, bottomleft=(text_x, rect.bottom - 10), tracking=1)
