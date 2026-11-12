"""First-boot onboarding: theme, wallpaper, PIN, done."""

from __future__ import annotations

import pygame

from core.input import EventKind, PointerEvent
from core.scene import Scene, clamp
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, make_glow, vertical_gradient


class OnboardingScene(Scene):
    name = "onboarding"
    shows_status_bar = False
    STEPS = ("Welcome", "Theme", "Wallpaper", "PIN", "Done")

    def __init__(self, kernel) -> None:
        super().__init__(kernel)
        self.step = 0
        self._engine = self.registry.require("theme_engine")
        self._packs = [key for key, _label, _slots in self._engine.builtins()]
        self._pack_index = 1 if len(self._packs) > 1 else 0
        self._wallpaper_names = [
            "flow",
            "plasma",
            "aurora",
            "ocean",
            "constellation",
            "grid",
            "rings",
            "sunburst",
            "synthwave",
            "forest",
            "mountains",
        ]
        self._wall_index = 0
        self._pin = ""
        self._buttons: dict[str, pygame.Rect] = {}
        self._theme_key: tuple | None = None
        self._background = vertical_gradient(self.size, self.theme.palette.bg_top, self.theme.palette.bg_bottom)
        self._glow = make_glow(220, self.theme.palette.accent, power=2.2)

    def _ensure_theme(self):
        key = self.theme.signature
        if self._theme_key != key:
            self._theme_key = key
            pal = self.theme.palette
            self._background = vertical_gradient(self.size, pal.bg_top, pal.bg_bottom)
            self._glow = make_glow(220, pal.accent, power=2.2)
        return self.theme.palette

    def on_canvas_resize(self, old, new) -> None:
        self._theme_key = None

    # -- input ---------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self._finish(skip=True)
                return True
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._next()
                return True
            if self.step == 3 and event.text and event.text.isdigit() and len(self._pin) < 4:
                self._pin += event.text
                return True
            if self.step == 3 and event.key == pygame.K_BACKSPACE and self._pin:
                self._pin = self._pin[:-1]
                return True
            return True
        if event.kind is not EventKind.TAP:
            return False
        for name, rect in self._buttons.items():
            if not rect.collidepoint(event.pos):
                continue
            if name == "next":
                self._next()
            elif name == "back":
                self.step = max(0, self.step - 1)
            elif name == "skip":
                self._finish(skip=True)
            elif name == "theme":
                self._pack_index = (self._pack_index + 1) % max(1, len(self._packs))
                self._engine.apply(self._packs[self._pack_index])
            elif name == "wallpaper":
                self._wall_index = (self._wall_index + 1) % len(self._wallpaper_names)
            return True
        return True

    def _next(self) -> None:
        if self.step == 4:
            self._finish()
        else:
            self.step = min(4, self.step + 1)

    def _finish(self, *, skip: bool = False) -> None:
        if not skip:
            if self._packs:
                self._engine.apply(self._packs[self._pack_index])
            self.settings.set("display.wallpaper", self._wallpaper_names[self._wall_index])
            pin = self._pin if len(self._pin) == 4 else "0000"
            self.settings.set("security.pin", pin)
            self.settings.set("security.pin_enabled", len(self._pin) == 4)
        self.settings.set("system.onboarded", True)
        self.settings.flush()
        self.notifications.post(
            "system",
            "Welcome to PhoneOS",
            "Onboarding complete. Swipe up or enter your PIN to unlock.",
            self.theme.palette.accent,
            banner=False,
        )
        device = self.registry.get("device")
        if device is not None:
            device.lock()
        self.go("lock", fade=0.25)

    # -- drawing -------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self._ensure_theme()
        surface.blit(self._background, (0, 0))
        self._glow.set_alpha(44)
        surface.blit(self._glow, self._glow.get_rect(center=(current_layout.center_x, current_layout.height // 2)))
        width = min(self.size[0] - 44, 470)
        card = pygame.Rect(0, 0, width, min(self.size[1] - 120, 520))
        card.center = (current_layout.center_x, current_layout.height // 2)
        draw_rounded(surface, card, fill=pal.surface, stroke=pal.stroke_soft, radius=30, alpha=245)
        step = self.step
        title_font = self.font(26, True)
        blit_text(surface, title_font, ("PhoneOS", "Choose a theme", "Pick a wallpaper", "Set a PIN", "You're ready")[step], pal.text, center=(card.centerx, card.y + 48))
        blit_text(surface, self.font(11), f"SETUP {min(step + 1, 4)} OF 4", pal.text_muted, center=(card.centerx, card.y + 78), tracking=2)

        self._buttons = {}
        if step == 0:
            blit_text(surface, self.font(14), "A tiny, fully procedural phone.", pal.text_secondary, center=(card.centerx, card.centery - 20))
            blit_text(surface, self.font(12), "Pick a few basics, then unlock.", pal.text_muted, center=(card.centerx, card.centery + 10))
        elif step == 1:
            pack = self._packs[self._pack_index] if self._packs else "dark"
            swatch = pygame.Rect(card.centerx - 80, card.centery - 80, 160, 92)
            draw_rounded(surface, swatch, fill=self._engine.slots(pack)["surface"], stroke=pal.accent, radius=20)
            draw_rounded(surface, pygame.Rect(swatch.x + 16, swatch.y + 16, 50, 50), fill=self._engine.slots(pack)["accent"], radius=12)
            blit_text(surface, self.font(16, True), self._engine.label_for(pack), pal.text, center=(card.centerx, swatch.bottom + 30))
            self._buttons["theme"] = pygame.Rect(card.centerx - 90, swatch.bottom + 54, 180, 40)
            self._draw_button(surface, "Tap to change", self._buttons["theme"], pal)
        elif step == 2:
            name = self._wallpaper_names[self._wall_index]
            preview = pygame.Rect(card.x + 40, card.centery - 96, card.w - 80, 152)
            draw_rounded(surface, preview, fill=self._engine.slots(self._packs[self._pack_index])["surface_alt"], radius=20)
            blit_text(surface, self.font(18, True), name.replace("_", " ").title(), pal.text, center=(preview.centerx, preview.centery))
            self._buttons["wallpaper"] = pygame.Rect(card.centerx - 90, preview.bottom + 24, 180, 40)
            self._draw_button(surface, "Next wallpaper", self._buttons["wallpaper"], pal)
        elif step == 3:
            blit_text(surface, self.font(13), "Enter four digits, or skip to use 0000.", pal.text_secondary, center=(card.centerx, card.centery - 62))
            boxes = []
            for index in range(4):
                box = pygame.Rect(card.centerx - 104 + index * 54, card.centery - 34, 44, 56)
                boxes.append(box)
                draw_rounded(surface, box, fill=pal.surface_alt, stroke=pal.accent if index < len(self._pin) else pal.stroke_soft, radius=12)
                if index < len(self._pin):
                    blit_text(surface, self.font(22, True), self._pin[index], pal.text, center=box.center)
            blit_text(surface, self.font(10), f"{len(self._pin)}/4", pal.text_muted, center=(card.centerx, card.centery + 44))
        else:
            blit_text(surface, self.font(14), "Everything is ready.", pal.text_secondary, center=(card.centerx, card.centery - 20))
            blit_text(surface, self.font(12), "You can change all of this in Settings.", pal.text_muted, center=(card.centerx, card.centery + 10))

        button_y = card.bottom - 74
        self._buttons["next"] = pygame.Rect(card.right - 154, button_y, 124, 44)
        self._draw_button(surface, "Done" if step == 4 else "Continue", self._buttons["next"], pal, primary=True)
        if step > 0:
            self._buttons["back"] = pygame.Rect(card.x + 26, button_y, 84, 44)
            self._draw_button(surface, "Back", self._buttons["back"], pal)
        self._buttons["skip"] = pygame.Rect(card.x + 26, card.y + 16, 68, 30)
        blit_text(surface, self.font(11, True), "SKIP", pal.text_muted, center=self._buttons["skip"].center, tracking=1)

    def _draw_button(self, surface, label: str, rect: pygame.Rect, pal, primary: bool = False) -> None:
        draw_rounded(surface, rect, fill=pal.accent if primary else pal.surface_alt, stroke=pal.accent if primary else pal.stroke_soft, radius=14)
        blit_text(surface, self.font(13, True), label, pal.on_accent if primary else pal.text, center=rect.center)
