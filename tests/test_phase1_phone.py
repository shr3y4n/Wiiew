"""
Test Suite for Wiiew Phase 1: Automatic Trusted-Phone Presence Detection.

Validates:
1. 4-State Machine: UNKNOWN, PHONE_PRESENT, PHONE_MAYBE_AWAY, PHONE_AWAY
2. Safe-Card Suppression: PHONE_PRESENT and PHONE_MAYBE_AWAY suppress presence alert
3. Alert Firing: Transition to PHONE_AWAY allows PRESENCE_UNTRUSTED alert
4. Heartbeat: PWA heartbeat or manual "I'm Home" immediately marks PHONE_PRESENT
5. Serialized status schema: phone_state, online, is_home, status_label, remaining_grace_seconds
"""

import asyncio
import time
from pathlib import Path
import sys

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wiiew.backend.config import WiiewSettings
from wiiew.backend.trusted_device import TrustedDeviceDetector
from wiiew.backend import trusted_device
from wiiew.backend.decision_engine import DecisionEngine

# Fast deterministic ping mock for tests
trusted_device.ping_host_sync = lambda ip, timeout_ms=600: False


def make_test_settings(grace_period: int = 120) -> WiiewSettings:
    return WiiewSettings(
        room_name="Phase 1 Test Room",
        trusted_phone_name="Shreyan's Phone",
        trusted_phone_ip="192.0.2.99",
        trusted_phone_mac="02:00:00:00:00:99",
        presence_sustained_seconds=15.0,
        presence_clear_seconds=10.0,
        phone_grace_period_seconds=grace_period,
        is_armed=True,
    )


def test_01_unknown_state_when_unconfigured():
    """When neither IP nor MAC is configured, state is UNKNOWN, not home."""
    settings = WiiewSettings(
        trusted_phone_ip="",
        trusted_phone_mac="",
        phone_grace_period_seconds=180,
    )
    detector = TrustedDeviceDetector(settings)
    res = asyncio.run(detector.check_presence())

    assert res is False
    assert detector.phone_state == "UNKNOWN"
    assert detector.is_home is False
    status = detector.get_status()
    assert status["configured"] is False
    assert status["phone_state"] == "UNKNOWN"
    assert status["online"] is False
    assert status["is_home"] is False
    print("  [PASS] Test 1: UNKNOWN state when unconfigured")


def test_02_heartbeat_sets_phone_present():
    """Calling heartbeat() sets PHONE_PRESENT and is_home=True."""
    settings = make_test_settings()
    detector = TrustedDeviceDetector(settings)

    detector.heartbeat(source="PWA foreground")
    assert detector.phone_state == "PHONE_PRESENT"
    assert detector.is_home is True
    assert detector.status_label == "HOME (ACTIVE)"

    status = detector.get_status()
    assert status["phone_state"] == "PHONE_PRESENT"
    assert status["online"] is True
    assert status["is_home"] is True
    assert status["last_seen_seconds_ago"] is not None
    assert status["last_seen_seconds_ago"] <= 1
    print("  [PASS] Test 2: Heartbeat sets PHONE_PRESENT and is_home=True")


def test_03_sleep_grace_transitions():
    """
    Simulate elapsed time:
    - <= 15s -> PHONE_PRESENT (ACTIVE)
    - 16s .. grace_period -> PHONE_MAYBE_AWAY (SLEEPING), is_home=True
    - > grace_period -> PHONE_AWAY (AWAY), is_home=False
    """
    settings = make_test_settings(grace_period=60)
    detector = TrustedDeviceDetector(settings)

    # Mock ping and ARP to return false (host sleeping/unreachable)
    original_last_seen = time.time() - 5
    detector.last_seen_timestamp = original_last_seen

    # Within 15s
    asyncio.run(detector.check_presence())
    assert detector.phone_state == "PHONE_PRESENT"
    assert detector.is_home is True

    # 30s elapsed (within 60s grace)
    detector.last_seen_timestamp = time.time() - 30
    asyncio.run(detector.check_presence())
    assert detector.phone_state == "PHONE_MAYBE_AWAY"
    assert detector.is_home is True
    assert detector.status_label == "HOME (SLEEPING)"
    status = detector.get_status()
    assert status["phone_state"] == "PHONE_MAYBE_AWAY"
    assert status["online"] is False
    assert status["is_home"] is True
    assert status["remaining_grace_seconds"] > 0

    # 65s elapsed (exceeded 60s grace)
    detector.last_seen_timestamp = time.time() - 65
    asyncio.run(detector.check_presence())
    assert detector.phone_state == "PHONE_AWAY"
    assert detector.is_home is False
    assert detector.status_label == "AWAY"
    status_away = detector.get_status()
    assert status_away["phone_state"] == "PHONE_AWAY"
    assert status_away["online"] is False
    assert status_away["is_home"] is False
    assert status_away["remaining_grace_seconds"] == 0
    print("  [PASS] Test 3: Sleep grace transitions (PRESENT -> MAYBE_AWAY -> AWAY)")


