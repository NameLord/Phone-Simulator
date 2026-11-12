"""Icon pack presets: shape, radius, padding and label treatment."""

from __future__ import annotations

from dataclasses import dataclass

import pygame

from ui.paint import draw_rounded


@dataclass(frozen=True, slots=True)
class IconPack:
    key: str
    label: str
    radius_ratio: float
    padding: float
    plate: bool


PACKS: tuple[IconPack, ...] = (
    IconPack("rounded", "Rounded", 0.30, 0.02, True),
    IconPack("squircle", "Squircle", 0.42, 0.0, True),
    IconPack("circle", "Circle", 0.50, 0.04, False),
    IconPack("soft", "Soft tiles", 0.22, 0.06, True),
    IconPack("bare", "Bare icons", 0.0, 0.0, False),
)
PACK_MAP = {pack.key: pack for pack in PACKS}
PACK_ORDER = tuple(pack.key for pack in PACKS)


def get_pack(key: str | None) -> IconPack:
    return PACK_MAP.get(str(key), PACKS[0])


def draw_plate(surface: pygame.Surface, rect: pygame.Rect, pack: IconPack, palette, *, alpha: int = 120) -> None:
    if not pack.plate:
        return
    if pack.key == "circle":
        pygame.draw.circle(surface, palette.surface_raised, rect.center, rect.w // 2)
        pygame.draw.circle(surface, palette.stroke_soft, rect.center, rect.w // 2, 1)
    else:
        draw_rounded(
            surface,
            rect,
            fill=palette.surface_raised,
            stroke=palette.stroke_soft,
            radius=max(4, int(rect.w * pack.radius_ratio)),
            alpha=alpha,
        )


def icon_rect(rect: pygame.Rect, pack: IconPack, scale: float = 1.0) -> pygame.Rect:
    size = max(20, int(min(rect.w, rect.h) * 0.68 * max(0.7, min(1.3, scale))))
    inner = pygame.Rect(0, 0, size, size)
    inner.center = (rect.centerx, rect.y + max(4, int(rect.h * 0.38)))
    return inner
