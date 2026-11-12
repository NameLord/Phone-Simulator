"""Procedural drawing primitives shared by every screen.

Nothing here knows about the phone OS: these helpers only turn shapes, text and
numpy arrays into pygame surfaces. Keeping them in one module means the boot
splash, the lock screen and the settings UI all speak the same visual language.
"""

from __future__ import annotations

import math

import numpy
import pygame

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]

ELLIPSIS = "\u2026"


def with_alpha(color: RGB, alpha: int) -> RGBA:
    """Return ``color`` with an alpha channel, clamped to 0..255."""
    return (int(color[0]), int(color[1]), int(color[2]), max(0, min(255, int(alpha))))


def mix(first: RGB, second: RGB, amount: float) -> RGB:
    """Linear blend between two colours (0.0 -> first, 1.0 -> second)."""
    amount = max(0.0, min(1.0, float(amount)))
    return (
        int(round(first[0] + (second[0] - first[0]) * amount)),
        int(round(first[1] + (second[1] - first[1]) * amount)),
        int(round(first[2] + (second[2] - first[2]) * amount)),
    )


def darken(color: RGB, amount: float) -> RGB:
    return mix(color, (0, 0, 0), amount)


def lighten(color: RGB, amount: float) -> RGB:
    return mix(color, (255, 255, 255), amount)


def luminance(color: RGB) -> float:
    channels = [max(0.0, min(1.0, value / 255.0)) for value in color[:3]]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_text(color: RGB) -> RGB:
    """Pick black or white text for a coloured surface."""
    return (8, 12, 20) if luminance(color) > 0.62 else (255, 255, 255)


def vertical_gradient(
    size: tuple[int, int],
    top: RGB,
    bottom: RGB,
) -> pygame.Surface:
    """Build an opaque vertical gradient surface with numpy."""
    width, height = int(size[0]), int(size[1])
    ramp = numpy.linspace(0.0, 1.0, height, dtype=numpy.float32)
    rows = numpy.zeros((height, 3), dtype=numpy.float32)
    for channel in range(3):
        rows[:, channel] = top[channel] + (bottom[channel] - top[channel]) * ramp
    column = rows[:, None, :]
    array = numpy.transpose(numpy.repeat(column, width, axis=1), (1, 0, 2))
    return pygame.surfarray.make_surface(array.astype(numpy.uint8))


def make_glow(radius: int, color: RGB, *, power: float = 2.2) -> pygame.Surface:
    """Radial falloff sprite used for ambient light."""
    size = max(4, int(radius) * 2)
    grid = numpy.mgrid[0:size, 0:size]
    yy = grid[0].astype(numpy.float32)
    xx = grid[1].astype(numpy.float32)
    center = (size - 1) / 2.0
    distance = numpy.sqrt((xx - center) ** 2 + (yy - center) ** 2) / max(1.0, center)
    falloff = numpy.clip(1.0 - distance, 0.0, 1.0) ** power

    rgb = numpy.zeros((size, size, 3), dtype=numpy.uint8)
    rgb[:, :, 0] = color[0]
    rgb[:, :, 1] = color[1]
    rgb[:, :, 2] = color[2]

    glow = pygame.Surface((size, size), pygame.SRCALPHA)
    glow.fill((0, 0, 0, 0))
    glow.blit(pygame.surfarray.make_surface(rgb), (0, 0))
    alpha = pygame.surfarray.pixels_alpha(glow)
    alpha[:, :] = (falloff * 255.0).astype(numpy.uint8)
    del alpha
    return glow


def render_tracked(
    font: pygame.font.Font,
    text: str,
    color: RGB,
    tracking: float,
) -> pygame.Surface:
    """Render text with manual letter spacing."""
    if tracking <= 0.01:
        return font.render(text, True, color)
    glyphs = [font.render(character, True, color) for character in text]
    if not glyphs:
        return pygame.Surface((1, 1), pygame.SRCALPHA)
    width = int(sum(glyph.get_width() for glyph in glyphs) + tracking * (len(glyphs) - 1))
    height = font.get_height()
    image = pygame.Surface((max(1, width), max(1, height)), pygame.SRCALPHA)
    x = 0.0
    for glyph in glyphs:
        image.blit(glyph, (int(round(x)), 0))
        x += glyph.get_width() + tracking
    return image


