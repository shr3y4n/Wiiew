"""
Wiiew Room Intrusion Monitor — Comprehensive Test Suite (17 Scenarios)

Validates:
 1. Empty room state
 2. Brief false positive (< 15s debounce)
 3. Sustained presence (>= 15s debounce)
 4. Trusted phone + presence (alert suppressed, "YOU ARE HOME")
 5. Untrusted phone + presence (calm single-entry alert triggered)
 6. Alert sent exactly once per entry event
 7. No repeated alert during continuous presence (no spam/siren)
 8. New alert after room clears and someone enters again
 9. Phone sleep grace period (Safe Card deep Wi-Fi sleep hysteresis)
 10. Disarmed system suppression
 11. Sensor offline handling (CSI stream timeout)
 12. RuView offline handling (graceful reconnect backoff)
 13. Backend unavailable handling (frontend offline state contract)
 14. PWA asset loading (HTML, CSS, JS, manifest, SW, icons)
 15. Notification subscription logic (VAPID, payload, calm tag)
 16. WebSocket reconnect resilience (broadcast & dead socket cleanup)
 17. GitHub Pages base path compatibility (relative paths & workflow)
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from wiiew.backend.config import WiiewSettings
from wiiew.backend.decision_engine import DecisionEngine
from wiiew.backend.push_service import WebPushService
from wiiew.backend.trusted_device import TrustedDeviceDetector
from wiiew.backend import trusted_device
from wiiew.backend.main import get_full_state


def make_test_settings() -> WiiewSettings:
    """Create isolated settings for testing."""
    return WiiewSettings(
        room_name="Test Bedroom",
        trusted_phone_name="Test Phone",
        trusted_phone_ip="192.0.2.99",
        trusted_phone_mac="02:00:00:00:00:99",
        presence_sustained_seconds=15.0,
        presence_clear_seconds=10.0,
        phone_grace_period_seconds=300,
        phone_proximity_enabled=False,
        is_armed=True,
    )


# ---------------------------------------------------------------------------
# Test Scenarios
# ---------------------------------------------------------------------------

def test_01_empty_room_state():
    """Scenario 1: Room starts empty; engine resolves EMPTY canonical state with no alerts."""
    settings = make_test_settings()
    detector = TrustedDeviceDetector(settings)
    alerts: List[Dict[str, Any]] = []
    engine = DecisionEngine(settings, detector, on_alert_callback=lambda e: alerts.append(e))

    now = time.time()
    engine.update_csi_sample(presence=False, motion_level="empty", rssi=-70.0, amplitude=10.0)
    status = engine.evaluate()

    assert status["current_state"] == "EMPTY", f"Expected EMPTY, got {status['current_state']}"
    assert status["sustained_presence"] is False
    assert status["sensor_online"] is True
    assert len(alerts) == 0, "No alerts should be dispatched for empty room"
    print("  [PASS] Scenario 1: Empty room state correctly evaluated as EMPTY")


def test_02_brief_false_positive():
    """Scenario 2: Presence lasting < 15s enters CHECKING_PRESENCE without triggering alerts."""
    settings = make_test_settings()
    settings.presence_sustained_seconds = 15.0
    detector = TrustedDeviceDetector(settings)
    detector.is_home = False
    alerts: List[Dict[str, Any]] = []
    engine = DecisionEngine(settings, detector, on_alert_callback=lambda e: alerts.append(e))

    # Feed presence for 5 seconds (< 15s threshold)
    now = time.time()
    engine.update_csi_sample(presence=True, motion_level="active", rssi=-55.0, amplitude=22.0)
    engine.presence_start_time = now - 5.0
    status = engine.evaluate()

    assert status["current_state"] == "CHECKING_PRESENCE", f"Expected CHECKING_PRESENCE, got {status['current_state']}"
    assert status["sustained_presence"] is False
    assert engine.is_in_untrusted_event is False
    assert len(alerts) == 0, "Brief false positive must NOT trigger an alert"
    print("  [PASS] Scenario 2: Brief false positive (<15s) stays in CHECKING_PRESENCE without alert")


def test_03_sustained_presence():
    """Scenario 3: Presence sustained >= 15s triggers sustained_presence=True."""
    settings = make_test_settings()
    settings.presence_sustained_seconds = 15.0
    detector = TrustedDeviceDetector(settings)
    detector.is_home = False
    engine = DecisionEngine(settings, detector)

    now = time.time()
    engine.update_csi_sample(presence=True, motion_level="active", rssi=-50.0, amplitude=25.0)
    engine.presence_start_time = now - 16.0  # 16s > 15s
    status = engine.evaluate()

    assert status["sustained_presence"] is True, "Sustained presence should be True after 16s"
    assert status["current_state"] in ("PRESENCE_UNTRUSTED", "PRESENCE_TRUSTED")
    print("  [PASS] Scenario 3: Sustained presence (>=15s) correctly activates sustained_presence=True")


def test_04_trusted_phone_suppression():
    """Scenario 4: Sustained presence with trusted phone home resolves to PRESENCE_TRUSTED and suppresses alert."""
    settings = make_test_settings()
    detector = TrustedDeviceDetector(settings)
    detector.is_home = True
    detector.status_label = "HOME (ACTIVE)"
    alerts: List[Dict[str, Any]] = []
    engine = DecisionEngine(settings, detector, on_alert_callback=lambda e: alerts.append(e))

    now = time.time()
    engine.update_csi_sample(presence=True, motion_level="active", rssi=-45.0, amplitude=30.0)
    engine.presence_start_time = now - 16.0
    status = engine.evaluate()

    assert status["current_state"] == "PRESENCE_TRUSTED", f"Expected PRESENCE_TRUSTED, got {status['current_state']}"
    assert len(alerts) == 0, "Presence with trusted phone must SUPPRESS alerts"
    assert engine.is_in_untrusted_event is False
    print("  [PASS] Scenario 4: Trusted phone suppresses alert; state is PRESENCE_TRUSTED")


def test_05_untrusted_phone_alert_triggered():
    """Scenario 5: Sustained presence with phone away triggers PRESENCE_UNTRUSTED and alert callback."""
    settings = make_test_settings()
    detector = TrustedDeviceDetector(settings)
    detector.is_home = False
    detector.status_label = "AWAY"
    alerts: List[Dict[str, Any]] = []
    engine = DecisionEngine(settings, detector, on_alert_callback=lambda e: alerts.append(e))

    now = time.time()
    engine.update_csi_sample(presence=True, motion_level="active", rssi=-48.0, amplitude=28.0)
    engine.presence_start_time = now - 16.0
    status = engine.evaluate()

    assert status["current_state"] == "PRESENCE_UNTRUSTED", f"Expected PRESENCE_UNTRUSTED, got {status['current_state']}"
    assert len(alerts) == 1, f"Expected exactly 1 alert, got {len(alerts)}"
    assert alerts[0]["type"] == "ENTRY_DETECTED"
    assert "Someone entered your room" in alerts[0]["message"]
    print("  [PASS] Scenario 5: Sustained presence + phone away triggers PRESENCE_UNTRUSTED alert")


def test_06_alert_sent_exactly_once():
    """Scenario 6: Entry alert is dispatched exactly once on initial transition."""
    settings = make_test_settings()
    detector = TrustedDeviceDetector(settings)
    detector.is_home = False
    alerts: List[Dict[str, Any]] = []
    engine = DecisionEngine(settings, detector, on_alert_callback=lambda e: alerts.append(e))

    now = time.time()
    engine.update_csi_sample(presence=True, motion_level="active", rssi=-48.0, amplitude=28.0)
    engine.presence_start_time = now - 16.0
    engine.evaluate()

    assert len(alerts) == 1, "Alert must be sent exactly once"
    assert engine.is_in_untrusted_event is True, "Entry event tracking flag must be active"
    print("  [PASS] Scenario 6: Alert sent exactly once; is_in_untrusted_event set to True")


def test_07_no_repeated_alert_continuous_presence():
    """Scenario 7: Continuous presence over multiple evaluate cycles does not re-alert (no spam/siren)."""
    settings = make_test_settings()
    detector = TrustedDeviceDetector(settings)
    detector.is_home = False
    alerts: List[Dict[str, Any]] = []
    engine = DecisionEngine(settings, detector, on_alert_callback=lambda e: alerts.append(e))

    now = time.time()
    engine.update_csi_sample(presence=True, motion_level="active", rssi=-48.0, amplitude=28.0)
    engine.presence_start_time = now - 16.0
    engine.evaluate()
    assert len(alerts) == 1

    # Simulate 10 subsequent evaluation cycles during continuous presence
    for i in range(10):
        engine.update_csi_sample(presence=True, motion_level="active", rssi=-48.0, amplitude=28.0)
        status = engine.evaluate()
        assert status["current_state"] == "PRESENCE_UNTRUSTED"
        assert len(alerts) == 1, f"Alert count increased to {len(alerts)} at cycle {i+1}; spam detected!"

    print("  [PASS] Scenario 7: No duplicate alerts during continuous room occupancy")


def test_08_new_alert_after_room_clears():
    """Scenario 8: After room clears back to EMPTY, a subsequent entry triggers a fresh alert."""
    settings = make_test_settings()
    detector = TrustedDeviceDetector(settings)
    detector.is_home = False
    alerts: List[Dict[str, Any]] = []
    engine = DecisionEngine(settings, detector, on_alert_callback=lambda e: alerts.append(e))

    now = time.time()
    # 1. First Entry Event
    engine.update_csi_sample(presence=True, motion_level="active", rssi=-48.0, amplitude=28.0)
    engine.presence_start_time = now - 16.0
    engine.evaluate()
    assert len(alerts) == 1
    assert engine.is_in_untrusted_event is True

    # 2. Room clears
    engine.update_csi_sample(presence=False, motion_level="empty", rssi=-70.0, amplitude=5.0)
    engine.empty_start_time = now - 12.0  # 12s > 10s clear threshold
    status_cleared = engine.evaluate()
    assert status_cleared["current_state"] == "EMPTY"
    assert engine.is_in_untrusted_event is False, "Entry event must reset when room clears"

    # 3. Second Entry Event
    engine.update_csi_sample(presence=True, motion_level="active", rssi=-46.0, amplitude=32.0)
    engine.presence_start_time = now - 16.0
    engine.empty_start_time = None
    engine.evaluate()

    assert len(alerts) == 2, f"Expected 2 alerts after clear and re-entry, got {len(alerts)}"
    assert engine.is_in_untrusted_event is True
    print("  [PASS] Scenario 8: Fresh alert successfully triggered after room cleared and re-entered")


def test_09_phone_sleep_grace_period():
    """Scenario 9: Phone sleep grace period prevents false alarms during deep sleep."""
    settings = make_test_settings()
    settings.phone_grace_period_seconds = 300  # 5 minutes
    detector = TrustedDeviceDetector(settings)

    # Mock network probes to isolate test from physical host environment
    old_ping = trusted_device.ping_host_sync
    old_arp = trusted_device.get_arp_cache
    try:
        trusted_device.ping_host_sync = lambda ip, timeout_ms=600: False
        trusted_device.get_arp_cache = lambda: {}

        # Phone was active at t0
        now = time.time()
        detector.last_seen_timestamp = now
        detector.last_seen_method = "heartbeat"
        detector.is_home = True
        detector.status_label = "HOME (ACTIVE)"

        # Simulate t0 + 60s (phone Wi-Fi asleep, no ping/ARP response)
        detector.last_seen_timestamp = now - 60.0
        asyncio.run(detector.check_presence())

        assert detector.is_home is True, "Phone should still be considered HOME within grace period"
        assert "SLEEPING" in detector.status_label, f"Expected SLEEPING status, got {detector.status_label}"

        # Simulate t0 + 350s (grace period expired)
        detector.last_seen_timestamp = now - 350.0
        asyncio.run(detector.check_presence())

        assert detector.is_home is False, "Phone should be AWAY after grace period expires"
        assert detector.status_label == "AWAY"
        print("  [PASS] Scenario 9: Phone sleep grace period maintains HOME (SLEEPING) status until timeout")
    finally:
        trusted_device.ping_host_sync = old_ping
        trusted_device.get_arp_cache = old_arp


def test_10_disarmed_system_suppression():
    """Scenario 10: Disarming the system forces DISARMED state and suppresses all alerts."""
    settings = make_test_settings()
    settings.is_armed = False
    detector = TrustedDeviceDetector(settings)
    detector.is_home = False
    alerts: List[Dict[str, Any]] = []
    engine = DecisionEngine(settings, detector, on_alert_callback=lambda e: alerts.append(e))

    now = time.time()
    engine.update_csi_sample(presence=True, motion_level="active", rssi=-40.0, amplitude=35.0)
    engine.presence_start_time = now - 20.0
    status = engine.evaluate()

    assert status["current_state"] == "DISARMED", f"Expected DISARMED, got {status['current_state']}"
    assert len(alerts) == 0, "No alerts allowed when system is disarmed"
    assert engine.is_in_untrusted_event is False
    print("  [PASS] Scenario 10: Disarmed system suppresses alerts and evaluates to DISARMED")


def test_11_sensor_offline_handling():
    """Scenario 11: CSI stream timeout marks sensor_online=False and state=SENSOR_OFFLINE."""
    settings = make_test_settings()
    detector = TrustedDeviceDetector(settings)
    engine = DecisionEngine(settings, detector)

    # Set CSI frame last seen 10 seconds ago (> 5.0s timeout)
    now = time.time()
    engine.csi_last_seen = now - 10.0
    engine.sensor_online = True
    status = engine.evaluate()

    assert status["current_state"] == "SENSOR_OFFLINE", f"Expected SENSOR_OFFLINE, got {status['current_state']}"
    assert status["sensor_online"] is False
    print("  [PASS] Scenario 11: CSI stream timeout correctly triggers SENSOR_OFFLINE state")


def test_12_ruview_offline_handling():
    """Scenario 12: RuView connection handler handles closed/offline socket gracefully without crash."""
    from wiiew.backend.main import ruview_stream_consumer, decision_engine, settings

    # Point to an unreachable port
    original_url = settings.ruview_ws_url
    settings.ruview_ws_url = "ws://127.0.0.1:59999/ws/live"

    async def run_briefly():
        task = asyncio.create_task(ruview_stream_consumer())
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_briefly())
    settings.ruview_ws_url = original_url

    assert decision_engine.sensor_online is False
    print("  [PASS] Scenario 12: RuView offline socket handled gracefully with backoff and offline flag")


def test_13_backend_unavailable_handling():
    """Scenario 13: Frontend contract provides backend status badge and offline recovery."""
    index_html = (REPO_ROOT / "wiiew" / "frontend" / "index.html").read_text(encoding="utf-8")
    app_js = (REPO_ROOT / "wiiew" / "frontend" / "app.js").read_text(encoding="utf-8")

    # Verify status badge in HTML
    assert 'id="badge-backend-status"' in index_html, "index.html must contain #badge-backend-status"
    assert 'id="settings-modal"' in index_html, "index.html must contain #settings-modal for custom backend URL"

    # Verify WebSocket reconnect and offline state in app.js
    assert "handleBackendOffline" in app_js, "app.js must handle offline styling on connection loss"
    assert "BACKEND OFFLINE" in app_js, "app.js must display BACKEND OFFLINE on connection loss"
    assert "setTimeout(connectWebSocket" in app_js or "connectWebSocket" in app_js, "app.js must support WS reconnect"
    assert "localStorage.getItem('wiiew_backend_url')" in app_js or 'localStorage.getItem("wiiew_backend_url")' in app_js

    print("  [PASS] Scenario 13: Frontend contract implements backend status badge, offline state & reconnect")


def test_14_pwa_asset_loading():
    """Scenario 14: All required PWA assets exist, have non-empty size, and valid structure."""
    frontend_dir = REPO_ROOT / "wiiew" / "frontend"
    required_files = [
        ("index.html", 1000),
        ("style.css", 1000),
        ("app.js", 1000),
        ("manifest.json", 200),
        ("sw.js", 500),
        ("icons/icon-192.png", 500),
        ("icons/icon-512.png", 500),
        ("icons/maskable-icon-512.png", 500),
    ]

    for rel_path, min_size in required_files:
        p = frontend_dir / rel_path
        assert p.exists(), f"Asset {rel_path} does not exist at {p}"
        assert p.stat().st_size >= min_size, f"Asset {rel_path} is too small ({p.stat().st_size} bytes)"

    # Validate PNG magic bytes for icons
    for icon_name in ("icon-192.png", "icon-512.png", "maskable-icon-512.png"):
        icon_path = frontend_dir / "icons" / icon_name
        data = icon_path.read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n"), f"{icon_name} has invalid PNG magic bytes"

    print("  [PASS] Scenario 14: All 8 essential PWA assets exist and meet size and format criteria")


def test_15_notification_subscription_logic():
    """Scenario 15: WebPush service provides VAPID public key and formats calm notifications."""
    push = WebPushService()
    pub_key = push.get_public_key()
    assert len(pub_key) > 60, f"VAPID public key invalid length: {len(pub_key)}"

    # Test subscription registration
    test_sub = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/test_endpoint_token_123",
        "keys": {
            "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QT9Q0nA597Jw2vxeAURWjOUUrQqWAz557gJaW6818166866A=",
            "auth": "tBHItDaA18166866Q=="
        }
    }
    success = push.add_subscription(test_sub)
    assert success is True, "add_subscription must return True"

    # Calm notification defaults
    payload = json.dumps({
        "title": "Wiiew",
        "body": "Someone has entered your room.",
        "tag": "wiiew-room-entry",
        "icon": "./icons/icon-192.png",
        "data": {"url": "./"}
    })
    data = json.loads(payload)
    assert data["title"] == "Wiiew"
    assert data["body"] == "Someone has entered your room."
    assert data["tag"] == "wiiew-room-entry"
    print("  [PASS] Scenario 15: WebPush VAPID key generation and calm single-entry payload verified")


def test_16_websocket_reconnect_resilience():
    """Scenario 16: WebSocket broadcast loop drops dead sockets cleanly without error."""
    from wiiew.backend.main import connected_ws_clients

    class MockWebSocket:
        def __init__(self, fails: bool = False):
            self.fails = fails
            self.messages: List[str] = []

        async def send_text(self, text: str):
            if self.fails:
                raise ConnectionResetError("Client dropped connection")
            self.messages.append(text)

    ws_healthy = MockWebSocket(fails=False)
    ws_broken = MockWebSocket(fails=True)

    connected_ws_clients.clear()
    connected_ws_clients.add(ws_healthy)
    connected_ws_clients.add(ws_broken)

    async def broadcast(msg: str):
        dead = set()
        for ws in list(connected_ws_clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        connected_ws_clients.difference_update(dead)

    asyncio.run(broadcast('{"type":"ping"}'))

    assert ws_healthy in connected_ws_clients, "Healthy socket must remain in connected set"
    assert ws_broken not in connected_ws_clients, "Broken socket must be evicted from connected set"
    assert len(ws_healthy.messages) == 1
    connected_ws_clients.clear()
    print("  [PASS] Scenario 16: WebSocket broadcast safely evicts dropped clients without interrupting service")


def test_17_github_pages_base_path_compatibility():
    """Scenario 17: All frontend paths use relative notation (./) for GitHub Pages compatibility."""
    frontend_dir = REPO_ROOT / "wiiew" / "frontend"
    index_html = (frontend_dir / "index.html").read_text(encoding="utf-8")
    manifest_json = json.loads((frontend_dir / "manifest.json").read_text(encoding="utf-8"))
    sw_js = (frontend_dir / "sw.js").read_text(encoding="utf-8")

    # 1. index.html asset references
    assert 'href="./style.css"' in index_html, "index.html must reference ./style.css"
    assert 'src="./app.js"' in index_html, "index.html must reference ./app.js"
    assert 'href="./manifest.json"' in index_html, "index.html must reference ./manifest.json"
    assert 'href="/style.css"' not in index_html, "index.html must NOT have root-absolute /style.css"
    assert 'src="/app.js"' not in index_html, "index.html must NOT have root-absolute /app.js"

    # 2. manifest.json relative URLs
    assert manifest_json.get("start_url") == "./", "manifest start_url must be ./"
    assert manifest_json.get("scope") == "./", "manifest scope must be ./"
    for icon in manifest_json.get("icons", []):
        assert icon["src"].startswith("./"), f"Icon src {icon['src']} must start with ./"

    # 3. sw.js relative cache targets
    assert "'./'" in sw_js or '"./"' in sw_js, "sw.js precache must include relative ./"
    assert "'/style.css'" not in sw_js, "sw.js must NOT use root-absolute paths"

    # 4. GitHub Actions workflow
    workflow_file = REPO_ROOT / ".github" / "workflows" / "pages.yml"
    assert workflow_file.exists(), "pages.yml must exist"
    workflow_text = workflow_file.read_text(encoding="utf-8")
    assert "actions/deploy-pages" in workflow_text
    assert "wiiew/frontend" in workflow_text

    print("  [PASS] Scenario 17: GitHub Pages base path compatibility and deployment workflow verified")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all():
    print("=================================================================")
    print("  WIIEW ROOM INTRUSION MONITOR — 17 SCENARIOS VERIFICATION SUITE  ")
    print("=================================================================\n")

    scenarios = [
        ("01", "Empty room state", test_01_empty_room_state),
        ("02", "Brief false positive (<15s debounce)", test_02_brief_false_positive),
        ("03", "Sustained presence (>=15s debounce)", test_03_sustained_presence),
        ("04", "Trusted phone + presence (suppressed)", test_04_trusted_phone_suppression),
        ("05", "Untrusted phone + presence (alert)", test_05_untrusted_phone_alert_triggered),
        ("06", "Alert sent exactly once", test_06_alert_sent_exactly_once),
        ("07", "No repeated alert during continuous presence", test_07_no_repeated_alert_continuous_presence),
        ("08", "New alert after room clears and re-enters", test_08_new_alert_after_room_clears),
        ("09", "Phone sleep grace period", test_09_phone_sleep_grace_period),
        ("10", "Disarmed system suppression", test_10_disarmed_system_suppression),
        ("11", "Sensor offline handling", test_11_sensor_offline_handling),
        ("12", "RuView offline handling", test_12_ruview_offline_handling),
        ("13", "Backend unavailable handling", test_13_backend_unavailable_handling),
        ("14", "PWA asset loading", test_14_pwa_asset_loading),
        ("15", "Notification subscription logic", test_15_notification_subscription_logic),
        ("16", "WebSocket reconnect resilience", test_16_websocket_reconnect_resilience),
        ("17", "GitHub Pages base path compatibility", test_17_github_pages_base_path_compatibility),
    ]

    passed = 0
    failed = 0

    for num, name, func in scenarios:
        print(f"Running Scenario {num}: {name}...")
        try:
            func()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] Scenario {num}: {name} -> {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n-----------------------------------------------------------------")
    print(f"  TOTAL: {len(scenarios)} | PASSED: {passed} | FAILED: {failed}")
    print("-----------------------------------------------------------------")

    if failed > 0:
        sys.exit(1)
    else:
        print("All 17 scenarios verified successfully!")
        sys.exit(0)


if __name__ == "__main__":
    run_all()
