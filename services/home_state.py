"""Persistent home-screen layout: pages, dock, folders, icons and widgets.

The model is deliberately plain JSON. App icons are stored as app ids (with a
special ``settings`` entry) so installing or uninstalling an app reconciles the
layout instead of leaving dead tiles behind.
"""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from services.storage import Storage

LOG = logging.getLogger("phoneos.home")
HOME_FILE = "home.json"
VERSION = 1
SETTINGS_ID = "settings"
FOLDER_PREFIX = "folder:"
WIDGET_SIZES = {
    "clock": (4, 2),
    "calendar": (4, 2),
    "weather": (2, 2),
    "music": (4, 2),
    "battery": (2, 1),
    "notes": (2, 2),
}


def _default() -> dict:
    return {
        "version": VERSION,
        "grid": {"portrait": [4, 5], "landscape": [6, 2]},
        "dock": {"show": True, "max": 4, "ids": []},
        "icons": {"labels": True, "pack": "rounded", "scale": 1.0},
        "pages": [[SETTINGS_ID]],
        "widgets": [],
        "next_folder": 1,
    }


class HomeState:
    """Read/write access to the home layout with app reconciliation."""

    def __init__(self, storage: "Storage", settings=None) -> None:
        self._storage = storage
        self._settings = settings
        self._data = _default()
        self._loaded = False
        self.load()

    # -- persistence ---------------------------------------------------------
    def load(self) -> None:
        raw = self._storage.read_json(HOME_FILE, {})
        data = _default()
        if isinstance(raw, dict) and raw.get("version") == VERSION:
            for key in ("grid", "dock", "icons", "pages", "widgets", "next_folder"):
                if key in raw:
                    data[key] = raw[key]
        self._data = self._validate(data)
        self._loaded = True

    def _validate(self, data: dict) -> dict:
        base = _default()
        grid = data.get("grid", base["grid"])
        if not isinstance(grid, dict):
            grid = base["grid"]
        for key in ("portrait", "landscape"):
            pair = grid.get(key)
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                grid[key] = base["grid"][key]
            else:
                grid[key] = [max(2, min(6, int(pair[0]))), max(2, min(6, int(pair[1])))]
        data["grid"] = grid
        dock = data.get("dock", base["dock"])
        if not isinstance(dock, dict):
            dock = base["dock"]
        dock["show"] = bool(dock.get("show", True))
        dock["max"] = max(2, min(6, int(dock.get("max", 4) or 4)))
        ids = dock.get("ids", [])
        dock["ids"] = [str(item) for item in ids if isinstance(item, str)][: dock["max"]]
        data["dock"] = dock
        icons = data.get("icons", base["icons"])
        if not isinstance(icons, dict):
            icons = base["icons"]
        icons["labels"] = bool(icons.get("labels", True))
        icons["pack"] = str(icons.get("pack", "rounded"))
        try:
            icons["scale"] = max(0.7, min(1.3, float(icons.get("scale", 1.0))))
        except (TypeError, ValueError):
            icons["scale"] = 1.0
        data["icons"] = icons
        pages = data.get("pages", base["pages"])
        if not isinstance(pages, list) or not pages:
            pages = base["pages"]
        cleaned_pages = []
        seen: set[str] = set()
        for page in pages:
            if not isinstance(page, list):
                continue
            cleaned = []
            for item in page:
                value = self._clean_item(item, seen)
                if value is not None:
                    cleaned.append(value)
            cleaned_pages.append(cleaned)
        data["pages"] = cleaned_pages or [[SETTINGS_ID]]
        widgets = data.get("widgets", [])
        if not isinstance(widgets, list):
            widgets = []
        cleaned_widgets = []
        for widget in widgets:
            if not isinstance(widget, dict):
                continue
            kind = str(widget.get("type", ""))
            if kind not in WIDGET_SIZES:
                continue
            try:
                cleaned_widgets.append(
                    {
                        "id": str(widget.get("id") or f"w{int(time.time() * 1000)}"),
                        "type": kind,
                        "page": max(0, int(widget.get("page", 0))),
                        "col": max(0, int(widget.get("col", 0))),
                        "row": max(0, int(widget.get("row", 0))),
                        "w": max(1, min(6, int(widget.get("w", WIDGET_SIZES[kind][0])))),
                        "h": max(1, min(6, int(widget.get("h", WIDGET_SIZES[kind][1])))),
                        "options": widget.get("options", {}) if isinstance(widget.get("options"), dict) else {},
                    }
                )
            except (TypeError, ValueError):
                continue
        data["widgets"] = cleaned_widgets
        try:
            data["next_folder"] = max(1, int(data.get("next_folder", 1)))
        except (TypeError, ValueError):
            data["next_folder"] = 1
        return data

    def _clean_item(self, item, seen: set[str]):
        if isinstance(item, str):
            if not item or item in seen:
                return None
            seen.add(item)
            return item
        if isinstance(item, dict) and item.get("type") == "folder":
            folder_id = str(item.get("id") or f"{FOLDER_PREFIX}{len(seen) + 1}")
            children = []
            for child in item.get("items", []):
                if isinstance(child, str) and child not in seen:
                    seen.add(child)
                    children.append(child)
            if not children:
                return None
            return {
                "id": folder_id,
                "type": "folder",
                "name": str(item.get("name") or "Folder")[:24],
                "items": children,
            }
        return None

    def save(self) -> None:
        self._storage.write_json(HOME_FILE, self._data)

    # -- reconciliation ------------------------------------------------------
    def sync(self, installed_ids: Iterable[str], system_ids: Iterable[str] = (), *, initial_dock: bool | None = None) -> None:
        installed = [str(app_id) for app_id in installed_ids]
        system = [str(app_id) for app_id in system_ids]
        available = set(installed) | {SETTINGS_ID}
        changed = False

        # Remove apps that are no longer installed, including folder children.
        for page in self._data["pages"]:
            for index in range(len(page) - 1, -1, -1):
                item = page[index]
                if isinstance(item, str):
                    if item not in available:
                        page.pop(index)
                        changed = True
                elif isinstance(item, dict):
                    kept = [child for child in item.get("items", []) if child in available]
                    if len(kept) != len(item.get("items", [])):
                        changed = True
                    if kept:
                        item["items"] = kept
                    else:
                        page.pop(index)
                        changed = True

        dock = self._data["dock"]
        kept_dock = [item for item in dock.get("ids", []) if item in available]
        if kept_dock != dock.get("ids", []):
            dock["ids"] = kept_dock
            changed = True
        if initial_dock is None:
            initial_dock = not bool(dock.get("ids")) and not any(
                isinstance(item, dict) for page in self._data["pages"] for item in page
            )
        if initial_dock and not dock.get("ids"):
            dock["ids"] = system[: dock["max"]]
            changed = True

        # Add newly installed apps to the first page with room.
        present = self.all_app_ids()
        for app_id in installed:
            if app_id in present:
                continue
            if not self._append(app_id):
                self._data["pages"].append([app_id])
            changed = True

        if changed:
            self.save()

    def _append(self, item) -> bool:
        pair = self._data["grid"]["portrait"]
        capacity = int(pair[0] * pair[1])
        for page in self._data["pages"]:
            if len(page) < capacity:
                page.append(item)
                return True
        return False

    def all_app_ids(self) -> set[str]:
        found: set[str] = set()
        for page in self._data["pages"]:
            for item in page:
                if isinstance(item, str):
                    found.add(item)
                elif isinstance(item, dict):
                    found.update(item.get("items", []))
        found.update(self._data["dock"].get("ids", []))
        return found

    # -- pages / items -------------------------------------------------------
    @property
    def pages(self) -> list[list]:
        return self._data["pages"]

    @property
    def icons(self) -> dict:
        return self._data["icons"]

    @property
    def dock(self) -> dict:
        return self._data["dock"]

    def grid(self, landscape: bool) -> tuple[int, int]:
        pair = self._data["grid"]["landscape" if landscape else "portrait"]
        return int(pair[0]), int(pair[1])

    def set_grid(self, landscape: bool, cols: int, rows: int) -> None:
        self._data["grid"]["landscape" if landscape else "portrait"] = [
            max(2, min(6, int(cols))),
            max(2, min(6, int(rows))),
        ]
        self.save()

    def set_icon_option(self, name: str, value) -> None:
        if name in ("labels", "pack", "scale"):
            self._data["icons"][name] = value
            self.save()

    def set_dock_option(self, name: str, value) -> None:
        if name == "show":
            self._data["dock"]["show"] = bool(value)
        elif name == "max":
            self._data["dock"]["max"] = max(2, min(6, int(value)))
            self._data["dock"]["ids"] = self._data["dock"]["ids"][: self._data["dock"]["max"]]
        self.save()

    def set_dock_ids(self, ids: Iterable[str]) -> None:
        self._data["dock"]["ids"] = [str(item) for item in ids][: self._data["dock"]["max"]]
        self.save()

    def add_to_dock(self, app_id: str) -> bool:
        dock_ids = self._data["dock"]["ids"]
        if app_id in dock_ids:
            return False
        if len(dock_ids) >= self._data["dock"]["max"]:
            return False
        dock_ids.append(app_id)
        # A docked app is removed from the page grid to avoid a duplicate tile.
        for page in self._data["pages"]:
            for index in range(len(page) - 1, -1, -1):
                item = page[index]
                if item == app_id:
                    page.pop(index)
                elif isinstance(item, dict) and app_id in item.get("items", []):
                    item["items"].remove(app_id)
                    if not item["items"]:
                        page.pop(index)
        self.save()
        return True

    def reflow(self, cols: int, rows: int) -> None:
        """Push icons that no longer fit (because of widgets) to the next page."""
        cols = max(2, int(cols))
        rows = max(2, int(rows))
        capacity = cols * rows
        changed = False
        page_index = 0
        while page_index < len(self._data["pages"]):
            occupied = 0
            for widget in self._data["widgets"]:
                if int(widget.get("page", 0)) == page_index:
                    occupied += int(widget.get("w", 1)) * int(widget.get("h", 1))
            free = max(1, capacity - occupied)
            page = self._data["pages"][page_index]
            if len(page) > free:
                overflow = page[free:]
                del page[free:]
                if page_index + 1 >= len(self._data["pages"]):
                    self._data["pages"].append([])
                self._data["pages"][page_index + 1][0:0] = overflow
                changed = True
            page_index += 1
        if changed:
            self.save()

    def remove_item(self, app_id: str) -> bool:
        changed = False
        for page in self._data["pages"]:
            for index in range(len(page) - 1, -1, -1):
                item = page[index]
                if item == app_id:
                    page.pop(index)
                    changed = True
                elif isinstance(item, dict) and app_id in item.get("items", []):
                    item["items"].remove(app_id)
                    if not item["items"]:
                        page.pop(index)
                    changed = True
        dock_ids = self._data["dock"]["ids"]
        if app_id in dock_ids:
            dock_ids.remove(app_id)
            changed = True
        if changed:
            self.save()
        return changed

    def find_item(self, app_id: str) -> tuple[int, int] | None:
        for page_index, page in enumerate(self._data["pages"]):
            for item_index, item in enumerate(page):
                if item == app_id:
                    return page_index, item_index
                if isinstance(item, dict) and app_id in item.get("items", []):
                    return page_index, item_index
        return None

    def move_item(self, app_id: str, target_page: int, target_index: int) -> bool:
        found = self.find_item(app_id)
        if found is None:
            return False
        source_page, source_index = found
        item = self._data["pages"][source_page][source_index]
        # If the item is inside a folder, detach it first.
        if isinstance(item, dict) and app_id in item.get("items", []):
            item["items"].remove(app_id)
            if not item["items"]:
                self._data["pages"][source_page].pop(source_index)
            moving = app_id
        else:
            self._data["pages"][source_page].pop(source_index)
            moving = item
        while len(self._data["pages"]) <= target_page:
            self._data["pages"].append([])
        page = self._data["pages"][target_page]
        target_index = max(0, min(len(page), target_index))
        page.insert(target_index, moving)
        self.save()
        return True

    def make_folder(self, first: str, second: str) -> str | None:
        if first == second:
            return None
        a = self.find_item(first)
        b = self.find_item(second)
        if a is None or b is None:
            return None
        page_a, index_a = a
        page_b, index_b = b
        # Folders are created on the target's page.
        self.move_item(first, page_b, index_b)
        page = self._data["pages"][page_b]
        for index, item in enumerate(page):
            if item == first:
                page.pop(index)
                break
        name = f"Folder {self._data.get('next_folder', 1)}"
        self._data["next_folder"] = int(self._data.get("next_folder", 1)) + 1
        folder = {"id": f"{FOLDER_PREFIX}{self._data['next_folder']}", "type": "folder", "name": name, "items": [first, second]}
        target = next((index for index, item in enumerate(page) if item == second), len(page))
        page[target] = folder
        self.save()
        return folder["id"]

    def add_to_folder(self, folder_id: str, app_id: str) -> bool:
        folder = self.folder(folder_id)
        if folder is None:
            return False
        found = self.find_item(app_id)
        if found is None:
            return False
        page_index, item_index = found
        page = self._data["pages"][page_index]
        item = page[item_index]
        if isinstance(item, dict):
            if item.get("id") == folder_id:
                return False
            item["items"].remove(app_id)
            if not item["items"]:
                page.pop(item_index)
        else:
            page.pop(item_index)
        folder["items"].append(app_id)
        self.save()
        return True

    def remove_from_folder(self, folder_id: str, app_id: str) -> bool:
        folder = self.folder(folder_id)
        if folder is None or app_id not in folder.get("items", []):
            return False
        folder["items"].remove(app_id)
        page_index = 0
        for index, page in enumerate(self._data["pages"]):
            if folder in page:
                page_index = index
                break
        page = self._data["pages"][page_index]
        if not folder["items"]:
            page[:] = [item for item in page if item is not folder]
        page.append(app_id)
        self.save()
        return True

    def folder(self, folder_id: str) -> dict | None:
        for page in self._data["pages"]:
            for item in page:
                if isinstance(item, dict) and item.get("id") == folder_id:
                    return item
        return None

    def rename_folder(self, folder_id: str, name: str) -> bool:
        folder = self.folder(folder_id)
        if folder is None:
            return False
        folder["name"] = " ".join(str(name).split())[:24] or "Folder"
        self.save()
        return True

    def ungroup(self, folder_id: str) -> bool:
        for page_index, page in enumerate(self._data["pages"]):
            for item_index, item in enumerate(page):
                if isinstance(item, dict) and item.get("id") == folder_id:
                    children = list(item.get("items", []))
                    page[item_index : item_index + 1] = children
                    self.save()
                    return True
        return False

    def delete_folder(self, folder_id: str) -> dict | None:
        """Delete a folder; its apps return to the last page with room."""
        for page_index, page in enumerate(self._data["pages"]):
            for item_index, item in enumerate(page):
                if isinstance(item, dict) and item.get("id") == folder_id:
                    children = list(item.get("items", []))
                    snapshot = {
                        "folder": deepcopy(item),
                        "page": page_index,
                        "index": item_index,
                        "children": children,
                    }
                    page.pop(item_index)
                    cols, rows = self._data["grid"]["portrait"]
                    capacity = int(cols) * int(rows)
                    pages = self._data["pages"]
                    for child in children:
                        if len(pages) == 0:
                            pages.append([])
                        if len(pages[-1]) < capacity:
                            pages[-1].append(child)
                        else:
                            pages.append([child])
                    self.save()
                    return snapshot
        return None

    def restore_folder(self, snapshot: dict) -> bool:
        folder = snapshot.get("folder")
        if not isinstance(folder, dict):
            return False
        # The children were returned to pages, so remove those copies first.
        for child in snapshot.get("children", []):
            for page in self._data["pages"]:
                for index in range(len(page) - 1, -1, -1):
                    if page[index] == child:
                        page.pop(index)
            dock_ids = self._data["dock"].get("ids", [])
            if child in dock_ids:
                dock_ids.remove(child)
        page_index = max(0, int(snapshot.get("page", 0)))
        index = max(0, int(snapshot.get("index", 0)))
        while len(self._data["pages"]) <= page_index:
            self._data["pages"].append([])
        page = self._data["pages"][page_index]
        if not any(isinstance(item, dict) and item.get("id") == folder.get("id") for item in page):
            page.insert(min(index, len(page)), deepcopy(folder))
            self.save()
            return True
        return False

    # -- widgets -------------------------------------------------------------
    def widgets(self, page: int | None = None) -> list[dict]:
        if page is None:
            return self._data["widgets"]
        return [widget for widget in self._data["widgets"] if int(widget.get("page", 0)) == page]

    def add_widget(self, kind: str, page: int = 0, col: int = 0, row: int = 0, width: int | None = None, height: int | None = None) -> dict | None:
        if kind not in WIDGET_SIZES:
            return None
        default_w, default_h = WIDGET_SIZES[kind]
        widget = {
            "id": f"w{int(time.time() * 1000)}-{len(self._data['widgets'])}",
            "type": kind,
            "page": max(0, int(page)),
            "col": max(0, int(col)),
            "row": max(0, int(row)),
            "w": max(1, min(6, int(width or default_w))),
            "h": max(1, min(6, int(height or default_h))),
            "options": {},
        }
        self._data["widgets"].append(widget)
        self.save()
        return widget

    def update_widget(self, widget_id: str, **changes) -> bool:
        for widget in self._data["widgets"]:
            if widget.get("id") != widget_id:
                continue
            for key in ("page", "col", "row", "w", "h"):
                if key in changes:
                    widget[key] = max(0, int(changes[key]))
            if "options" in changes and isinstance(changes["options"], dict):
                widget["options"].update(changes["options"])
            self.save()
            return True
        return False

    def remove_widget(self, widget_id: str) -> bool:
        for index, widget in enumerate(self._data["widgets"]):
            if widget.get("id") == widget_id:
                self._data["widgets"].pop(index)
                self.save()
                return True
        return False

    def reset(self) -> None:
        self._data = _default()
        self.save()

    def snapshot(self) -> dict:
        return deepcopy(self._data)
