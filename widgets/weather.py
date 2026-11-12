"""Weather widget backed by the deterministic Weather app forecast model."""

from __future__ import annotations

import pygame

from apps.weather import CONDITION_LABELS, build_forecast, condition_icon
from ui.paint import blit_text, fit_text
from widgets.base import BaseWidget, WidgetContext


class WeatherWidget(BaseWidget):
    kind = "weather"
    label = "Weather"

    def __init__(self, options: dict | None = None) -> None:
        super().__init__(options)
        self._key: tuple[object, ...] | None = None
        self._forecast = None

    def draw(self, surface: pygame.Surface, rect: pygame.Rect, ctx: WidgetContext) -> None:
        self.panel(surface, rect, ctx, alpha=150)
        city = ctx.settings.get_str("weather.city", "Lisbon")
        now = ctx.clock.simulated_time
        key = (city, now.date(), now.hour)
        if self._key != key:
            self._key = key
            self._forecast = build_forecast(city, now.date(), now)
        forecast = self._forecast
        if forecast is None:
            return
        self.title(surface, rect, ctx, city, icon_color=ctx.palette.accent)
        icon_center = (rect.x + 34, rect.y + rect.h // 2 + 8)
        condition_icon(forecast.condition, surface, icon_center, 0.72 if rect.w < 170 else 0.9, ctx.palette)
        blit_text(surface, ctx.mono(28, True), f"{forecast.current}°", ctx.palette.text, topleft=(rect.x + 60, rect.y + 30))
        font = ctx.font(9, True)
        label = CONDITION_LABELS.get(forecast.condition, forecast.condition.title())
        blit_text(surface, font, fit_text(font, label, rect.w - 72), ctx.palette.text_secondary, topleft=(rect.x + 62, rect.y + 66))
        if rect.h > 110:
            blit_text(
                surface,
                ctx.font(8),
                f"H {forecast.week[0].high}°  L {forecast.week[0].low}°",
                ctx.palette.text_muted,
                bottomleft=(rect.x + 16, rect.bottom - 12),
            )
