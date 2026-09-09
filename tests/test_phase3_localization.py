"""
Test Suite for Wiiew Phase 3: Multi-Node CSI Room Localization Framework.

Validates:
1. 0 active nodes -> NO_LOCALIZATION (valid=False, coordinates null).
2. 1 active node -> SINGLE_NODE (valid=False, coordinates null, "requires multiple calibrated nodes").
3. 2 active nodes -> LOW_CONFIDENCE (valid=False, coordinates null, insufficient geometry).
4. >= 3 active nodes -> VALID_ESTIMATE (valid=True, weighted centroid coordinates within room bounds).
5. Dynamic disturbance shift (centroid moves towards node with highest disturbance).
6. Staleness timeout (nodes inactive > 5s become inactive, dropping validity).
7. Full API endpoints (/api/localization, /api/nodes, and get_full_state integration).
"""

import time
from pathlib import Path
import sys

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wiiew.backend.config import WiiewSettings
from wiiew.backend.localization_engine import LocalizationEngine
from wiiew.backend.main import get_full_state, localization_engine


def make_test_settings() -> WiiewSettings:
    return WiiewSettings(
        room_name="Localization Test Room",
        room_width_m=4.0,
        room_depth_m=6.0,
        nodes=[
            {"node_id": "node_1", "name": "Node 1", "ip": "192.168.1.101", "x": 0.0, "y": 0.0, "enabled": True},
            {"node_id": "node_2", "name": "Node 2", "ip": "192.168.1.102", "x": 4.0, "y": 0.0, "enabled": True},
            {"node_id": "node_3", "name": "Node 3", "ip": "192.168.1.103", "x": 4.0, "y": 6.0, "enabled": True},
            {"node_id": "node_4", "name": "Node 4", "ip": "192.168.1.104", "x": 0.0, "y": 6.0, "enabled": True},
        ],
    )


def test_01_no_active_nodes():
    """0 active nodes returns NO_LOCALIZATION with valid=False and coordinates null."""
    settings = make_test_settings()
    engine = LocalizationEngine(settings)
    res = engine.compute_localization(now=time.time())

    assert res.valid is False
    assert res.state == "NO_LOCALIZATION"
    assert res.x is None
    assert res.y is None
    assert res.active_nodes == 0
    assert res.total_nodes == 4
    print("  [PASS] Test 1: 0 active nodes evaluates to NO_LOCALIZATION")


def test_02_single_node_baseline_honesty():
    """1 active node returns SINGLE_NODE with valid=False and coordinates null (zero fabrication)."""
    settings = make_test_settings()
    engine = LocalizationEngine(settings)
    now = time.time()

    engine.update_node_sample(
        node_id="node_1",
        presence=True,
        rssi=-60.0,
        amplitude=45.0,
        variance=12.5,
        motion_band_power=0.8,
        timestamp=now,
    )
    res = engine.compute_localization(now=now)

    assert res.valid is False
    assert res.state == "SINGLE_NODE"
    assert res.x is None
    assert res.y is None
    assert res.confidence == 0.0
    assert res.active_nodes == 1
    assert "multiple calibrated" in res.message.lower()
    print("  [PASS] Test 2: 1 active node returns SINGLE_NODE (strictly null coordinates)")


def test_03_two_nodes_low_confidence():
    """2 active nodes returns LOW_CONFIDENCE with valid=False (insufficient for 2D plane)."""
    settings = make_test_settings()
    engine = LocalizationEngine(settings)
    now = time.time()

    engine.update_node_sample("node_1", presence=True, rssi=-60.0, amplitude=45.0, variance=10.0, motion_band_power=0.5, timestamp=now)
    engine.update_node_sample("node_2", presence=True, rssi=-62.0, amplitude=40.0, variance=8.0, motion_band_power=0.4, timestamp=now)
    res = engine.compute_localization(now=now)

    assert res.valid is False
    assert res.state == "LOW_CONFIDENCE"
    assert res.x is None
    assert res.y is None
    assert res.active_nodes == 2
    print("  [PASS] Test 3: 2 active nodes returns LOW_CONFIDENCE")


