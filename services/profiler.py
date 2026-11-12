"""Rolling frame + per-scene timing used by the F1 overlay.

Frame time is the wall-clock delta reported by ``pygame.time.Clock``; CPU time
is measured around event processing, fixed steps and rendering. Scene timing
covers that scene's draw pass (including kernel chrome and the shade), so the
breakdown shows which screen is expensive rather than which subsystem.
"""

from __future__ import annotations

from collections import deque


class Profiler:
    """Bounded history so profiling can run for hours without growing."""

    def __init__(self, window: int = 120) -> None:
        self._window = max(8, int(window))
        self._frames: deque[float] = deque(maxlen=self._window)
        self._cpu: deque[float] = deque(maxlen=self._window)
        self._scenes: dict[str, list[float]] = {}
        self._effects: dict[str, list[float]] = {}
        self.worst_frame_ms = 0.0
        self.worst_cpu_ms = 0.0

    # -- recording ------------------------------------------------------------
    def record_frame(self, frame_ms: float, cpu_ms: float) -> None:
        frame_ms = max(0.0, float(frame_ms))
        cpu_ms = max(0.0, float(cpu_ms))
        self._frames.append(frame_ms)
        self._cpu.append(cpu_ms)
        self.worst_frame_ms = max(self.worst_frame_ms, frame_ms)
        self.worst_cpu_ms = max(self.worst_cpu_ms, cpu_ms)

    def record_scene(self, name: str, ms: float) -> None:
        entry = self._scenes.setdefault(name, [0.0, 0.0])
        entry[0] += max(0.0, float(ms))
        entry[1] += 1.0

    def record_effect(self, name: str, ms: float) -> None:
        """Record expensive media work (numpy generation, filters, decodes)."""
        entry = self._effects.setdefault(str(name), [0.0, 0.0])
        entry[0] += max(0.0, float(ms))
        entry[1] += 1.0

    # -- reads ----------------------------------------------------------------
    @property
    def avg_frame_ms(self) -> float:
        return sum(self._frames) / len(self._frames) if self._frames else 0.0

    @property
    def avg_cpu_ms(self) -> float:
        return sum(self._cpu) / len(self._cpu) if self._cpu else 0.0

    @property
    def avg_fps(self) -> float:
        average = self.avg_frame_ms
        return 1000.0 / average if average > 0.0 else 0.0

    def scene_breakdown(self, limit: int = 3) -> list[tuple[str, float, int]]:
        rows = [
            (name, total / count, int(count))
            for name, (total, count) in self._scenes.items()
            if count > 0
        ]
        rows.sort(key=lambda row: row[1], reverse=True)
        return rows[: max(1, int(limit))]

    def effect_rows(self, limit: int = 6) -> list[tuple[str, float, int]]:
        rows = [
            (name, total / count, int(count))
            for name, (total, count) in self._effects.items()
            if count > 0
        ]
        rows.sort(key=lambda row: row[1], reverse=True)
        return rows[: max(1, int(limit))]

    def reset(self) -> None:
        self._frames.clear()
        self._cpu.clear()
        self._scenes.clear()
        self._effects.clear()
        self.worst_frame_ms = 0.0
        self.worst_cpu_ms = 0.0

    def __repr__(self) -> str:
        return (
            f"Profiler(avg={self.avg_frame_ms:.2f}ms, worst={self.worst_frame_ms:.2f}ms, "
            f"scenes={len(self._scenes)})"
        )
