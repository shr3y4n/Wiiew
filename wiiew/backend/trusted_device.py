"""
Trusted Device Presence Detector for Wiiew.
Monitors local network (ARP cache, ICMP ping, in-app PWA heartbeats)
to detect whether the trusted phone is currently at home.
Includes sleep tolerance with configurable grace period.
"""

from __future__ import annotations

import asyncio
import re
import socket
import subprocess
import time
from typing import Dict, List, Optional

from .config import WiiewSettings
from .proximity import ProximityEngine, ProximityState, ProximityTelemetry


def get_arp_cache() -> Dict[str, str]:
    """
    Query the Windows ARP cache.
    Returns mapping of IP address -> normalized MAC address (aa:bb:cc:dd:ee:ff).
    """
    arp_map: Dict[str, str] = {}
    try:
        output = subprocess.check_output(
            ["arp", "-a"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3.0,
        )
        # Windows arp -a output format:
        # 192.168.1.101         de-f3-d1-75-ab-bf     dynamic
        pattern = re.compile(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F\-]{17})\s+(\w+)")
        for line in output.splitlines():
            m = pattern.search(line)
            if m:
                ip, mac_raw, type_ = m.group(1), m.group(2), m.group(3).lower()
                if type_ in ("dynamic", "static"):
                    mac_norm = mac_raw.lower().replace("-", ":")
                    # Ignore broadcast / multicast
                    if mac_norm not in ("ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:16", "01:00:5e:00:00:fb"):
                        arp_map[ip] = mac_norm
    except Exception as e:
        print(f"[TrustedDevice] ARP query error: {e}")
    return arp_map