def blit_text(
    surface: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    color: RGB,
    *,
    center: tuple[float, float] | None = None,
    topleft: tuple[float, float] | None = None,
    midleft: tuple[float, float] | None = None,
    midright: tuple[float, float] | None = None,
    bottomleft: tuple[float, float] | None = None,
    bottomright: tuple[float, float] | None = None,
    topright: tuple[float, float] | None = None,
    alpha: int = 255,
    tracking: float = 0.0,
) -> pygame.Rect:
    """Blit one line of text at an anchor and return its rect."""
    image = render_tracked(font, text, color, tracking)
    if alpha < 255:
        image = image.copy()
        image.set_alpha(max(0, min(255, int(alpha))))
    if center is not None:
        rect = image.get_rect(center=(int(round(center[0])), int(round(center[1]))))
    elif midleft is not None:
        rect = image.get_rect(midleft=(int(round(midleft[0])), int(round(midleft[1]))))
    elif midright is not None:
        rect = image.get_rect(midright=(int(round(midright[0])), int(round(midright[1]))))
    elif bottomleft is not None:
        rect = image.get_rect(bottomleft=(int(round(bottomleft[0])), int(round(bottomleft[1]))))
    elif bottomright is not None:
        rect = image.get_rect(bottomright=(int(round(bottomright[0])), int(round(bottomright[1]))))
    elif topright is not None:
        rect = image.get_rect(topright=(int(round(topright[0])), int(round(topright[1]))))
    elif topleft is not None:
        rect = image.get_rect(topleft=(int(round(topleft[0])), int(round(topleft[1]))))
    else:
        rect = image.get_rect()
    surface.blit(image, rect)
    return rect


def fit_text(font: pygame.font.Font, text: str, max_width: int) -> str:
    """Truncate ``text`` with an ellipsis so it fits ``max_width`` pixels."""
    if max_width <= 0:
        return ""
    if font.size(text)[0] <= max_width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[:middle].rstrip() + ELLIPSIS
        if font.size(candidate)[0] <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + ELLIPSIS if low > 0 else ELLIPSIS