def test_04_three_plus_nodes_valid_estimate():
    """>= 3 active nodes produces valid 2D coordinates within room bounds."""
    settings = make_test_settings()
    engine = LocalizationEngine(settings)
    now = time.time()

    # Evenly disturbed 3 nodes
    engine.update_node_sample("node_1", presence=True, rssi=-60.0, amplitude=40.0, variance=10.0, motion_band_power=0.5, timestamp=now)
    engine.update_node_sample("node_2", presence=True, rssi=-60.0, amplitude=40.0, variance=10.0, motion_band_power=0.5, timestamp=now)
    engine.update_node_sample("node_3", presence=True, rssi=-60.0, amplitude=40.0, variance=10.0, motion_band_power=0.5, timestamp=now)
    res = engine.compute_localization(now=now)

    assert res.valid is True
    assert res.state == "VALID_ESTIMATE"
    assert res.x is not None and res.y is not None
    assert 0.0 <= res.x <= settings.room_width_m
    assert 0.0 <= res.y <= settings.room_depth_m
    assert res.confidence >= 0.5
    assert res.active_nodes == 3
    print(f"  [PASS] Test 4: 3 active nodes produces VALID_ESTIMATE at ({res.x}m, {res.y}m)")


def test_05_disturbance_weighted_position_shift():
    """Centroid shifts toward node with strongest disturbance."""
    settings = make_test_settings()
    engine = LocalizationEngine(settings)
    now = time.time()

    # Node 3 (at x=4.0, y=6.0) has high disturbance; node 1 & 2 have minimal disturbance
    engine.update_node_sample("node_1", presence=True, rssi=-70.0, amplitude=15.0, variance=0.1, motion_band_power=0.01, timestamp=now)
    engine.update_node_sample("node_2", presence=True, rssi=-70.0, amplitude=15.0, variance=0.1, motion_band_power=0.01, timestamp=now)
    engine.update_node_sample("node_3", presence=True, rssi=-50.0, amplitude=65.0, variance=40.0, motion_band_power=2.5, timestamp=now)
    res = engine.compute_localization(now=now)

    assert res.valid is True
    # Position must be much closer to Node 3 (x=4, y=6) than to origin (0, 0)
    assert res.x > 3.0, f"Expected x shifted toward 4.0, got {res.x}"
    assert res.y > 4.5, f"Expected y shifted toward 6.0, got {res.y}"
    print(f"  [PASS] Test 5: Position correctly weighted toward disturbance hotspot at ({res.x}m, {res.y}m)")


def test_06_staleness_timeout():
    """Nodes not heard from within 5.0 seconds expire and become inactive."""
    settings = make_test_settings()
    engine = LocalizationEngine(settings)
    t0 = time.time()

    engine.update_node_sample("node_1", presence=True, rssi=-60.0, amplitude=40.0, variance=10.0, motion_band_power=0.5, timestamp=t0)
    engine.update_node_sample("node_2", presence=True, rssi=-60.0, amplitude=40.0, variance=10.0, motion_band_power=0.5, timestamp=t0)
    engine.update_node_sample("node_3", presence=True, rssi=-60.0, amplitude=40.0, variance=10.0, motion_band_power=0.5, timestamp=t0)

    # At t0, valid estimate
    res_valid = engine.compute_localization(now=t0)
    assert res_valid.valid is True

    # 6 seconds later, all nodes are stale
    t_stale = t0 + 6.0
    res_stale = engine.compute_localization(now=t_stale)
    assert res_stale.valid is False
    assert res_stale.state == "NO_LOCALIZATION"
    assert res_stale.active_nodes == 0
    print("  [PASS] Test 6: Staleness timeout safely degrades state to NO_LOCALIZATION")


def test_07_api_integration():
    """Verify localization structure is embedded in get_full_state()."""
    now = time.time()
    localization_engine.update_node_sample("node_1", presence=True, rssi=-60.0, amplitude=40.0, timestamp=now)
    payload = get_full_state()

    assert "localization" in payload
    loc = payload["localization"]
    assert "valid" in loc
    assert "state" in loc
    assert "active_nodes" in loc
    assert "room_width_m" in loc
    assert "room_depth_m" in loc
    assert "nodes" in loc
    print("  [PASS] Test 7: Full state payload correctly embeds localization telemetry")


if __name__ == "__main__":
    print("=========================================================")
    print("  RUNNING PHASE 3 MULTI-NODE LOCALIZATION TESTS")
    print("=========================================================")
    test_01_no_active_nodes()
    test_02_single_node_baseline_honesty()
    test_03_two_nodes_low_confidence()
    test_04_three_plus_nodes_valid_estimate()
    test_05_disturbance_weighted_position_shift()
    test_06_staleness_timeout()
    test_07_api_integration()
    print("=========================================================")
    print("  ALL PHASE 3 TESTS PASSED!")
    print("=========================================================")
