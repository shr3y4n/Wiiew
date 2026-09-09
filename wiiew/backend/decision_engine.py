"""
Decision Engine for Wiiew.
Evaluates CSI presence, applies hysteresis/debounce, checks trusted phone status,
and determines canonical room occupancy states with calm, single-entry alerts.
"""

from __future__ import annotations

import collections
import json
import time
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional

from .config import DATA_DIR, WiiewSettings
from .trusted_device import TrustedDeviceDetector

EVENTS_FILE = DATA_DIR / "events.json"


class DecisionEngine:
    """
    Canonical state machine for Wiiew room occupancy monitoring.
    States:
      - DISARMED
      - EMPTY
      - CHECKING_PRESENCE
      - PRESENCE_TRUSTED
      - PRESENCE_UNTRUSTED
      - SENSOR_OFFLINE
    """

    def __init__(
        self,
        settings: WiiewSettings,
        phone_detector: TrustedDeviceDetector,
        on_alert_callback: Optional[Callable[[Dict], None]] = None,
    ) -> None:
        self.settings = settings
        self.phone_detector = phone_detector
        self.on_alert = on_alert_callback

        # Raw sensor inputs
        self.raw_presence: bool = False
        self.motion_level: str = "empty"
        self.rssi_dbm: float = -80.0
        self.mean_amplitude: float = 0.0
        self.variance: float = 0.0
        self.motion_band_power: float = 0.0
        self.csi_last_seen: float = 0.0
        self.sensor_online: bool = False

        # Movement & Direction tracking (Phase 2)
        self.movement_state: str = "NONE"  # NONE, STATIONARY, MOVEMENT_DETECTED
        self.movement_direction: str = "UNKNOWN"  # Strictly unresolvable from single node
        self.movement_intensity: float = 0.0  # 0.0 - 1.0

        # Debounce / Hysteresis timers
        self.presence_start_time: Optional[float] = None
        self.empty_start_time: Optional[float] = None
        self.sustained_presence: bool = False

        # State tracking
        self.current_state: str = "EMPTY"
        self.state_message: str = "Room is empty and quiet."
        self.last_activity_time: Optional[float] = None

        # Entry Event tracking (Requirement: One entry = one notification)
        self.is_in_untrusted_event: bool = False
        self.last_alert_time: float = 0.0

        # Event History Log (last 100 entries)
        self.events: Deque[Dict] = collections.deque(maxlen=100)
        self._load_events()

    def update_csi_sample(
        self,
        presence: bool,
        motion_level: str,
        rssi: float,
        amplitude: float,
        variance: float = 0.0,
        motion_band_power: float = 0.0,
    ) -> None:
        """Feed a new sensing frame from RuView."""
        now = time.time()
        self.raw_presence = presence
        self.motion_level = motion_level
        self.rssi_dbm = rssi
        self.mean_amplitude = amplitude
        self.variance = variance
        self.motion_band_power = motion_band_power
        self.csi_last_seen = now
        self.sensor_online = True

        if presence:
            self.last_activity_time = now
            if self.presence_start_time is None:
                self.presence_start_time = now
            self.empty_start_time = None
        else:
            if self.empty_start_time is None:
                self.empty_start_time = now

    def evaluate(self) -> Dict:
        """
        Evaluate current state, update hysteresis, and dispatch single-entry alerts.
        Called once per second by the background loop.
        """
        now = time.time()

        # 1. Sensor Health
        if now - self.csi_last_seen > 5.0:
            self.sensor_online = False
            self.raw_presence = False

        # 2. Hysteresis logic
        if self.raw_presence and self.sensor_online:
            if self.presence_start_time is None:
                self.presence_start_time = now
            self.empty_start_time = None

            presence_duration = now - self.presence_start_time
            if presence_duration >= self.settings.presence_sustained_seconds:
                self.sustained_presence = True
        else:
            if self.empty_start_time is None:
                self.empty_start_time = now

            empty_duration = now - self.empty_start_time
            if empty_duration >= self.settings.presence_clear_seconds:
                self.sustained_presence = False
                self.presence_start_time = None

        prev_state = self.current_state
        phone_in_room = getattr(self.phone_detector, "is_trusted_in_room", self.phone_detector.is_home)
        is_armed = self.settings.is_armed

        # 3. Canonical State Resolution
        if not self.sensor_online:
            self.current_state = "SENSOR_OFFLINE"
            self.state_message = "CSI sensor is offline."
        elif not is_armed:
            self.current_state = "DISARMED"
            self.state_message = "System is disarmed. Monitoring paused."
        elif not self.sustained_presence:
            if self.raw_presence:
                self.current_state = "CHECKING_PRESENCE"
                dur = round(now - self.presence_start_time, 1) if self.presence_start_time else 0.0
                thresh = self.settings.presence_sustained_seconds
                self.state_message = f"Checking presence... ({dur}s / {thresh}s)"
            else:
                self.current_state = "EMPTY"
                self.state_message = "Room is empty and quiet."
        elif phone_in_room:
            self.current_state = "PRESENCE_TRUSTED"
            self.state_message = "Trusted device present — alert suppressed."
        else:
            self.current_state = "PRESENCE_UNTRUSTED"
            self.state_message = "Someone has entered your room."

        # 4. Movement & Direction Evaluation (Phase 2)
        if not self.sensor_online or not self.raw_presence:
            self.movement_state = "NONE"
            self.movement_intensity = 0.0
        elif self.motion_level == "active" or self.motion_band_power > 0.35 or self.variance > 4.0:
            self.movement_state = "MOVEMENT_DETECTED"
            norm_var = min(1.0, max(0.0, self.variance / 50.0))
            norm_pwr = min(1.0, max(0.0, self.motion_band_power / 2.0))
            self.movement_intensity = round(min(1.0, 0.4 + 0.6 * max(norm_var, norm_pwr)), 2)
        else:
            self.movement_state = "STATIONARY"
            self.movement_intensity = round(min(0.35, max(0.05, self.variance / 20.0)), 2)

        self.movement_direction = "UNKNOWN"

        # 5. Single-Entry Event Alert Dispatch & Logging
        if self.current_state == "PRESENCE_UNTRUSTED":
            if not self.is_in_untrusted_event:
                self.is_in_untrusted_event = True
                self.last_alert_time = now
                event_data = self.log_event(
                    "ENTRY_DETECTED",
                    "Someone entered your room.",
                )
                if self.on_alert:
                    try:
                        self.on_alert(event_data)
                    except Exception as e:
                        print(f"[DecisionEngine] Alert callback error: {e}")
        else:
            # When leaving PRESENCE_UNTRUSTED state
            if self.is_in_untrusted_event:
                if self.current_state in ("EMPTY", "DISARMED"):
                    self.log_event("ROOM_CLEARED", "Room became empty.")
                    self.is_in_untrusted_event = False
                elif self.current_state == "PRESENCE_TRUSTED":
                    self.log_event(
                        "ALERT_SUPPRESSED",
                        "Presence detected — trusted phone present. Alert suppressed.",
                    )
                    self.is_in_untrusted_event = False

        # State transition log for arm/disarm
        if prev_state != self.current_state:
            if self.current_state == "DISARMED" and prev_state != "DISARMED":
                self.is_in_untrusted_event = False

        return self.get_status()

    def log_event(self, event_type: str, message: str) -> Dict:
        """Record a human-readable event into the history log."""
        now = time.time()
        time_str = time.strftime("%H:%M")
        item = {
            "timestamp": now,
            "time_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
            "time_short": time_str,
            "type": event_type,
            "message": message,
            "state": self.current_state,
            "phone_home": self.phone_detector.is_home,
            "phone_status": self.phone_detector.status_label,
            "armed": self.settings.is_armed,
            "rssi_dbm": round(self.rssi_dbm, 1),
            "mean_amplitude": round(self.mean_amplitude, 1),
        }
        self.events.appendleft(item)
        self._save_events()
        return item

    def get_status(self) -> Dict:
        """Compile complete decision engine status payload for UI."""
        now = time.time()
        sustained_duration = (
            round(now - self.presence_start_time, 1)
            if (self.presence_start_time and self.raw_presence)
            else 0.0
        )
        last_activity_ago = (
            int(now - self.last_activity_time)
            if self.last_activity_time
            else None
        )

        return {
            "current_state": self.current_state,
            "state_message": self.state_message,
            "sustained_presence": self.sustained_presence,
            "raw_presence": self.raw_presence,
            "presence_duration_seconds": sustained_duration,
            "presence_threshold_seconds": self.settings.presence_sustained_seconds,
            "is_armed": self.settings.is_armed,
            "sensor_online": self.sensor_online,
            "last_activity_seconds_ago": last_activity_ago,
            "motion_level": self.motion_level,
            "movement_state": self.movement_state,
            "movement_direction": self.movement_direction,
            "movement_intensity": self.movement_intensity,
            "variance": round(self.variance, 2),
            "motion_band_power": round(self.motion_band_power, 2),
            "rssi_dbm": self.rssi_dbm,
            "mean_amplitude": self.mean_amplitude,
            "is_in_untrusted_event": self.is_in_untrusted_event,
        }

    def _save_events(self) -> None:
        try:
            with open(EVENTS_FILE, "w", encoding="utf-8") as f:
                json.dump(list(self.events), f, indent=2)
        except Exception:
            pass

    def _load_events(self) -> None:
        if EVENTS_FILE.exists():
            try:
                with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                    items = json.load(f)
                    self.events = collections.deque(items[:100], maxlen=100)
            except Exception:
                pass
