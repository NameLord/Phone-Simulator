"""F2 self-test scene.

Runs five non-destructive-ish assertions on demand and prints PASS/FAIL per
check. The notes test uses a throwaway storage root so real notes are untouched.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pygame

from apps.base_app import APP_CHROME_HEIGHT, AppContext
from apps.browser import PAGES
from apps.calculator import evaluate_expression
from apps.notes import NotesApp
from core.input import EventKind, PointerEvent
from core.scene import Scene
from services.storage import Storage
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, wrap_text


@dataclass(slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


class QAScene(Scene):
    name = "qa"

    def __init__(self, kernel) -> None:
        super().__init__(kernel)
        self.results: list[CheckResult] = []
        self.duration_ms = 0.0

    # -- run ------------------------------------------------------------------
    def on_enter(self) -> None:
        super().on_enter()
        started = time.perf_counter()
        self.results = []
        for check in (
            self._check_settings,
            self._check_notes,
            self._check_apps,
            self._check_calculator,
            self._check_browser,
            self._check_search_index,
            self._check_plugins,
            self._check_backup,
            self._check_split_viewport,
            self._check_clipboard,
        ):
            try:
                name, passed, detail = check()
            except Exception as exc:  # noqa: BLE001 - a failed check must render
                name = getattr(check, "__name__", "check").replace("_check_", "")
                passed = False
                detail = f"exception: {exc}"
            self.results.append(CheckResult(name, bool(passed), str(detail)))
        self.duration_ms = (time.perf_counter() - started) * 1000.0

    # -- checks ---------------------------------------------------------------
    def _check_settings(self) -> tuple[str, bool, str]:
        key = "qa.probe.value"
        self.settings.set(key, 4242)
        read = self.settings.get_int(key, -1)
        deleted = self.settings.delete(key)
        gone = self.settings.get(key, None) is None
        passed = read == 4242 and deleted and gone
        return ("settings round-trip", passed, f"write={read} delete={deleted} gone={gone}")

    def _check_notes(self) -> tuple[str, bool, str]:
        root = tempfile.mkdtemp(prefix="phoneos-qa-")
        try:
            storage = Storage(root)
            context = AppContext(
                settings=self.settings,
                storage=storage,
                theme=self.theme,
                clock=self.device_clock,
                notifications=self.notifications,
                apps=self.registry.require("app_manager"),
                size=self.size,
                chrome_height=APP_CHROME_HEIGHT,
            )
            first = NotesApp(context)
            first.on_launch()
            first._new_note()
            note = first._note_by_id(first._editor_id)
            if note is None:
                return ("notes persistence", False, "could not create a note")
            note.body = "QA persistence probe"
            first._changed(note)
            first._close_editor()
            first.on_close()

            second = NotesApp(context)
            second.on_launch()
            found = any("QA persistence probe" in item.body for item in second._notes)
            second.on_close()
            return ("notes persistence", found, "reloaded note from a fresh app instance")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def _check_apps(self) -> tuple[str, bool, str]:
        manager = self.registry.require("app_manager")
        app_id = "music"
        was_installed = manager.is_installed(app_id)
        if was_installed:
            manager.uninstall(app_id)
        removed = (
            not manager.is_installed(app_id)
            and app_id not in manager.installed_ids
            and manager.manifest(app_id) is not None
        )
        installed = manager.install(app_id)
        present = (
            installed
            and manager.is_installed(app_id)
            and app_id in manager.installed_ids
            and app_id in (self.settings.get("apps.installed", []) or [])
        )
        if not was_installed:
            manager.uninstall(app_id)
        unique = len(set(manager.installed_ids)) == len(manager.installed_ids)
        passed = removed and present and unique
        return (
            "install/uninstall registry",
            passed,
            f"removed={removed} installed={present} unique={unique}",
        )

    def _check_calculator(self) -> tuple[str, bool, str]:
        cases = (
            ("1+2*3", 7.0),
            ("(1+2)*3", 9.0),
            ("-5+2", -3.0),
            ("2^3^2", 512.0),
            ("50%", 0.5),
            ("200*10%", 20.0),
            ("2*-3", -6.0),
            ("10/4", 2.5),
            ("-(2+3)", -5.0),
            ("1.5+2.25", 3.75),
        )
        passed_count = 0
        for expression, expected in cases:
            try:
                if abs(evaluate_expression(expression) - expected) < 1e-9:
                    passed_count += 1
            except ValueError:
                pass
        return (
            "calculator engine",
            passed_count == len(cases),
            f"{passed_count}/{len(cases)} expressions correct",
        )

    def _check_search_index(self) -> tuple[str, bool, str]:
        from ui.settings_app import PAGE_CLASSES, SEARCH_INDEX

        keys = {cls.key for cls in PAGE_CLASSES}
        missing = sorted({page for page, _label, _words in SEARCH_INDEX if page not in keys})
        duplicates = len(SEARCH_INDEX) - len({(page, label) for page, label, _words in SEARCH_INDEX})
        passed = not missing and duplicates == 0
        return ("search index integrity", passed, f"{len(SEARCH_INDEX)} entries, missing={missing or 'none'}, duplicate pairs={duplicates}")

    def _check_plugins(self) -> tuple[str, bool, str]:
        import types

        from services.plugin_loader import PluginLoader

        manager = self.registry.require("app_manager")
        loader = PluginLoader(self.registry.get("storage"), manager)
        loader.scan()
        plugin = next((info for info in loader.plugins if info.app_id == "coin_flip"), None)
        broken_rejected = False
        try:
            loader._make_manifest("broken", {"name": "Broken"}, types.SimpleNamespace())
        except ValueError:
            broken_rejected = True
        loaded = plugin is not None and plugin.error == "" and manager.manifest("coin_flip") is not None
        passed = loaded and broken_rejected
        return ("plugin loader isolation", passed, f"example_loaded={loaded} broken_rejected={broken_rejected}")

    def _check_backup(self) -> tuple[str, bool, str]:
        import services.backup as backup_module
        from services.backup import BackupService

        root = tempfile.mkdtemp(prefix="phoneos-qa-backup-")
        old_root = backup_module.BACKUP_ROOT
        try:
            backup_module.BACKUP_ROOT = Path(root) / "backups"
            storage = Storage(Path(root) / "data")
            storage.fs_write_text("roundtrip.txt", "backup payload")
            service = BackupService(storage)
            job = service.begin_backup()
            guard = 0
            while job is not None and not job.done and guard < 10000:
                service.update(0.016)
                guard += 1
            backups = service.list_backups()
            name = backups[0].name if backups else ""
            storage.fs_delete("roundtrip.txt")
            restore = service.begin_restore(name) if name else None
            guard = 0
            while service.job is not None and not service.job.done and guard < 10000:
                service.update(0.016)
                guard += 1
            text = storage.fs_read_text("roundtrip.txt")
            passed = text == "backup payload"
            return ("backup round-trip", passed, f"backup={name or 'none'} restored={text!r}")
        finally:
            backup_module.BACKUP_ROOT = old_root
            shutil.rmtree(root, ignore_errors=True)

    def _check_split_viewport(self) -> tuple[str, bool, str]:
        manager = self.registry.require("app_manager")
        app = manager.active_app
        if app is None:
            app = manager.launch("notes")
        if app is None:
            return ("split sub-viewport", False, "no app available")
        previous = app.size
        app.set_viewport(pygame.Rect(0, 0, 300, 400))
        pane_ok = app.size == (300, 400) and app.surface.get_size() == (300, 400) and app.content.y == 58
        app.set_viewport(None)
        restored = app.size == previous and app.content.y == APP_CHROME_HEIGHT
        passed = pane_ok and restored
        return ("split sub-viewport", passed, f"pane={pane_ok} restored={restored}")

    def _check_clipboard(self) -> tuple[str, bool, str]:
        from services.clipboard import ClipboardService

        root = tempfile.mkdtemp(prefix="phoneos-qa-clip-")
        try:
            storage = Storage(root)
            first = ClipboardService(storage)
            copied = first.copy("clipboard probe")
            second = ClipboardService(storage)
            persisted = second.paste() == "clipboard probe" and len(second.history) == 1
            cleared = second.clear() is None and ClipboardService(storage).paste() == ""
            passed = copied and persisted and cleared
            return ("clipboard persistence", passed, f"copied={copied} persisted={persisted} cleared={cleared}")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def _check_browser(self) -> tuple[str, bool, str]:
        dead: list[str] = []
        for page_id, page in PAGES.items():
            for _label, target in page.links:
                if target not in PAGES:
                    dead.append(f"{page_id}->{target}")
        reachable = {"home"}
        frontier = ["home"]
        while frontier:
            current = frontier.pop()
            for _label, target in PAGES[current].links:
                if target in PAGES and target not in reachable:
                    reachable.add(target)
                    frontier.append(target)
        all_reachable = len(reachable) == len(PAGES)
        passed = not dead and all_reachable
        return (
            "browser link graph",
            passed,
            f"{len(PAGES)} pages, {len(dead)} dead links, all reachable={all_reachable}",
        )

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.KEY_DOWN and event.key in (pygame.K_ESCAPE, pygame.K_F2):
            self.kernel.close_qa()
            return True
        return False

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.theme.palette
        surface.fill(pal.bg_bottom)

        passed = sum(1 for result in self.results if result.passed)
        total = len(self.results)
        header_y = current_layout.status_bar_height + (34 if current_layout.compact else 96)
        blit_text(
            surface,
            self.font(22 if current_layout.compact else 28, True),
            "SYSTEM SELF-TEST",
            pal.text,
            topleft=(current_layout.margin, header_y),
        )
        blit_text(
            surface,
            self.font(11),
            f"{passed}/{total} checks passed \u00b7 {self.duration_ms:.1f} ms \u00b7 F2 / Esc returns",
            pal.text_muted,
            topleft=(current_layout.margin + 2, header_y + 34),
        )

        body_top = header_y + 56
        body = pygame.Rect(
            current_layout.margin,
            body_top,
            current_layout.width - current_layout.margin * 2,
            current_layout.height - body_top - 44,
        )
        cards = current_layout.grid_rects(
            len(self.results),
            preferred_height=118,
            columns=2 if current_layout.is_landscape else 1,
            gap=10,
            body=body,
        )
        for result, card in zip(self.results, cards):
            draw_rounded(surface, card, fill=pal.surface, stroke=pal.stroke_soft, radius=18)
            badge = pygame.Rect(card.right - 80, card.y + 12, 64, 24)
            badge_color = pal.success if result.passed else pal.danger
            draw_rounded(surface, badge, fill=badge_color, radius=12)
            blit_text(
                surface,
                self.font(10, True),
                "PASS" if result.passed else "FAIL",
                pal.on_accent,
                center=badge.center,
                tracking=1.0,
            )

            name_font = self.font(14, True)
            blit_text(
                surface,
                name_font,
                result.name,
                pal.text,
                topleft=(card.x + 16, card.y + 12),
            )
            detail_font = self.font(10)
            detail_y = card.y + 38
            for line in wrap_text(detail_font, result.detail, card.w - 32)[:2]:
                blit_text(surface, detail_font, line, pal.text_secondary, topleft=(card.x + 16, detail_y))
                detail_y += 16

        blit_text(
            surface,
            self.font(10),
            "run again by pressing F2 twice \u00b7 F1 shows effect timings",
            pal.text_muted,
            center=(current_layout.center_x, current_layout.height - 16),
        )
