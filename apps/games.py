"""Games: a complete 2048 implementation with persistence and one-step undo."""

from __future__ import annotations

import random

import numpy as np
import pygame

from apps._kit import draw_button
from apps.base_app import App, AppContext
from core.input import Direction, EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, fit_text

APP_ID = "games"
APP_NAME = "Games"
STORE_FILE = "game_2048.json"
SIZE = 4
WIN_TILE = 2048

TILE_COLORS = {
    2: (238, 228, 218),
    4: (237, 224, 200),
    8: (242, 177, 121),
    16: (245, 149, 99),
    32: (246, 124, 95),
    64: (246, 94, 59),
    128: (237, 207, 114),
    256: (237, 204, 97),
    512: (237, 200, 80),
    1024: (237, 197, 63),
    2048: (237, 194, 46),
}
TILE_TEXT = {2: (119, 110, 101), 4: (119, 110, 101)}


def _empty_board() -> np.ndarray:
    return np.zeros((SIZE, SIZE), dtype=np.int32)


def _spawn(board: np.ndarray, rng: random.Random) -> bool:
    empty = [(row, column) for row in range(SIZE) for column in range(SIZE) if board[row, column] == 0]
    if not empty:
        return False
    row, column = rng.choice(empty)
    board[row, column] = 4 if rng.random() < 0.10 else 2
    return True


def _merge_line(line: list[int]) -> tuple[list[int], int]:
    values = [value for value in line if value != 0]
    merged: list[int] = []
    score = 0
    index = 0
    while index < len(values):
        if index + 1 < len(values) and values[index] == values[index + 1]:
            value = values[index] * 2
            merged.append(value)
            score += value
            index += 2
        else:
            merged.append(values[index])
            index += 1
    merged.extend([0] * (SIZE - len(merged)))
    return merged, score


def _move_board(board: np.ndarray, direction: str) -> tuple[np.ndarray, int, bool]:
    result = board.copy()
    score = 0
    moved = False
    if direction in ("left", "right"):
        for row in range(SIZE):
            line = list(result[row, :])
            if direction == "right":
                line = list(reversed(line))
            merged, gained = _merge_line(line)
            if direction == "right":
                merged = list(reversed(merged))
            score += gained
            if merged != list(result[row, :]):
                moved = True
            result[row, :] = merged
    else:
        for column in range(SIZE):
            line = list(result[:, column])
            if direction == "down":
                line = list(reversed(line))
            merged, gained = _merge_line(line)
            if direction == "down":
                merged = list(reversed(merged))
            score += gained
            if merged != list(result[:, column]):
                moved = True
            result[:, column] = merged
    return result, score, moved


