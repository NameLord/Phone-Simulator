"""Calculator with a safe expression engine (tokenizer + shunting-yard + RPN).

No ``eval`` is used. Supported syntax:

* numbers with one decimal point (``.5`` and ``5.`` are accepted)
* ``+ - * /`` binary operators, ``^`` right-associative power
* unary minus (``-5``, ``2*-3``, ``-(2+3)``)
* postfix percent (``50%`` -> 0.5, ``200*10%`` -> 20)
* parentheses

Live preview evaluates the partial expression whenever it is valid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pygame

from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, fit_text

APP_ID = "calculator"
APP_NAME = "Calculator"


class CalculatorError(ValueError):
    """Raised for malformed expressions and arithmetic errors."""


BINARY_OPS = {
    "+": (1, "L"),
    "-": (1, "L"),
    "*": (2, "L"),
    "/": (2, "L"),
    "^": (3, "R"),
}
UNARY_PRECEDENCE = 2.5


def tokenize(expression: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    length = len(expression)
    while index < length:
        char = expression[index]
        if char.isspace():
            index += 1
            continue
        if char.isdigit() or char == ".":
            start = index
            dots = 0
            while index < length and (expression[index].isdigit() or expression[index] == "."):
                if expression[index] == ".":
                    dots += 1
                    if dots > 1:
                        raise CalculatorError("Invalid number")
                index += 1
            text = expression[start:index]
            if text == ".":
                raise CalculatorError("Invalid number")
            tokens.append(text)
            continue
        if char in "+-*/^%()":
            if char in "+-":
                previous = tokens[-1] if tokens else None
                unary = previous is None or previous == "(" or previous in BINARY_OPS or previous == "u-"
                if unary:
                    if char == "-":
                        tokens.append("u-")
                    index += 1
                    continue
            tokens.append(char)
            index += 1
            continue
        raise CalculatorError(f"Unexpected character {char!r}")
    return tokens


def to_rpn(tokens: list[str]) -> list[str]:
    output: list[str] = []
    stack: list[str] = []

    for token in tokens:
        if token == "(":
            stack.append(token)
        elif token == ")":
            while stack and stack[-1] != "(":
                output.append(stack.pop())
            if not stack:
                raise CalculatorError("Mismatched parentheses")
            stack.pop()
        elif token == "%":
            # Postfix: applies to the operand already emitted.
            output.append(token)
        elif token == "u-":
            # Unary operators bind to the following operand; push directly so
            # that 2^-3 and -2^2 both parse as expected.
            stack.append(token)
        elif token in BINARY_OPS:
            precedence, associativity = BINARY_OPS[token]
            while stack and stack[-1] != "(":
                top = stack[-1]
                top_precedence = UNARY_PRECEDENCE if top == "u-" else BINARY_OPS[top][0]
                if top_precedence > precedence or (
                    top_precedence == precedence and associativity == "L"
                ):
                    output.append(stack.pop())
                else:
                    break
            stack.append(token)
        else:
            output.append(token)

    while stack:
        top = stack.pop()
        if top == "(":
            raise CalculatorError("Mismatched parentheses")
        output.append(top)
    return output


def evaluate_rpn(rpn: list[str]) -> float:
    stack: list[float] = []
    for token in rpn:
        if token == "u-":
            if not stack:
                raise CalculatorError("Incomplete expression")
            stack.append(-stack.pop())
        elif token == "%":
            if not stack:
                raise CalculatorError("Incomplete expression")
            stack.append(stack.pop() / 100.0)
        elif token in BINARY_OPS:
            if len(stack) < 2:
                raise CalculatorError("Incomplete expression")
            right = stack.pop()
            left = stack.pop()
            if token == "+":
                stack.append(left + right)
            elif token == "-":
                stack.append(left - right)
            elif token == "*":
                stack.append(left * right)
            elif token == "/":
                if right == 0.0:
                    raise CalculatorError("Division by zero")
                stack.append(left / right)
            else:
                try:
                    stack.append(math.pow(left, right))
                except (ValueError, OverflowError) as exc:
                    raise CalculatorError("Invalid power") from exc
        else:
            try:
                stack.append(float(token))
            except ValueError as exc:
                raise CalculatorError("Invalid number") from exc

    if len(stack) != 1:
        raise CalculatorError("Incomplete expression")
    result = stack[0]
    if not math.isfinite(result):
        raise CalculatorError("Result out of range")
    return result


def evaluate_expression(expression: str) -> float:
    """Evaluate a calculator expression without using ``eval``."""
    tokens = tokenize(expression)
    if not tokens:
        raise CalculatorError("Empty expression")
    return evaluate_rpn(to_rpn(tokens))


def format_number(value: float) -> str:
    """Compact human formatting for results."""
    if abs(value) < 1e-12:
        value = 0.0
    if abs(value) >= 1e12 or 0.0 < abs(value) < 1e-9:
        return f"{value:.8g}"
    if abs(value - round(value)) < 1e-10:
        return str(int(round(value)))
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text or "0"


def fit_tail(font: pygame.font.Font, text: str, max_width: int) -> str:
    """Keep the end of a long expression, prefixing an ellipsis."""
    if max_width <= 0 or font.size(text)[0] <= max_width:
        return text
    for cut in range(1, len(text)):
        candidate = "\u2026" + text[cut:]
        if font.size(candidate)[0] <= max_width:
            return candidate
    return "\u2026"


@dataclass(slots=True)
class CalcKey:
    label: str
    token: str
    rect: pygame.Rect
    style: str = "digit"


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.accent, radius=max(8, rect.w // 4))
    inner = rect.inflate(-int(rect.w * 0.36), -int(rect.h * 0.36))
    cell = max(3, inner.w // 3)
    color = pal.on_accent
    for row in range(2):
        for column in range(2):
            x = inner.x + column * (cell + 4)
            y = inner.y + row * (cell + 4)
            pygame.draw.rect(surface, color, pygame.Rect(x, y, cell, cell), border_radius=2)
    pygame.draw.rect(
        surface,
        color,
        pygame.Rect(inner.x, inner.bottom - cell, inner.w, max(3, cell // 2)),
        border_radius=2,
    )


class CalculatorApp(App):
    """Working calculator app."""

    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._expr = ""
        self._error = ""
        self._just_evaluated = False
        self._pressed: str | None = None
        self._flash: str | None = None
        self._flash_t = 0.0
        self._keys = self._build_keys()

    # -- layout ---------------------------------------------------------------
    def _display_rect(self) -> pygame.Rect:
        margin = max(14, current_layout.margin - 4)
        if current_layout.is_landscape:
            width = int((self.content.w - margin * 3) * 0.36)
            return pygame.Rect(
                margin,
                self.content.y + 14,
                max(180, width),
                max(120, self.content.h - 28),
            )
        return pygame.Rect(
            margin,
            self.content.y + 16,
            self.content.w - margin * 2,
            min(396, int(self.content.h * 0.42)),
        )

    def _key_area(self) -> pygame.Rect:
        display = self._display_rect()
        margin = max(14, current_layout.margin - 4)
        x = display.right + margin if current_layout.is_landscape else margin
        y = self.content.y + 14 if current_layout.is_landscape else display.bottom + 18
        return pygame.Rect(
            x,
            y,
            self.content.right - margin - x,
            self.content.bottom - 24 - y,
        )

    def _build_keys(self) -> list[CalcKey]:
        keys: list[CalcKey] = []
        area = self._key_area()
        gap = max(6, min(12, int(area.w * 0.025)))
        width = (area.w - 3 * gap) / 4.0
        height = (area.h - 5 * gap) / 6.0
        pitch = height + gap
        rows: tuple[tuple[tuple[str, str], ...], ...] = (
            (("AC", "AC"), ("DEL", "BACK"), ("(", "("), (")", ")")),
            (("7", "7"), ("8", "8"), ("9", "9"), ("\u00f7", "/")),
            (("4", "4"), ("5", "5"), ("6", "6"), ("\u00d7", "*")),
            (("1", "1"), ("2", "2"), ("3", "3"), ("\u2212", "-")),
            (("%", "%"), ("^", "^"), ("0", "0"), ("+", "+")),
        )
        for row_index, row in enumerate(rows):
            for column, (label, token) in enumerate(row):
                style = "digit"
                if token in "+-*/^%":
                    style = "operator"
                elif token in ("AC", "BACK"):
                    style = "utility"
                rect = pygame.Rect(
                    int(round(area.x + column * (width + gap))),
                    int(round(area.y + row_index * pitch)),
                    int(round(width)),
                    int(round(height)),
                )
                keys.append(CalcKey(label, token, rect, style))

        last_y = int(round(area.y + 5 * pitch))
        dot_width = int(round(width))
        equals_width = int(round(3 * width + 2 * gap))
        keys.append(
            CalcKey(".", ".", pygame.Rect(int(round(area.x)), last_y, dot_width, int(height)), "digit")
        )
        keys.append(
            CalcKey(
                "=",
                "=",
                pygame.Rect(int(round(area.x + width + gap)), last_y, equals_width, int(height)),
                "equals",
            )
        )
        return keys

    def on_resize(self) -> None:
        self._keys = self._build_keys()
        self._pressed = None

    # -- expression -----------------------------------------------------------
    def _append(self, token: str) -> None:
        if len(self._expr) >= 80:
            return
        if self._just_evaluated:
            if token not in "+-*/^%)":
                self._expr = ""
            self._just_evaluated = False
        self._error = ""
        self._expr += token

    def _backspace(self) -> None:
        if not self._expr:
            return
        if self._expr.endswith("u-"):
            self._expr = self._expr[:-2]
        else:
            self._expr = self._expr[:-1]
        self._just_evaluated = False
        self._error = ""

    def _clear(self) -> None:
        self._expr = ""
        self._error = ""
        self._just_evaluated = False

    def _evaluate(self) -> None:
        if not self._expr:
            return
        try:
            result = evaluate_expression(self._expr)
        except CalculatorError as exc:
            self._error = str(exc)
            self._just_evaluated = False
            return
        self._expr = format_number(result)
        self._error = ""
        self._just_evaluated = True

    def _preview(self) -> str | None:
        if not self._expr or self._just_evaluated:
            return None
        try:
            value = evaluate_expression(self._expr)
        except CalculatorError:
            return None
        return format_number(value)

    def _activate(self, token: str) -> None:
        self._flash = token
        self._flash_t = 0.12
        if token == "AC":
            self._clear()
        elif token == "BACK":
            self._backspace()
        elif token == "=":
            self._evaluate()
        else:
            self._append(token)

    # -- input ----------------------------------------------------------------
    def _hit(self, pos: tuple[float, float]) -> str | None:
        for key in reversed(self._keys):
            if key.rect.collidepoint(pos):
                return key.token
        return None

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.POINTER_DOWN:
            token = self._hit(event.pos)
            self._pressed = token
            return token is not None
        if event.kind is EventKind.POINTER_UP:
            was = self._pressed is not None
            self._pressed = None
            return was
        if event.kind is EventKind.TAP:
            token = self._hit(event.pos)
            if token is None:
                return False
            self._activate(token)
            return True
        if event.kind is EventKind.KEY_DOWN:
            text = event.text or ""
            if text and text in "0123456789.+-*/^%()":
                self._append(text)
                return True
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) or text == "=":
                self._evaluate()
                return True
            if event.key == pygame.K_BACKSPACE:
                self._backspace()
                return True
            if text.lower() == "c":
                self._clear()
                return True
            return False
        return False

    def update(self, dt: float) -> None:
        super().update(dt)
        if self._flash_t > 0.0:
            self._flash_t = max(0.0, self._flash_t - dt)
            if self._flash_t == 0.0:
                self._flash = None

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)

        display = self._display_rect()
        draw_rounded(surface, display, fill=pal.surface, stroke=pal.stroke_soft, radius=26)
        blit_text(
            surface,
            self.font(11),
            "EXPRESSION",
            pal.text_muted,
            topleft=(display.x + 22, display.y + 18),
            tracking=2.0,
        )

        expression = self._expr or "0"
        expression_color = pal.text if self._expr else pal.text_muted
        font = self.mono(30, True)
        expression = fit_tail(font, expression, display.w - 44)
        blit_text(
            surface,
            font,
            expression,
            expression_color,
            midright=(display.right - 22, display.y + int(display.h * 0.38)),
        )

        preview = self._preview()
        if preview is not None and preview != self._expr:
            blit_text(
                surface,
                self.mono(24),
                f"= {preview}",
                pal.accent_ink,
                midright=(display.right - 22, display.y + int(display.h * 0.58)),
            )

        if self._error:
            blit_text(
                surface,
                self.font(13),
                self._error,
                pal.danger,
                midright=(display.right - 22, display.y + int(display.h * 0.76)),
            )

        hint_font = self.font(10)
        for line, y in (
            ("LIVE RESULT UPDATES AS YOU TYPE", display.bottom - 46),
            ("x% is percent \u00b7 ^ is power \u00b7 unary minus supported", display.bottom - 28),
        ):
            blit_text(
                surface,
                hint_font,
                line,
                pal.text_muted,
                midleft=(display.x + 22, y),
                tracking=0.6,
            )

        for key in self._keys:
            self._draw_key(surface, key)

        blit_text(
            surface,
            self.font(9),
            "keyboard: digits . + - * / ^ % ( ) \u00b7 Enter = \u00b7 Backspace",
            pal.text_muted,
            center=(self.content.centerx, self.content.bottom - 8),
            tracking=0.2,
        )

    def _draw_key(self, surface: pygame.Surface, key: CalcKey) -> None:
        pal = self.palette
        pressed = self._pressed == key.token or self._flash == key.token

        if key.style == "equals":
            fill = pal.accent
            text_color = pal.on_accent
        elif key.style == "operator":
            fill = pal.accent_soft
            text_color = pal.accent_ink
        elif key.style == "utility":
            fill = pal.surface_alt
            text_color = pal.danger if key.token == "AC" else pal.text_secondary
        else:
            fill = pal.surface_raised
            text_color = pal.text

        rect = key.rect.copy()
        if pressed:
            rect = rect.inflate(-4, -4)
            fill = pal.accent if key.style != "equals" else pal.accent_ink
            if key.style == "equals":
                text_color = pal.on_accent

        draw_rounded(surface, rect, fill=fill, stroke=pal.stroke_soft, radius=20)

        size = max(12, min(28, int(key.rect.h * 0.34))) if key.label not in ("AC", "DEL") else max(10, min(15, int(key.rect.h * 0.20)))
        blit_text(
            surface,
            self.font(size, key.style != "utility"),
            key.label,
            text_color,
            center=rect.center,
        )

    # -- icon -----------------------------------------------------------------
    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)
