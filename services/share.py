"""Share-sheet state and payload hand-off rules."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.app_manager import AppManager

LOG = logging.getLogger("phoneos.share")

ACCEPTS: dict[str, tuple[str, ...]] = {
    "notes": ("text",),
    "messages": ("text",),
    "photos": ("image",),
    "files": ("file",),
}


class ShareService:
    def __init__(self, apps: "AppManager") -> None:
        self._apps = apps
        self.payload: dict | None = None
        self.is_open = False

    def open(self, payload: dict) -> bool:
        kind = str(payload.get("type", "")).lower()
        if kind not in ("text", "image", "file"):
            LOG.warning("rejected share payload %r", payload)
            return False
        self.payload = dict(payload)
        self.is_open = True
        return True

    def close(self) -> None:
        self.is_open = False
        self.payload = None

    def candidates(self) -> list[tuple[str, str]]:
        if self.payload is None:
            return []
        kind = str(self.payload.get("type", ""))
        rows: list[tuple[str, str]] = []
        for app_id, accepted in ACCEPTS.items():
            if kind not in accepted:
                continue
            manifest = self._apps.manifest(app_id)
            if manifest is None:
                continue
            if not (self._apps.is_installed(app_id) or manifest.system):
                continue
            rows.append((app_id, manifest.name))
        return rows

    def handoff(self, app_id: str) -> bool:
        payload = dict(self.payload or {})
        kind = str(payload.get("type", ""))
        if app_id not in ACCEPTS or kind not in ACCEPTS[app_id]:
            return False
        app = self._apps.launch(app_id, payload=payload)
        if app is None:
            return False
        self.close()
        LOG.info("shared %s payload to %s", kind, app_id)
        return True


__all__ = ["ACCEPTS", "ShareService"]