def ping_host_sync(ip: str, timeout_ms: int = 600) -> bool:
    """Send a single ICMP echo request on Windows."""
    if not ip:
        return False
    try:
        res = subprocess.run(
            ["ping", "-n", "1", "-w", str(timeout_ms), ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        )
        return res.returncode == 0
    except Exception:
        return False


def resolve_hostname(ip: str) -> str:
    """Attempt reverse DNS lookup for friendly device name."""
    try:
        host, _, _ = socket.gethostbyaddr(ip)
        return host
    except Exception:
        return ""


class TrustedDeviceDetector:
    """
    Tracks presence of a designated trusted smartphone.
    Uses multi-signal fusion:
    1. Direct ICMP Ping
    2. Windows ARP Cache inspection
    3. In-app PWA foreground heartbeat (HTTP)
    4. Sleep grace period state machine
    """

    def __init__(self, settings: WiiewSettings) -> None:
        self.settings = settings
        self.proximity_engine = ProximityEngine(settings)
        self.last_seen_timestamp: Optional[float] = None
        self.last_seen_method: Optional[str] = None
        self.is_home: bool = False
        self._is_trusted_in_room_override: Optional[bool] = None
        self.network_state: str = "NETWORK_UNKNOWN"
        self.phone_state: str = "UNKNOWN"
        self.status_label: str = "NOT CONFIGURED"
        self._lock = asyncio.Lock()

    @property
    def is_trusted_in_room(self) -> bool:
        """
        Determine whether the trusted phone authorizes intrusion alert suppression.
        If proximity enabled: requires NETWORK_PRESENT (is_home) + PROXIMITY_NEAR.
        If proximity is UNKNOWN or FAR: user is considered AWAY from the room.
        If proximity is disabled: falls back to is_home.
        """
        if self._is_trusted_in_room_override is not None:
            return self._is_trusted_in_room_override
        if self.settings.phone_proximity_enabled:
            is_near = (self.proximity_engine.current_state == ProximityState.NEAR)
            return bool(self.is_home and is_near)
        return bool(self.is_home)

    @is_trusted_in_room.setter
    def is_trusted_in_room(self, val: Optional[bool]) -> None:
        self._is_trusted_in_room_override = val

    def _resolve_authorization(self) -> None:
        """Clear manual override to allow dynamic state resolution."""
        self._is_trusted_in_room_override = None

    def heartbeat(self, source: str = "pwa") -> None:
        """Called when PWA on phone sends a live heartbeat or user taps I'm Home."""
        self.last_seen_timestamp = time.time()
        self.last_seen_method = f"heartbeat ({source})"
        self.network_state = "NETWORK_PRESENT"
        self.phone_state = "PHONE_PRESENT"
        self.is_home = True
        self.status_label = "HOME (ACTIVE)"
        self._resolve_authorization()

    async def check_presence(self) -> bool:
        """
        Execute one probe cycle.
        Returns True if phone is on the network (accounting for grace period).
        """
        target_ip = self.settings.trusted_phone_ip.strip()
        target_mac = self.settings.trusted_phone_mac.strip().lower().replace("-", ":")

        if not target_ip and not target_mac:
            self.network_state = "NETWORK_UNKNOWN"
            self.phone_state = "UNKNOWN"
            self.status_label = "NOT CONFIGURED"
            self.is_home = False
            self._is_trusted_in_room_override = None
            return False

        detected_now = False
        method_now = None

        # 1. Ping probe if IP is known
        if target_ip:
            alive = await asyncio.to_thread(ping_host_sync, target_ip, 600)
            if alive:
                detected_now = True
                method_now = "ping"

        # 2. ARP Cache probe (helps if phone drops ping or changed IP with same MAC)
        if not detected_now:
            arp = await asyncio.to_thread(get_arp_cache)
            if target_mac and target_mac in arp.values():
                detected_now = True
                method_now = "arp (mac match)"
                # If IP changed on DHCP, auto-update cached IP
                for ip, mac in arp.items():
                    if mac == target_mac and ip != target_ip:
                        self.settings.trusted_phone_ip = ip
            elif target_ip and target_ip in arp:
                detected_now = True
                method_now = "arp (ip match)"

        now = time.time()
        if detected_now:
            self.last_seen_timestamp = now
            self.last_seen_method = method_now
            self.network_state = "NETWORK_PRESENT"
            self.phone_state = "PHONE_PRESENT"
            self.is_home = True
            self.status_label = "HOME (ACTIVE)"
        else:
            # 3. Grace period evaluation (Requirement 7: Phone sleep tolerance)
            if self.last_seen_timestamp is not None:
                elapsed = now - self.last_seen_timestamp
                grace = float(self.settings.phone_grace_period_seconds)
                if elapsed <= 15:
                    self.network_state = "NETWORK_PRESENT"
                    self.phone_state = "PHONE_PRESENT"
                    self.is_home = True
                    self.status_label = "HOME (ACTIVE)"
                elif elapsed <= grace:
                    self.network_state = "NETWORK_PRESENT"
                    self.phone_state = "PHONE_MAYBE_AWAY"
                    self.is_home = True
                    self.status_label = "HOME (SLEEPING)"
                else:
                    self.network_state = "NETWORK_AWAY"
                    self.phone_state = "PHONE_AWAY"
                    self.is_home = False
                    self.status_label = "AWAY"
            else:
                self.network_state = "NETWORK_AWAY"
                self.phone_state = "PHONE_AWAY"
                self.is_home = False
                self.status_label = "AWAY"

        # 4. Proximity evaluation
        await self.proximity_engine.update(
            mac=target_mac,
            ip=target_ip,
            network_present=(self.network_state == "NETWORK_PRESENT"),
        )
        self._resolve_authorization()
        return self.is_home

    def get_status(self) -> Dict:
        """Return human-readable status payload for UI."""
        now = time.time()
        last_seen_ago = int(now - self.last_seen_timestamp) if self.last_seen_timestamp else None
        grace = self.settings.phone_grace_period_seconds
        remaining_grace = max(0, int(grace - last_seen_ago)) if (last_seen_ago is not None and last_seen_ago <= grace) else 0
        prox_data = self.proximity_engine.get_telemetry().to_dict()

        return {
            "configured": bool(self.settings.trusted_phone_ip or self.settings.trusted_phone_mac),
            "device_name": self.settings.trusted_phone_name,
            "ip": self.settings.trusted_phone_ip,
            "mac": self.settings.trusted_phone_mac,
            "phone_state": self.phone_state,
            "network_state": self.network_state,
            "proximity": prox_data,
            "online": (self.phone_state == "PHONE_PRESENT"),
            "is_home": self.is_home,
            "is_trusted_in_room": self.is_trusted_in_room,
            "status_label": self.status_label,
            "last_seen_seconds_ago": last_seen_ago,
            "last_seen_method": self.last_seen_method,
            "grace_period_seconds": grace,
            "remaining_grace_seconds": remaining_grace,
        }

    async def discover_devices(self) -> List[Dict]:
        """
        Scan the local subnet ARP cache and return potential devices.
        Allows 1-click selection of the user's phone in Wiiew settings.
        """
        arp = await asyncio.to_thread(get_arp_cache)
        devices = []
        for ip, mac in sorted(arp.items(), key=lambda x: [int(p) for p in x[0].split('.') if p.isdigit()]):
            if ip.endswith(".1"):
                label = "Wi-Fi Router / Gateway"
            elif "48:ca:43" in mac:
                label = "ESP32 CSI Sensor Node"
            else:
                label = f"Device ({ip})"

            is_current = (ip == self.settings.trusted_phone_ip or mac == self.settings.trusted_phone_mac)
            devices.append({
                "ip": ip,
                "mac": mac,
                "hostname": label,
                "is_current_trusted": is_current,
            })
        return devices
