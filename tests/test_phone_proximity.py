"""
Automated Tests for Wiiew Phone Proximity & 15 ft Router Boundary Logic.
Verifies zero-fabrication guarantees, network-presence vs physical proximity separation,
hysteresis filtering, calibration sampling, and CSI alert suppression interactions.
"""

import asyncio
import time
import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wiiew.backend.config import WiiewSettings
from wiiew.backend.decision_engine import DecisionEngine
from wiiew.backend.proximity import (
    BaseProximityProvider,
    ProximityEngine,
    ProximityState,
    RouterRSSIProvider,
    UnavailableProximityProvider,
)
from wiiew.backend.trusted_device import TrustedDeviceDetector


class MockSignalProvider(BaseProximityProvider):
    """Test provider allowing simulated client RSSI values."""

    def __init__(self, initial_rssi: float = -55.0) -> None:
        self.current_rssi: float = initial_rssi

    async def get_client_signal(self, mac: str, ip: str):
        return self.current_rssi

    def get_method_name(self) -> str:
        return "router_rssi"


class TestPhoneProximityLogic(unittest.TestCase):
    """Unit and integration tests for phone proximity boundary."""

    def setUp(self):
        self.settings = WiiewSettings(
            trusted_phone_ip="192.168.1.101",
            trusted_phone_mac="de:f3:d1:75:ab:bf",
            trusted_phone_name="Test Phone",
            phone_proximity_enabled=True,
            phone_boundary_ft=15.0,
            proximity_grace_seconds=3,
            calibrated_near_rssi=-45.0,
            calibrated_boundary_rssi=-65.0,
            presence_sustained_seconds=15.0,
            presence_clear_seconds=10.0,
        )
        self.detector = TrustedDeviceDetector(self.settings)
        self.engine = DecisionEngine(self.settings, self.detector)
        self.engine.sensor_online = True
        self.engine.csi_last_seen = time.time()

    # -----------------------------------------------------------------------
    # 1. Zero Fabrication Policy
    # -----------------------------------------------------------------------
    def test_zero_fabrication_when_unavailable(self):
        """When router RSSI is unavailable, distance and signal MUST be strictly null."""
        engine = ProximityEngine(self.settings, UnavailableProximityProvider())
        telemetry = asyncio.run(engine.update("de:f3:d1:75:ab:bf", "192.168.1.101", network_present=True))

        self.assertIsNone(telemetry.distance_estimate_ft, "Distance must be null when unavailable")
        self.assertIsNone(telemetry.signal_strength, "Signal must be null when unavailable")
        self.assertEqual(telemetry.method, "unavailable")
        self.assertEqual(telemetry.state, ProximityState.UNKNOWN)
        self.assertIn("requires router client RSSI", telemetry.message)

    # -----------------------------------------------------------------------
    # 2. Proximity Unknown -> Do NOT Falsely Claim Home
    # -----------------------------------------------------------------------
    def test_network_present_and_proximity_unknown_is_not_trusted(self):
        """If phone is on Wi-Fi (ARP/ping) but proximity is UNKNOWN, do not falsely claim in-room."""
        self.detector.network_state = "NETWORK_PRESENT"
        self.detector.is_home = True
        # Proximity is UNKNOWN by default with UnavailableProximityProvider
        self.detector._resolve_authorization()

        self.assertFalse(self.detector.is_trusted_in_room, "Proximity UNKNOWN must not authorize alert suppression")

        # Now test CSI presence under this condition: MUST trigger PRESENCE_UNTRUSTED alert
        now = time.time()
        self.engine.csi_last_seen = now
        self.engine.update_csi_sample(
            presence=True,
            motion_level="active",
            rssi=-40.0,
            amplitude=25.0,
        )
        self.engine.presence_start_time = now - 16.0  # > 15s sustained

        res = self.engine.evaluate()
        self.assertEqual(res["current_state"], "PRESENCE_UNTRUSTED", "CSI presence with unknown proximity must alert")
        self.assertTrue(self.engine.is_in_untrusted_event)

    # -----------------------------------------------------------------------
    # 3. Network Present + Proximity Near -> Alert Suppressed
    # -----------------------------------------------------------------------
    def test_network_present_and_proximity_near_suppresses_alert(self):
        """If phone is on Wi-Fi AND within 15 ft boundary (NEAR), alert is suppressed."""
        mock_provider = MockSignalProvider(initial_rssi=-50.0)  # -50 dBm > -65 dBm boundary -> NEAR
        self.detector.proximity_engine.set_provider(mock_provider)

        asyncio.run(self.detector.proximity_engine.update("de:f3:d1:75:ab:bf", "192.168.1.101", network_present=True))
        self.detector.network_state = "NETWORK_PRESENT"
        self.detector.is_home = True
        self.detector._resolve_authorization()

        self.assertEqual(self.detector.proximity_engine.current_state, ProximityState.NEAR)
        self.assertTrue(self.detector.is_trusted_in_room)

        # Feed sustained CSI presence
        now = time.time()
        self.engine.csi_last_seen = now
        self.engine.update_csi_sample(
            presence=True,
            motion_level="active",
            rssi=-40.0,
            amplitude=25.0,
        )
        self.engine.presence_start_time = now - 16.0

        res = self.engine.evaluate()
        self.assertEqual(res["current_state"], "PRESENCE_TRUSTED")
        self.assertFalse(self.engine.is_in_untrusted_event)

    # -----------------------------------------------------------------------
    # 4. Network Present + Proximity Far -> Alert Triggered
    # -----------------------------------------------------------------------
    def test_network_present_and_proximity_far_triggers_alert(self):
        """If phone is on Wi-Fi but beyond 15 ft boundary (FAR), user is away from room -> alert triggers."""
        mock_provider = MockSignalProvider(initial_rssi=-78.0)  # -78 dBm < -65 dBm boundary -> FAR
        self.detector.proximity_engine.set_provider(mock_provider)

        asyncio.run(self.detector.proximity_engine.update("de:f3:d1:75:ab:bf", "192.168.1.101", network_present=True))
        self.detector.network_state = "NETWORK_PRESENT"
        self.detector.is_home = True
        self.detector._resolve_authorization()

        self.assertEqual(self.detector.proximity_engine.current_state, ProximityState.FAR)
        self.assertFalse(self.detector.is_trusted_in_room, "Proximity FAR must not authorize alert suppression")

        # Feed sustained CSI presence
        now = time.time()
        self.engine.csi_last_seen = now
        self.engine.update_csi_sample(
            presence=True,
            motion_level="active",
            rssi=-40.0,
            amplitude=25.0,
        )
        self.engine.presence_start_time = now - 16.0

        res = self.engine.evaluate()
        self.assertEqual(res["current_state"], "PRESENCE_UNTRUSTED", "User far from room must trigger alert")
        self.assertTrue(self.engine.is_in_untrusted_event)

    # -----------------------------------------------------------------------
    # 5. Network Absent -> Away -> Alert Triggered
    # -----------------------------------------------------------------------
    def test_network_absent_triggers_alert(self):
        """If phone is completely absent from Wi-Fi, user is away -> alert triggers."""
        self.detector.network_state = "NETWORK_AWAY"
        self.detector.is_home = False
        self.detector._resolve_authorization()

        self.assertFalse(self.detector.is_trusted_in_room)

        now = time.time()
        self.engine.csi_last_seen = now
        self.engine.update_csi_sample(
            presence=True,
            motion_level="active",
            rssi=-40.0,
            amplitude=25.0,
        )
        self.engine.presence_start_time = now - 16.0

        res = self.engine.evaluate()
        self.assertEqual(res["current_state"], "PRESENCE_UNTRUSTED")
        self.assertTrue(self.engine.is_in_untrusted_event)

    # -----------------------------------------------------------------------
    # 6. Hysteresis Filtering (N Seconds)
    # -----------------------------------------------------------------------
    def test_hysteresis_filtering(self):
        """Brief momentary signal dips below boundary do NOT immediately flip state to FAR."""
        mock_provider = MockSignalProvider(initial_rssi=-50.0)
        engine = ProximityEngine(self.settings, mock_provider)

        # Initial probe: established as NEAR
        t1 = asyncio.run(engine.update("de:f3:d1:75:ab:bf", "192.168.1.101", network_present=True))
        self.assertEqual(t1.state, ProximityState.NEAR)

        # Signal drops below boundary (-75 dBm < -65 dBm) for 1 second (< 3s grace)
        mock_provider.current_rssi = -75.0
        t2 = asyncio.run(engine.update("de:f3:d1:75:ab:bf", "192.168.1.101", network_present=True))
        self.assertEqual(t2.state, ProximityState.NEAR, "State should remain NEAR during hysteresis window")

        # Signal recovers to -50 dBm before grace period expires
        mock_provider.current_rssi = -50.0
        t3 = asyncio.run(engine.update("de:f3:d1:75:ab:bf", "192.168.1.101", network_present=True))
        self.assertEqual(t3.state, ProximityState.NEAR, "Transient fluctuation safely filtered out by hysteresis")

        # Now simulate sustained drop beyond 3.5s
        mock_provider.current_rssi = -75.0
        asyncio.run(engine.update("de:f3:d1:75:ab:bf", "192.168.1.101", network_present=True))
        time.sleep(3.2)  # Wait past grace_s (3.0s)
        t4 = asyncio.run(engine.update("de:f3:d1:75:ab:bf", "192.168.1.101", network_present=True))
        self.assertEqual(t4.state, ProximityState.FAR, "Sustained signal drop past grace period switches to FAR")

    # -----------------------------------------------------------------------
    # 7. Calibration Sampling
    # -----------------------------------------------------------------------
    def test_calibration_sampling(self):
        """Sampling near and boundary signal stores median thresholds in settings."""
        mock_provider = MockSignalProvider(initial_rssi=-48.0)
        engine = ProximityEngine(self.settings, mock_provider)

        # Sample near
        near_val = asyncio.run(engine.sample_near_calibration(samples=3, delay_s=0.01))
        self.assertEqual(near_val, -48.0)
        self.assertEqual(self.settings.calibrated_near_rssi, -48.0)

        # Sample boundary
        mock_provider.current_rssi = -68.5
        boundary_val = asyncio.run(engine.sample_boundary_calibration(samples=3, delay_s=0.01))
        self.assertEqual(boundary_val, -68.5)
        self.assertEqual(self.settings.calibrated_boundary_rssi, -68.5)

    # -----------------------------------------------------------------------
    # 8. Fallback When Proximity Checking is Disabled
    # -----------------------------------------------------------------------
    def test_proximity_disabled_fallback(self):
        """When phone_proximity_enabled is False, network presence alone authorizes alert suppression."""
        self.settings.phone_proximity_enabled = False
        self.detector.network_state = "NETWORK_PRESENT"
        self.detector.is_home = True
        self.detector.proximity_engine.current_state = ProximityState.UNKNOWN
        self.detector._resolve_authorization()

        self.assertTrue(self.detector.is_trusted_in_room, "Disabled proximity must fall back to network presence")


if __name__ == "__main__":
    unittest.main(verbosity=2)
