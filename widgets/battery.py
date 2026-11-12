"""Battery widget: live level, charging state and drain estimate."""

from __future__ import annotations

import pygame

from ui.paint import blit_text, draw_rounded
from widgets.base import BaseWidget, WidgetContext


class BatteryWidget(BaseWidget):
    kind = "battery"
    label = "Battery"

    def draw(self, surface: pygame.Surface, rect: pygame.Rect, ctx: WidgetContext) -> None:
        self.panel(surface, rect, ctx, alpha=150)
        power = ctx.power
        percent = int(round(power.percent)) if power is not None else ctx.clock.battery_level
        charging = bool(power.charging) if power is not None else ctx.clock.charging
        color = ctx.palette.danger if percent <= 15 else (ctx.palette.warning if percent <= 30 else ctx.palette.success)
        self.title(surface, rect, ctx, "Battery", icon_color=color)
        font = ctx.mono(26, True)
        blit_text(surface, font, f"{percent}%", ctx.palette.text, topleft=(rect.x + 16, rect.y + 30))
        body = pygame.Rect(rect.x + 16, rect.bottom - 34, max(30, rect.w - 54), 12)
        draw_rounded(surface, body, fill=ctx.palette.track, radius=4)
        draw_rounded(surface, pygame.Rect(body.x, body.y, max(2, int(body.w * percent / 100)), body.h), fill=color, radius=4)
        pygame.draw.rect(surface, ctx.palette.text_muted, (body.right + 3, body.centery - 4, 3, 8), border_radius=1)
        if charging:
            blit_text(surface, ctx.font(8, True), "CHARGING", ctx.palette.success, topright=(rect.right - 14, rect.y + 14), tracking=1)
