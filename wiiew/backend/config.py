"""
Wiiew Configuration and Settings Manager.
Handles persistent configuration for trusted device, hysteresis thresholds, and ports.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"


class WiiewSettings(BaseModel):
    # System arming
    is_armed: bool = Field(default=True, description="Whether monitoring is currently armed")

    # Trusted Device (Safe Card)
    trusted_phone_ip: str = Field(default="", description="Target IP of the trusted phone")
    trusted_phone_mac: str = Field(default="", description="MAC address of trusted phone (for ARP matching)")
    trusted_phone_name: str = Field(default="My Phone", description="Friendly device name")
    phone_grace_period_seconds: int = Field(default=180, description="Grace period for phone Wi-Fi sleep in seconds")

    # Presence Hysteresis & Debounce
    presence_sustained_seconds: float = Field(default=15.0, description="Required sustained CSI presence before alert")
    presence_clear_seconds: float = Field(default=10.0, description="Required sustained absence before clearing presence")
    alert_cooldown_seconds: float = Field(default=300.0, description="Cooldown between push alerts in seconds")

    # Audio / UI
    sound_enabled: bool = Field(default=True, description="Play in-browser alert sound")

    # RuView endpoints
    ruview_ws_url: str = Field(default="ws://localhost:8765/ws/sensing", description="RuView WebSocket sensing stream")
    ruview_http_url: str = Field(default="http://localhost:8080", description="RuView REST API")

    # Wiiew Server
    host: str = Field(default="0.0.0.0", description="Bind address")
    port: int = Field(default=8000, description="Wiiew HTTP/PWA port")


def load_settings() -> WiiewSettings:
    """Load settings from JSON file or return defaults."""
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return WiiewSettings(**data)
        except Exception as e:
            print(f"[Config] Error loading settings: {e}, using defaults")
    return WiiewSettings()


def save_settings(settings: WiiewSettings) -> None:
    """Persist settings to disk."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings.model_dump(), f, indent=2)
    except Exception as e:
        print(f"[Config] Error saving settings: {e}")