def wrap_text(font: pygame.font.Font, text: str, max_width: int) -> list[str]:
    """Greedy word wrap, used by dialogs and notification bodies."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if font.size(candidate)[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def draw_rounded(
    surface: pygame.Surface,
    rect: pygame.Rect,
    *,
    fill: RGB | None = None,
    stroke: RGB | None = None,
    width: int = 1,
    radius: int = 16,
    alpha: int = 255,
) -> None:
    """Rounded rectangle with optional alpha compositing."""
    if rect.w <= 0 or rect.h <= 0:
        return
    if alpha >= 255:
        if fill is not None:
            pygame.draw.rect(surface, fill, rect, border_radius=radius)
        if stroke is not None:
            pygame.draw.rect(surface, stroke, rect, width=width, border_radius=radius)
        return
    layer = pygame.Surface(rect.size, pygame.SRCALPHA)
    local = layer.get_rect()
    if fill is not None:
        pygame.draw.rect(layer, with_alpha(fill, alpha), local, border_radius=radius)
    if stroke is not None:
        pygame.draw.rect(layer, with_alpha(stroke, alpha), local, width=width, border_radius=radius)
    surface.blit(layer, rect.topleft)


def draw_chevron(
    surface: pygame.Surface,
    center: tuple[float, float],
    color: RGB,
    *,
    size: float = 8.0,
    thickness: int = 2,
    direction: str = "right",
) -> None:
    """Draw a small navigation chevron (right / left / up / down)."""
    cx, cy = float(center[0]), float(center[1])
    span = max(2.0, float(size))
    if direction == "right":
        points = [(cx - span * 0.45, cy - span), (cx + span * 0.45, cy), (cx - span * 0.45, cy + span)]
    elif direction == "left":
        points = [(cx + span * 0.45, cy - span), (cx - span * 0.45, cy), (cx + span * 0.45, cy + span)]
    elif direction == "up":
        points = [(cx - span, cy + span * 0.4), (cx, cy - span * 0.5), (cx + span, cy + span * 0.4)]
    else:
        points = [(cx - span, cy - span * 0.4), (cx, cy + span * 0.5), (cx + span, cy - span * 0.4)]
    pygame.draw.lines(surface, color, False, points, thickness)


def draw_home_indicator(
    surface: pygame.Surface,
    color: RGB,
    *,
    y: int | None = None,
    width: int = 140,
    height: int = 5,
) -> None:
    anchor = surface.get_height() - 34 if y is None else int(y)
    rect = pygame.Rect(0, anchor, int(width), int(height))
    rect.centerx = surface.get_width() // 2
    pygame.draw.rect(surface, color, rect, border_radius=max(1, height // 2))


def draw_lock_glyph(
    surface: pygame.Surface,
    center: tuple[int, int],
    color: RGB,
    *,
    open_shackle: bool = False,
    scale: float = 1.0,
    background: RGB = (0, 0, 0),
) -> None:
    """Vector padlock used by the lock screen and security rows."""
    cx, cy = center
    body_w = max(10, int(42 * scale))
    body_h = max(8, int(32 * scale))
    body = pygame.Rect(0, 0, body_w, body_h)
    body.center = (cx, cy + int(8 * scale))
    shackle_h = max(6, int(26 * scale))
    shackle = pygame.Rect(
        body.centerx - body_w // 2 + int(5 * scale),
        body.top - shackle_h,
        body_w - int(10 * scale),
        shackle_h * 2,
    )
    start = math.pi * 0.35 if open_shackle else math.pi
    pygame.draw.arc(surface, color, shackle, start, math.tau, max(2, int(2 * scale)))
    pygame.draw.rect(surface, color, body, border_radius=max(3, int(7 * scale)))
    pygame.draw.circle(surface, background, (body.centerx, body.centery - 2), max(2, int(3 * scale)))


def draw_bell(
    surface: pygame.Surface,
    center: tuple[int, int],
    color: RGB,
    *,
    scale: float = 1.0,
) -> None:
    """Simple vector bell for empty notification states."""
    cx, cy = int(center[0]), int(center[1])
    width = max(8, int(34 * scale))
    height = max(8, int(26 * scale))
    rect = pygame.Rect(cx - width // 2, cy - height // 2, width, height)
    pygame.draw.arc(surface, color, rect, math.pi, math.tau, max(2, int(2 * scale)))
    pygame.draw.line(
        surface, color, (rect.left, rect.centery), (rect.left, rect.bottom), max(2, int(2 * scale))
    )
    pygame.draw.line(
        surface, color, (rect.right, rect.centery), (rect.right, rect.bottom), max(2, int(2 * scale))
    )
    pygame.draw.line(
        surface, color, (rect.left - 3, rect.bottom), (rect.right + 3, rect.bottom), max(2, int(2 * scale))
    )
    pygame.draw.circle(surface, color, (cx, rect.bottom + int(5 * scale)), max(2, int(2 * scale)))


def draw_gear(
    surface: pygame.Surface,
    center: tuple[int, int],
    color: RGB,
    *,
    radius: float = 18.0,
    thickness: int = 3,
    teeth: int = 8,
    background: RGB | None = None,
) -> None:
    """Procedural gear icon (no asset, no font dependency)."""
    cx, cy = float(center[0]), float(center[1])
    inner = float(radius) * 0.72
    tooth_width = max(3, int(thickness) + 2)
    for index in range(teeth):
        angle = math.tau * index / teeth
        outer_x = cx + math.cos(angle) * radius
        outer_y = cy + math.sin(angle) * radius
        inner_x = cx + math.cos(angle) * inner
        inner_y = cy + math.sin(angle) * inner
        pygame.draw.line(
            surface, color, (inner_x, inner_y), (outer_x, outer_y), tooth_width
        )
    pygame.draw.circle(surface, color, (int(cx), int(cy)), max(3, int(inner)))
    hole_radius = max(2, int(inner * 0.42))
    if background is not None:
        pygame.draw.circle(surface, background, (int(cx), int(cy)), hole_radius)
    else:
        pygame.draw.circle(surface, color, (int(cx), int(cy)), hole_radius, max(1, int(thickness)))

