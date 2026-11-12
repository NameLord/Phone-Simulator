"""Live home-screen widgets.

Widgets render from shared services and never own application state. The
engine imports this package's ``WIDGETS`` registry lazily so a broken widget
cannot stop the home screen from booting.
"""

from widgets.base import BaseWidget, WidgetContext
from widgets.battery import BatteryWidget
from widgets.calendar import CalendarWidget
from widgets.clock import ClockWidget
from widgets.music import MusicWidget
from widgets.notes import NotesWidget
from widgets.weather import WeatherWidget

WIDGETS = {
    "clock": ClockWidget,
    "calendar": CalendarWidget,
    "weather": WeatherWidget,
    "music": MusicWidget,
    "battery": BatteryWidget,
    "notes": NotesWidget,
}

WIDGET_LABELS = {
    "clock": "Clock",
    "calendar": "Calendar",
    "weather": "Weather",
    "music": "Music",
    "battery": "Battery",
    "notes": "Notes",
}

__all__ = ["BaseWidget", "WidgetContext", "WIDGETS", "WIDGET_LABELS"]
