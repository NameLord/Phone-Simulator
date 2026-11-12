"""Shared photo metadata and procedural capture rendering.

The Camera app and the Photos gallery both go through this service, so captures
are persisted once in ``photos.json`` without either app importing the other.
Captures store a deterministic animation spec (seed, time, filter, facing)
rather than image files; the pixels are regenerated with numpy on demand.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

import numpy

if TYPE_CHECKING:
    from services.storage import Storage

STORE_FILE = "photos.json"
CAPTURE_START_ID = 1001
FILTERS = ("none", "grayscale", "sepia", "invert")


class PhotoLibrary:
    def __init__(self, storage: "Storage") -> None:
        self._storage = storage
        self.deleted: set[int] = set()
        self.captures: list[dict[str, object]] = []
        self._next_id = CAPTURE_START_ID
        self.reload()

    # -- persistence ----------------------------------------------------------
    def reload(self) -> None:
        payload = self._storage.read_json(STORE_FILE, {})
        if not isinstance(payload, dict):
            payload = {}
        raw_deleted = payload.get("deleted", [])
        self.deleted = {
            int(value)
            for value in raw_deleted
            if isinstance(value, (int, float)) and 1 <= int(value) <= 8
        } if isinstance(raw_deleted, list) else set()
        captures: list[dict[str, object]] = []
        raw = payload.get("captures", [])
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    capture_id = int(item.get("id", 0))
                    seed = int(item.get("seed", 0))
                    moment = float(item.get("time", 0.0))
                except (TypeError, ValueError):
                    continue
                if capture_id < CAPTURE_START_ID:
                    continue
                captures.append(
                    {
                        "id": capture_id,
                        "name": str(item.get("name", f"Capture {capture_id}"))[:40],
                        "timestamp": float(item.get("timestamp", time.time())),
                        "seed": seed,
                        "time": moment,
                        "filter": str(item.get("filter", "none")) if str(item.get("filter", "none")) in FILTERS else "none",
                        "facing": "front" if str(item.get("facing", "back")) == "front" else "back",
                    }
                )
        captures.sort(key=lambda item: float(item["timestamp"]), reverse=True)
        self.captures = captures
        try:
            self._next_id = max(
                CAPTURE_START_ID,
                int(payload.get("next_capture", CAPTURE_START_ID)),
                max((int(item["id"]) for item in captures), default=0) + 1,
            )
        except (TypeError, ValueError):
            self._next_id = CAPTURE_START_ID

    def save(self) -> None:
        self._storage.write_json(
            STORE_FILE,
            {
                "deleted": sorted(self.deleted),
                "captures": self.captures,
                "next_capture": self._next_id,
            },
        )

    # -- mutations ------------------------------------------------------------
    def add_capture(
        self,
        seed: int,
        moment: float,
        filter_name: str = "none",
        facing: str = "back",
        name: str | None = None,
    ) -> dict[str, object]:
        capture_id = self._next_id
        self._next_id += 1
        timestamp = time.time()
        record = {
            "id": capture_id,
            "name": name or f"Capture {time.strftime('%H:%M:%S')}",
            "timestamp": timestamp,
            "seed": int(seed),
            "time": float(moment),
            "filter": filter_name if filter_name in FILTERS else "none",
            "facing": "front" if facing == "front" else "back",
        }
        self.captures.insert(0, record)
        self.save()
        return dict(record)

    def delete_capture(self, capture_id: int) -> bool:
        before = len(self.captures)
        self.captures = [item for item in self.captures if int(item["id"]) != int(capture_id)]
        if len(self.captures) == before:
            return False
        self.save()
        return True

    def delete_builtin(self, photo_id: int) -> bool:
        if 1 <= int(photo_id) <= 8:
            self.deleted.add(int(photo_id))
            self.save()
            return True
        return False

    def capture(self, capture_id: int) -> dict[str, object] | None:
        return next((item for item in self.captures if int(item["id"]) == int(capture_id)), None)

    # -- rendering ------------------------------------------------------------
    @staticmethod
    def render_feed(
        seed: int,
        moment: float,
        size: tuple[int, int],
        *,
        facing: str = "back",
        filter_name: str = "none",
    ) -> numpy.ndarray:
        width, height = max(1, int(size[0])), max(1, int(size[1]))
        x = numpy.linspace(0.0, 1.0, width, dtype=numpy.float32)
        y = numpy.linspace(0.0, 1.0, height, dtype=numpy.float32)
        xx, yy = numpy.meshgrid(x, y)
        phase = float(seed) * 0.017 + float(moment) * 0.9
        value = (
            numpy.sin(xx * 9.0 + phase)
            + numpy.sin(yy * 7.0 - phase * 0.8)
            + numpy.sin((xx + yy) * 12.0 + phase * 0.35)
            + 0.7 * numpy.sin(numpy.sqrt((xx - 0.5) ** 2 + (yy - 0.5) ** 2) * 34.0 - phase)
        )
        value = (value + 3.7) / 7.4
        red = 0.35 + 0.55 * numpy.sin(value * math.tau + phase * 0.4)
        green = 0.32 + 0.58 * numpy.sin(value * math.tau + 2.1 - phase * 0.25)
        blue = 0.45 + 0.50 * numpy.cos(value * math.tau + 4.2 + phase * 0.18)
        rgb = numpy.stack(
            [numpy.clip(red, 0.0, 1.0), numpy.clip(green, 0.0, 1.0), numpy.clip(blue, 0.0, 1.0)],
            axis=2,
        )
        if facing == "front":
            rgb = numpy.flip(rgb, axis=1)
        if filter_name == "grayscale":
            lum = rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722
            rgb = numpy.stack([lum, lum, lum], axis=2)
        elif filter_name == "sepia":
            lum = rgb[:, :, 0] * 0.393 + rgb[:, :, 1] * 0.769 + rgb[:, :, 2] * 0.189
            lum2 = rgb[:, :, 0] * 0.349 + rgb[:, :, 1] * 0.686 + rgb[:, :, 2] * 0.168
            lum3 = rgb[:, :, 0] * 0.272 + rgb[:, :, 1] * 0.534 + rgb[:, :, 2] * 0.131
            rgb = numpy.stack([numpy.minimum(1.0, lum), numpy.minimum(1.0, lum2), numpy.minimum(1.0, lum3)], axis=2)
        elif filter_name == "invert":
            rgb = 1.0 - rgb
        return (numpy.clip(rgb, 0.0, 1.0) * 255.0).astype(numpy.uint8)

    def render_capture(self, record: dict[str, object], size: tuple[int, int]) -> numpy.ndarray:
        return self.render_feed(
            int(record.get("seed", 0)),
            float(record.get("time", 0.0)),
            size,
            facing=str(record.get("facing", "back")),
            filter_name=str(record.get("filter", "none")),
        )

    def __repr__(self) -> str:
        return f"PhotoLibrary(deleted={len(self.deleted)}, captures={len(self.captures)})"


__all__ = ["PhotoLibrary", "FILTERS", "CAPTURE_START_ID"]
