"""
Decision Engine for Wiiew.
Evaluates CSI presence, applies hysteresis/debounce, checks trusted phone status,
and determines whether to trigger an intrusion alert or suppress it.
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
    State machine for room occupancy and intrusion alerts.
    Implements debounce/hysteresis and safe-card suppression.
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
        self.csi_last_seen: float = 0.0
        self.sensor_online: bool = False

        # Debounce / Hysteresis timers
        self.presence_start_time: Optional[float] = None
        self.empty_start_time: Optional[float] = None
        self.sustained_presence: bool = False

        # Alert state
        self.last_alert_time: float = 0.0
        self.last_suppression_log_time: float = 0.0
        self.current_state: str = "ROOM_EMPTY"
        self.state_message: str = "Room is empty and quiet."
        self.last_activity_time: Optional[float] = None

        # Event History Log (last 100 entries)
        self.events: Deque[Dict] = collections.deque(maxlen=100)
        self._load_events()

    def update_csi_sample(
        self,
        presence: bool,
        motion_level: str,
        rssi: float,
        amplitude: float,
    ) -> None:
        """Feed a new sensing frame from RuView."""
        now = time.time()
        self.raw_presence = presence
        self.motion_level = motion_level
        self.rssi_dbm = rssi
        self.mean_amplitude = amplitude
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
        Evaluate current state, update hysteresis, and dispatch alerts if needed.
        Called once per second by the background loop.
        """
        now = time.time()

        # Check if sensor stream went stale (> 5 seconds without CSI frame)
        if now - self.csi_last_seen > 5.0:
            self.sensor_online = False
            self.raw_presence = False

        # 1. Hysteresis logic (Requirement 5)
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

        # 2. Decision Logic
        phone_home = self.phone_detector.is_home
        is_armed = self.settings.is_armed

        prev_state = self.current_state

        if not self.sustained_presence:
            self.current_state = "ROOM_EMPTY"
            self.state_message = "Room is empty and secure."
        elif not is_armed:
            self.current_state = "DISARMED_PRESENCE"
            self.state_message = "Presence detected (System Disarmed)."
        elif phone_home:
            # Safe Card: Presence detected but trusted phone is home -> Suppress!
            self.current_state = "PRESENCE_DETECTED_SUPPRESSED"
            self.state_message = "Trusted device present — alert suppressed."
            if now - self.last_suppression_log_time >= 60.0:
                self.last_suppression_log_time = now
                self.log_event(
                    "ALERT_SUPPRESSED",
                    "Presence detected in room; alert suppressed because trusted phone is home.",
                )
        else:
            # INTRUSION: Sustained presence + Armed + Phone NOT present!
            self.current_state = "PRESENCE_DETECTED_INTRUDER"
            self.state_message = "Someone may be in your room while you are away!"

            # Check alert cooldown
            if now - self.last_alert_time >= self.settings.alert_cooldown_seconds:
                self.last_alert_time = now
                event_data = self.log_event(
                    "ALERT_TRIGGERED",
                    "INTRUSION ALERT: Sustained presence detected and trusted phone is AWAY!",
                )
                if self.on_alert:
                    try:
                        self.on_alert(event_data)
                    except Exception as e:
                        print(f"[DecisionEngine] Alert callback error: {e}")

        # State transition logging
        if prev_state != self.current_state and self.current_state in ("ROOM_EMPTY", "DISARMED_PRESENCE"):
            if prev_state.startswith("PRESENCE"):
                self.log_event("ROOM_CLEARED", "Room presence has ended; room is now clear.")

        return self.get_status()

    def log_event(self, event_type: str, message: str) -> Dict:
        """Record an event into the history log."""
        item = {
            "timestamp": time.time(),
            "time_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
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
            "rssi_dbm": self.rssi_dbm,
            "mean_amplitude": self.mean_amplitude,
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
