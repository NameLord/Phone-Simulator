"""Two independent device profiles backed by subfolders under data/profiles."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from services.storage import Storage

LOG = logging.getLogger("phoneos.profiles")
PROFILE_NAMES = ("personal", "work")
ACTIVE_FILE = "profiles/active.json"


class ProfileService:
    def __init__(self, storage: "Storage") -> None:
        self._storage = storage
        self.root = Path(storage.root)
        self.profiles_root = self.root / "profiles"
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        for name in PROFILE_NAMES:
            (self.profiles_root / name).mkdir(parents=True, exist_ok=True)
        self._active = self._read_active()

    def _read_active(self) -> str:
        path = self.root / "profiles" / "active.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        name = str(payload.get("active", "personal")) if isinstance(payload, dict) else "personal"
        return name if name in PROFILE_NAMES else "personal"

    @property
    def active(self) -> str:
        return self._active

    def list_profiles(self) -> list[tuple[str, str]]:
        return [(name, name.title()) for name in PROFILE_NAMES]

    def _write_active(self, name: str) -> None:
        path = self.root / "profiles" / "active.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps({"active": name}, indent=2), encoding="utf-8")
        except OSError as exc:
            LOG.error("could not write active profile: %s", exc)

    @staticmethod
    def _copy_tree(source: Path, target: Path, *, clear_target: bool = False) -> int:
        target.mkdir(parents=True, exist_ok=True)
        copied = 0
        if clear_target:
            for child in target.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                except OSError:
                    pass
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            if relative.parts and relative.parts[0] == "profiles":
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied += 1
        return copied

    def save_current(self) -> int:
        """Snapshot the live data directory into the active profile folder."""
        target = self.profiles_root / self._active
        copied = self._copy_tree(self.root, target, clear_target=True)
        self._write_active(self._active)
        LOG.info("saved profile %s (%d files)", self._active, copied)
        return copied

    def switch(self, name: str, apply_callback: Callable[[], None] | None = None) -> bool:
        name = str(name)
        if name not in PROFILE_NAMES or name == self._active:
            return False
        self.save_current()
        source = self.profiles_root / name
        if not source.exists():
            (source).mkdir(parents=True, exist_ok=True)
        # Remove live data (except profiles) and copy the target profile in.
        for child in self.root.iterdir():
            if child.name == "profiles":
                continue
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError:
                pass
        copied = self._copy_tree(source, self.root, clear_target=False)
        self._active = name
        self._write_active(name)
        LOG.info("switched to profile %s (%d files)", name, copied)
        if apply_callback is not None:
            apply_callback()
        return True


__all__ = ["PROFILE_NAMES", "ProfileService"]
