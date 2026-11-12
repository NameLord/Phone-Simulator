"""Weather: deterministic, fully procedural forecast with drawn conditions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from random import Random

import pygame

from apps._kit import draw_button, draw_card
from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, mix

APP_ID = "weather"
APP_NAME = "Weather"
CITIES = {
    "Lisbon": (0.0, 17.0, 7.0),
    "Tokyo": (0.0, 15.0, 6.0),
    "New York": (0.0, 12.0, 9.0),
    "Sydney": (10.0, 22.0, 11.0),
    "Reykjavik": (0.0, 4.0, 12.0),
}
CONDITIONS = ("sun", "partly", "cloud", "rain", "storm", "snow")
CONDITION_LABELS = {
    "sun": "Clear",
    "partly": "Partly cloudy",
    "cloud": "Cloudy",
    "rain": "Rain",
    "storm": "Thunderstorms",
    "snow": "Snow",
}


@dataclass(frozen=True, slots=True)
class DayForecast:
    day: date
    high: int
    low: int
    condition: str
    humidity: int
    wind: int
    rain: int


@dataclass(frozen=True, slots=True)
class Forecast:
    today: date
    city: str
    current: int
    condition: str
    humidity: int
    wind: int
    hourly: tuple[tuple[int, int, str], ...]
    week: tuple[DayForecast, ...]


def _seed(city: str, day: date) -> int:
    return (day.toordinal() * 7919 + sum((index + 1) * ord(char) for index, char in enumerate(city))) & 0x7FFFFFFF


def condition_icon(condition: str, surface: pygame.Surface, center: tuple[int, int], scale: float, pal) -> None:
    """Draw a condition entirely from primitives."""
    cx, cy = center
    size = max(10.0, 32.0 * scale)
    sun_color = pal.warning
    cloud_color = pal.text_secondary if pal.text[0] < 128 else (250, 251, 255)
    cloud_shadow = mix(cloud_color, pal.scrim, 0.24)
    if condition in ("sun", "partly"):
        pygame.draw.circle(surface, sun_color, (int(cx), int(cy - size * 0.12)), int(size * 0.42))
        for index in range(8):
            angle = index * math.tau / 8
            start = (cx + math.cos(angle) * size * 0.58, cy - size * 0.12 + math.sin(angle) * size * 0.58)
            end = (cx + math.cos(angle) * size * 0.78, cy - size * 0.12 + math.sin(angle) * size * 0.78)
            pygame.draw.line(surface, sun_color, start, end, max(2, int(scale * 3)))
    if condition in ("partly", "cloud", "rain", "storm", "snow"):
        offset = size * 0.24 if condition != "sun" else 0
        blobs = ((-0.30, 0.0, 0.28), (0.0, -0.10, 0.36), (0.32, 0.02, 0.26))
        for bx, by, radius in blobs:
            pygame.draw.circle(
                surface,
                cloud_shadow,
                (int(cx + bx * size + offset), int(cy + by * size + size * 0.10)),
                int(size * radius),
            )
        for bx, by, radius in blobs:
            pygame.draw.circle(
                surface,
                cloud_color,
                (int(cx + bx * size + offset), int(cy + by * size)),
                int(size * radius),
            )
    if condition in ("rain", "storm"):
        for index in range(4):
            x = cx - size * 0.35 + index * size * 0.22
            pygame.draw.line(
                surface,
                pal.accent,
                (x, cy + size * 0.46),
                (x - size * 0.10, cy + size * 0.78),
                max(2, int(scale * 2)),
            )
    if condition == "storm":
        pygame.draw.polygon(
            surface,
            pal.warning,
            [
                (cx + size * 0.04, cy + size * 0.28),
                (cx - size * 0.16, cy + size * 0.66),
                (cx + size * 0.01, cy + size * 0.64),
                (cx - size * 0.08, cy + size * 0.98),
                (cx + size * 0.24, cy + size * 0.52),
                (cx + size * 0.08, cy + size * 0.52),
            ],
        )
    if condition == "snow":
        for index in range(4):
            x = cx - size * 0.35 + index * size * 0.22
            y = cy + size * 0.58
            for arm in range(3):
                angle = arm * math.pi / 3
                dx = math.cos(angle) * size * 0.12
                dy = math.sin(angle) * size * 0.12
                pygame.draw.line(surface, pal.text, (x - dx, y - dy), (x + dx, y + dy), max(1, int(scale * 2)))


def build_forecast(city: str, today: date, now: datetime) -> Forecast:
    rng = Random(_seed(city, today))
    _offset, summer, swing = CITIES.get(city, (0.0, 14.0, 8.0))
    seasonal = math.sin((today.timetuple().tm_yday / 365.0) * math.tau - 1.55)
    base = summer + seasonal * swing * 0.55
    week: list[DayForecast] = []
    for index in range(7):
        day_rng = Random(_seed(city, today) + index * 104729)
        day = date.fromordinal(today.toordinal() + index)
        high = int(round(base + day_rng.uniform(-3.0, 4.0)))
        low = int(round(high - day_rng.uniform(5.0, 9.0)))
        condition = day_rng.choices(CONDITIONS, weights=(32, 24, 20, 12, 6, 6), k=1)[0]
        week.append(
            DayForecast(
                day=day,
                high=high,
                low=low,
                condition=condition,
                humidity=day_rng.randint(40, 88),
                wind=day_rng.randint(4, 27),
                rain=day_rng.randint(0, 90) if condition in ("rain", "storm", "snow") else day_rng.randint(0, 18),
            )
        )
    hourly: list[tuple[int, int, str]] = []
    for hour in range(24):
        hour_rng = Random(_seed(city, today) + hour * 8191)
        curve = math.sin((hour - 8.0) / 24.0 * math.tau)
        temp = int(round(base + curve * 4.0 + hour_rng.uniform(-1.5, 1.5)))
        if 6 <= hour <= 18:
            condition = week[0].condition if hour_rng.random() > 0.18 else hour_rng.choice(CONDITIONS)
        else:
            condition = "partly" if hour_rng.random() > 0.35 else week[0].condition
        hourly.append((hour, temp, condition))
    return Forecast(
        today=today,
        city=city,
        current=int(round(base + math.sin((now.hour - 8.0) / 24.0 * math.tau) * 4.0)),
        condition=week[0].condition,
        humidity=week[0].humidity,
        wind=week[0].wind,
        hourly=tuple(hourly),
        week=tuple(week),
    )


class WeatherApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._city = self.ctx.settings.get_str("weather.city", "Lisbon")
        if self._city not in CITIES:
            self._city = "Lisbon"
        self._forecast: Forecast | None = None
        self._forecast_key: tuple[str, int] | None = None
        self._city_open = False
        self._city_rects: list[tuple[str, pygame.Rect]] = []

    # -- lifecycle ------------------------------------------------------------
    def on_launch(self) -> None:
        self._refresh()

    def update(self, dt: float) -> None:
        super().update(dt)
        self._refresh()

    def _refresh(self) -> None:
        now = self.ctx.clock.simulated_time
        key = (self._city, now.date().toordinal(), now.hour)
        if key == self._forecast_key and self._forecast is not None:
            return
        self._forecast_key = key
        self._forecast = build_forecast(self._city, now.date(), now)

    # -- geometry -------------------------------------------------------------
    def _city_button(self) -> pygame.Rect:
        return pygame.Rect(
            current_layout.margin,
            self.content.y + 6,
            min(210, self.content.w - current_layout.margin * 2),
            36,
        )

    def _current_panel(self) -> pygame.Rect:
        margin = current_layout.margin
        if current_layout.is_landscape:
            return pygame.Rect(margin, self.content.y + 50, int(self.content.w * 0.46), 168)
        return pygame.Rect(margin, self.content.y + 50, self.content.w - margin * 2, 220)

    def _hourly_panel(self) -> pygame.Rect:
        current = self._current_panel()
        if current_layout.is_landscape:
            return pygame.Rect(current.x, current.bottom + 10, current.w, max(70, self.content.bottom - current.bottom - 18))
        return pygame.Rect(current.x, current.bottom + 10, current.w, 92)

    def _week_panel(self) -> pygame.Rect:
        hourly = self._hourly_panel()
        if current_layout.is_landscape:
            return pygame.Rect(hourly.right + 12, self.content.y + 50, self.content.right - current_layout.margin - hourly.right - 12, self.content.h - 60)
        return pygame.Rect(hourly.x, hourly.bottom + 10, hourly.w, max(80, self.content.bottom - hourly.bottom - 12))

    def _city_menu(self) -> pygame.Rect:
        width = min(340, self.content.w - 40)
        return pygame.Rect(self.content.centerx - width // 2, self.content.y + 60, width, 330)

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if self._city_open:
            if event.kind is EventKind.KEY_DOWN:
                if event.key == pygame.K_ESCAPE:
                    self._city_open = False
                return True
            if event.kind is not EventKind.TAP:
                return event.kind is EventKind.POINTER_DOWN
            if not self._city_menu().collidepoint(event.pos):
                self._city_open = False
                return True
            for city, rect in self._city_rects:
                if rect.collidepoint(event.pos):
                    self._city = city
                    self.ctx.settings.set("weather.city", city)
                    self._city_open = False
                    self._forecast_key = None
                    return True
            return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        if self._city_button().collidepoint(event.pos):
            self._city_open = True
            return True
        return False

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        forecast = self._forecast
        if forecast is None:
            return
        # A subtle condition-coloured wash, entirely procedural.
        wash = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        color = self._condition_color(forecast.condition, pal)
        pygame.draw.circle(wash, (*color, 38), (self.content.right - 30, self.content.y + 20), 190)
        surface.blit(wash, (0, 0))
        self._draw_city(surface, pal, forecast)
        self._draw_current(surface, pal, forecast)
        self._draw_hourly(surface, pal, forecast)
        self._draw_week(surface, pal, forecast)
        if self._city_open:
            self._draw_city_menu(surface, pal)

    def _condition_color(self, condition: str, pal):
        return {
            "sun": pal.warning,
            "partly": pal.accent,
            "cloud": pal.text_secondary,
            "rain": pal.accent,
            "storm": pal.warning,
            "snow": pal.text,
        }.get(condition, pal.accent)

    def _draw_city(self, surface, pal, forecast: Forecast) -> None:
        rect = self._city_button()
        draw_rounded(surface, rect, fill=pal.surface, stroke=pal.stroke_soft, radius=12)
        blit_text(surface, self.font(13, True), forecast.city, pal.text, midleft=(rect.x + 12, rect.centery))
        blit_text(surface, self.font(10), "CHANGE", pal.text_muted, midright=(rect.right - 12, rect.centery), tracking=1.0)
        blit_text(
            surface,
            self.font(10),
            forecast.today.strftime("%A, %B %d"),
            pal.text_muted,
            midright=(self.content.right - current_layout.margin, rect.centery),
        )

    def _draw_current(self, surface, pal, forecast: Forecast) -> None:
        panel = self._current_panel()
        draw_card(surface, panel, pal, radius=20)
        center = (panel.right - 74, panel.y + 86)
        condition_icon(forecast.condition, surface, center, 1.65, pal)
        blit_text(
            surface,
            self.mono(48, True),
            f"{forecast.current}\u00b0",
            pal.text,
            topleft=(panel.x + 16, panel.y + 12),
        )
        blit_text(
            surface,
            self.font(13, True),
            CONDITION_LABELS.get(forecast.condition, forecast.condition.title()),
            pal.text_secondary,
            topleft=(panel.x + 18, panel.y + 70),
        )
        blit_text(
            surface,
            self.font(10),
            f"H {forecast.week[0].high}\u00b0   L {forecast.week[0].low}\u00b0",
            pal.text_muted,
            topleft=(panel.x + 18, panel.y + 92),
        )
        stats_y = panel.bottom - 52
        for index, (label, value) in enumerate(
            (
                ("HUMIDITY", f"{forecast.humidity}%"),
                ("WIND", f"{forecast.wind} km/h"),
            )
        ):
            x = panel.x + 18 + index * (panel.w // 2)
            blit_text(surface, self.font(8, True), label, pal.text_muted, topleft=(x, stats_y), tracking=1.2)
            blit_text(surface, self.font(11, True), value, pal.text, topleft=(x, stats_y + 14))

    def _draw_hourly(self, surface, pal, forecast: Forecast) -> None:
        panel = self._hourly_panel()
        draw_card(surface, panel, pal, radius=16)
        blit_text(surface, self.font(8, True), "HOURLY", pal.text_muted, topleft=(panel.x + 12, panel.y + 8), tracking=1.4)
        area = pygame.Rect(panel.x + 10, panel.y + 26, panel.w - 20, panel.h - 32)
        step = area.w / 24.0
        temps = [item[1] for item in forecast.hourly]
        low, high = min(temps), max(temps)
        span = max(1, high - low)
        points: list[tuple[int, int]] = []
        for hour, temp, condition in forecast.hourly:
            x = int(area.x + hour * step + step / 2)
            y = int(area.bottom - 20 - (temp - low) / span * max(12, area.h - 34))
            points.append((x, y))
        if len(points) > 1:
            pygame.draw.lines(surface, pal.accent, False, points, 2)
        for hour, temp, condition in forecast.hourly:
            if hour % 3 == 0:
                x = int(area.x + hour * step + step / 2)
                y = int(area.bottom - 20 - (temp - low) / span * max(12, area.h - 34))
                pygame.draw.circle(surface, pal.accent, (x, y), 2)
                blit_text(surface, self.font(7), f"{hour:02d}", pal.text_muted, center=(x, area.bottom - 7))
                blit_text(surface, self.font(7, True), f"{temp}\u00b0", pal.text_secondary, center=(x, area.bottom - 22))
        if not current_layout.is_landscape:
            blit_text(surface, self.font(7), "next 24h", pal.text_muted, midright=(area.right, area.y + 2))

    def _draw_week(self, surface, pal, forecast: Forecast) -> None:
        panel = self._week_panel()
        draw_card(surface, panel, pal, radius=16)
        blit_text(surface, self.font(8, True), "7-DAY FORECAST", pal.text_muted, topleft=(panel.x + 12, panel.y + 8), tracking=1.4)
        area = pygame.Rect(panel.x + 8, panel.y + 28, panel.w - 16, panel.h - 38)
        if current_layout.is_landscape:
            row_h = max(30, area.h // 7)
            for index, day in enumerate(forecast.week):
                row = pygame.Rect(area.x, area.y + index * row_h, area.w, row_h - 2)
                draw_rounded(surface, row, fill=pal.surface_alt if index == 0 else pal.surface, radius=8)
                blit_text(surface, self.font(9, True), day.day.strftime("%a"), pal.text, midleft=(row.x + 8, row.centery))
                condition_icon(day.condition, surface, (row.x + 50, row.centery), 0.45, pal)
                blit_text(surface, self.font(9), day.condition.upper(), pal.text_muted, midleft=(row.x + 78, row.centery))
                blit_text(surface, self.font(10, True), f"{day.high}\u00b0 / {day.low}\u00b0", pal.text, midright=(row.right - 8, row.centery))
        else:
            row_h = max(38, area.h // 7)
            for index, day in enumerate(forecast.week):
                row = pygame.Rect(area.x, area.y + index * row_h, area.w, row_h - 4)
                if index == 0:
                    draw_rounded(surface, row, fill=pal.surface_alt, radius=8)
                else:
                    pygame.draw.line(surface, pal.stroke_soft, (row.x, row.bottom), (row.right, row.bottom), 1)
                blit_text(
                    surface,
                    self.font(10, True),
                    "TODAY" if index == 0 else day.day.strftime("%a").upper(),
                    pal.text if index == 0 else pal.text_secondary,
                    midleft=(row.x + 10, row.centery - 7),
                )
                blit_text(
                    surface,
                    self.font(8),
                    day.day.strftime("%b %d"),
                    pal.text_muted,
                    midleft=(row.x + 10, row.centery + 10),
                )
                condition_icon(day.condition, surface, (row.x + 96, row.centery), 0.42, pal)
                blit_text(
                    surface,
                    self.font(9),
                    CONDITION_LABELS.get(day.condition, day.condition.title()),
                    pal.text_muted,
                    midleft=(row.x + 126, row.centery),
                )
                blit_text(
                    surface,
                    self.font(11, True),
                    f"{day.high}\u00b0",
                    pal.text,
                    midright=(row.right - 42, row.centery),
                )
                blit_text(
                    surface,
                    self.font(9),
                    f"{day.low}\u00b0",
                    pal.text_muted,
                    midright=(row.right - 10, row.centery),
                )

    def _draw_city_menu(self, surface, pal) -> None:
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill((0, 0, 0, 170))
        surface.blit(scrim, (0, 0))
        menu = self._city_menu()
        draw_rounded(surface, menu, fill=pal.surface_raised, stroke=pal.stroke, radius=20)
        blit_text(surface, self.font(16, True), "Choose a city", pal.text, topleft=(menu.x + 18, menu.y + 16))
        blit_text(surface, self.font(9), "Deterministic forecasts are seeded by local date.", pal.text_muted, topleft=(menu.x + 18, menu.y + 38))
        self._city_rects = []
        for index, city in enumerate(CITIES):
            row = pygame.Rect(menu.x + 14, menu.y + 68 + index * 48, menu.w - 28, 40)
            draw_card(surface, row, pal, selected=city == self._city, radius=10)
            blit_text(surface, self.font(12, True), city, pal.text, midleft=(row.x + 12, row.centery))
            offset = CITIES[city][0]
            blit_text(
                surface,
                self.font(9),
                f"UTC{'+' if offset >= 0 else '-'}{abs(offset):.0f}",
                pal.text_muted,
                midright=(row.right - 12, row.centery),
            )
            self._city_rects.append((city, row))

    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=max(8, rect.w // 4))
    center = (rect.centerx - 2, rect.centery - 2)
    pygame.draw.circle(surface, pal.warning, center, max(6, int(rect.w * 0.22)))
    cloud = ((rect.centerx + 3, rect.centery + 7), max(5, int(rect.w * 0.18)))
    pygame.draw.circle(surface, pal.text_secondary, cloud[0], cloud[1])
    pygame.draw.circle(
        surface,
        pal.text_secondary,
        (cloud[0][0] + cloud[1], cloud[0][1] + 2),
        max(4, int(cloud[1] * 0.78)),
    )


__all__ = ["WeatherApp", "draw_icon", "CITIES", "build_forecast"]
