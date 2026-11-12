"""Single audio authority for media, UI sounds and rings.

Three reserved mixer channels keep concerns separate:

* music   - generated procedural tracks, survives leaving the Music app
* ui      - short pre-generated click/swipe/unlock/notification sounds
* ring    - looping call/alarm ring tone

Nothing is loaded from disk. If the mixer cannot initialise every method
degrades to a no-op and the apps remain usable in visual-only mode.
"""

from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy
import pygame

if TYPE_CHECKING:
    from services.settings import SettingsService
    from services.storage import Storage

LOG = logging.getLogger("phoneos.audio")

SAMPLE_RATE = 44100
TRACK_SECONDS = 24.0
REPEAT_MODES = ("off", "one", "all")
TONE_NAMES = ("click", "swipe", "unlock", "notification")


@dataclass(frozen=True, slots=True)
class Track:
    index: int
    title: str
    artist: str
    seed: int
    bpm: float
    waveform: str
    duration: float
    melody: tuple[int | None, ...]
    bass: tuple[int | None, ...]


TRACKS: tuple[Track, ...] = (
    Track(
        0,
        "Neon Rain",
        "Procedural",
        11,
        104,
        "sine",
        TRACK_SECONDS,
        (60, 64, 67, 72, 67, 64, 62, 67, 60, 64, 69, 72, 69, 64, 62, 59),
        (36, 36, 41, 43, 36, 36, 41, 38),
    ),
    Track(
        1,
        "Glass Transit",
        "Procedural",
        23,
        122,
        "saw",
        TRACK_SECONDS,
        (64, 67, 71, 74, 71, 67, 66, 71, 64, 67, 71, 76, 74, 71, 69, 66),
        (40, 40, 45, 47, 40, 40, 45, 43),
    ),
    Track(
        2,
        "Low Orbit",
        "Procedural",
        37,
        88,
        "square",
        TRACK_SECONDS,
        (57, 60, 64, 67, 64, 60, 59, 64, 57, 60, 65, 67, 65, 60, 59, 55),
        (33, 33, 38, 40, 33, 33, 38, 35),
    ),
    Track(
        3,
        "Paper Sun",
        "Procedural",
        51,
        96,
        "sine",
        TRACK_SECONDS,
        (62, 65, 69, 74, 72, 69, 65, 62, 60, 64, 67, 72, 69, 67, 64, 60),
        (38, 38, 43, 45, 38, 38, 43, 40),
    ),
    Track(
        4,
        "Static Bloom",
        "Procedural",
        67,
        112,
        "saw",
        TRACK_SECONDS,
        (59, 62, 67, 71, 67, 62, 60, 67, 59, 63, 67, 74, 71, 67, 63, 60),
        (35, 35, 40, 42, 35, 35, 40, 37),
    ),
)


