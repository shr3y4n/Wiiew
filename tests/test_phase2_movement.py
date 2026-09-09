"""
Test Suite for Wiiew Phase 2: CSI Movement & Direction Visualization.

Validates:
1. Empty room evaluates movement_state to NONE (intensity 0.0).
2. Stationary presence (breathing/micro-motion) evaluates to STATIONARY (low intensity).
3. Active macroscopic movement evaluates to MOVEMENT_DETECTED (scaled intensity).
4. Direction is strictly reported as UNKNOWN (zero coordinate fabrication policy).
5. get_status() and get_full_state() include all movement metrics.
"""

import time
from pathlib import Path
import sys

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wiiew.backend.config import WiiewSettings
from wiiew.backend.trusted_device import TrustedDeviceDetector
from wiiew.backend.decision_engine import DecisionEngine
from wiiew.backend.main import get_full_state, decision_engine, phone_detector


def make_test_settings() -> WiiewSettings:
    return WiiewSettings(
        room_name="Phase 2 Test Room",
        trusted_phone_name="Test Phone",
        trusted_phone_ip="192.0.2.99",
        trusted_phone_mac="02:00:00:00:00:99",
        presence_sustained_seconds=15.0,
        presence_clear_seconds=10.0,
        phone_grace_period_seconds=180,
        is_armed=True,
    )


def test_01_empty_room_movement_none():
    """Empty room must produce movement_state=NONE, intensity=0.0, direction=UNKNOWN."""
    settings = make_test_settings()
    detector = TrustedDeviceDetector(settings)
    engine = DecisionEngine(settings, detector)

    engine.update_csi_sample(
        presence=False,
        motion_level="empty",
        rssi=-75.0,
        amplitude=12.0,
        variance=0.2,
        motion_band_power=0.01,
    )
    status = engine.evaluate()

    assert status["movement_state"] == "NONE", f"Expected NONE, got {status['movement_state']}"
    assert status["movement_intensity"] == 0.0
    assert status["movement_direction"] == "UNKNOWN"
    print("  [PASS] Test 1: Empty room produces movement_state=NONE and intensity=0.0")


def test_02_stationary_presence():
    """Presence with low motion level evaluates to STATIONARY."""
    settings = make_test_settings()
    detector = TrustedDeviceDetector(settings)
    engine = DecisionEngine(settings, detector)

    engine.update_csi_sample(
        presence=True,
        motion_level="stationary",
        rssi=-62.0,
        amplitude=35.0,
        variance=1.8,
        motion_band_power=0.12,
    )
    status = engine.evaluate()

    assert status["movement_state"] == "STATIONARY", f"Expected STATIONARY, got {status['movement_state']}"
    assert 0.05 <= status["movement_intensity"] <= 0.4, f"Intensity {status['movement_intensity']} out of stationary range"
    assert status["movement_direction"] == "UNKNOWN"
    print("  [PASS] Test 2: Stationary presence produces movement_state=STATIONARY")


def test_03_active_movement():
    """Presence with high motion level or motion_band_power evaluates to MOVEMENT_DETECTED."""
    settings = make_test_settings()
    detector = TrustedDeviceDetector(settings)
    engine = DecisionEngine(settings, detector)

    # Active motion via motion_level
    engine.update_csi_sample(
        presence=True,
        motion_level="active",
        rssi=-58.0,
        amplitude=48.0,
        variance=15.4,
        motion_band_power=0.85,
    )
    status = engine.evaluate()

    assert status["movement_state"] == "MOVEMENT_DETECTED"
    assert 0.4 <= status["movement_intensity"] <= 1.0, f"Intensity {status['movement_intensity']} out of active range"
    assert status["movement_direction"] == "UNKNOWN"

    # Active motion triggered via motion_band_power and variance even if motion_level is not 'active'
    engine.update_csi_sample(
        presence=True,
        motion_level="unspecified",
        rssi=-58.0,
        amplitude=48.0,
        variance=12.0,
        motion_band_power=0.6,
    )
    status2 = engine.evaluate()
    assert status2["movement_state"] == "MOVEMENT_DETECTED"
    print("  [PASS] Test 3: Active movement produces movement_state=MOVEMENT_DETECTED")


def test_04_zero_fabrication_direction_policy():
    """Verify single-node CSI direction is always reported honestly as UNKNOWN."""
    settings = make_test_settings()
    detector = TrustedDeviceDetector(settings)
    engine = DecisionEngine(settings, detector)

    for motion in ["empty", "stationary", "active", "running", "walking"]:
        engine.update_csi_sample(
            presence=(motion != "empty"),
            motion_level=motion,
            rssi=-60.0,
            amplitude=40.0,
            variance=25.0,
            motion_band_power=1.2,
        )
        status = engine.evaluate()
        assert status["movement_direction"] == "UNKNOWN", (
            f"Direction must be UNKNOWN for single node, got {status['movement_direction']}"
        )
    print("  [PASS] Test 4: Zero fabrication policy holds (direction strictly UNKNOWN)")


def test_05_api_payload_integration():
    """Verify get_full_state() includes movement_state, movement_direction, movement_intensity."""
    # Feed sample into the global singleton
    decision_engine.update_csi_sample(
        presence=True,
        motion_level="active",
        rssi=-55.0,
        amplitude=42.0,
        variance=18.2,
        motion_band_power=0.9,
    )
    decision_engine.evaluate()
    payload = get_full_state()

    assert "movement_state" in payload["room"]
    assert "movement_direction" in payload["room"]
    assert "movement_intensity" in payload["room"]
    assert payload["room"]["movement_state"] == "MOVEMENT_DETECTED"
    assert payload["room"]["movement_direction"] == "UNKNOWN"
    assert payload["room"]["movement_intensity"] >= 0.4
    assert "variance" in payload["sensor"]
    assert "motion_band_power" in payload["sensor"]
    print("  [PASS] Test 5: Full state API payload includes movement telemetry")


if __name__ == "__main__":
    print("=========================================================")
    print("  RUNNING PHASE 2 CSI MOVEMENT & DIRECTION TESTS")
    print("=========================================================")
    test_01_empty_room_movement_none()
    test_02_stationary_presence()
    test_03_active_movement()
    test_04_zero_fabrication_direction_policy()
    test_05_api_payload_integration()
    print("=========================================================")
    print("  ALL PHASE 2 TESTS PASSED!")
    print("=========================================================")