def test_04_alert_suppression_logic():
    """
    Sustained presence with:
    1. PHONE_PRESENT -> PRESENCE_TRUSTED (suppressed)
    2. PHONE_MAYBE_AWAY -> PRESENCE_TRUSTED (suppressed)
    3. PHONE_AWAY -> PRESENCE_UNTRUSTED (alert fired)
    """
    settings = make_test_settings(grace_period=100)
    detector = TrustedDeviceDetector(settings)
    alerts = []
    engine = DecisionEngine(settings, detector, on_alert_callback=lambda a: alerts.append(a))

    # Scenario A: Phone is actively present
    detector.heartbeat(source="test")
    # Feed sustained presence
    engine.update_csi_sample(presence=True, motion_level="active", rssi=-60.0, amplitude=45.0)
    engine.presence_start_time = time.time() - 16.0
    engine.evaluate()
    assert engine.current_state == "PRESENCE_TRUSTED"
    assert len(alerts) == 0, "Alert must be suppressed when phone is present"

    # Scenario B: Phone goes to sleep (MAYBE_AWAY)
    detector.last_seen_timestamp = time.time() - 45  # 45s ago, grace is 100s
    asyncio.run(detector.check_presence())
    assert detector.phone_state == "PHONE_MAYBE_AWAY"
    engine.update_csi_sample(presence=True, motion_level="active", rssi=-60.0, amplitude=45.0)
    engine.presence_start_time = time.time() - 20.0
    engine.evaluate()
    assert engine.current_state == "PRESENCE_TRUSTED"
    assert len(alerts) == 0, "Alert must still be suppressed when phone is sleeping within grace"

    # Scenario C: Phone exceeds grace period (AWAY)
    detector.last_seen_timestamp = time.time() - 120  # 120s ago, grace is 100s
    asyncio.run(detector.check_presence())
    assert detector.phone_state == "PHONE_AWAY"
    engine.update_csi_sample(presence=True, motion_level="active", rssi=-60.0, amplitude=45.0)
    engine.presence_start_time = time.time() - 25.0
    engine.evaluate()
    assert engine.current_state == "PRESENCE_UNTRUSTED"
    assert len(alerts) == 1, "Alert must be dispatched when phone is AWAY"
    print("  [PASS] Test 4: Alert suppression logic across phone states")


def test_05_device_discovery():
    """discover_devices returns list of dicts with ip, mac, hostname, is_current_trusted."""
    settings = make_test_settings()
    detector = TrustedDeviceDetector(settings)
    devices = asyncio.run(detector.discover_devices())

    assert isinstance(devices, list)
    for dev in devices:
        assert "ip" in dev
        assert "mac" in dev
        assert "hostname" in dev
        assert "is_current_trusted" in dev
    print("  [PASS] Test 5: Device discovery structure and safety")


if __name__ == "__main__":
    print("=========================================================")
    print("  RUNNING PHASE 1 AUTOMATIC PHONE PRESENCE TESTS")
    print("=========================================================")
    test_01_unknown_state_when_unconfigured()
    test_02_heartbeat_sets_phone_present()
    test_03_sleep_grace_transitions()
    test_04_alert_suppression_logic()
    test_05_device_discovery()
    print("=========================================================")
    print("  ALL PHASE 1 TESTS PASSED!")
    print("=========================================================")
