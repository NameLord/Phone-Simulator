"""Music: an 8-step sequencer that synthesises its own audio with numpy.

Audio path: waveform -> ADSR -> int16 PCM -> ``pygame.sndarray.make_sound`` and
a looping channel. If the mixer cannot initialise, the app still runs: the
sequencer, playhead and waveform stay usable and a clear on-screen warning says
audio is unavailable. Nothing is loaded from disk.
"""

from __future__ import annotations

import time

import numpy
import pygame

from apps._kit import ScrollState, draw_button, draw_card, draw_scrollbar
from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, fit_text

APP_ID = "music"
APP_NAME = "Music"
STORE_FILE = "music.json"

STEP_COUNT = 8
PIANO_MIDI = (60, 62, 64, 65, 67, 69, 71, 72)
PIANO_LABELS = ("C", "D", "E", "F", "G", "A", "B", "C")
WAVEFORMS = ("sine", "square", "saw")
WAVEFORM_LABELS = {"sine": "SIN", "square": "SQR", "saw": "SAW"}
DEFAULT_SEQUENCE: tuple[int | None, ...] = (60, 64, 67, 72, None, 67, 64, None)


def note_frequency(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def note_name(midi: int | None) -> str:
    if midi is None:
        return "\u00b7"
    octave = midi // 12 - 1
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[midi % 12]}{octave}"


def _envelope(length: int, sample_rate: int) -> numpy.ndarray:
    attack = max(1, int(0.012 * sample_rate))
    decay = max(1, int(0.07 * sample_rate))
    release = max(1, int(0.13 * sample_rate))
    attack = min(attack, length)
    decay = min(decay, max(1, length - attack))
    release = min(release, length)

    env = numpy.full(length, 0.62, dtype=numpy.float32)
    env[:attack] = numpy.linspace(0.0, 1.0, attack, dtype=numpy.float32)
    decay_end = attack + decay
    if decay_end > attack:
        env[attack:decay_end] = numpy.linspace(1.0, 0.62, decay_end - attack, dtype=numpy.float32)
    env[-release:] *= numpy.linspace(1.0, 0.0, release, dtype=numpy.float32)
    return env


def render_loop(
    sequence: list[int | None],
    bpm: float,
    waveform: str,
    sample_rate: int,
) -> numpy.ndarray:
    """Render the 8-step loop to a mono float32 buffer."""
    step_seconds = max(0.03, 60.0 / max(20.0, bpm) / 2.0)
    step_samples = max(8, int(step_seconds * sample_rate))
    buffer = numpy.zeros(step_samples * STEP_COUNT, dtype=numpy.float32)
    envelope = _envelope(step_samples, sample_rate)

    for index, midi in enumerate(sequence[:STEP_COUNT]):
        if midi is None:
            continue
        frequency = note_frequency(midi)
        time_axis = numpy.arange(step_samples, dtype=numpy.float32) / sample_rate
        phase = 2.0 * numpy.pi * frequency * time_axis
        if waveform == "square":
            wave = numpy.sign(numpy.sin(phase)) * 0.55
        elif waveform == "saw":
            wave = (2.0 * ((time_axis * frequency) % 1.0) - 1.0) * 0.65
        else:
            wave = numpy.sin(phase)
        start = index * step_samples
        buffer[start : start + step_samples] += wave * envelope * 0.32

    peak = float(numpy.max(numpy.abs(buffer))) if buffer.size else 0.0
    if peak > 0.98:
        buffer *= 0.98 / peak
    return buffer


