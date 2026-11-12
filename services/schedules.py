"""Alarms, timer and upcoming-calendar notifications.

The clock app owns the UI; this service owns time-based state so an alarm can
fire while another app is open (or while the device is asleep). Everything is
advanced from the kernel's fixed timestep and persisted atomically through
``services.storage.py``. Snooze uses five minutes of simulated time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from services.storage import Storage

if TYPE_CHECKING:
    from core.clock import DeviceClock
    from services.notifications import NotificationCenter
    from services.settings import SettingsService
    from ui.theme import Theme

LOG = logging.getLogger("phoneos.schedules")

ALARMS_FILE = "alarms.json"
CALENDAR_FILE = "calendar.json"
SNOOZE_SECONDS = 5 * 60.0

DAY_LABELS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


@dataclass(slots=True)
class Alarm:
    alarm_id: int
    hour: int
    minute: int
    label: str = "Alarm"
    days: list[int] = field(default_factory=list)  # empty = every day
    enabled: bool = True
    snooze_until: float | None = None
    last_fired: str = ""

    def matches(self, moment: datetime) -> bool:
        if not self.enabled:
            return False
        if self.hour != moment.hour or self.minute != moment.minute:
            return False
        return not self.days or moment.weekday() in self.days

    def day_text(self) -> str:
        if not self.days:
            return "Every day"
        ordered = sorted(set(day for day in self.days if 0 <= day <= 6))
        if ordered == [0, 1, 2, 3, 4]:
            return "Weekdays"
        if ordered == [5, 6]:
            return "Weekends"
        return " ".join(DAY_LABELS[day] for day in ordered)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.alarm_id,
            "hour": self.hour,
            "minute": self.minute,
            "label": self.label,
            "days": list(self.days),
            "enabled": self.enabled,
            "snooze_until": self.snooze_until,
            "last_fired": self.last_fired,
        }


class ScheduleService:
    """Persistent alarm/timer engine plus calendar look-ahead notifications."""

    def __init__(
        self,
        settings: "SettingsService",
        storage: "Storage",
        clock: "DeviceClock",
        notifications: "NotificationCenter",
        theme: "Theme",
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._clock = clock
        self._notifications = notifications
        self._theme = theme

        self._alarms: list[Alarm] = []
        self._next_id = 1
        self._timer = {
            "duration": 300.0,
            "remaining": 300.0,
            "running": False,
            "end_ts": None,
            "label": "Timer",
        }
        self._calendar_notified: list[str] = []
        self._minute_key = ""
        self._load()
        self._minute_key = ""
        self.active_event: dict[str, object] | None = None

    # -- persistence ----------------------------------------------------------
    def _load(self) -> None:
        payload = self._storage.read_json(ALARMS_FILE, {})
        if not isinstance(payload, dict):
            payload = {}
        alarms: list[Alarm] = []
        raw = payload.get("alarms", [])
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    alarm_id = int(item.get("id", 0))
                    hour = max(0, min(23, int(item.get("hour", 0))))
                    minute = max(0, min(59, int(item.get("minute", 0))))
                except (TypeError, ValueError):
                    continue
                days = item.get("days", [])
                days = [int(day) for day in days if isinstance(day, (int, float)) and 0 <= int(day) <= 6] if isinstance(days, list) else []
                alarms.append(
                    Alarm(
                        alarm_id=alarm_id if alarm_id > 0 else len(alarms) + 1,
                        hour=hour,
                        minute=minute,
                        label=str(item.get("label", "Alarm"))[:28] or "Alarm",
                        days=sorted(set(days)),
                        enabled=bool(item.get("enabled", True)),
                        snooze_until=float(item["snooze_until"]) if isinstance(item.get("snooze_until"), (int, float)) else None,
                        last_fired=str(item.get("last_fired", "")),
                    )
                )
        self._alarms = alarms
        self._next_id = max(
            int(payload.get("next_id", 1)) if isinstance(payload.get("next_id"), (int, float)) else 1,
            max((alarm.alarm_id for alarm in alarms), default=0) + 1,
        )
        timer = payload.get("timer")
        if isinstance(timer, dict):
            try:
                duration = max(1.0, float(timer.get("duration", 300.0)))
                remaining = max(0.0, min(duration, float(timer.get("remaining", duration))))
            except (TypeError, ValueError):
                duration, remaining = 300.0, 300.0
            self._timer = {
                "duration": duration,
                "remaining": remaining,
                "running": bool(timer.get("running", False)),
                "end_ts": float(timer["end_ts"]) if isinstance(timer.get("end_ts"), (int, float)) else None,
                "label": str(timer.get("label", "Timer"))[:28] or "Timer",
            }
        notified = payload.get("calendar_notified", [])
        if isinstance(notified, list):
            self._calendar_notified = [str(item) for item in notified if isinstance(item, str)][-200:]
        if self._timer["running"]:
            end = self._timer["end_ts"]
            if not isinstance(end, (int, float)):
                self._timer["running"] = False
                self._timer["remaining"] = float(self._timer["duration"])
            else:
                self._timer["remaining"] = max(0.0, float(end) - self._clock.simulated_time.timestamp())

    def _save(self) -> None:
        self._storage.write_json(
            ALARMS_FILE,
            {
                "next_id": self._next_id,
                "alarms": [alarm.as_dict() for alarm in self._alarms],
                "timer": dict(self._timer),
                "calendar_notified": self._calendar_notified[-200:],
            },
        )

    # -- alarm API ------------------------------------------------------------
    def alarms(self) -> tuple[Alarm, ...]:
        return tuple(self._alarms)

    def alarm(self, alarm_id: int) -> Alarm | None:
        return next((alarm for alarm in self._alarms if alarm.alarm_id == alarm_id), None)

    def add_alarm(
        self,
        hour: int,
        minute: int,
        label: str = "Alarm",
        days: list[int] | None = None,
    ) -> Alarm:
        alarm = Alarm(
            alarm_id=self._next_id,
            hour=max(0, min(23, int(hour))),
            minute=max(0, min(59, int(minute))),
            label=(label.strip() or "Alarm")[:28],
            days=sorted(set(day for day in (days or []) if 0 <= day <= 6)),
        )
        self._next_id += 1
        self._alarms.append(alarm)
        self._alarms.sort(key=lambda item: (item.hour, item.minute, item.alarm_id))
        self._save()
        return alarm

    def update_alarm(
        self,
        alarm_id: int,
        *,
        hour: int | None = None,
        minute: int | None = None,
        label: str | None = None,
        days: list[int] | None = None,
        enabled: bool | None = None,
    ) -> bool:
        alarm = self.alarm(alarm_id)
        if alarm is None:
            return False
        if hour is not None:
            alarm.hour = max(0, min(23, int(hour)))
        if minute is not None:
            alarm.minute = max(0, min(59, int(minute)))
        if label is not None:
            alarm.label = (label.strip() or "Alarm")[:28]
        if days is not None:
            alarm.days = sorted(set(day for day in days if 0 <= day <= 6))
        if enabled is not None:
            alarm.enabled = bool(enabled)
        alarm.last_fired = ""
        self._alarms.sort(key=lambda item: (item.hour, item.minute, item.alarm_id))
        self._save()
        return True

    def delete_alarm(self, alarm_id: int) -> bool:
        before = len(self._alarms)
        self._alarms = [alarm for alarm in self._alarms if alarm.alarm_id != alarm_id]
        if len(self._alarms) == before:
            return False
        if self.active_event is not None and self.active_event.get("alarm_id") == alarm_id:
            self.active_event = None
        self._save()
        return True

    def toggle_alarm(self, alarm_id: int) -> bool:
        alarm = self.alarm(alarm_id)
        if alarm is None:
            return False
        alarm.enabled = not alarm.enabled
        if not alarm.enabled:
            alarm.snooze_until = None
        self._save()
        return alarm.enabled

    # -- timer API ------------------------------------------------------------
    @property
    def timer(self) -> dict[str, object]:
        return dict(self._timer)

    def set_timer(self, seconds: float, label: str = "Timer") -> None:
        duration = max(1.0, min(24 * 3600.0, float(seconds)))
        self._timer = {
            "duration": duration,
            "remaining": duration,
            "running": False,
            "end_ts": None,
            "label": label[:28] or "Timer",
        }
        self._save()

    def start_timer(self) -> bool:
        remaining = float(self._timer.get("remaining", 0.0))
        if remaining <= 0.01:
            remaining = float(self._timer.get("duration", 300.0))
        self._timer["remaining"] = remaining
        self._timer["running"] = True
        self._timer["end_ts"] = self._clock.simulated_time.timestamp() + remaining
        self._save()
        return True

    def pause_timer(self) -> None:
        if not self._timer.get("running"):
            return
        self._timer["remaining"] = max(0.0, float(self._timer.get("remaining", 0.0)))
        self._timer["running"] = False
        self._timer["end_ts"] = None
        self._save()

    def reset_timer(self) -> None:
        self._timer["remaining"] = float(self._timer.get("duration", 300.0))
        self._timer["running"] = False
        self._timer["end_ts"] = None
        self._save()

    # -- event handling -------------------------------------------------------
    @property
    def clock(self) -> "DeviceClock":
        return self._clock

    @property
    def has_active_event(self) -> bool:
        return self.active_event is not None

    def dismiss(self) -> None:
        self.active_event = None

    def snooze(self) -> bool:
        event = self.active_event
        if event is None or event.get("kind") != "alarm":
            return False
        alarm = self.alarm(int(event.get("alarm_id", 0)))
        if alarm is None:
            return False
        alarm.snooze_until = self._clock.simulated_time.timestamp() + SNOOZE_SECONDS
        alarm.last_fired = ""
        self.active_event = None
        self._save()
        return True

    def update(self, dt: float) -> None:
        """Advance timers once per fixed step; never sleeps or blocks."""
        del dt
        now = self._clock.simulated_time
        minute_key = now.strftime("%Y%m%d%H%M")
        if minute_key != self._minute_key:
            self._minute_key = minute_key
            self._check_calendar(now, minute_key)
        if self.active_event is None:
            self._check_alarms(now, minute_key)
        self._check_timer(now)

    def _check_alarms(self, now: datetime, minute_key: str) -> None:
        now_ts = now.timestamp()
        for alarm in self._alarms:
            if alarm.snooze_until is not None:
                if now_ts >= alarm.snooze_until:
                    alarm.snooze_until = None
                    alarm.last_fired = minute_key
                    self._fire_alarm(alarm)
                    self._save()
                    return
                # A future snooze suppresses the normal schedule until it expires.
                continue
            if alarm.matches(now) and alarm.last_fired != minute_key:
                alarm.last_fired = minute_key
                self._fire_alarm(alarm)
                self._save()
                return

    def _fire_alarm(self, alarm: Alarm) -> None:
        self.active_event = {
            "kind": "alarm",
            "alarm_id": alarm.alarm_id,
            "title": alarm.label,
            "time": f"{alarm.hour:02d}:{alarm.minute:02d}",
        }
        self._notifications.post(
            "clock",
            "Alarm",
            f"{alarm.label} \u00b7 {alarm.hour:02d}:{alarm.minute:02d}",
            self._theme.palette.warning,
            banner=True,
            actions=(
                {"id": "snooze", "label": "Snooze", "primary": True},
                {"id": "dismiss", "label": "Dismiss"},
            ),
        )

    def _check_timer(self, now: datetime) -> None:
        if not self._timer.get("running"):
            return
        end = self._timer.get("end_ts")
        if not isinstance(end, (int, float)):
            self._timer["running"] = False
            return
        remaining = max(0.0, float(end) - now.timestamp())
        self._timer["remaining"] = remaining
        if remaining > 0.01:
            return
        self._timer["running"] = False
        self._timer["end_ts"] = None
        self._timer["remaining"] = 0.0
        self._save()
        if self.active_event is None:
            self.active_event = {
                "kind": "timer",
                "title": str(self._timer.get("label", "Timer")),
                "time": "00:00",
            }
            self._notifications.post(
                "clock",
                "Timer finished",
                str(self._timer.get("label", "Timer")),
                self._theme.palette.success,
                banner=True,
                actions=(
                    {"id": "dismiss", "label": "Dismiss", "primary": True},
                ),
            )

    def _check_calendar(self, now: datetime, minute_key: str) -> None:
        payload = self._storage.read_json(CALENDAR_FILE, {})
        events = payload.get("events", []) if isinstance(payload, dict) else []
        if not isinstance(events, list):
            return
        dnd = self._settings.get_bool("simulation.dnd", False)
        changed = False
        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                event_date = datetime.strptime(str(event.get("date", "")), "%Y-%m-%d").date()
                event_time = datetime.strptime(str(event.get("time", "")), "%H:%M").time()
            except ValueError:
                continue
            if event_date != now.date():
                continue
            event_dt = datetime.combine(event_date, event_time)
            delta = (event_dt - now).total_seconds()
            if not (0.0 < delta <= 3600.0):
                continue
            key = f"{minute_key}:{event.get('id', 0)}"
            if key in self._calendar_notified:
                continue
            self._calendar_notified.append(key)
            self._calendar_notified = self._calendar_notified[-200:]
            changed = True
            if dnd:
                continue
            title = str(event.get("title", "Event"))[:60]
            note = str(event.get("note", "")).strip()
            body = f"{event_time.strftime('%H:%M')}" + (f" \u00b7 {note[:80]}" if note else "")
            self._notifications.post(
                "calendar",
                "Next hour",
                f"{title} at {body}",
                self._theme.palette.accent,
                banner=True,
            )
        if changed:
            self._save()

    def shutdown(self) -> None:
        self._save()

    def __repr__(self) -> str:
        return f"ScheduleService(alarms={len(self._alarms)}, timer={self._timer['remaining']:.0f}s)"