def _can_move(board: np.ndarray) -> bool:
    if np.any(board == 0):
        return True
    for row in range(SIZE):
        for column in range(SIZE):
            value = int(board[row, column])
            if column + 1 < SIZE and board[row, column + 1] == value:
                return True
            if row + 1 < SIZE and board[row + 1, column] == value:
                return True
    return False


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=max(8, rect.w // 4))
    cell = max(5, rect.w // 5)
    gap = max(2, rect.w // 22)
    for row in range(2):
        for column in range(2):
            x = rect.x + rect.w // 2 - cell - gap // 2 + column * (cell + gap)
            y = rect.y + rect.h // 2 - cell - gap // 2 + row * (cell + gap)
            color = (237, 194, 46) if (row + column) == 1 else (246, 124, 95)
            draw_rounded(surface, pygame.Rect(x, y, cell, cell), fill=color, radius=3)


class GamesApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._rng = random.Random(0x2048)
        self._board = _empty_board()
        self._score = 0
        self._best = 0
        self._undo_board: np.ndarray | None = None
        self._undo_score = 0
        self._won = False
        self._win_seen = False
        self._game_over = False
        self._new_rect = pygame.Rect(0, 0, 1, 1)
        self._undo_rect = pygame.Rect(0, 0, 1, 1)
        self._continue_rect = pygame.Rect(0, 0, 1, 1)
        self._restart_rect = pygame.Rect(0, 0, 1, 1)

    # -- lifecycle ------------------------------------------------------------
    def on_launch(self) -> None:
        self._load()
        if not np.any(self._board):
            self._new_game()

    def on_pause(self) -> None:
        self._save()

    def on_close(self) -> None:
        self._save()

    def on_resume(self) -> None:
        self._load()

    # -- persistence ----------------------------------------------------------
    def _load(self) -> None:
        payload = self.ctx.storage.read_json(STORE_FILE, {})
        if not isinstance(payload, dict):
            return
        try:
            best = int(payload.get("best", 0))
        except (TypeError, ValueError):
            best = 0
        self._best = max(0, best)
        raw = payload.get("board")
        board = _empty_board()
        if isinstance(raw, list) and len(raw) == SIZE:
            valid = True
            for row in range(SIZE):
                if not isinstance(raw[row], list) or len(raw[row]) != SIZE:
                    valid = False
                    break
                for column in range(SIZE):
                    try:
                        value = int(raw[row][column])
                    except (TypeError, ValueError):
                        valid = False
                        break
                    if value < 0 or (value != 0 and (value & (value - 1)) != 0):
                        valid = False
                        break
                    board[row, column] = value
                if not valid:
                    break
            if valid:
                self._board = board
        try:
            self._score = max(0, int(payload.get("score", 0)))
        except (TypeError, ValueError):
            self._score = 0
        self._won = bool(payload.get("won", False))
        self._win_seen = bool(payload.get("win_seen", self._won))
        self._game_over = bool(payload.get("game_over", False))
        undo = payload.get("undo")
        if isinstance(undo, dict) and isinstance(undo.get("board"), list):
            try:
                candidate = np.array(undo["board"], dtype=np.int32)
                if candidate.shape == (SIZE, SIZE):
                    self._undo_board = candidate
                    self._undo_score = max(0, int(undo.get("score", 0)))
            except (TypeError, ValueError):
                self._undo_board = None

    def _save(self) -> None:
        self.ctx.storage.write_json(
            STORE_FILE,
            {
                "board": self._board.tolist(),
                "score": int(self._score),
                "best": int(self._best),
                "won": bool(self._won),
                "win_seen": bool(self._win_seen),
                "game_over": bool(self._game_over),
                "undo": (
                    {"board": self._undo_board.tolist(), "score": int(self._undo_score)}
                    if self._undo_board is not None
                    else None
                ),
            },
        )

    # -- game logic -----------------------------------------------------------
    def _new_game(self) -> None:
        self._board = _empty_board()
        self._score = 0
        self._undo_board = None
        self._undo_score = 0
        self._won = False
        self._win_seen = False
        self._game_over = False
        _spawn(self._board, self._rng)
        _spawn(self._board, self._rng)
        self._save()

    def _move(self, direction: str) -> bool:
        if self._game_over or self._winning_modal():
            return False
        result, gained, moved = _move_board(self._board, direction)
        if not moved:
            return False
        self._undo_board = self._board.copy()
        self._undo_score = self._score
        self._board = result
        self._score += gained
        self._best = max(self._best, self._score)
        if not self._won and np.any(self._board >= WIN_TILE):
            self._won = True
            self._win_seen = False
        _spawn(self._board, self._rng)
        if not _can_move(self._board):
            self._game_over = True
        self._save()
        return True

    def _undo(self) -> bool:
        if self._undo_board is None:
            return False
        self._board = self._undo_board.copy()
        self._score = self._undo_score
        self._undo_board = None
        self._game_over = False
        self._won = False
        self._win_seen = True
        self._save()
        return True

    def _winning_modal(self) -> bool:
        return self._won and not self._win_seen

    # -- geometry -------------------------------------------------------------
    def _board_rect(self) -> pygame.Rect:
        margin = current_layout.margin
        area = pygame.Rect(
            margin,
            self.content.y + 104,
            self.content.w - margin * 2,
            self.content.h - 158,
        )
        side = min(area.w, area.h)
        if current_layout.is_landscape:
            side = min(side, int(self.content.h * 0.66))
        return pygame.Rect(area.centerx - side // 2, area.y, side, side)

    def _tile_rect(self, row: int, column: int) -> pygame.Rect:
        board = self._board_rect()
        gap = max(6, board.w // 42)
        cell = (board.w - gap * (SIZE + 1)) // SIZE
        return pygame.Rect(
            board.x + gap + column * (cell + gap),
            board.y + gap + row * (cell + gap),
            cell,
            cell,
        )

    def _new_rect_for(self) -> pygame.Rect:
        width = min(120, (self.content.w - current_layout.margin * 2 - 10) // 2)
        return pygame.Rect(current_layout.margin, self.content.y + 58, width, 34)

    def _undo_rect_for(self) -> pygame.Rect:
        new = self._new_rect_for()
        return pygame.Rect(new.right + 10, new.y, new.w, new.h)

    def _modal_buttons(self) -> tuple[pygame.Rect, pygame.Rect]:
        width = min(320, self.content.w - 40)
        left = self.content.centerx - width // 2
        top = self.content.centery + 40
        return (
            pygame.Rect(left, top, (width - 10) // 2, 40),
            pygame.Rect(left + (width - 10) // 2 + 10, top, (width - 10) // 2, 40),
        )

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if self._winning_modal() or self._game_over:
            if event.kind is EventKind.KEY_DOWN:
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if self._winning_modal():
                        self._win_seen = True
                        self._save()
                    else:
                        self._new_game()
                    return True
                if event.key == pygame.K_ESCAPE:
                    if self._winning_modal():
                        self._win_seen = True
                        self._save()
                    return True
            if event.kind is not EventKind.TAP:
                return event.kind is EventKind.POINTER_DOWN
            primary, secondary = self._modal_buttons()
            if self._winning_modal():
                if primary.collidepoint(event.pos):
                    self._win_seen = True
                    self._save()
                elif secondary.collidepoint(event.pos):
                    self._new_game()
            elif self._game_over:
                if primary.collidepoint(event.pos) or secondary.collidepoint(event.pos):
                    self._new_game()
            return True
        if event.kind is EventKind.KEY_DOWN:
            mapping = {
                pygame.K_LEFT: "left",
                pygame.K_a: "left",
                pygame.K_RIGHT: "right",
                pygame.K_d: "right",
                pygame.K_UP: "up",
                pygame.K_w: "up",
                pygame.K_DOWN: "down",
                pygame.K_s: "down",
            }
            if event.key in mapping:
                self._move(mapping[event.key])
                return True
            if event.key == pygame.K_BACKSPACE:
                self._undo()
                return True
            if event.key == pygame.K_n:
                self._new_game()
                return True
        if event.kind is EventKind.SWIPE:
            direction = {
                Direction.LEFT: "left",
                Direction.RIGHT: "right",
                Direction.UP: "up",
                Direction.DOWN: "down",
            }.get(event.direction)
            if direction is not None:
                self._move(direction)
                return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        if self._new_rect_for().collidepoint(event.pos):
            self._new_game()
            return True
        if self._undo_rect_for().collidepoint(event.pos):
            self._undo()
            return True
        return False

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        title_font = self.font(28, True)
        blit_text(surface, title_font, "2048", pal.text, topleft=(current_layout.margin, self.content.y + 8))
        blit_text(surface, self.font(8, True), "SWIPE OR ARROW KEYS", pal.text_muted, topleft=(current_layout.margin + 84, self.content.y + 12), tracking=1.2)
        score_rect = pygame.Rect(self.content.right - current_layout.margin - 166, self.content.y + 8, 78, 38)
        best_rect = pygame.Rect(score_rect.right + 8, score_rect.y, 78, 38)
        for rect, label, value in ((score_rect, "SCORE", self._score), (best_rect, "BEST", self._best)):
            draw_rounded(surface, rect, fill=pal.surface, stroke=pal.stroke_soft, radius=10)
            blit_text(surface, self.font(7, True), label, pal.text_muted, center=(rect.centerx, rect.y + 10), tracking=1.0)
            blit_text(surface, self.font(13, True), str(value), pal.text, center=(rect.centerx, rect.y + 24))
        draw_button(surface, self._new_rect_for(), "NEW", self.font(9, True), pal, fill=pal.surface_alt, radius=10)
        draw_button(surface, self._undo_rect_for(), "UNDO", self.font(9, True), pal, fill=pal.surface_alt if self._undo_board is not None else pal.surface, color=pal.text_secondary if self._undo_board is not None else pal.text_muted, radius=10)
        board = self._board_rect()
        draw_rounded(surface, board, fill=pal.surface_alt, stroke=pal.stroke, radius=16)
        for row in range(SIZE):
            for column in range(SIZE):
                rect = self._tile_rect(row, column)
                value = int(self._board[row, column])
                if value == 0:
                    pygame.draw.rect(surface, pal.track, rect, border_radius=8)
                    continue
                color = TILE_COLORS.get(value, (60, 58, 50))
                draw_rounded(surface, rect, fill=color, radius=8)
                text_color = TILE_TEXT.get(value, (250, 248, 244))
                size = 24 if value < 100 else 20 if value < 1000 else 16
                blit_text(surface, self.font(size, True), str(value), text_color, center=rect.center)
        if self._winning_modal():
            self._draw_modal(surface, pal, "You reached 2048!", "Continue", "New game")
        elif self._game_over:
            self._draw_modal(surface, pal, "No moves left", "New game", "New game")

    def _draw_modal(self, surface, pal, title: str, primary: str, secondary: str) -> None:
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill((0, 0, 0, 170))
        surface.blit(scrim, (0, 0))
        width = min(380, self.content.w - 36)
        panel = pygame.Rect(self.content.centerx - width // 2, self.content.centery - 90, width, 198)
        draw_rounded(surface, panel, fill=pal.surface_raised, stroke=pal.stroke, radius=22)
        blit_text(surface, self.font(18, True), title, pal.text, center=(panel.centerx, panel.y + 46))
        blit_text(surface, self.font(10), f"Score {self._score}  \u00b7  Best {self._best}", pal.text_muted, center=(panel.centerx, panel.y + 72))
        first, second = self._modal_buttons()
        draw_button(surface, first, primary, self.font(11, True), pal, fill=pal.accent, color=pal.on_accent, stroke=None)
        draw_button(surface, second, secondary, self.font(11, True), pal, fill=pal.surface_alt)

    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)


__all__ = ["GamesApp", "draw_icon", "_move_board", "_merge_line"]
