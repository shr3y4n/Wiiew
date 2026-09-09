"""
Wiiew Phone Proximity Abstraction & Calibration Engine.
Decouples Wi-Fi LAN network presence from physical room proximity (15 ft boundary).
Implements calibration sampling, hysteresis filtering, and zero-fabrication guarantees.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import WiiewSettings, save_settings


class ProximityState:
    UNKNOWN = "UNKNOWN"
    NEAR = "NEAR"
    FAR = "FAR"


@dataclass
class ProximityTelemetry:
    state: str = ProximityState.UNKNOWN
    distance_estimate_ft: Optional[float] = None
    signal_strength: Optional[float] = None
    method: str = "unavailable"
    boundary_ft: float = 15.0
    calibrated: bool = False
    message: str = "15 ft proximity detection requires router client RSSI."

    def to_dict(self) -> Dict:
        return {
            "state": self.state,
            "distance_estimate_ft": self.distance_estimate_ft,
            "signal_strength": self.signal_strength,
            "method": self.method,
            "boundary_ft": self.boundary_ft,
            "calibrated": self.calibrated,
            "message": self.message,
        }


class BaseProximityProvider(ABC):
    """Abstract interface for querying physical Wi-Fi link signal strength."""

    @abstractmethod
    async def get_client_signal(self, mac: str, ip: str) -> Optional[float]:
        """Return real client RSSI in dBm, or None if unavailable."""
        pass

    @abstractmethod
    def get_method_name(self) -> str:
        """Return provider identification string."""
        pass


class UnavailableProximityProvider(BaseProximityProvider):
    """Honest fallback when router does not expose client RSSI API."""

    async def get_client_signal(self, mac: str, ip: str) -> Optional[float]:
        return None

    def get_method_name(self) -> str:
        return "unavailable"


class RouterRSSIProvider(BaseProximityProvider):
    """
    Extensible provider for querying router client signal tables.
    Supports injected signal provider or simulated/configured hooks.
    """

    def __init__(self, query_fn=None) -> None:
        self._query_fn = query_fn

    async def get_client_signal(self, mac: str, ip: str) -> Optional[float]:
        if self._query_fn:
            try:
                res = self._query_fn(mac, ip)
                if asyncio.iscoroutine(res):
                    return await res
                return res
            except Exception:
                return None
        return None

    def get_method_name(self) -> str:
        return "router_rssi"


class ProximityEngine:
    """
    Evaluates physical proximity relative to the Wi-Fi router (15 ft boundary).
    Features:
    - Zero fabrication: null distance/signal if router RSSI unavailable.
    - Hysteresis: requires signal to stay across boundary for N seconds before switching states.
    - Calibration: samples near and 15-ft boundary signals.
    """

    def __init__(
        self,
        settings: WiiewSettings,
        provider: Optional[BaseProximityProvider] = None,
    ) -> None:
        self.settings = settings
        self.provider = provider or UnavailableProximityProvider()

        # Telemetry state
        self.current_state: str = ProximityState.UNKNOWN
        self.last_signal_dbm: Optional[float] = None
        self.last_distance_ft: Optional[float] = None

        # Hysteresis state tracking
        self._pending_state: Optional[str] = None
        self._pending_state_start: Optional[float] = None
        self._lock = asyncio.Lock()

    def set_provider(self, provider: BaseProximityProvider) -> None:
        """Update active proximity provider."""
        self.provider = provider
        self.settings.proximity_method = provider.get_method_name()

    async def update(self, mac: str, ip: str, network_present: bool) -> ProximityTelemetry:
        """
        Probe proximity provider and evaluate hysteresis boundary.
        """
        now = time.time()
        boundary_ft = float(self.settings.phone_boundary_ft)

        # If phone is not even on the local network, proximity is FAR or UNKNOWN
        if not network_present:
            self.current_state = ProximityState.FAR
            self.last_signal_dbm = None
            self.last_distance_ft = None
            self._pending_state = None
            return self.get_telemetry()

        # Query provider
        signal = await self.provider.get_client_signal(mac, ip)
        self.last_signal_dbm = signal

        if signal is None:
            # RSSI unavailable from router
            self.current_state = ProximityState.UNKNOWN
            self.last_distance_ft = None
            self._pending_state = None
            return self.get_telemetry()

        # We have actual RSSI from the router
        boundary_rssi = self.settings.calibrated_boundary_rssi
        near_rssi = self.settings.calibrated_near_rssi

        if boundary_rssi is None:
            # Signal is available but uncalibrated
            self.current_state = ProximityState.UNKNOWN
            self.last_distance_ft = None
            return self.get_telemetry()

        # Estimate distance from calibrated model if near baseline exists
        if near_rssi is not None and near_rssi > boundary_rssi:
            # Estimate using log-distance path loss between 1m and 15ft
            # RSSI(d) = RSSI_near - 10 * n * log10(d / d_near)
            # When d = 15 ft, RSSI = boundary_rssi
            # delta = near_rssi - boundary_rssi
            # n_factor = delta / log10(15 / 3)
            try:
                if signal >= near_rssi:
                    self.last_distance_ft = max(1.0, round(3.0 * ((near_rssi - signal) / 10.0 + 1.0), 1))
                else:
                    ratio = (near_rssi - signal) / max(1.0, (near_rssi - boundary_rssi))
                    self.last_distance_ft = max(1.0, round(15.0 * (ratio ** 1.5), 1))
            except Exception:
                self.last_distance_ft = 15.0 if signal <= boundary_rssi else 5.0
        else:
            self.last_distance_ft = 15.0 if signal <= boundary_rssi else 5.0

        # Raw candidate state based on boundary RSSI
        # Stronger signal (>= boundary) means closer to router (NEAR)
        # Weaker signal (< boundary) means farther than 15 ft (FAR)
        candidate = ProximityState.NEAR if signal >= boundary_rssi else ProximityState.FAR

        # Apply hysteresis
        grace_s = max(1, int(self.settings.proximity_grace_seconds))
        if self.current_state == ProximityState.UNKNOWN:
            self.current_state = candidate
            self._pending_state = None
        elif candidate != self.current_state:
            if self._pending_state != candidate:
                self._pending_state = candidate
                self._pending_state_start = now
            elif self._pending_state_start and (now - self._pending_state_start >= grace_s):
                self.current_state = candidate
                self._pending_state = None
                self._pending_state_start = None
        else:
            self._pending_state = None
            self._pending_state_start = None

        return self.get_telemetry()

    def get_telemetry(self) -> ProximityTelemetry:
        """Return current proximity telemetry object."""
        method = self.provider.get_method_name()
        boundary_ft = float(self.settings.phone_boundary_ft)
        calibrated = bool(self.settings.calibrated_boundary_rssi is not None)

        if method == "unavailable" or self.last_signal_dbm is None:
            msg = "15 ft proximity detection requires router client RSSI."
            return ProximityTelemetry(
                state=self.current_state if self.current_state == ProximityState.FAR else ProximityState.UNKNOWN,
                distance_estimate_ft=None,
                signal_strength=None,
                method="unavailable",
                boundary_ft=boundary_ft,
                calibrated=calibrated,
                message=msg,
            )

        if not calibrated:
            msg = "Router signal detected. Calibration required to establish 15 ft boundary."
            return ProximityTelemetry(
                state=ProximityState.UNKNOWN,
                distance_estimate_ft=None,
                signal_strength=self.last_signal_dbm,
                method=method,
                boundary_ft=boundary_ft,
                calibrated=False,
                message=msg,
            )

        msg = (
            f"Phone within {boundary_ft:g} ft boundary."
            if self.current_state == ProximityState.NEAR
            else f"Phone outside {boundary_ft:g} ft boundary."
        )

        return ProximityTelemetry(
            state=self.current_state,
            distance_estimate_ft=self.last_distance_ft,
            signal_strength=self.last_signal_dbm,
            method=method,
            boundary_ft=boundary_ft,
            calibrated=True,
            message=msg,
        )

    # -----------------------------------------------------------------------
    # Calibration Sampling
    # -----------------------------------------------------------------------

    async def sample_near_calibration(
        self,
        samples: int = 5,
        delay_s: float = 0.5,
        mac: str = "",
        ip: str = "",
    ) -> Optional[float]:
        """
        Sample client RSSI at the router or very near position.
        Stores median RSSI in settings.calibrated_near_rssi.
        """
        readings: List[float] = []
        for _ in range(samples):
            sig = await self.provider.get_client_signal(mac, ip)
            if sig is not None:
                readings.append(sig)
            await asyncio.sleep(delay_s)

        if not readings:
            return None

        median_rssi = round(float(statistics.median(readings)), 1)
        self.settings.calibrated_near_rssi = median_rssi
        save_settings(self.settings)
        return median_rssi

    async def sample_boundary_calibration(
        self,
        samples: int = 5,
        delay_s: float = 0.5,
        mac: str = "",
        ip: str = "",
    ) -> Optional[float]:
        """
        Sample client RSSI at the 15-ft physical boundary.
        Stores median RSSI in settings.calibrated_boundary_rssi.
        """
        readings: List[float] = []
        for _ in range(samples):
            sig = await self.provider.get_client_signal(mac, ip)
            if sig is not None:
                readings.append(sig)
            await asyncio.sleep(delay_s)

        if not readings:
            return None

        median_rssi = round(float(statistics.median(readings)), 1)
        self.settings.calibrated_boundary_rssi = median_rssi
        save_settings(self.settings)
        return median_rssi
