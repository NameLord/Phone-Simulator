"""Central in-memory device log.

Every service and app logs through this module. A bounded ring buffer keeps
the most recent entries for the developer console while still forwarding to
Python's standard logging handlers, so terminal output is unchanged.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque

MAX_ENTRIES = 400
_FORMAT = "%H:%M:%S"


@dataclass(frozen=True, slots=True)
class LogEntry:
    timestamp: float
    level: str
    name: str
    message: str

    def time_label(self) -> str:
        try:
            return datetime.fromtimestamp(self.timestamp).strftime(_FORMAT)
        except (OverflowError, OSError, ValueError):
            return "--:--:--"


class DeviceLog:
    def __init__(self, capacity: int = MAX_ENTRIES) -> None:
        self.entries: Deque[LogEntry] = deque(maxlen=max(20, capacity))
        self._installed = False

    def install(self) -> None:
        """Attach a handler to the root logger exactly once."""
        if self._installed:
            return
        self._installed = True
        handler = _RingHandler(self)
        handler.setLevel(logging.DEBUG)
        root = logging.getLogger()
        if root.level > logging.INFO:
            root.setLevel(logging.INFO)
        root.addHandler(handler)

    def record(self, level: str, name: str, message: str) -> None:
        import time

        self.entries.append(LogEntry(time.time(), level, name, message))

    def recent(self, limit: int = 30, minimum: str = "INFO") -> list[LogEntry]:
        levels = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
        threshold = levels.get(minimum.upper(), 20)
        rows = [entry for entry in self.entries if levels.get(entry.level, 20) >= threshold]
        return rows[-max(1, int(limit)) :]

    def clear(self) -> None:
        self.entries.clear()


class _RingHandler(logging.Handler):
    def __init__(self, device_log: DeviceLog) -> None:
        super().__init__()
        self.device_log = device_log
        self.setFormatter(logging.Formatter("%(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.device_log.record(record.levelname, record.name, self.format(record))
        except Exception:  # noqa: BLE001 - logging must never break the OS
            self.handleError(record)


DEVICE_LOG = DeviceLog()


def install() -> DeviceLog:
    DEVICE_LOG.install()
    return DEVICE_LOG


def get_logger(name: str = "phoneos") -> logging.Logger:
    install()
    return logging.getLogger(name if name.startswith("phoneos") else f"phoneos.{name}")


def recent(limit: int = 30, minimum: str = "INFO") -> list[LogEntry]:
    return DEVICE_LOG.recent(limit, minimum)


def clear() -> None:
    DEVICE_LOG.clear()


__all__ = ["DEVICE_LOG", "LogEntry", "clear", "get_logger", "install", "recent"]
