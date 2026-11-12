"""Calendar widget: compact month view with today marked."""

from __future__ import annotations

import calendar as month_calendar

import pygame

from ui.paint import blit_text
from widgets.base import BaseWidget, WidgetContext


class CalendarWidget(BaseWidget):
    kind = "calendar"
    label = "Calendar"

    def draw(self, surface: pygame.Surface, rect: pygame.Rect, ctx: WidgetContext) -> None:
        self.panel(surface, rect, ctx, alpha=150)
        today = ctx.clock.simulated_time.date()
        self.title(surface, rect, ctx, today.strftime("%B %Y"), icon_color=ctx.palette.accent)
        weeks = month_calendar.Calendar(firstweekday=0).monthdayscalendar(today.year, today.month)
        left = rect.x + 14
        top = rect.y + 34
        cell_w = max(1, (rect.w - 28) / 7)
        cell_h = max(8, (rect.h - 46) / max(1, len(weeks)))
        font = ctx.font(8, True)
        for column, name in enumerate(("M", "T", "W", "T", "F", "S", "S")):
            blit_text(surface, font, name, ctx.palette.text_muted, center=(int(left + (column + 0.5) * cell_w), int(top + 2)))
        for row, week in enumerate(weeks):
            for column, day in enumerate(week):
                if day == 0:
                    continue
                center = (int(left + (column + 0.5) * cell_w), int(top + 14 + (row + 0.5) * cell_h))
                if day == today.day:
                    pygame.draw.circle(surface, ctx.palette.accent, center, max(7, int(cell_h * 0.32)))
                    color = ctx.palette.on_accent
                else:
                    color = ctx.palette.text_secondary
                blit_text(surface, font, str(day), color, center=center)
