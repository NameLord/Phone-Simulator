"""Simulated Wi-Fi, Bluetooth and airplane-mode connectivity."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.settings import SettingsService

LOG = logging.getLogger("phoneos.network")
WIFI_NETWORKS = ("HomeNet", "Cafe Guest", "PhoneOS Lab", "Neighbour 5G")
BLUETOOTH_DEVICES = ("AirPods Pro", "WH-1000XM5", "Car Audio", "Desk Speaker")


class NetworkService:
    def __init__(self, settings: "SettingsService") -> None:
        self._settings = settings

    @property
    def airplane(self) -> bool:
        return self._settings.get_bool("quick.airplane", False)

    @property
    def wifi_enabled(self) -> bool:
        return self._settings.get_bool("quick.wifi", True) and not self.airplane

    @property
    def bluetooth_enabled(self) -> bool:
        return self._settings.get_bool("quick.bluetooth", False) and not self.airplane

    @property
    def wifi_network(self) -> str:
        network = self._settings.get_str("quick.wifi_network", "HomeNet")
        return network if network in WIFI_NETWORKS else WIFI_NETWORKS[0]

    @property
    def bluetooth_device(self) -> str:
        device = self._settings.get_str("quick.bluetooth_device", "")
        return device if device in BLUETOOTH_DEVICES else ""

    @property
    def internet(self) -> bool:
        return self.wifi_enabled and bool(self.wifi_network)

    def set_wifi(self, enabled: bool) -> bool:
        if self.airplane and enabled:
            return False
        LOG.info("wifi %s", "on" if enabled else "off")
        return self._settings.set("quick.wifi", bool(enabled))

    def set_bluetooth(self, enabled: bool) -> bool:
        if self.airplane and enabled:
            return False
        LOG.info("bluetooth %s", "on" if enabled else "off")
        return self._settings.set("quick.bluetooth", bool(enabled))

    def set_airplane(self, enabled: bool) -> bool:
        self._settings.set("quick.airplane", bool(enabled))
        if enabled:
            self._settings.set("quick.wifi", False)
            self._settings.set("quick.bluetooth", False)
        LOG.info("airplane %s", "on" if enabled else "off")
        return True

    def connect_wifi(self, network: str) -> bool:
        if not self.wifi_enabled:
            return False
        if network not in WIFI_NETWORKS:
            return False
        return self._settings.set("quick.wifi_network", network)

    def pair_bluetooth(self, device: str) -> bool:
        if not self.bluetooth_enabled:
            return False
        if device not in BLUETOOTH_DEVICES:
            return False
        return self._settings.set("quick.bluetooth_device", device)

    def disconnect_bluetooth(self) -> bool:
        return self._settings.set("quick.bluetooth_device", "")

    def status_label(self) -> str:
        if self.airplane:
            return "Airplane mode"
        if not self.wifi_enabled:
            return "Wi-Fi off"
        return self.wifi_network

    def __repr__(self) -> str:
        return f"NetworkService(wifi={self.wifi_enabled}, bt={self.bluetooth_enabled}, airplane={self.airplane})"


__all__ = ["BLUETOOTH_DEVICES", "NetworkService", "WIFI_NETWORKS"]
