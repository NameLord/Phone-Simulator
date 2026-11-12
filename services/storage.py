"""Single persistence helper for application data.

Every app-owned file (notes, music sequence) goes through this class. It keeps
writes atomic, confines names to ``data/``, and never raises on a corrupt file:
the bad file is quarantined and the caller receives its default value.
"""

from __future__ import annotations

import json
import logging
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

LOG = logging.getLogger("phoneos.storage")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / "data"


class Storage:
    """Tiny JSON document store rooted at ``data/``."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else DEFAULT_ROOT

    # -- paths ---------------------------------------------------------------
    def path(self, name: str) -> Path:
        cleaned = str(name).strip()
        if not cleaned or cleaned in (".", ".."):
            raise ValueError("storage name must be a file name")
        if "/" in cleaned or "\\" in cleaned:
            raise ValueError("storage name must not contain path separators")
        if not cleaned.endswith(".json"):
            cleaned = f"{cleaned}.json"
        return self.root / cleaned

    # -- reads ---------------------------------------------------------------
    def read_json(self, name: str, default: Any) -> Any:
        path = self.path(name)
        if not path.exists():
            return deepcopy(default)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            LOG.error("could not read %s (%s); quarantining", path, exc)
            self._quarantine(path)
            return deepcopy(default)
        return raw

    # -- writes --------------------------------------------------------------
    def write_json(self, name: str, payload: Any) -> bool:
        path = self.path(name)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                # Atomic replace without a blocking fsync (see settings.py).
                handle.flush()
            os.replace(temporary, path)
            LOG.debug("wrote %s", path)
            return True
        except (OSError, TypeError, ValueError) as exc:
            LOG.error("could not write %s: %s", path, exc)
            return False
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def delete(self, name: str) -> bool:
        path = self.path(name)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            LOG.error("could not delete %s: %s", path, exc)
            return False

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    # -- virtual filesystem (Files app) -------------------------------------
    @property
    def fs_root(self) -> Path:
        """Root of the user-visible virtual filesystem."""
        return self.root / "fs"

    def fs_resolve(self, relative: str = "") -> Path:
        """Resolve a POSIX-style relative path inside ``data/fs``.

        Absolute paths, ``..`` segments and NUL bytes are rejected so the app
        can never escape the storage sandbox.
        """
        cleaned = str(relative).replace("\\", "/").strip("/")
        parts = [part for part in cleaned.split("/") if part and part != "."]
        if any(part == ".." or "\x00" in part for part in parts):
            raise ValueError("filesystem path escapes data/fs")
        return self.fs_root.joinpath(*parts)

    def fs_list(self, relative: str = "") -> list[dict[str, object]]:
        """List a directory with name, kind, size and modified time."""
        path = self.fs_resolve(relative)
        try:
            entries = list(path.iterdir())
        except (FileNotFoundError, NotADirectoryError, OSError):
            return []
        result: list[dict[str, object]] = []
        for entry in entries:
            try:
                stat = entry.stat()
                is_dir = entry.is_dir()
                result.append(
                    {
                        "name": entry.name,
                        "path": str(entry.relative_to(self.fs_root)).replace(os.sep, "/"),
                        "dir": is_dir,
                        "size": 0 if is_dir else int(stat.st_size),
                        "modified": float(stat.st_mtime),
                    }
                )
            except OSError:
                continue
        result.sort(key=lambda item: (not bool(item["dir"]), str(item["name"]).casefold()))
        return result

    def fs_mkdir(self, relative: str) -> Path:
        path = self.fs_resolve(relative)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def fs_read_text(self, relative: str) -> str:
        path = self.fs_resolve(relative)
        return path.read_text(encoding="utf-8")

    def fs_write_text(self, relative: str, text: str) -> bool:
        """Atomic text write (tmp file + ``os.replace``) inside ``data/fs``."""
        path = self.fs_resolve(relative)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(str(text))
                handle.flush()
            os.replace(temporary, path)
            return True
        except OSError as exc:
            LOG.error("could not write filesystem path %s: %s", path, exc)
            return False
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def fs_snapshot(self, relative: str) -> dict[str, object] | None:
        """Capture a file or folder so a delete can be undone in memory."""
        path = self.fs_resolve(relative)
        if not path.exists():
            return None
        if path.is_file():
            try:
                return {"kind": "file", "path": relative, "text": path.read_text(encoding="utf-8")}
            except (OSError, UnicodeDecodeError):
                return {"kind": "file", "path": relative, "text": ""}
        entries = []
        for child in sorted(path.rglob("*")):
            child_relative = str(child.relative_to(self.fs_root)).replace(os.sep, "/")
            if child.is_dir():
                entries.append({"kind": "dir", "path": child_relative})
            else:
                try:
                    text = child.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    text = ""
                entries.append({"kind": "file", "path": child_relative, "text": text})
        return {"kind": "dir", "path": relative, "entries": entries}

    def fs_restore(self, snapshot: dict[str, object]) -> bool:
        if not isinstance(snapshot, dict):
            return False
        kind = snapshot.get("kind")
        if kind == "file":
            return self.fs_write_text(str(snapshot.get("path", "")), str(snapshot.get("text", "")))
        if kind == "dir":
            root = self.fs_resolve(str(snapshot.get("path", "")))
            try:
                root.mkdir(parents=True, exist_ok=True)
                for entry in snapshot.get("entries", []):  # type: ignore[union-attr]
                    target = self.fs_resolve(str(entry.get("path", "")))
                    if entry.get("kind") == "dir":
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(str(entry.get("text", "")), encoding="utf-8")
                return True
            except (OSError, TypeError):
                return False
        return False

    def fs_delete(self, relative: str) -> bool:
        path = self.fs_resolve(relative)
        try:
            if path.is_dir():
                for child in sorted(path.rglob("*"), reverse=True):
                    child.unlink() if child.is_file() else child.rmdir()
                path.rmdir()
            else:
                path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            LOG.error("could not delete filesystem path %s: %s", path, exc)
            return False

    def fs_rename(self, relative: str, new_name: str) -> bool:
        cleaned = str(new_name).strip().replace("/", "").replace("\\", "")
        if not cleaned or cleaned in (".", "..") or "\x00" in cleaned:
            return False
        source = self.fs_resolve(relative)
        target = source.with_name(cleaned)
        if target.exists():
            return False
        try:
            source.rename(target)
            return True
        except OSError as exc:
            LOG.error("could not rename %s: %s", source, exc)
            return False

    def fs_usage(self) -> tuple[int, int]:
        """Return (used_bytes, file_count) below ``data/fs``."""
        used = 0
        count = 0
        try:
            for path in self.fs_root.rglob("*"):
                if path.is_file():
                    used += path.stat().st_size
                    count += 1
        except OSError:
            pass
        return used, count

    # -- internals -----------------------------------------------------------
    def _quarantine(self, path: Path) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = path.with_name(f"{path.stem}.corrupt-{stamp}.json")
        try:
            path.replace(target)
            LOG.warning("moved corrupt storage file to %s", target)
        except OSError as exc:
            LOG.error("could not quarantine %s: %s", path, exc)

    def __repr__(self) -> str:
        return f"Storage(root={self.root})"