def _midi_frequency(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def _envelope(length: int, attack: float = 0.01, release: float = 0.12) -> numpy.ndarray:
    attack_n = max(1, int(attack * SAMPLE_RATE))
    release_n = max(1, int(release * SAMPLE_RATE))
    env = numpy.ones(length, dtype=numpy.float32)
    attack_n = min(length, attack_n)
    release_n = min(length, release_n)
    env[:attack_n] = numpy.linspace(0.0, 1.0, attack_n, dtype=numpy.float32)
    env[-release_n:] *= numpy.linspace(1.0, 0.0, release_n, dtype=numpy.float32)
    return env


def render_track(track: Track, sample_rate: int = SAMPLE_RATE) -> numpy.ndarray:
    """Render one procedural track to a mono float32 buffer."""
    length = max(1, int(track.duration * sample_rate))
    buffer = numpy.zeros(length, dtype=numpy.float32)
    rng = random.Random(track.seed)
    step = 60.0 / max(20.0, track.bpm) / 2.0
    step_samples = max(8, int(step * sample_rate))
    time_axis = numpy.arange(step_samples, dtype=numpy.float32) / sample_rate
    env = _envelope(step_samples, 0.008, 0.16)

    for index, midi in enumerate(track.melody):
        if midi is None:
            continue
        frequency = _midi_frequency(midi)
        phase = 2.0 * math.tau * frequency * time_axis
        if track.waveform == "square":
            wave = numpy.sign(numpy.sin(phase)) * 0.32
        elif track.waveform == "saw":
            wave = (2.0 * ((time_axis * frequency) % 1.0) - 1.0) * 0.32
        else:
            wave = numpy.sin(phase) * 0.36
        start = index * step_samples * 2
        end = min(length, start + step_samples)
        if end > start:
            buffer[start:end] += wave[: end - start] * env[: end - start]

    for index, midi in enumerate(track.bass):
        if midi is None:
            continue
        frequency = _midi_frequency(midi)
        samples = step_samples * 4
        phase = 2.0 * math.tau * frequency * (numpy.arange(samples, dtype=numpy.float32) / sample_rate)
        wave = numpy.sin(phase) * _envelope(samples, 0.01, 0.3) * 0.18
        start = index * step_samples * 4
        end = min(length, start + samples)
        if end > start:
            buffer[start:end] += wave[: end - start]

    # Soft kick on every fourth eighth-note, plus a quiet noise hat.
    for index in range(0, len(track.melody), 4):
        start = index * step_samples * 2
        samples = min(int(0.18 * sample_rate), max(1, length - start))
        if samples <= 0:
            continue
        t = numpy.arange(samples, dtype=numpy.float32) / sample_rate
        kick = numpy.sin(math.tau * (58.0 - 22.0 * t) * t) * numpy.exp(-t * 22.0) * 0.28
        buffer[start : start + samples] += kick
    noise_rng = numpy.random.default_rng(track.seed + 991)
    noise = noise_rng.random(length, dtype=numpy.float32) * 2.0 - 1.0
    phase = (numpy.arange(length, dtype=numpy.float32) % max(1, step_samples)) / sample_rate
    hat = numpy.exp(-phase * 55.0).astype(numpy.float32)
    buffer += noise * hat * 0.035

    peak = float(numpy.max(numpy.abs(buffer))) if buffer.size else 0.0
    if peak > 0.98:
        buffer *= 0.98 / peak
    return buffer


def _tone(frequency: float, seconds: float, volume: float = 0.3) -> numpy.ndarray:
    length = max(8, int(seconds * SAMPLE_RATE))
    t = numpy.arange(length, dtype=numpy.float32) / SAMPLE_RATE
    env = numpy.exp(-t * (4.5 / max(0.02, seconds)))
    return numpy.sin(math.tau * frequency * t) * env * volume


def _noise_burst(seconds: float, volume: float = 0.2) -> numpy.ndarray:
    length = max(8, int(seconds * SAMPLE_RATE))
    rng = numpy.random.default_rng(0x51EE5)
    env = numpy.linspace(1.0, 0.0, length, dtype=numpy.float32) ** 2.0
    return (rng.random(length, dtype=numpy.float32) * 2.0 - 1.0) * env * volume


class AudioService:
    """Reserved-channel audio manager."""

    def __init__(
        self,
        settings: "SettingsService",
        storage: "Storage | None" = None,
        profiler=None,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._profiler = profiler
        self._available = False
        self._channels: tuple[pygame.mixer.Channel, ...] | None = None
        self._ui_sounds: dict[str, pygame.mixer.Sound] = {}
        self._ring_sound: pygame.mixer.Sound | None = None
        self._track_sounds: dict[int, pygame.mixer.Sound] = {}
        self._track_waves: dict[int, numpy.ndarray] = {}
        self._current = 0
        self._state = "stopped"
        self._offset = 0.0
        self._started = 0.0
        self._shuffle = False
        self._repeat = "off"
        self._playlist = list(range(len(TRACKS)))
        self._last_alert = False
        self._load_state()
        self._init_mixer()

    # -- setup / fallback -----------------------------------------------------
    @property
    def available(self) -> bool:
        return self._available

    def _init_mixer(self) -> None:
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=1, buffer=512)
            info = pygame.mixer.get_init()
            if info is None:
                raise pygame.error("mixer unavailable")
            actual_rate, _size, channels = info
            pygame.mixer.set_num_channels(max(3, pygame.mixer.get_num_channels()))
            pygame.mixer.set_reserved(3)
            self._channels = (pygame.mixer.Channel(0), pygame.mixer.Channel(1), pygame.mixer.Channel(2))
            self._sample_rate = actual_rate
            self._channels_count = channels
            self._available = True
            self._build_ui_sounds()
            self._build_ring()
        except (pygame.error, OSError, ValueError, TypeError) as exc:
            LOG.warning("audio unavailable: %s", exc)
            self._available = False
            self._channels = None
            self._sample_rate = SAMPLE_RATE
            self._channels_count = 1

    def _sound(self, mono: numpy.ndarray) -> pygame.mixer.Sound | None:
        if not self._available:
            return None
        try:
            pcm = numpy.clip(mono, -1.0, 1.0) * 32767.0
            pcm = pcm.astype(numpy.int16)
            if self._channels_count >= 2:
                pcm = numpy.repeat(pcm[:, None], self._channels_count, axis=1)
            return pygame.sndarray.make_sound(numpy.ascontiguousarray(pcm))
        except (pygame.error, ValueError):
            LOG.warning("could not create sound")
            return None

    def _build_ui_sounds(self) -> None:
        self._ui_sounds = {}
        click = numpy.concatenate((_tone(920.0, 0.045, 0.26), _tone(1380.0, 0.035, 0.08)))
        swipe = _noise_burst(0.07, 0.16)
        unlock = numpy.concatenate((_tone(660.0, 0.09, 0.24), _tone(990.0, 0.12, 0.24)))
        notification = numpy.concatenate((_tone(1046.0, 0.11, 0.25), _tone(1318.0, 0.16, 0.22)))
        for name, data in (
            ("click", click),
            ("swipe", swipe),
            ("unlock", unlock),
            ("notification", notification),
        ):
            sound = self._sound(data)
            if sound is not None:
                self._ui_sounds[name] = sound

    def _build_ring(self) -> None:
        first = _tone(880.0, 0.22, 0.28)
        gap = numpy.zeros(int(0.12 * SAMPLE_RATE), dtype=numpy.float32)
        second = _tone(1174.0, 0.22, 0.28)
        period = numpy.concatenate((first, gap, second, gap))
        self._ring_sound = self._sound(numpy.tile(period, 4))

    # -- settings -------------------------------------------------------------
    def _master(self) -> float:
        try:
            return max(0.0, min(1.0, float(self._settings.get_float("sound.master_volume", 0.6))))
        except (TypeError, ValueError):
            return 0.6

    def _muted(self) -> bool:
        return self._settings.get_bool("sound.muted", False)

    def _dnd(self) -> bool:
        return self._settings.get_bool("simulation.dnd", False)

    def _music_gain(self) -> float:
        # DND suppresses non-alarm audio; the ring channel deliberately ignores
        # both mute and DND so alarms and calls still work.
        if self._muted() or self._dnd():
            return 0.0
        return self._master()

    # -- UI cues --------------------------------------------------------------
    def play_ui(self, name: str = "click") -> bool:
        if not self._available or self._channels is None:
            return False
        if self._muted() or self._dnd():
            return False
        sound = self._ui_sounds.get(name)
        if sound is None:
            return False
        try:
            channel = self._channels[1]
            channel.set_volume(self._master())
            channel.play(sound)
            return True
        except pygame.error:
            return False

    # -- rings ----------------------------------------------------------------
    def sync_alerts(self, call_ringing: bool, alarm_active: bool) -> None:
        if not self._available or self._channels is None or self._ring_sound is None:
            return
        active = bool(call_ringing or alarm_active)
        channel = self._channels[2]
        channel.set_volume(self._master())
        if active and not self._last_alert:
            try:
                channel.play(self._ring_sound, loops=-1)
            except pygame.error:
                return
        elif not active and self._last_alert:
            channel.stop()
        self._last_alert = active

    # -- tracks ---------------------------------------------------------------
    @property
    def tracks(self) -> tuple[Track, ...]:
        return TRACKS

    @property
    def current_index(self) -> int:
        return self._current

    @property
    def current_track(self) -> Track:
        return TRACKS[self._current]

    @property
    def is_playing(self) -> bool:
        return self._state == "playing"

    @property
    def has_track(self) -> bool:
        return self._state in ("playing", "paused")

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    @property
    def repeat(self) -> str:
        return self._repeat

    @property
    def position(self) -> float:
        if self._state == "playing":
            pos = self._offset + (time.monotonic() - self._started)
            return max(0.0, min(self.current_track.duration, pos))
        return self._offset

    @property
    def duration(self) -> float:
        return self.current_track.duration

    def waveform(self, index: int | None = None) -> numpy.ndarray:
        track_index = self._current if index is None else max(0, min(len(TRACKS) - 1, int(index)))
        wave = self._track_waves.get(track_index)
        if wave is None:
            started = time.perf_counter()
            wave = render_track(TRACKS[track_index], self._sample_rate)
            self._track_waves[track_index] = wave
            if self._profiler is not None:
                self._profiler.record_effect("music-gen", (time.perf_counter() - started) * 1000.0)
        return wave

    def _sound_for(self, index: int, offset: float = 0.0) -> pygame.mixer.Sound | None:
        if offset <= 0.01:
            sound = self._track_sounds.get(index)
            if sound is None:
                sound = self._sound(self.waveform(index))
                if sound is not None:
                    self._track_sounds[index] = sound
            return sound
        wave = self.waveform(index)
        start = max(0, min(len(wave) - 1, int(offset * self._sample_rate)))
        return self._sound(wave[start:])

    def play_track(self, index: int, *, restart: bool = True) -> bool:
        index = max(0, min(len(TRACKS) - 1, int(index)))
        self._current = index
        self._offset = 0.0
        self._state = "playing"
        self._play_current()
        self._save_state()
        return self._state == "playing"

    def _play_current(self) -> None:
        if not self._available or self._channels is None:
            self._state = "playing" if self._state == "playing" else self._state
            return
        channel = self._channels[0]
        channel.stop()
        sound = self._sound_for(self._current, self._offset)
        if sound is None:
            self._state = "paused"
            return
        try:
            channel.set_volume(self._music_gain())
            channel.play(sound)
            self._started = time.monotonic()
            self._state = "playing"
        except pygame.error:
            self._state = "paused"

    def toggle_play(self) -> None:
        if self._state == "playing":
            self._offset = self.position
            if self._channels is not None:
                self._channels[0].stop()
            self._state = "paused"
        elif self._state == "paused":
            self._state = "playing"
            self._play_current()
        else:
            self._offset = 0.0
            self._state = "playing"
            self._play_current()
        self._save_state()

    def seek(self, seconds: float) -> None:
        self._offset = max(0.0, min(self.current_track.duration - 0.05, float(seconds)))
        if self._state == "playing":
            self._play_current()
        self._save_state()

    def next_track(self) -> None:
        if self._shuffle and len(TRACKS) > 1:
            choices = [index for index in range(len(TRACKS)) if index != self._current]
            self._current = random.choice(choices)
        else:
            self._current = (self._current + 1) % len(TRACKS)
        self._offset = 0.0
        self._state = "playing"
        self._play_current()
        self._save_state()

    def previous_track(self) -> None:
        if self.position > 3.0:
            self.seek(0.0)
            return
        self._current = (self._current - 1) % len(TRACKS)
        self._offset = 0.0
        self._state = "playing"
        self._play_current()
        self._save_state()

    def toggle_shuffle(self) -> bool:
        self._shuffle = not self._shuffle
        self._save_state()
        return self._shuffle

    def cycle_repeat(self) -> str:
        self._repeat = REPEAT_MODES[(REPEAT_MODES.index(self._repeat) + 1) % len(REPEAT_MODES)]
        self._save_state()
        return self._repeat

    def stop_music(self, *, clear_state: bool = False) -> None:
        if self._channels is not None:
            try:
                self._channels[0].stop()
            except pygame.error:
                pass
        self._state = "stopped"
        self._offset = 0.0
        if clear_state:
            self._track_sounds.clear()
            self._track_waves.clear()
            self._current = 0
            self._shuffle = False
            self._repeat = "off"
            if self._storage is not None:
                self._storage.delete("audio.json")
        else:
            self._save_state()

    def update(self, dt: float) -> None:
        del dt
        if not self._available or self._channels is None:
            return
        channel = self._channels[0]
        channel.set_volume(self._music_gain())
        if self._state == "playing" and not channel.get_busy():
            # A track just ended.
            if self._repeat == "one":
                self._offset = 0.0
                self._play_current()
            elif self._repeat == "all":
                self.next_track()
            else:
                self._offset = self.current_track.duration
                self._state = "stopped"
                self._save_state()

    # -- persistence ----------------------------------------------------------
    def _load_state(self) -> None:
        if self._storage is None:
            return
        payload = self._storage.read_json("audio.json", {})
        if not isinstance(payload, dict):
            return
        try:
            self._current = max(0, min(len(TRACKS) - 1, int(payload.get("current", 0))))
            self._shuffle = bool(payload.get("shuffle", False))
        except (TypeError, ValueError):
            self._current = 0
            self._shuffle = False
        repeat = str(payload.get("repeat", "off"))
        self._repeat = repeat if repeat in REPEAT_MODES else "off"
        self._state = "stopped"

    def _save_state(self) -> None:
        if self._storage is None:
            return
        self._storage.write_json(
            "audio.json",
            {
                "current": self._current,
                "shuffle": self._shuffle,
                "repeat": self._repeat,
            },
        )

    def shutdown(self) -> None:
        if self._channels is not None:
            for channel in self._channels:
                try:
                    channel.stop()
                except pygame.error:
                    pass
        self._state = "stopped"
        self._save_state()


__all__ = ["AudioService", "Track", "TRACKS", "render_track", "SAMPLE_RATE"]
