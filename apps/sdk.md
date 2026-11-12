# PhoneOS App SDK

Third-party apps are plain Python modules in `./plugins/`. They use the same
`App` base class as built-in apps, so they get the theme, storage, notifications,
clipboard, sharing and lifecycle for free.

## Minimal app

```python
from apps.base_app import App
from core.input import EventKind
from ui.paint import blit_text, draw_rounded

MANIFEST = {
    "app_id": "hello",
    "name": "Hello",
    "description": "A tiny plugin.",
    "category": "Plugins",
    "power_draw": 20.0,
}

class HelloApp(App):
    app_id = "hello"
    name = "Hello"

    def draw(self, surface):
        pal = self.palette
        surface.fill(pal.bg_bottom)
        blit_text(surface, self.font(28, True), "Hello", pal.text,
                  center=self.content.center)

    def handle(self, event):
        return False

    def icon_draw(self, surface, rect):
        draw_rounded(surface, rect, fill=self.palette.accent_soft, radius=16)

def draw_icon(surface, rect, theme):
    draw_rounded(surface, rect, fill=theme.palette.surface_raised, radius=16)
    blit_text(surface, theme.font(20, True), "H", theme.palette.accent_ink,
              center=rect.center)
```

`MANIFEST` must contain `app_id` and `name`. `description`, `category` and
`power_draw` are optional. The loader also accepts a module-level `draw_icon`
function; without it a generic lettered icon is drawn.

## Required methods

* `draw(surface)` — draw into the app's surface. Use `self.content` for the area
  below the host toolbar.
* `handle(event)` — receive pointer/keyboard events in virtual coordinates.
* `icon_draw(surface, rect)` — draw the launcher icon.

## Optional hooks

* `open_payload(payload)` — receive a shared item or search deep link.
* `copy_text()` / `paste_text()` — helpers backed by the system clipboard.
* `clipboard_copy()` / `clipboard_paste()` — return `True` to handle Ctrl+C/V.
* `ctx.share({"type": "text", "title": "...", "text": "..."})` — open the share
  sheet. Payload types are `text`, `image` and `file`.
* `ctx.undo_toast("Deleted", restore_callback)` — show a 4-second Undo toast.
* `ctx.network.internet` — `False` while Wi-Fi is off or Airplane Mode is on.
* `ctx.notify(title, body)` — post an app notification.
* `self.log.info(...)` — write to the device log/dev console.

## Rules and limits

* No external network, no subprocesses, no blocking loops in `update()`.
* Keep per-frame work under a few milliseconds; cache generated surfaces.
* Use `self.ctx.storage` for JSON or text files. Do not write outside `data/`.
* Broken plugins are caught by `services/plugin_loader.py`, logged, and disabled.
  A plugin never crashes the phone.

## Installing

Drop the module into `./plugins/` and open **Settings > Advanced > Plugins >
Reload**. Built-in example: `plugins/example_plugin.py`.
