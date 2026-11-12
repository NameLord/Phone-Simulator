"""Service locator.

This module owns the single sanctioned piece of process-wide mutable state:
the global :data:`_GLOBAL` registry instance. Everything else in the project
receives dependencies explicitly (constructor injection) or pulls them from
the registry by name, which keeps subsystems from importing each other.
"""

from __future__ import annotations

from typing import Any, Iterator, TypeVar

T = TypeVar("T")


class ServiceName:
    """Canonical registry keys, so services never drift by typo."""

    KERNEL = "kernel"
    CONFIG = "config"
    DISPLAY = "display"
    INPUT = "input"
    CLOCK = "device_clock"
    SETTINGS = "settings"
    THEME = "theme"
    THEME_ENGINE = "theme_engine"
    HOME_STATE = "home_state"
    QUICK_SETTINGS = "quick_settings"
    NETWORK = "network"
    CLIPBOARD = "clipboard"
    TOAST = "toast"
    SHARE = "share"
    BACKUP = "backup"
    PROFILES = "profiles"
    PLUGINS = "plugins"
    NOTIFICATIONS = "notifications"
    STATUS_BAR = "status_bar"
    STORAGE = "storage"
    APP_MANAGER = "app_manager"
    FX = "fx"
    PROFILER = "profiler"
    POWER = "power"
    DEVICE = "device"
    TELEPHONY = "telephony"
    SIM_EVENTS = "sim_events"
    SCHEDULES = "schedules"
    AUDIO = "audio"
    PHOTOS = "photo_library"


class ServiceMissing(LookupError):
    """Raised when a service name has not been registered."""


class Registry:
    """A tiny typed service locator."""

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}

    # -- registration -------------------------------------------------------
    def provide(self, name: str, service: Any, *, replace: bool = False) -> None:
        """Register ``service`` under ``name``.

        Raises ``KeyError`` if the name is taken and ``replace`` is False,
        which catches accidental double-wiring during boot.
        """
        if not name:
            raise ValueError("service name must be a non-empty string")
        if name in self._services and not replace:
            raise KeyError(f"service {name!r} is already registered")
        self._services[name] = service

    def set(self, name: str, service: Any) -> None:
        """Register or replace a service (idempotent)."""
        self._services[name] = service

    # -- lookup -------------------------------------------------------------
    def require(self, name: str, expected: type[T] | None = None) -> T:
        """Return the service registered under ``name``.

        When ``expected`` is given the value is type-checked at runtime,
        which turns wiring mistakes into immediate, readable failures.
        """
        try:
            service = self._services[name]
        except KeyError as exc:
            raise ServiceMissing(
                f"service {name!r} is not registered (known: {', '.join(sorted(self._services)) or 'none'})"
            ) from exc
        if expected is not None and not isinstance(service, expected):
            raise TypeError(
                f"service {name!r} is {type(service).__name__}, expected {expected.__name__}"
            )
        return service

    def get(self, name: str, default: Any = None) -> Any:
        return self._services.get(name, default)

    def has(self, name: str) -> bool:
        return name in self._services

    def remove(self, name: str) -> Any:
        return self._services.pop(name)

    def clear(self) -> None:
        self._services.clear()

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._services))

    def __contains__(self, name: object) -> bool:
        return name in self._services

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._services))

    def __len__(self) -> int:
        return len(self._services)

    def __repr__(self) -> str:
        return f"Registry({', '.join(self.names()) or 'empty'})"


_GLOBAL = Registry()


def get_registry() -> Registry:
    """Return the process-wide registry used by the kernel and its services."""
    return _GLOBAL


def install(registry: Registry) -> None:
    """Replace the process-wide registry (used by tests and tooling)."""
    global _GLOBAL
    _GLOBAL = registry
