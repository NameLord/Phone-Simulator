"""Persistent text clipboard with a ten-entry history."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.storage import Storage

LOG = logging.getLogger("phoneos.clipboard")
FILE = "clipboard.json"
MAX_HISTORY = 10


class ClipboardService:
    def __init__(self, storage: "Storage") -> None:
        self._storage = storage
        self._history: list[str] = []
        self._load()

    def reload(self) -> None:
        self._history = []
        self._load()

    def _load(self) -> None:
        payload = self._storage.read_json(FILE, {})
        values = payload.get("history", []) if isinstance(payload, dict) else []
        if isinstance(values, list):
            self._history = [str(value) for value in values if str(value).strip()][:MAX_HISTORY]

    def _save(self) -> None:
        self._storage.write_json(FILE, {"history": self._history})

    @property
    def text(self) -> str:
        return self._history[0] if self._history else ""

    @property
    def history(self) -> tuple[str, ...]:
        return tuple(self._history)

    def copy(self, text: str) -> bool:
        value = str(text)
        if not value:
            return False
        self._history = [value] + [item for item in self._history if item != value]
        self._history = self._history[:MAX_HISTORY]
        self._save()
        LOG.info("copied %d characters", len(value))
        return True

    def paste(self) -> str:
        return self.text

    def use(self, text: str) -> bool:
        """Move an older history entry to the front."""
        if text not in self._history:
            return False
        self._history.remove(text)
        self._history.insert(0, text)
        self._save()
        return True

    def clear(self) -> None:
        self._history.clear()
        self._save()


__all__ = ["ClipboardService", "MAX_HISTORY"]
