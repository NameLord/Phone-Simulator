"""Contacts, dialer calls and message threads.

Everything is fake but stateful: contacts seed once into ``contacts.json``,
messages persist in ``messages.json`` and recent calls in ``calls.json``.
Incoming calls are scheduled with a dt timer; accepting one starts a live call
timer in :class:`CallState`. Messages schedule canned auto-replies after 2-6 s.
No audio is played here; the call overlay owns the ring animation and the
notification service owns message notifications.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from services.storage import Storage

if TYPE_CHECKING:
    from core.clock import DeviceClock
    from services.device import DeviceService
    from services.notifications import NotificationCenter
    from services.settings import SettingsService
    from ui.theme import Theme

LOG = logging.getLogger("phoneos.telephony")

CONTACTS_FILE = "contacts.json"
MESSAGES_FILE = "messages.json"
CALLS_FILE = "calls.json"
MAX_CALL_LOG = 24
RING_SECONDS = 30.0


@dataclass(slots=True)
class Contact:
    contact_id: str
    name: str
    number: str
    color: tuple[int, int, int]
    email: str = ""
    note: str = ""
    favorite: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.contact_id,
            "name": self.name,
            "number": self.number,
            "color": list(self.color),
            "email": self.email,
            "note": self.note,
            "favorite": self.favorite,
        }


@dataclass(slots=True)
class Message:
    sender: str  # "me" or "them"
    text: str
    timestamp: float
    read: bool = True


@dataclass(slots=True)
class CallRecord:
    name: str
    number: str
    direction: str  # incoming / outgoing / missed
    duration: float
    timestamp: float


@dataclass(slots=True)
class CallState:
    contact_id: str
    name: str
    number: str
    direction: str
    status: str = "ringing"  # ringing / active / ended
    ringing: float = 0.0
    accepted_at: float | None = None
    started_clock: float = 0.0

    @property
    def is_ringing(self) -> bool:
        return self.status == "ringing"

    @property
    def is_active(self) -> bool:
        return self.status == "active"


SEED_CONTACTS = (
    ("alex", "Alex Rivera", "+1 555 0101", (90, 220, 255)),
    ("sam", "Sam Chen", "+1 555 0142", (150, 120, 255)),
    ("mom", "Mom", "+1 555 0188", (255, 186, 84)),
    ("jordan", "Jordan Blake", "+1 555 0119", (72, 226, 178)),
)

REPLY_POOL = {
    "alex": ("On my way.", "Give me five minutes.", "That works for me.", "Ha! Fair."),
    "sam": ("Sounds good.", "I'll check and get back to you.", "Can we do later?", "Nice."),
    "mom": ("Call me when you can.", "Are you eating enough?", "Love you.", "Okay, sweetie."),
    "jordan": ("Yep.", "Running late, sorry.", "See you there.", "I'll bring snacks."),
}
DEFAULT_REPLIES = ("Got it.", "Interesting.", "Sure.", "Let me get back to you.")


class TelephonyService:
    """Contacts, calls and persisted conversations."""

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
        self._device: "DeviceService | None" = None
        self._rng = random.Random(0xCA11)
        self._contacts: list[Contact] = []
        self._threads: dict[str, list[Message]] = {}
        self._recent: list[CallRecord] = []
        self._reply_queue: list[tuple[float, str]] = []
        self._typing: dict[str, float] = {}
        self.call: CallState | None = None
        self._call_rate = self._settings.get_float("simulation.calls_per_hour", 0.25)
        self._call_timer = self._next_call_interval()
        self._load_contacts()
        self._load_messages()
        self._load_calls()

    # -- bindings -------------------------------------------------------------
    def bind_device(self, device: "DeviceService") -> None:
        self._device = device

    # -- persistence ----------------------------------------------------------
    def _load_contacts(self) -> None:
        raw = self._storage.read_json(CONTACTS_FILE, None)
        if not isinstance(raw, list) or not raw:
            self._contacts = [
                Contact(contact_id, name, number, color)
                for contact_id, name, number, color in SEED_CONTACTS
            ]
            self._save_contacts()
            return
        contacts: list[Contact] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                color = tuple(int(value) for value in item.get("color", (90, 220, 255)))[:3]
                contacts.append(
                    Contact(
                        str(item.get("id", "")),
                        str(item.get("name", "Unknown"))[:80],
                        str(item.get("number", ""))[:40],
                        (int(color[0]), int(color[1]), int(color[2])),
                        str(item.get("email", ""))[:120],
                        str(item.get("note", ""))[:400],
                        bool(item.get("favorite", False)),
                    )
                )
            except (TypeError, ValueError, IndexError):
                continue
        self._contacts = [contact for contact in contacts if contact.contact_id]

    def _save_contacts(self) -> None:
        self._storage.write_json(CONTACTS_FILE, [contact.as_dict() for contact in self._contacts])

    def _load_messages(self) -> None:
        raw = self._storage.read_json(MESSAGES_FILE, {"threads": {}})
        threads: dict[str, list[Message]] = {}
        if isinstance(raw, dict) and isinstance(raw.get("threads"), dict):
            for contact_id, items in raw["threads"].items():
                if not isinstance(items, list):
                    continue
                cleaned: list[Message] = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    try:
                        cleaned.append(
                            Message(
                                sender=str(item.get("sender", "them")),
                                text=str(item.get("text", ""))[:1000],
                                timestamp=float(item.get("timestamp", 0.0)),
                                read=bool(item.get("read", True)),
                            )
                        )
                    except (TypeError, ValueError):
                        continue
                threads[str(contact_id)] = cleaned
        self._threads = threads

    def _save_messages(self) -> None:
        payload = {
            "threads": {
                contact_id: [
                    {"sender": message.sender, "text": message.text, "timestamp": message.timestamp, "read": message.read}
                    for message in messages
                ]
                for contact_id, messages in self._threads.items()
            }
        }
        self._storage.write_json(MESSAGES_FILE, payload)

    def _load_calls(self) -> None:
        raw = self._storage.read_json(CALLS_FILE, [])
        calls: list[CallRecord] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    calls.append(
                        CallRecord(
                            str(item.get("name", "Unknown")),
                            str(item.get("number", "")),
                            str(item.get("direction", "incoming")),
                            float(item.get("duration", 0.0)),
                            float(item.get("timestamp", 0.0)),
                        )
                    )
                except (TypeError, ValueError):
                    continue
        self._recent = calls[:MAX_CALL_LOG]

    def _save_calls(self) -> None:
        self._storage.write_json(
            CALLS_FILE,
            [
                {
                    "name": call.name,
                    "number": call.number,
                    "direction": call.direction,
                    "duration": round(call.duration, 1),
                    "timestamp": call.timestamp,
                }
                for call in self._recent
            ],
        )

    # -- contacts / reads -----------------------------------------------------
    @property
    def contacts(self) -> tuple[Contact, ...]:
        return tuple(self._contacts)

    @property
    def recent_calls(self) -> tuple[CallRecord, ...]:
        return tuple(self._recent)

    @property
    def active_call(self) -> CallState | None:
        return self.call if self.call is not None and self.call.status != "ended" else None

    def contact(self, contact_id: str) -> Contact | None:
        for item in self._contacts:
            if item.contact_id == contact_id:
                return item
        return None

    def contact_for_number(self, number: str) -> Contact | None:
        digits = "".join(ch for ch in number if ch.isdigit())
        for item in self._contacts:
            if "".join(ch for ch in item.number if ch.isdigit()) == digits:
                return item
        return None

    def conversation(self, contact_id: str) -> tuple[Message, ...]:
        return tuple(self._threads.get(contact_id, ()))

    def add_contact(
        self,
        name: str,
        number: str,
        email: str = "",
        note: str = "",
        color: tuple[int, int, int] | None = None,
    ) -> Contact | None:
        name = " ".join(str(name).split())[:80]
        number = " ".join(str(number).split())[:40]
        if not name or not number:
            return None
        palette = ((90, 220, 255), (150, 120, 255), (255, 186, 84), (72, 226, 178), (255, 108, 150))
        contact = Contact(
            contact_id=f"c{self._next_contact_id()}",
            name=name,
            number=number,
            color=color or palette[len(self._contacts) % len(palette)],
            email=str(email).strip()[:120],
            note=str(note).strip()[:400],
        )
        self._contacts.append(contact)
        self._save_contacts()
        return contact

    def update_contact(self, contact_id: str, **fields: object) -> bool:
        contact = self.contact(contact_id)
        if contact is None:
            return False
        if "name" in fields:
            name = " ".join(str(fields["name"]).split())[:80]
            if not name:
                return False
            contact.name = name
        if "number" in fields:
            number = " ".join(str(fields["number"]).split())[:40]
            if not number:
                return False
            contact.number = number
        if "email" in fields:
            contact.email = str(fields["email"]).strip()[:120]
        if "note" in fields:
            contact.note = str(fields["note"]).strip()[:400]
        if "favorite" in fields:
            contact.favorite = bool(fields["favorite"])
        if "color" in fields and isinstance(fields["color"], tuple):
            color = fields["color"]
            contact.color = (int(color[0]), int(color[1]), int(color[2]))
        self._save_contacts()
        return True

    def delete_contact(self, contact_id: str) -> bool:
        before = len(self._contacts)
        self._contacts = [contact for contact in self._contacts if contact.contact_id != contact_id]
        if len(self._contacts) == before:
            return False
        self._threads.pop(contact_id, None)
        self._typing.pop(contact_id, None)
        self._save_contacts()
        self._save_messages()
        return True

    def toggle_favorite(self, contact_id: str) -> bool:
        contact = self.contact(contact_id)
        if contact is None:
            return False
        contact.favorite = not contact.favorite
        self._save_contacts()
        return contact.favorite

    def search_contacts(self, query: str) -> list[Contact]:
        query = str(query).strip().casefold()
        if not query:
            return list(self._contacts)
        return [
            contact
            for contact in self._contacts
            if query in contact.name.casefold()
            or query in contact.number.casefold()
            or query in contact.email.casefold()
        ]

    def _next_contact_id(self) -> str:
        highest = 0
        for contact in self._contacts:
            suffix = contact.contact_id.lstrip("c")
            if suffix.isdigit():
                highest = max(highest, int(suffix))
        return str(highest + 1)

    def is_typing(self, contact_id: str) -> bool:
        return self._typing.get(contact_id, 0.0) > 0.0

    def mark_thread_read(self, contact_id: str) -> None:
        changed = False
        for message in self._threads.get(contact_id, []):
            if not message.read:
                message.read = True
                changed = True
        if changed:
            self._save_messages()

    def unread_count(self, contact_id: str) -> int:
        return sum(1 for message in self._threads.get(contact_id, []) if not message.read)

    def total_unread(self) -> int:
        return sum(self.unread_count(contact_id) for contact_id in self._threads)

    def delete_thread(self, contact_id: str) -> bool:
        if contact_id not in self._threads:
            return False
        self._threads.pop(contact_id, None)
        self._typing.pop(contact_id, None)
        self._save_messages()
        return True

    def thread_summaries(self) -> list[tuple[Contact, str, float]]:
        summaries: list[tuple[Contact, str, float]] = []
        for contact in self._contacts:
            messages = self._threads.get(contact.contact_id, [])
            preview = messages[-1].text if messages else "No messages yet"
            stamp = messages[-1].timestamp if messages else 0.0
            summaries.append((contact, preview, stamp))
        summaries.sort(key=lambda row: row[2], reverse=True)
        return summaries

    # -- calls ----------------------------------------------------------------
    def start_incoming(self, contact_id: str | None = None, *, force: bool = False) -> bool:
        if self.active_call is not None:
            return False
        if not force and self._settings.get_bool("quick.airplane", False):
            return False
        if not force and self._dnd_active():
            return False
        if contact_id is None and self._contacts:
            contact = self._rng.choice(self._contacts)
        else:
            contact = self.contact(contact_id) if contact_id else None
        if contact is None:
            return False
        self.call = CallState(
            contact_id=contact.contact_id,
            name=contact.name,
            number=contact.number,
            direction="incoming",
            started_clock=self._clock.uptime,
        )
        return True

    def place_call(self, number: str, name: str | None = None) -> bool:
        if self.active_call is not None:
            return False
        if self._settings.get_bool("quick.airplane", False):
            return False
        number = "".join(ch for ch in str(number) if ch.isdigit() or ch in "+ -")
        if not number.strip():
            return False
        contact = self.contact_for_number(number)
        self.call = CallState(
            contact_id=contact.contact_id if contact else "",
            name=name or (contact.name if contact else "Unknown"),
            number=contact.number if contact else number,
            direction="outgoing",
            status="active",
            accepted_at=self._clock.uptime,
            started_clock=self._clock.uptime,
        )
        return True

    def accept_call(self) -> bool:
        if self.call is None or not self.call.is_ringing:
            return False
        self.call.status = "active"
        self.call.accepted_at = self._clock.uptime
        return True

    def decline_call(self) -> bool:
        if self.call is None or not self.call.is_ringing:
            return False
        call = self.call
        self._log_call(call, "missed" if call.direction == "incoming" else "outgoing")
        self.call = None
        self._notifications.post(
            "phone",
            "Call declined",
            f"{call.name} was declined.",
            self._theme.palette.text_secondary,
            banner=False,
        )
        return True

    def end_call(self) -> bool:
        if self.call is None:
            return False
        call = self.call
        duration = self.call_duration()
        self._log_call(call, call.direction, duration)
        self.call = None
        return True

    def call_duration(self) -> float:
        if self.call is None or self.call.accepted_at is None:
            return 0.0
        return max(0.0, self._clock.uptime - self.call.accepted_at)

    def _log_call(self, call: CallState, direction: str, duration: float = 0.0) -> None:
        self._recent.insert(
            0,
            CallRecord(
                name=call.name,
                number=call.number,
                direction=direction,
                duration=duration,
                timestamp=self._clock.simulated_time.timestamp(),
            ),
        )
        self._recent = self._recent[:MAX_CALL_LOG]
        self._save_calls()

    def _next_call_interval(self) -> float:
        rate = max(0.0, self._settings.get_float("simulation.calls_per_hour", 0.25))
        if rate <= 0.0:
            return float("inf")
        mean = 3600.0 / rate
        return max(20.0, self._rng.expovariate(1.0 / mean))

    # -- messages -------------------------------------------------------------
    def send_message(self, contact_id: str, text: str) -> bool:
        contact = self.contact(contact_id)
        text = " ".join(str(text).split())
        if contact is None or not text:
            return False
        message = Message("me", text[:500], self._clock.simulated_time.timestamp())
        self._threads.setdefault(contact_id, []).append(message)
        self._save_messages()
        delay = self._rng.uniform(2.0, 6.0)
        self._reply_queue.append((delay, contact_id))
        self._typing[contact_id] = delay
        return True

    def _deliver_reply(self, contact_id: str) -> None:
        contact = self.contact(contact_id)
        if contact is None:
            return
        pool = REPLY_POOL.get(contact_id, DEFAULT_REPLIES)
        text = self._rng.choice(pool)
        self._threads.setdefault(contact_id, []).append(
            Message("them", text, self._clock.simulated_time.timestamp(), read=False)
        )
        self._typing.pop(contact_id, None)
        self._save_messages()
        if not self._dnd_active():
            self._notifications.post(
                "messages",
                contact.name,
                text,
                contact.color,
                banner=True,
                extra={"contact_id": contact.contact_id},
                actions=(
                    {"id": "reply", "label": "Reply", "reply": True, "primary": True},
                    {"id": "mark_read", "label": "Mark read"},
                ),
            )

    # -- input-independent update --------------------------------------------
    def update(self, dt: float) -> None:
        dt = max(0.0, float(dt))
        rate = self._settings.get_float("simulation.calls_per_hour", 0.25)
        if abs(rate - self._call_rate) > 1e-6:
            self._call_rate = rate
            self._call_timer = self._next_call_interval()

        if self.call is not None:
            if self.call.is_ringing:
                self.call.ringing += dt
                if self.call.ringing >= RING_SECONDS:
                    missed = self.call
                    self._log_call(missed, "missed")
                    self.call = None
                    if not self._dnd_active():
                        self._notifications.post(
                            "phone",
                            "Missed call",
                            f"{missed.name} \u00b7 {missed.number}",
                            self._theme.palette.warning,
                        )

        if self.active_call is None:
            self._call_timer -= dt
            if self._call_timer <= 0.0:
                self._call_timer = self._next_call_interval()
                if self._can_ring() and not self._dnd_active():
                    self.start_incoming(force=True)

        remaining: list[tuple[float, str]] = []
        for due, contact_id in self._reply_queue:
            due -= dt
            if due <= 0.0:
                self._deliver_reply(contact_id)
            else:
                remaining.append((due, contact_id))
        self._reply_queue = remaining
        for contact_id in list(self._typing):
            self._typing[contact_id] -= dt
            if self._typing[contact_id] <= 0.0:
                self._typing.pop(contact_id, None)

    def _can_ring(self) -> bool:
        if self._device is None:
            return True
        from services.device import DeviceState

        return self._device.state in (DeviceState.LOCKED, DeviceState.UNLOCKED)

    def _dnd_active(self) -> bool:
        if self._settings.get_bool("quick.dnd", False):
            return True
        if not self._settings.get_bool("simulation.dnd", False):
            return False
        start = self._settings.get_int("simulation.dnd_start", 22) % 24
        end = self._settings.get_int("simulation.dnd_end", 7) % 24
        hour = self._clock.simulated_time.hour
        if start == end:
            return True
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def __repr__(self) -> str:
        return (
            f"TelephonyService(contacts={len(self._contacts)}, "
            f"threads={len(self._threads)}, call={self.call.status if self.call else None})"
        )