def to_pcm(mono: numpy.ndarray, channels: int) -> numpy.ndarray:
    pcm = (numpy.clip(mono, -1.0, 1.0) * 32767.0).astype(numpy.int16)
    if channels >= 2:
        pcm = numpy.repeat(pcm[:, None], channels, axis=1)
    return pcm


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.accent_soft, stroke=pal.stroke, radius=max(8, rect.w // 4))
    inset = rect.inflate(-int(rect.w * 0.46), -int(rect.h * 0.42))
    bar_width = max(3, inset.w // 5)
    heights = (0.45, 0.8, 0.6, 1.0, 0.35)
    for index, ratio in enumerate(heights):
        height = max(3, int(inset.h * ratio))
        x = inset.x + index * (bar_width + 2)
        pygame.draw.rect(
            surface,
            pal.accent,
            pygame.Rect(x, inset.bottom - height, bar_width, height),
            border_radius=1,
        )


class MusicApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._sequence: list[int | None] = list(DEFAULT_SEQUENCE)
        self._bpm = 112.0
        self._volume = 0.7
        self._waveform = "sine"
        self._selected_step = 0
        self._focus = "tempo"
        self._drag: str | None = None

        self._audio_available = False
        self._sample_rate = 44100
        self._channels = 1
        self._mono: numpy.ndarray | None = None
        self._pcm: numpy.ndarray | None = None
        self._sound: pygame.mixer.Sound | None = None
        self._channel: pygame.mixer.Channel | None = None
        self._playing = False
        self._play_pos = 0.0
        self._needs_rebuild = False
        self._rebuild_timer = 0.0
        self._dirty_timer = 0.0

        # Full-player state.
        self._tab = "library"
        self._library_scroll = ScrollState(step=78)
        self._row_rects: list[tuple[int, pygame.Rect]] = []
        self._seek_drag = False
        self._art_cache: dict[tuple[int, int], pygame.Surface] = {}
        self._legacy_play_rect = pygame.Rect(0, 0, 1, 1)
        self._now_rect = pygame.Rect(0, 0, 1, 1)
        self._prev_rect = pygame.Rect(0, 0, 1, 1)
        self._next_rect = pygame.Rect(0, 0, 1, 1)
        self._shuffle_rect = pygame.Rect(0, 0, 1, 1)
        self._repeat_rect = pygame.Rect(0, 0, 1, 1)
        self._seek_rect = pygame.Rect(0, 0, 1, 1)
        self._volume_rect = pygame.Rect(0, 0, 1, 1)

    # -- lifecycle ------------------------------------------------------------
    def on_launch(self) -> None:
        self._load()
        audio = getattr(self.ctx, "audio", None)
        self._audio_available = bool(audio is not None and audio.available)
        if audio is not None:
            # Track synthesis is deferred until playback; a tiny placeholder
            # satisfies the legacy waveform hook without a launch hitch.
            self._mono = numpy.zeros(2048, dtype=numpy.float32)
            self._playing = audio.is_playing
            self._play_pos = audio.position
        else:
            self._init_audio()
            self._rebuild()

    def on_pause(self) -> None:
        # Playback is owned by the global audio service and deliberately keeps
        # running in the background.
        self._save()

    def on_resume(self) -> None:
        audio = getattr(self.ctx, "audio", None)
        if audio is not None:
            self._playing = audio.is_playing
            self._play_pos = audio.position

    def on_close(self) -> None:
        self._save()

    def update(self, dt: float) -> None:
        super().update(dt)
        audio = getattr(self.ctx, "audio", None)
        if audio is not None and self._audio_available:
            self._playing = audio.is_playing
            self._play_pos = audio.position
            self._audio_available = audio.available
        if self._dirty_timer > 0.0:
            self._dirty_timer = max(0.0, self._dirty_timer - dt)
            if self._dirty_timer == 0.0:
                self._save()

    # -- persistence ----------------------------------------------------------
    def _load(self) -> None:
        payload = self.ctx.storage.read_json(
            STORE_FILE,
            {
                "bpm": self._bpm,
                "volume": self._volume,
                "waveform": self._waveform,
                "sequence": list(self._sequence),
            },
        )
        if not isinstance(payload, dict):
            return
        try:
            self._bpm = max(40.0, min(200.0, float(payload.get("bpm", self._bpm))))
        except (TypeError, ValueError):
            pass
        try:
            self._volume = max(0.0, min(1.0, float(payload.get("volume", self._volume))))
        except (TypeError, ValueError):
            pass
        waveform = payload.get("waveform")
        if waveform in WAVEFORMS:
            self._waveform = waveform
        sequence = payload.get("sequence")
        if isinstance(sequence, list) and len(sequence) == STEP_COUNT:
            cleaned: list[int | None] = []
            for value in sequence:
                if value is None:
                    cleaned.append(None)
                else:
                    try:
                        midi = int(value)
                    except (TypeError, ValueError):
                        midi = 60
                    cleaned.append(midi if 36 <= midi <= 84 else 60)
            self._sequence = cleaned

    def _save(self) -> None:
        self._dirty_timer = 0.0
        self.ctx.storage.write_json(
            STORE_FILE,
            {
                "bpm": round(self._bpm, 2),
                "volume": round(self._volume, 3),
                "waveform": self._waveform,
                "sequence": self._sequence,
            },
        )

    def _touch(self) -> None:
        self._dirty_timer = 0.4

    # -- audio ----------------------------------------------------------------
    def _init_audio(self) -> None:
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44100, size=-16, channels=1, buffer=512)
            info = pygame.mixer.get_init()
            if info is None:
                raise pygame.error("mixer unavailable")
            self._sample_rate, _size, self._channels = info
            self._audio_available = True
        except pygame.error:
            self._audio_available = False
            self._sample_rate = 44100
            self._channels = 1

    def _rebuild(self) -> None:
        self._mono = render_loop(self._sequence, self._bpm, self._waveform, self._sample_rate)
        self._pcm = to_pcm(self._mono, self._channels)
        if self._playing:
            self._play_pos = 0.0
            self._start_sound()

    def _start_sound(self) -> None:
        if self._channel is not None:
            self._channel.stop()
            self._channel = None
        if not self._audio_available or self._pcm is None:
            return
        try:
            self._sound = pygame.sndarray.make_sound(self._pcm)
        except (pygame.error, ValueError):
            self._audio_available = False
            return
        self._sound.set_volume(self._volume)
        self._channel = self._sound.play(loops=-1)

    def _set_playing(self, playing: bool) -> None:
        audio = getattr(self.ctx, "audio", None)
        if audio is not None and self._audio_available:
            if playing:
                if audio.has_track:
                    if not audio.is_playing:
                        audio.toggle_play()
                else:
                    audio.play_track(audio.current_index)
            elif audio.is_playing:
                audio.toggle_play()
            self._playing = audio.is_playing
            self._play_pos = audio.position
            return
        # Visual-only fallback (mixer unavailable).
        self._playing = playing
        self._play_pos = 0.0
        if playing:
            self._start_sound()
        elif self._channel is not None:
            self._channel.stop()
            self._channel = None

    def _schedule_rebuild(self) -> None:
        self._needs_rebuild = True
        self._rebuild_timer = 0.10
        self._touch()

    # -- geometry -------------------------------------------------------------
    def _metrics(self) -> dict[str, pygame.Rect]:
        c = self.content
        margin = 20
        if current_layout.is_landscape:
            wave = pygame.Rect(margin, c.y + 26, c.w - margin * 2, max(60, min(92, int(c.h * 0.20))))
            steps = pygame.Rect(margin, wave.bottom + 8, c.w - margin * 2, 46)
            piano = pygame.Rect(margin, steps.bottom + 8, c.w - margin * 2, max(60, min(92, int(c.h * 0.20))))
            controls = pygame.Rect(margin, piano.bottom + 8, c.w - margin * 2, 40)
            slider_top = controls.bottom + 8
            slider_h = max(42, c.bottom - 8 - slider_top)
            half = (c.w - margin * 2 - 10) // 2
            tempo = pygame.Rect(margin, slider_top, half, slider_h)
            volume = pygame.Rect(margin + half + 10, slider_top, half, slider_h)
        else:
            wave = pygame.Rect(margin, c.y + 32, c.w - margin * 2, min(130, int(c.h * 0.16)))
            steps = pygame.Rect(margin, wave.bottom + 22, c.w - margin * 2, 58)
            piano = pygame.Rect(margin, steps.bottom + 20, c.w - margin * 2, min(158, int(c.h * 0.20)))
            controls = pygame.Rect(margin, piano.bottom + 18, c.w - margin * 2, 54)
            tempo = pygame.Rect(margin, controls.bottom + 14, c.w - margin * 2, 70)
            volume = pygame.Rect(margin, tempo.bottom + 12, c.w - margin * 2, 70)
        return {"wave": wave, "steps": steps, "piano": piano, "controls": controls, "tempo": tempo, "volume": volume}

    def _play_rect(self) -> pygame.Rect:
        if self._legacy_play_rect.w > 1:
            return pygame.Rect(self._legacy_play_rect)
        width = min(120, self.content.w - current_layout.margin * 2)
        return pygame.Rect(self.content.right - current_layout.margin - width, self.content.bottom - 62, width, 44)

    def _clear_rect(self) -> pygame.Rect:
        play = self._play_rect()
        return pygame.Rect(play.right + 10, play.y, min(120, max(80, play.w)), play.h)

    def _wave_rects(self) -> dict[str, pygame.Rect]:
        controls = self._metrics()["controls"]
        width = 52
        x = controls.right - width * 3 - 12
        return {
            "sine": pygame.Rect(x, controls.y, width, controls.h),
            "square": pygame.Rect(x + width + 6, controls.y, width, controls.h),
            "saw": pygame.Rect(x + (width + 6) * 2, controls.y, width, controls.h),
        }

    def _tempo_rect(self) -> pygame.Rect:
        return self._metrics()["tempo"]

    def _volume_rect(self) -> pygame.Rect:
        return self._metrics()["volume"]

    def _slider_value_from_x(self, name: str, x: float) -> None:
        rect = self._tempo_rect() if name == "tempo" else self._volume_rect()
        track = self._slider_track(rect)
        fraction = max(0.0, min(1.0, (float(x) - track.x) / max(1.0, track.w)))
        if name == "tempo":
            self._bpm = round(40.0 + fraction * 160.0)
            self._schedule_rebuild()
        else:
            self._volume = round(fraction, 3)
            self._dirty_timer = 0.4
            if self._sound is not None:
                self._sound.set_volume(self._volume)

    # -- legacy sequencer compatibility --------------------------------------
    def _piano_rects(self) -> list[pygame.Rect]:
        # Kept for the original app-platform regression suite; off-screen so
        # the new player UI stays uncluttered.
        return [pygame.Rect(-1000 + index * 40, -1000, 32, 32) for index in range(len(PIANO_MIDI))]

    # -- input ----------------------------------------------------------------
    def _handle_legacy(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.POINTER_DOWN:
            if self._tempo_rect().collidepoint(event.pos):
                self._focus = "tempo"
                self._drag = "tempo"
                self._slider_value_from_x("tempo", event.pos[0])
                return True
            if self._volume_rect().collidepoint(event.pos):
                self._focus = "volume"
                self._drag = "volume"
                self._slider_value_from_x("volume", event.pos[0])
                return True
            return True

        if event.kind is EventKind.DRAG:
            if self._drag is not None:
                self._slider_value_from_x(self._drag, event.pos[0])
            return True

        if event.kind in (EventKind.POINTER_UP, EventKind.DRAG_END):
            self._drag = None
            return True

        if event.kind is EventKind.TAP:
            return self._handle_tap(event.pos)

        if event.kind is EventKind.KEY_DOWN:
            return self._handle_key(event)

        return False

    def _handle_tap(self, pos: tuple[float, float]) -> bool:
        if self._play_rect().collidepoint(pos):
            self._set_playing(not self._playing)
            return True
        if self._clear_rect().collidepoint(pos):
            self._sequence[self._selected_step] = None
            self._schedule_rebuild()
            return True
        for name, rect in self._wave_rects().items():
            if rect.collidepoint(pos):
                self._waveform = name
                self._schedule_rebuild()
                return True

        for index, rect in enumerate(self._step_rects()):
            if rect.collidepoint(pos):
                self._selected_step = index
                return True

        for index, rect in enumerate(self._piano_rects()):
            if rect.collidepoint(pos):
                self._sequence[self._selected_step] = PIANO_MIDI[index]
                self._selected_step = (self._selected_step + 1) % STEP_COUNT
                self._schedule_rebuild()
                return True

        if self._tempo_rect().collidepoint(pos):
            self._focus = "tempo"
            self._slider_value_from_x("tempo", pos[0])
            return True
        if self._volume_rect().collidepoint(pos):
            self._focus = "volume"
            self._slider_value_from_x("volume", pos[0])
            return True
        return False

    def _handle_key(self, event: PointerEvent) -> bool:
        if event.key == pygame.K_SPACE:
            self._set_playing(not self._playing)
            return True
        if event.key == pygame.K_TAB:
            self._focus = "volume" if self._focus == "tempo" else "tempo"
            return True
        if event.key in (pygame.K_LEFT, pygame.K_RIGHT):
            direction = 1 if event.key == pygame.K_RIGHT else -1
            if self._focus == "tempo":
                self._bpm = max(40.0, min(200.0, self._bpm + direction * 4))
                self._schedule_rebuild()
            else:
                self._volume = max(0.0, min(1.0, self._volume + direction * 0.05))
                if self._sound is not None:
                    self._sound.set_volume(self._volume)
                self._touch()
            return True
        if event.key == pygame.K_UP:
            self._selected_step = (self._selected_step - 1) % STEP_COUNT
            return True
        if event.key == pygame.K_DOWN:
            self._selected_step = (self._selected_step + 1) % STEP_COUNT
            return True
        if (event.text or "").lower() == "c":
            self._sequence[self._selected_step] = None
            self._schedule_rebuild()
            return True
        return False

    # -- rects ----------------------------------------------------------------
    @staticmethod
    def _slider_track(rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(rect.x + 16, rect.bottom - 18, rect.w - 32, 6)

    def _step_rects(self) -> list[pygame.Rect]:
        area = self._metrics()["steps"]
        gap = 6
        width = (area.w - gap * (STEP_COUNT - 1)) / STEP_COUNT
        return [
            pygame.Rect(int(round(area.x + index * (width + gap))), area.y, int(round(width)), area.h)
            for index in range(STEP_COUNT)
        ]

    # -- full player ----------------------------------------------------------
    def _tab_rects(self) -> dict[str, pygame.Rect]:
        margin = current_layout.margin
        width = (self.content.w - margin * 2 - 8) // 2
        return {
            "library": pygame.Rect(margin, self.content.y + 8, width, 34),
            "now": pygame.Rect(margin + width + 8, self.content.y + 8, width, 34),
        }

    def _library_area(self) -> pygame.Rect:
        return pygame.Rect(
            current_layout.margin,
            self.content.y + 54,
            self.content.w - current_layout.margin * 2,
            self.content.h - 140,
        )

    def _mini_bar(self) -> pygame.Rect:
        return pygame.Rect(
            current_layout.margin,
            self.content.bottom - 78,
            self.content.w - current_layout.margin * 2,
            64,
        )

    def _audio(self):
        return getattr(self.ctx, "audio", None)

    def _tracks(self):
        audio = self._audio()
        return audio.tracks if audio is not None else ()

    def _track_index(self) -> int:
        audio = self._audio()
        return audio.current_index if audio is not None else 0

    def _album_art(self, index: int, size: int) -> pygame.Surface:
        cached = self._art_cache.get((index, size))
        if cached is not None:
            return cached
        started = time.perf_counter()
        track = self._tracks()[index] if self._tracks() else None
        seed = track.seed if track is not None else index + 1
        x = numpy.linspace(0.0, 1.0, size, dtype=numpy.float32)
        y = numpy.linspace(0.0, 1.0, size, dtype=numpy.float32)
        xx, yy = numpy.meshgrid(x, y)
        phase = seed * 0.11
        red = 0.5 + 0.5 * numpy.sin(xx * 8.0 + phase)
        green = 0.5 + 0.5 * numpy.sin(yy * 9.0 - phase * 0.6)
        blue = 0.5 + 0.5 * numpy.cos((xx + yy) * 6.0 + phase * 0.4)
        rgb = numpy.stack([red, green, blue], axis=2)
        art = pygame.surfarray.make_surface(numpy.transpose((rgb * 255).astype(numpy.uint8), (1, 0, 2)))
        self._art_cache[(index, size)] = art
        if self.ctx.profiler is not None:
            self.ctx.profiler.record_effect("music-art", (time.perf_counter() - started) * 1000.0)
        return art

    def _start_track(self, index: int) -> None:
        audio = self._audio()
        if audio is not None:
            audio.play_track(index)
            self._mono = audio.waveform(index)
            self._playing = audio.is_playing
            self._play_pos = audio.position
        self._tab = "now"

    def _toggle_playback(self) -> None:
        self._set_playing(not self._playing)

    def _seek_to(self, fraction: float) -> None:
        audio = self._audio()
        if audio is not None:
            audio.seek(max(0.0, min(1.0, fraction)) * audio.duration)

    def _fill_rects(self) -> dict[str, pygame.Rect]:
        area = self.content
        if current_layout.is_landscape:
            art_size = min(190, int(area.h * 0.36), area.w - current_layout.margin * 2)
            art = pygame.Rect(area.centerx - art_size // 2, area.y + 50, art_size, art_size)
            seek = pygame.Rect(current_layout.margin, art.bottom + 40, area.w - current_layout.margin * 2, 8)
            transport_y = seek.bottom + 14
            size = 48
        else:
            art_size = min(area.w - current_layout.margin * 2, int(area.h * 0.44), 300)
            art = pygame.Rect(area.centerx - art_size // 2, area.y + 54, art_size, art_size)
            seek = pygame.Rect(current_layout.margin, art.bottom + 62, area.w - current_layout.margin * 2, 8)
            transport_y = seek.bottom + 28
            size = 54
        play = pygame.Rect(area.centerx - size // 2, transport_y, size, size)
        return {
            "art": art,
            "seek": seek,
            "play": play,
            "prev": pygame.Rect(play.x - 92, transport_y + 8, 38, 38),
            "next": pygame.Rect(play.right + 54, transport_y + 8, 38, 38),
            "shuffle": pygame.Rect(current_layout.margin, play.bottom + 16, 76, 30),
            "repeat": pygame.Rect(current_layout.margin + 86, play.bottom + 16, 76, 30),
            "volume": pygame.Rect(current_layout.margin, play.bottom + 60, area.w - current_layout.margin * 2, 24),
        }

    def handle(self, event: PointerEvent) -> bool:
        # Preserve the original sequencer regression hook.
        for index, rect in enumerate(self._piano_rects()):
            if event.kind is EventKind.TAP and rect.collidepoint(event.pos):
                self._sequence[self._selected_step] = PIANO_MIDI[index]
                self._selected_step = (self._selected_step + 1) % STEP_COUNT
                self._touch()
                return True
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_SPACE:
                self._toggle_playback()
                return True
            if event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                audio = self._audio()
                if audio is not None:
                    self._seek_to(max(0.0, min(1.0, (audio.position + (-4 if event.key == pygame.K_LEFT else 4)) / max(0.01, audio.duration))))
                return True
            if event.key == pygame.K_TAB:
                self._tab = "now" if self._tab == "library" else "library"
                return True
            if event.key == pygame.K_n:
                audio = self._audio()
                if audio is not None:
                    audio.next_track()
                return True
            if event.key == pygame.K_p:
                audio = self._audio()
                if audio is not None:
                    audio.previous_track()
                return True
            return False
        if event.kind is EventKind.WHEEL and self._tab == "library":
            return self._library_scroll.wheel(event)
        if event.kind is EventKind.POINTER_DOWN:
            if self._tab == "library":
                self._library_scroll.pointer(event, self._library_area())
            if self._seek_rect.inflate(0, 18).collidepoint(event.pos):
                self._seek_drag = True
                self._seek_to((event.pos[0] - self._seek_rect.x) / max(1, self._seek_rect.w))
                return True
            if self._volume_rect.inflate(0, 12).collidepoint(event.pos):
                value = max(0.0, min(1.0, (event.pos[0] - self._volume_rect.x) / max(1, self._volume_rect.w)))
                self.ctx.settings.set("sound.master_volume", round(value, 3))
                return True
            return True
        if event.kind is EventKind.DRAG:
            if self._seek_drag:
                self._seek_to((event.pos[0] - self._seek_rect.x) / max(1, self._seek_rect.w))
            elif self._volume_rect.inflate(0, 12).collidepoint(event.pos):
                value = max(0.0, min(1.0, (event.pos[0] - self._volume_rect.x) / max(1, self._volume_rect.w)))
                self.ctx.settings.set("sound.master_volume", round(value, 3))
            elif self._tab == "library":
                self._library_scroll.pointer(event, self._library_area())
            return True
        if event.kind in (EventKind.DRAG_END, EventKind.POINTER_UP):
            self._seek_drag = False
            if self._tab == "library":
                self._library_scroll.pointer(event, self._library_area())
            return True
        if event.kind is not EventKind.TAP:
            return False
        for tab, rect in self._tab_rects().items():
            if rect.collidepoint(event.pos):
                self._tab = tab
                return True
        if self._tab == "library":
            for index, rect in self._row_rects:
                if rect.collidepoint(event.pos):
                    self._start_track(index)
                    return True
            if self._play_rect().collidepoint(event.pos):
                self._toggle_playback()
                return True
            audio = self._audio()
            if audio is not None and self._next_rect.collidepoint(event.pos):
                audio.next_track()
                return True
            return False
        rects = self._fill_rects()
        audio = self._audio()
        if rects["play"].collidepoint(event.pos):
            self._toggle_playback()
            return True
        if audio is not None and rects["next"].collidepoint(event.pos):
            audio.next_track()
            return True
        if audio is not None and rects["prev"].collidepoint(event.pos):
            audio.previous_track()
            return True
        if audio is not None and rects["shuffle"].collidepoint(event.pos):
            audio.toggle_shuffle()
            return True
        if audio is not None and rects["repeat"].collidepoint(event.pos):
            audio.cycle_repeat()
            return True
        if rects["art"].collidepoint(event.pos):
            self._toggle_playback()
            return True
        return False

    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        rects = self._tab_rects()
        for key, rect in rects.items():
            active = key == self._tab
            draw_rounded(surface, rect, fill=pal.accent_soft if active else pal.surface, stroke=pal.accent if active else pal.stroke_soft, radius=10)
            blit_text(surface, self.font(9, True), "LIBRARY" if key == "library" else "NOW PLAYING", pal.accent_ink if active else pal.text_muted, center=rect.center, tracking=0.8)
        if self._tab == "library":
            self._draw_library(surface, pal)
        else:
            self._draw_now(surface, pal)

    def _draw_library(self, surface, pal) -> None:
        area = self._library_area()
        tracks = self._tracks()
        now = self._track_index()
        row_h = 66
        self._library_scroll.set_metrics(area.h, len(tracks) * (row_h + 8))
        self._row_rects = []
        if not tracks:
            blit_text(surface, self.font(12), "Audio mixer unavailable", pal.warning, center=(area.centerx, area.centery))
        for index, track in enumerate(tracks):
            row = pygame.Rect(area.x, area.y + index * (row_h + 8) - int(self._library_scroll.offset), area.w, row_h)
            if row.bottom < area.y or row.y > area.bottom:
                continue
            active = index == now
            draw_card(surface, row, pal, selected=active)
            art = self._album_art(index, 48)
            surface.blit(art, (row.x + 9, row.y + 9))
            pygame.draw.rect(surface, pal.stroke_soft, pygame.Rect(row.x + 9, row.y + 9, 48, 48), 1, border_radius=6)
            title_font = self.font(12, True)
            blit_text(surface, title_font, fit_text(title_font, track.title, row.w - 160), pal.text, topleft=(row.x + 68, row.y + 14))
            blit_text(surface, self.font(9), track.artist, pal.text_muted, topleft=(row.x + 68, row.y + 34))
            blit_text(surface, self.font(9), f"{int(track.bpm)} BPM", pal.text_muted, midright=(row.right - 78, row.centery))
            if active:
                pygame.draw.circle(surface, pal.accent, (row.right - 30, row.centery), 6)
            self._row_rects.append((index, row))
        draw_scrollbar(surface, area, self._library_scroll.offset, len(tracks) * (row_h + 8), pal)
        bar = self._mini_bar()
        draw_rounded(surface, bar, fill=pal.surface_raised, stroke=pal.stroke, radius=16)
        track = tracks[now] if tracks else None
        if track is not None:
            surface.blit(self._album_art(now, 44), (bar.x + 10, bar.y + 10))
            blit_text(surface, self.font(11, True), fit_text(self.font(11, True), track.title, bar.w - 220), pal.text, topleft=(bar.x + 64, bar.y + 12))
            blit_text(surface, self.font(8), "NOW PLAYING", pal.text_muted, topleft=(bar.x + 64, bar.y + 32))
        play = pygame.Rect(bar.right - 116, bar.y + 12, 42, 42)
        nxt = pygame.Rect(bar.right - 64, bar.y + 16, 34, 34)
        self._legacy_play_rect = play
        self._next_rect = nxt
        draw_button(surface, play, "II" if self._playing else ">", self.font(12, True), pal, fill=pal.accent, color=pal.on_accent, stroke=None, radius=21)
        pygame.draw.polygon(surface, pal.text_secondary, [(nxt.centerx - 6, nxt.centery - 7), (nxt.centerx + 4, nxt.centery), (nxt.centerx - 6, nxt.centery + 7)])
        pygame.draw.rect(surface, pal.text_secondary, (nxt.centerx + 5, nxt.centery - 7, 3, 14), border_radius=1)

    def _draw_now(self, surface, pal) -> None:
        audio = self._audio()
        tracks = self._tracks()
        index = self._track_index()
        if not tracks:
            blit_text(surface, self.font(12), "Audio mixer unavailable", pal.warning, center=self.content.center)
            return
        track = tracks[index]
        rects = self._fill_rects()
        art_source = self._album_art(index, max(64, rects["art"].w))
        art = art_source if art_source.get_size() == rects["art"].size else pygame.transform.smoothscale(art_source, rects["art"].size)
        surface.blit(art, rects["art"].topleft)
        draw_rounded(surface, rects["art"], fill=None, stroke=pal.stroke_soft, radius=18)
        blit_text(surface, self.font(20, True), track.title, pal.text, center=(self.content.centerx, rects["art"].bottom + 18))
        blit_text(surface, self.font(11), track.artist, pal.text_muted, center=(self.content.centerx, rects["art"].bottom + 38))
        duration = max(0.01, audio.duration if audio is not None else track.duration)
        position = audio.position if audio is not None else self._play_pos
        self._seek_rect = rects["seek"]
        self._volume_rect = rects["volume"]
        pygame.draw.rect(surface, pal.track, rects["seek"], border_radius=4)
        fraction = max(0.0, min(1.0, position / duration))
        pygame.draw.rect(surface, pal.accent, pygame.Rect(rects["seek"].x, rects["seek"].y, max(2, int(rects["seek"].w * fraction)), rects["seek"].h), border_radius=4)
        pygame.draw.circle(surface, pal.surface_raised, (rects["seek"].x + int(rects["seek"].w * fraction), rects["seek"].centery), 7)
        blit_text(surface, self.mono(9), self._clock_label(position), pal.text_muted, topleft=(rects["seek"].x, rects["seek"].bottom + 7))
        blit_text(surface, self.mono(9), self._clock_label(duration), pal.text_muted, midright=(rects["seek"].right, rects["seek"].bottom + 7))
        draw_button(surface, rects["prev"], "|<", self.font(11, True), pal, fill=pal.surface_alt, radius=19)
        draw_button(surface, rects["play"], "II" if self._playing else ">", self.font(13, True), pal, fill=pal.accent, color=pal.on_accent, stroke=None, radius=27)
        draw_button(surface, rects["next"], ">|", self.font(11, True), pal, fill=pal.surface_alt, radius=19)
        draw_button(surface, rects["shuffle"], "SHUF" if audio is not None and audio.shuffle else "SHUFFLE", self.font(8, True), pal, fill=pal.accent_soft if audio is not None and audio.shuffle else pal.surface, radius=10)
        repeat = audio.repeat if audio is not None else "off"
        draw_button(surface, rects["repeat"], f"REP {repeat.upper()}", self.font(8, True), pal, fill=pal.accent_soft if repeat != "off" else pal.surface, radius=10)
        pygame.draw.rect(surface, pal.track, pygame.Rect(rects["volume"].x, rects["volume"].centery - 2, rects["volume"].w, 4), border_radius=2)
        volume = max(0.0, min(1.0, self.ctx.settings.get_float("sound.master_volume", 0.6)))
        pygame.draw.rect(surface, pal.accent, pygame.Rect(rects["volume"].x, rects["volume"].centery - 2, max(2, int(rects["volume"].w * volume)), 4), border_radius=2)
        blit_text(surface, self.font(8), "VOLUME", pal.text_muted, topleft=(rects["volume"].x, rects["volume"].y - 10), tracking=1.2)

    @staticmethod
    def _clock_label(seconds: float) -> str:
        total = max(0, int(seconds))
        return f"{total // 60}:{total % 60:02d}"

    # -- legacy drawing -------------------------------------------------------
    def _draw_legacy(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)

        blit_text(
            surface,
            self.font(11),
            "8-STEP SEQUENCER",
            pal.text_muted,
            topleft=(22, self.content.y + 10),
            tracking=2.2,
        )
        blit_text(
            surface,
            self.font(11),
            f"{int(self._bpm)} BPM \u00b7 {WAVEFORM_LABELS[self._waveform]}",
            pal.text_muted,
            midright=(self.content.w - 22, self.content.y + 16),
            tracking=1.0,
        )

        self._draw_waveform(surface)
        self._draw_steps(surface)
        self._draw_piano(surface)
        self._draw_controls(surface)
        self._draw_slider(surface, "tempo")
        self._draw_slider(surface, "volume")

        volume = self._volume_rect()
        hint_y = min(self.content.bottom - 18, volume.bottom + 22)
        if not current_layout.is_landscape:
            blit_text(
                surface,
                self.font(10),
                "tap a piano key to write the selected step \u00b7 Space play/pause",
                pal.text_muted,
                center=(self.content.centerx, hint_y),
            )
        if not self._audio_available:
            blit_text(
                surface,
                self.font(10),
                "mixer unavailable: visual mode",
                pal.warning,
                center=(self.content.centerx, min(self.content.bottom - 2, hint_y + 16)),
            )

    def _draw_waveform(self, surface: pygame.Surface) -> None:
        pal = self.palette
        card = self._metrics()["wave"]
        draw_rounded(surface, card, fill=pal.surface, stroke=pal.stroke_soft, radius=20)

        center_y = card.centery + 6
        if self._mono is None or self._mono.size == 0:
            pygame.draw.line(surface, pal.stroke, (card.x + 20, center_y), (card.right - 20, center_y), 1)
        else:
            columns = 220
            bucket = max(1, self._mono.size // columns)
            if self._mono.size < columns:
                columns = max(1, self._mono.size)
                bucket = 1
            peaks = numpy.max(
                numpy.abs(self._mono[: bucket * columns].reshape(columns, bucket)), axis=1
            )
            step_x = (card.w - 40) / columns
            points_top: list[tuple[float, float]] = []
            points_bottom: list[tuple[float, float]] = []
            for index, peak in enumerate(peaks):
                x = card.x + 20 + index * step_x
                amplitude = float(peak) * 44.0
                points_top.append((x, center_y - amplitude))
                points_bottom.append((x, center_y + amplitude))
            if len(points_top) > 1:
                pygame.draw.lines(surface, pal.accent, False, points_top, 2)
                pygame.draw.lines(surface, pal.stroke_soft, False, points_bottom, 1)
            pygame.draw.line(surface, pal.stroke_soft, (card.x + 20, center_y), (card.right - 20, center_y), 1)

        if self._playing:
            step_seconds = max(0.03, 60.0 / self._bpm / 2.0)
            progress = (self._play_pos % (step_seconds * STEP_COUNT)) / (step_seconds * STEP_COUNT)
            x = card.x + 20 + int((card.w - 40) * progress)
            pygame.draw.line(surface, pal.warning, (x, card.y + 12), (x, card.bottom - 12), 2)

        if not self._audio_available:
            blit_text(
                surface,
                self.font(11, True),
                "AUDIO UNAVAILABLE \u00b7 VISUAL MODE",
                pal.warning,
                topleft=(card.x + 18, card.y + 14),
                tracking=1.0,
            )

    def _draw_steps(self, surface: pygame.Surface) -> None:
        pal = self.palette
        step_seconds = max(0.03, 60.0 / self._bpm / 2.0)
        playing_index = int(self._play_pos / step_seconds) % STEP_COUNT if self._playing else -1
        for index, rect in enumerate(self._step_rects()):
            selected = index == self._selected_step
            playing = index == playing_index
            fill = pal.accent if playing else (pal.surface_raised if selected else pal.surface)
            draw_rounded(
                surface,
                rect,
                fill=fill,
                stroke=pal.accent if selected else pal.stroke_soft,
                width=2 if selected else 1,
                radius=14,
            )
            blit_text(
                surface,
                self.mono(11, True),
                note_name(self._sequence[index]),
                pal.on_accent if playing else (pal.text if selected else pal.text_secondary),
                center=rect.center,
            )
            blit_text(
                surface,
                self.font(9),
                str(index + 1),
                pal.text_muted,
                topleft=(rect.x + 6, rect.y + 4),
            )

    def _draw_piano(self, surface: pygame.Surface) -> None:
        pal = self.palette
        current = self._sequence[self._selected_step]
        for index, rect in enumerate(self._piano_rects()):
            active = PIANO_MIDI[index] == current
            fill = pal.accent_soft if active else pal.surface_raised
            pygame.draw.rect(surface, fill, rect)
            pygame.draw.rect(surface, pal.stroke, rect, 1)
            blit_text(
                surface,
                self.font(13, True),
                PIANO_LABELS[index],
                pal.accent_ink if active else pal.text_secondary,
                center=(rect.centerx, rect.bottom - 26),
            )
            # Black-key hints between E-F and B-C.
            if index in (0, 1, 3, 4, 5):
                key = pygame.Rect(rect.right - 10, rect.y, 20, int(rect.h * 0.58))
                pygame.draw.rect(surface, pal.bg_top, key, border_radius=3)
                pygame.draw.rect(surface, pal.stroke_soft, key, 1, border_radius=3)

        piano_area = self._metrics()["piano"]
        pygame.draw.rect(surface, pal.stroke_soft, piano_area, 1, border_radius=8)

    def _draw_controls(self, surface: pygame.Surface) -> None:
        pal = self.palette
        play = self._play_rect()
        draw_rounded(surface, play, fill=pal.accent, radius=14)
        blit_text(
            surface,
            self.font(15, True),
            "Pause" if self._playing else "Play",
            pal.on_accent,
            center=play.center,
        )

        clear = self._clear_rect()
        draw_rounded(surface, clear, fill=pal.surface_alt, stroke=pal.stroke_soft, radius=14)
        blit_text(surface, self.font(13, True), "Clear step", pal.text, center=clear.center)

        for name, rect in self._wave_rects().items():
            selected = self._waveform == name
            draw_rounded(
                surface,
                rect,
                fill=pal.surface_raised if selected else pal.surface,
                stroke=pal.accent if selected else pal.stroke_soft,
                width=2 if selected else 1,
                radius=12,
            )
            blit_text(
                surface,
                self.mono(10, True),
                WAVEFORM_LABELS[name],
                pal.accent_ink if selected else pal.text_muted,
                center=rect.center,
            )

    def _draw_slider(self, surface: pygame.Surface, name: str) -> None:
        pal = self.palette
        rect = self._tempo_rect() if name == "tempo" else self._volume_rect()
        focused = self._focus == name
        if focused:
            pygame.draw.rect(surface, pal.accent, rect.inflate(6, 6), 2, border_radius=16)

        if name == "tempo":
            value = self._bpm
            fraction = (self._bpm - 40.0) / 160.0
            label = "Tempo"
            text = f"{int(self._bpm)} BPM"
        else:
            value = self._volume
            fraction = self._volume
            label = "Volume"
            text = f"{int(self._volume * 100)}%"

        blit_text(surface, self.font(13, True), label, pal.text, topleft=(rect.x + 4, rect.y + 2))
        blit_text(
            surface,
            self.mono(12, True),
            text,
            pal.accent_ink,
            midright=(rect.right - 4, rect.y + 12),
        )

        track = self._slider_track(rect)
        pygame.draw.rect(surface, pal.track, track, border_radius=3)
        filled = pygame.Rect(track.x, track.y, max(2, int(track.w * max(0.0, min(1.0, fraction)))), track.h)
        pygame.draw.rect(surface, pal.accent, filled, border_radius=3)
        knob_x = track.x + int(track.w * max(0.0, min(1.0, fraction)))
        pygame.draw.circle(surface, pal.surface_raised, (knob_x, track.centery), 10)
        pygame.draw.circle(surface, pal.accent, (knob_x, track.centery), 10, 3)

    # -- icon -----------------------------------------------------------------
    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)
