"""Recorder: a simulated voice recorder with live numpy waveform animation."""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pygame

from apps._kit import ScrollState, draw_button, draw_card, draw_scrollbar
from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, fit_text

APP_ID = "recorder"
APP_NAME = "Recorder"
STORE_FILE = "recordings.json"


@dataclass(slots=True)
class Recording:
    recording_id: int
    name: str
    duration: float
    created: float
    seed: int

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.recording_id,
            "name": self.name,
            "duration": self.duration,
            "created": self.created,
            "seed": self.seed,
        }


def _waveform(seed: int, count: int = 64) -> np.ndarray:
    rng = np.random.default_rng(seed)
    raw = rng.normal(0.0, 0.45, count)
    envelope = np.hanning(count) * 0.75 + 0.25
    values = np.clip(np.abs(raw) * envelope, 0.02, 1.0)
    return values.astype(np.float32)


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.danger, radius=max(8, rect.w // 4))
    center = rect.center
    body = pygame.Rect(0, 0, max(8, int(rect.w * 0.20)), max(14, int(rect.h * 0.34)))
    body.center = (center[0], center[1] - int(rect.h * 0.08))
    pygame.draw.rect(surface, pal.on_accent, body, border_radius=body.w // 2)
    pygame.draw.arc(
        surface,
        pal.on_accent,
        pygame.Rect(body.centerx - body.w, body.bottom - 2, body.w * 2, body.h),
        0,
        math_pi(),
        2,
    )
    pygame.draw.line(surface, pal.on_accent, (center[0], body.bottom + body.h // 2), (center[0], body.bottom + body.h), 2)
    pygame.draw.line(surface, pal.on_accent, (center[0] - 8, body.bottom + body.h), (center[0] + 8, body.bottom + body.h), 2)


def math_pi() -> float:
    return 3.141592653589793


class RecorderApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._recordings: list[Recording] = []
        self._next_id = 1
        self._recording = False
        self._paused = False
        self._elapsed = 0.0
        self._seed = 1
        self._live = _waveform(1)
        self._live_phase = 0.0
        self._selected: int | None = None
        self._playing: int | None = None
        self._playhead = 0.0
        self._list_scroll = ScrollState(step=56)
        self._row_rects: list[tuple[int, pygame.Rect]] = []
        self._confirm_delete: int | None = None
        self._toast = ""
        self._toast_t = 0.0

    # -- lifecycle ------------------------------------------------------------
    def on_launch(self) -> None:
        self._load()

    def on_pause(self) -> None:
        self._save()

    def on_close(self) -> None:
        self._save()

    def update(self, dt: float) -> None:
        super().update(dt)
        step = max(0.0, dt)
        if self._recording and not self._paused:
            self._elapsed += step
            self._live_phase += step
            if self._live_phase > 0.08:
                self._live_phase = 0.0
                started = time.perf_counter()
                rng = np.random.default_rng(self._seed + int(self._elapsed * 20.0))
                raw = rng.normal(0.0, 0.5, 64)
                envelope = np.hanning(64) * 0.7 + 0.3
                self._live = np.clip(np.abs(raw) * envelope, 0.02, 1.0).astype(np.float32)
                if self.ctx.profiler is not None:
                    self.ctx.profiler.record_effect("recorder", (time.perf_counter() - started) * 1000.0)
        if self._playing is not None:
            self._playhead += step
            recording = self._by_id(self._playing)
            if recording is None or self._playhead >= recording.duration:
                self._playing = None
                self._playhead = 0.0
        if self._toast_t > 0.0:
            self._toast_t = max(0.0, self._toast_t - dt)
            if self._toast_t == 0.0:
                self._toast = ""

    def on_resize(self) -> None:
        self._list_scroll.reset()

    # -- persistence ----------------------------------------------------------
    def _load(self) -> None:
        payload = self.ctx.storage.read_json(STORE_FILE, {})
        if not isinstance(payload, dict):
            payload = {}
        raw = payload.get("recordings", [])
        records: list[Recording] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    recording_id = int(item.get("id", 0))
                    duration = max(0.0, float(item.get("duration", 0.0)))
                    created = float(item.get("created", time.time()))
                    seed = int(item.get("seed", recording_id))
                except (TypeError, ValueError):
                    continue
                if recording_id <= 0:
                    continue
                records.append(Recording(recording_id, str(item.get("name", f"Recording {recording_id}"))[:48], duration, created, seed))
        self._recordings = sorted(records, key=lambda item: item.created, reverse=True)
        self._next_id = max(
            int(payload.get("next_id", 1)) if isinstance(payload.get("next_id"), (int, float)) else 1,
            max((item.recording_id for item in records), default=0) + 1,
        )

    def _save(self) -> None:
        self.ctx.storage.write_json(
            STORE_FILE,
            {"next_id": self._next_id, "recordings": [item.as_dict() for item in self._recordings]},
        )

    def _by_id(self, recording_id: int | None) -> Recording | None:
        if recording_id is None:
            return None
        return next((item for item in self._recordings if item.recording_id == recording_id), None)

    def _flash(self, text: str) -> None:
        self._toast = text
        self._toast_t = 1.8

    # -- geometry -------------------------------------------------------------
    def _recorder_panel(self) -> pygame.Rect:
        margin = current_layout.margin
        if current_layout.is_landscape:
            return pygame.Rect(margin, self.content.y + 8, int(self.content.w * 0.48), self.content.h - 22)
        return pygame.Rect(margin, self.content.y + 8, self.content.w - margin * 2, int(self.content.h * 0.54))

    def _list_panel(self) -> pygame.Rect:
        recorder = self._recorder_panel()
        if current_layout.is_landscape:
            return pygame.Rect(recorder.right + 12, recorder.y, self.content.right - current_layout.margin - recorder.right - 12, recorder.h)
        return pygame.Rect(recorder.x, recorder.bottom + 8, recorder.w, max(100, self.content.bottom - recorder.bottom - 14))

    def _list_area(self) -> pygame.Rect:
        panel = self._list_panel()
        return pygame.Rect(panel.x, panel.y + 26, panel.w, panel.h - 30)

    def _controls(self) -> dict[str, pygame.Rect]:
        panel = self._recorder_panel()
        size = 68
        gap = 14
        y = panel.bottom - 92
        total = size * 3 + gap * 2
        start = panel.centerx - total // 2
        return {
            "record": pygame.Rect(start, y, size, size),
            "pause": pygame.Rect(start + size + gap, y, size, size),
            "save": pygame.Rect(start + (size + gap) * 2, y, size, size),
        }

    def _confirm_rects(self) -> tuple[pygame.Rect, pygame.Rect]:
        panel = self._list_panel()
        y = panel.centery + 20
        width = (panel.w - 38) // 2
        return (
            pygame.Rect(panel.x + 14, y, width, 34),
            pygame.Rect(panel.x + 24 + width, y, width, 34),
        )

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if self._confirm_delete is not None:
            return self._handle_confirm(event)
        if event.kind is EventKind.WHEEL:
            return self._list_scroll.wheel(event)
        if event.kind is EventKind.POINTER_DOWN:
            self._list_scroll.pointer(event, self._list_area())
        if event.kind in (EventKind.DRAG, EventKind.DRAG_END, EventKind.POINTER_UP):
            self._list_scroll.pointer(event, self._list_area())
        if event.kind is EventKind.KEY_DOWN:
            if event.key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER):
                if self._recording:
                    self._save_recording()
                else:
                    self._start_recording()
                return True
            if self._list_scroll.key(event):
                return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        controls = self._controls()
        if controls["record"].collidepoint(event.pos):
            if not self._recording:
                self._start_recording()
            else:
                self._flash("Recording \u2014 use SAVE to finish")
            return True
        if controls["pause"].collidepoint(event.pos):
            if self._recording:
                self._paused = not self._paused
            return True
        if controls["save"].collidepoint(event.pos):
            if self._recording:
                self._save_recording()
            return True
        for recording_id, rect in self._row_rects:
            if not rect.collidepoint(event.pos):
                continue
            self._selected = recording_id
            if rect.x + rect.w - 48 < event.pos[0]:
                self._confirm_delete = recording_id
                return True
            if self._playing == recording_id:
                self._playing = None
                self._playhead = 0.0
            else:
                self._playing = recording_id
                self._playhead = 0.0
            return True
        return False

    def _start_recording(self) -> None:
        self._recording = True
        self._paused = False
        self._elapsed = 0.0
        self._seed = int(time.time()) & 0x7FFFFFFF
        self._live = _waveform(self._seed)
        self._playing = None

    def _save_recording(self) -> None:
        duration = max(0.0, self._elapsed)
        if duration < 0.2:
            self._flash("Hold REC a little longer")
            return
        name = f"Recording {len(self._recordings) + 1}"
        recording = Recording(self._next_id, name, duration, time.time(), self._seed)
        self._next_id += 1
        self._recordings.insert(0, recording)
        self._recording = False
        self._paused = False
        self._elapsed = 0.0
        self._selected = recording.recording_id
        self._save()
        self._flash("Recording saved")

    def _delete_confirmed(self) -> None:
        recording_id = self._confirm_delete
        self._confirm_delete = None
        if recording_id is None:
            return
        self._recordings = [item for item in self._recordings if item.recording_id != recording_id]
        if self._playing == recording_id:
            self._playing = None
        if self._selected == recording_id:
            self._selected = None
        self._save()
        self._flash("Recording deleted")

    def _handle_confirm(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.KEY_DOWN:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._delete_confirmed()
            elif event.key == pygame.K_ESCAPE:
                self._confirm_delete = None
            return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        yes, no = self._confirm_rects()
        if yes.collidepoint(event.pos):
            self._delete_confirmed()
        elif no.collidepoint(event.pos):
            self._confirm_delete = None
        return True

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        self._draw_recorder(surface, pal)
        self._draw_list(surface, pal)
        if self._toast:
            draw_rounded(
                surface,
                pygame.Rect(self.content.centerx - 140, self.content.bottom - 34, 280, 26),
                fill=pal.surface_raised,
                stroke=pal.stroke_soft,
                radius=13,
            )
            blit_text(surface, self.font(10, True), self._toast, pal.text, center=(self.content.centerx, self.content.bottom - 21))
        if self._confirm_delete is not None:
            self._draw_confirm(surface, pal)

    def _draw_recorder(self, surface, pal) -> None:
        panel = self._recorder_panel()
        draw_card(surface, panel, pal, radius=20)
        status = "RECORDING" if self._recording and not self._paused else ("PAUSED" if self._recording else "READY")
        blit_text(
            surface,
            self.font(9, True),
            status,
            pal.danger if self._recording and not self._paused else pal.text_muted,
            center=(panel.centerx, panel.y + 20),
            tracking=2.0,
        )
        blit_text(
            surface,
            self.mono(38, True),
            self._format_elapsed(self._elapsed),
            pal.text,
            center=(panel.centerx, panel.y + 62),
        )
        waveform_rect = pygame.Rect(panel.x + 18, panel.y + 96, panel.w - 36, max(60, panel.h - 210))
        draw_rounded(surface, waveform_rect, fill=pal.surface, stroke=pal.stroke_soft, radius=12)
        values = self._live if self._recording else _waveform(self._seed)
        self._draw_waveform(surface, waveform_rect, values, pal, pal.danger if self._recording else pal.accent)
        if not self._recording:
            blit_text(
                surface,
                self.font(9),
                "No microphone: numpy noise stands in for your voice.",
                pal.text_muted,
                center=(waveform_rect.centerx, waveform_rect.bottom + 14),
            )
        controls = self._controls()
        draw_button(
            surface,
            controls["record"],
            "REC",
            self.font(12, True),
            pal,
            fill=pal.danger if not self._recording else pal.warning,
            color=pal.on_accent,
            stroke=None,
            radius=34,
        )
        draw_button(
            surface,
            controls["pause"],
            "PAUSE" if not self._paused else "RESUME",
            self.font(9, True),
            pal,
            fill=pal.surface_alt,
            color=pal.text_secondary,
            radius=34,
        )
        draw_button(
            surface,
            controls["save"],
            "SAVE",
            self.font(9, True),
            pal,
            fill=pal.success if self._recording else pal.surface_alt,
            color=pal.on_accent if self._recording else pal.text_muted,
            stroke=None,
            radius=34,
        )

    def _draw_waveform(self, surface, rect: pygame.Rect, values: np.ndarray, pal, color) -> None:
        count = min(len(values), max(12, rect.w // 5))
        if count <= 1:
            return
        bar_w = max(2, rect.w // count - 1)
        mid = rect.centery
        for index in range(count):
            value = float(values[index * len(values) // count])
            height = max(2, int(value * (rect.h - 16) * 0.5))
            x = rect.x + 6 + index * (rect.w - 12) / count
            pygame.draw.rect(
                surface,
                color,
                pygame.Rect(int(x), mid - height, bar_w, height * 2),
                border_radius=1,
            )

    def _draw_list(self, surface, pal) -> None:
        panel = self._list_panel()
        blit_text(surface, self.font(9, True), "RECORDINGS", pal.text_muted, topleft=(panel.x, panel.y + 6), tracking=1.6)
        area = self._list_area()
        records = self._recordings
        row_h = 54
        self._list_scroll.set_metrics(area.h, len(records) * (row_h + 6))
        self._row_rects = []
        if not records:
            blit_text(surface, self.font(11), "Nothing recorded yet", pal.text_muted, center=(area.centerx, area.centery))
        for index, recording in enumerate(records):
            row = pygame.Rect(panel.x, area.y + index * (row_h + 6) - int(self._list_scroll.offset), panel.w, row_h)
            if row.bottom < area.y or row.y > area.bottom:
                continue
            active = recording.recording_id == self._selected
            draw_card(surface, row, pal, selected=active)
            play = pygame.Rect(row.x + 8, row.centery - 14, 28, 28)
            pygame.draw.circle(surface, pal.accent if self._playing != recording.recording_id else pal.warning, play.center, 14)
            if self._playing == recording.recording_id:
                pygame.draw.rect(surface, pal.on_accent, (play.centerx - 4, play.centery - 6, 8, 12), border_radius=2)
            else:
                pygame.draw.polygon(
                    surface,
                    pal.on_accent,
                    [(play.centerx - 4, play.centery - 6), (play.centerx + 6, play.centery), (play.centerx - 4, play.centery + 6)],
                )
            name_font = self.font(11, True)
            blit_text(
                surface,
                name_font,
                fit_text(name_font, recording.name, row.w - 100),
                pal.text,
                topleft=(row.x + 44, row.y + 8),
            )
            blit_text(
                surface,
                self.font(8),
                f"{self._format_elapsed(recording.duration)}  \u00b7  {time.strftime('%b %d, %H:%M', time.localtime(recording.created))}",
                pal.text_muted,
                topleft=(row.x + 44, row.y + 27),
            )
            if self._playing == recording.recording_id:
                fraction = max(0.0, min(1.0, self._playhead / max(0.01, recording.duration)))
                bar = pygame.Rect(row.x + 44, row.bottom - 10, row.w - 100, 3)
                pygame.draw.rect(surface, pal.track, bar, border_radius=1)
                pygame.draw.rect(surface, pal.warning, pygame.Rect(bar.x, bar.y, max(2, int(bar.w * fraction)), bar.h), border_radius=1)
            delete = pygame.Rect(row.right - 28, row.centery - 10, 20, 20)
            pygame.draw.line(surface, pal.danger, (delete.x + 5, delete.y + 5), (delete.right - 5, delete.bottom - 5), 2)
            pygame.draw.line(surface, pal.danger, (delete.right - 5, delete.y + 5), (delete.x + 5, delete.bottom - 5), 2)
            self._row_rects.append((recording.recording_id, row))
        draw_scrollbar(surface, area, self._list_scroll.offset, len(records) * (row_h + 6), pal)

    def _draw_confirm(self, surface, pal) -> None:
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill((0, 0, 0, 170))
        surface.blit(scrim, (0, 0))
        panel = self._list_panel()
        draw_rounded(surface, panel, fill=pal.surface_raised, stroke=pal.stroke, radius=18)
        blit_text(surface, self.font(14, True), "Delete recording?", pal.text, center=(panel.centerx, panel.centery - 28))
        yes, no = self._confirm_rects()
        draw_button(surface, yes, "DELETE", self.font(10, True), pal, fill=pal.danger, color=pal.on_accent, stroke=None)
        draw_button(surface, no, "CANCEL", self.font(10, True), pal, fill=pal.surface_alt)

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        total = max(0, int(seconds))
        minutes, secs = divmod(total, 60)
        return f"{minutes:02d}:{secs:02d}"

    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)


__all__ = ["RecorderApp", "draw_icon", "_waveform"]
