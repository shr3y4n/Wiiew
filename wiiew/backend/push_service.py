"""
Web Push Notification Service for Wiiew.
Handles VAPID cryptographic keys, browser subscriptions, and dispatching
push notifications to mobile PWAs.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from .config import DATA_DIR

logger = logging.getLogger(__name__)

VAPID_PRIVATE_KEY_FILE = DATA_DIR / "vapid_private.pem"
SUBSCRIPTIONS_FILE = DATA_DIR / "subscriptions.json"


class WebPushService:
    """Manages VAPID keys, subscriptions, and push message delivery."""

    def __init__(self) -> None:
        self.vapid: Optional[Vapid] = None
        self.public_key_b64: str = ""
        self.subscriptions: List[Dict] = []
        self._init_vapid()
        self._load_subscriptions()

    def _init_vapid(self) -> None:
        """Initialize or load persistent VAPID keys."""
        if VAPID_PRIVATE_KEY_FILE.exists():
            try:
                self.vapid = Vapid.from_file(str(VAPID_PRIVATE_KEY_FILE))
                print(f"[WebPush] Loaded existing VAPID keys from {VAPID_PRIVATE_KEY_FILE.name}")
            except Exception as e:
                print(f"[WebPush] Error loading VAPID key: {e}, regenerating")
                self.vapid = None

        if self.vapid is None:
            self.vapid = Vapid()
            self.vapid.generate_keys()
            self.vapid.save_key(str(VAPID_PRIVATE_KEY_FILE))
            print(f"[WebPush] Generated new VAPID keys saved to {VAPID_PRIVATE_KEY_FILE.name}")

        # Compute URL-safe uncompressed point public key for browsers
        raw_bytes = self.vapid.public_key.public_bytes(
            Encoding.X962,
            PublicFormat.UncompressedPoint,
        )
        self.public_key_b64 = (
            base64.urlsafe_b64encode(raw_bytes).decode("utf-8").rstrip("=")
        )

    def get_public_key(self) -> str:
        """Return the base64 URL-encoded VAPID public key for frontend pushManager.subscribe()."""
        return self.public_key_b64

    def add_subscription(self, sub: Dict) -> bool:
        """Register or update a browser push subscription."""
        endpoint = sub.get("endpoint")
        if not endpoint:
            return False

        # Remove duplicate endpoint if exists
        self.subscriptions = [s for s in self.subscriptions if s.get("endpoint") != endpoint]
        self.subscriptions.append(sub)
        self._save_subscriptions()
        print(f"[WebPush] Registered subscription: {endpoint[:45]}... (Total: {len(self.subscriptions)})")
        return True

    def remove_subscription(self, endpoint: str) -> None:
        """Unsubscribe an endpoint."""
        self.subscriptions = [s for s in self.subscriptions if s.get("endpoint") != endpoint]
        self._save_subscriptions()

    def send_notification(
        self,
        title: str,
        body: str,
        tag: str = "wiiew-alert",
        data: Optional[Dict] = None,
    ) -> int:
        """
        Broadcast a Web Push notification to all registered subscriptions.
        Prunes expired (HTTP 404/410) subscriptions.
        Returns count of successfully delivered notifications.
        """
        if not self.subscriptions:
            print("[WebPush] No active subscriptions to deliver push to.")
            return 0

        payload = json.dumps({
            "title": title,
            "body": body,
            "icon": "/icons/icon-192.png",
            "badge": "/icons/badge-72.png",
            "tag": tag,
            "renotify": True,
            "requireInteraction": True,
            "vibrate": [300, 100, 300, 100, 500],
            "data": data or {"url": "/", "timestamp": time.time() if "time" in globals() else 0},
        })

        vapid_claims = {"sub": "mailto:wiiew-local@local.net"}
        successful = 0
        dead_endpoints = []

        for sub in list(self.subscriptions):
            endpoint = sub.get("endpoint", "")
            try:
                webpush(
                    subscription_info=sub,
                    data=payload,
                    vapid_private_key=str(VAPID_PRIVATE_KEY_FILE),
                    vapid_claims=vapid_claims,
                    timeout=5.0,
                )
                successful += 1
            except WebPushException as ex:
                status_code = getattr(ex.response, "status_code", 0) if hasattr(ex, "response") else 0
                # 404 or 410 indicates expired/uninstalled subscription
                if status_code in (404, 410):
                    dead_endpoints.append(endpoint)
                print(f"[WebPush] Failed delivering to {endpoint[:35]}...: {ex}")
            except Exception as e:
                print(f"[WebPush] Delivery error to {endpoint[:35]}...: {e}")

        # Prune expired subscriptions
        if dead_endpoints:
            self.subscriptions = [s for s in self.subscriptions if s.get("endpoint") not in dead_endpoints]
            self._save_subscriptions()
            print(f"[WebPush] Pruned {len(dead_endpoints)} expired subscriptions.")

        return successful

    def _save_subscriptions(self) -> None:
        try:
            with open(SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.subscriptions, f, indent=2)
        except Exception as e:
            print(f"[WebPush] Error saving subscriptions: {e}")

    def _load_subscriptions(self) -> None:
        if SUBSCRIPTIONS_FILE.exists():
            try:
                with open(SUBSCRIPTIONS_FILE, "r", encoding="utf-8") as f:
                    self.subscriptions = json.load(f)
                    print(f"[WebPush] Loaded {len(self.subscriptions)} active push subscriptions.")
            except Exception:
                self.subscriptions = []
