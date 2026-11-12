"""Chunked backup / restore of the active data directory.

Copies are performed in small frame-budget slices so the kernel never stalls.
The UI polls ``job.progress`` and draws a progress modal.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from services.storage import Storage

LOG = logging.getLogger("phoneos.backup")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_ROOT = PROJECT_ROOT / "backups"
FRAME_BUDGET = 0.008
IGNORE_PARTS = {"profiles"}


@dataclass
class BackupJob:
    kind: str
    label: str
    steps: list[tuple] = field(default_factory=list)
    index: int = 0
    done: bool = False
    error: str = ""
    total: int = 0
    copied: int = 0
    pending_restore: str | None = None

    @property
    def progress(self) -> float:
        if self.done:
            return 1.0
        return min(1.0, self.index / max(1, self.total))

    @property
    def status(self) -> str:
        if self.error:
            return self.error
        if self.done:
            return "Complete"
        return f"{self.copied}/{self.total} files"


class BackupService:
    def __init__(self, storage: "Storage") -> None:
        self._storage = storage
        self.job: BackupJob | None = None
        self.on_restore: Callable[[], None] | None = None

    # -- listing -------------------------------------------------------------
    def list_backups(self) -> list[Path]:
        if not BACKUP_ROOT.exists():
            return []
        return sorted(
            [path for path in BACKUP_ROOT.iterdir() if path.is_dir() and path.name.startswith("backup_")],
            key=lambda path: path.name,
            reverse=True,
        )

    def last_backup_name(self) -> str:
        rows = self.list_backups()
        return rows[0].name if rows else "none"

    # -- jobs ----------------------------------------------------------------
    def _root(self) -> Path:
        root = Path(self._storage.root)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _data_files(self, root: Path) -> list[Path]:
        files: list[Path] = []
        if not root.exists():
            return files
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if relative.parts and relative.parts[0] in IGNORE_PARTS:
                continue
            files.append(path)
        return files

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def begin_backup(self, label: str | None = None) -> BackupJob | None:
        if self.job is not None and not self.job.done:
            return self.job
        root = self._root()
        name = f"backup_{self._timestamp()}"
        dest = BACKUP_ROOT / name
        suffix = 1
        while dest.exists():
            dest = BACKUP_ROOT / f"{name}_{suffix}"
            suffix += 1
        steps = []
        for source in self._data_files(root):
            relative = source.relative_to(root)
            target = dest / relative
            steps.append(("copy", source, target))
        job = BackupJob(kind="backup", label=label or dest.name, steps=steps, total=max(1, len(steps)))
        self.job = job
        LOG.info("backup started: %s", dest.name)
        return job

    def begin_restore(self, name: str) -> BackupJob | None:
        if self.job is not None and not self.job.done:
            return self.job
        source_root = BACKUP_ROOT / str(name)
        if not source_root.exists() or not source_root.is_dir():
            return None
        # Snapshot current data first so a bad restore is itself reversible.
        pre = self.begin_backup("pre-restore")
        if pre is None:
            return None
        pre.pending_restore = str(name)
        return pre

    def _start_restore_copy(self, name: str) -> None:
        source_root = BACKUP_ROOT / str(name)
        data_root = self._root()
        source_files = self._data_files(source_root)
        source_relatives = {path.relative_to(source_root) for path in source_files}
        steps: list[tuple] = []
        for current in self._data_files(data_root):
            relative = current.relative_to(data_root)
            if relative not in source_relatives:
                steps.append(("delete", current))
        for source in source_files:
            relative = source.relative_to(source_root)
            steps.append(("copy", source, data_root / relative))
        job = BackupJob(kind="restore", label=name, steps=steps, total=max(1, len(steps)))
        self.job = job
        LOG.info("restore started: %s", name)

    def update(self, dt: float) -> None:
        _ = dt
        job = self.job
        if job is None or job.done:
            return
        started = time.perf_counter()
        while job.index < len(job.steps):
            step = job.steps[job.index]
            job.index += 1
            try:
                if step[0] == "copy":
                    _source, target = step[1], step[2]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(_source, target)
                    job.copied += 1
                elif step[0] == "delete":
                    target = step[1]
                    if target.exists():
                        target.unlink()
            except OSError as exc:
                job.error = f"{step[0]} failed: {exc}"
                job.done = True
                LOG.error("backup job failed: %s", exc)
                return
            if time.perf_counter() - started >= FRAME_BUDGET:
                break
        if job.index >= len(job.steps):
            job.done = True
            LOG.info("%s complete: %s", job.kind, job.label)
            if job.kind == "backup" and job.pending_restore:
                name = job.pending_restore
                self._start_restore_copy(name)
            elif job.kind == "restore":
                if self.on_restore is not None:
                    self.on_restore()

    def cancel(self) -> None:
        if self.job is not None:
            self.job.done = True
            self.job.error = "Cancelled"
        self.job = None


__all__ = ["BACKUP_ROOT", "BackupJob", "BackupService"]
