"""
Wiiew Main Server (FastAPI).
Orchestrates CSI ingestion from RuView, trusted device probing,
decision engine evaluation, Web Push notifications, and serves the PWA frontend.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Set

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import websockets

from .config import WiiewSettings, load_settings, save_settings
from .decision_engine import DecisionEngine
from .push_service import WebPushService
from .trusted_device import TrustedDeviceDetector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wiiew")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Global Singletons
settings: WiiewSettings = load_settings()
push_service: WebPushService = WebPushService()
phone_detector: TrustedDeviceDetector = TrustedDeviceDetector(settings)


def handle_intrusion_alert(event: Dict) -> None:
    """Callback fired when the decision engine declares a new room entry."""
    logger.info("Room entry event: %s", event.get("message"))
    delivered = push_service.send_notification(
        title="Wiiew",
        body="Someone has entered your room.",
        tag="wiiew-room-entry",
        data={"url": "./", "state": event.get("state")},
    )
    logger.info("Delivered entry notification to %d push subscription(s)", delivered)


decision_engine: DecisionEngine = DecisionEngine(
    settings=settings,
    phone_detector=phone_detector,
    on_alert_callback=handle_intrusion_alert,
)

# Active frontend WebSocket clients
connected_ws_clients: Set[WebSocket] = set()
last_subcarriers: list = []


# ---------------------------------------------------------------------------
# Background Tasks
# ---------------------------------------------------------------------------

async def ruview_stream_consumer():
    """Consume sensing updates from the local RuView sensing server via WebSocket."""
    global last_subcarriers
    logger.info("Starting RuView stream consumer on %s", settings.ruview_ws_url)

    while True:
        try:
            async with websockets.connect(settings.ruview_ws_url, open_timeout=5) as ws:
                logger.info("Connected to RuView sensing stream.")
                async for raw in ws:
                    try:
                        data = json.loads(raw)
                        if data.get("type") == "sensing_update":
                            clf = data.get("classification", {})
                            presence = bool(clf.get("presence", False))
                            motion = str(clf.get("motion_level", "empty"))

                            nodes = data.get("nodes", [])
                            rssi = -80.0
                            amp = 0.0
                            if nodes:
                                n0 = nodes[0]
                                rssi = float(n0.get("rssi_dbm", -80.0))
                                amp = float(n0.get("mean_amplitude", 0.0))
                                last_subcarriers = n0.get("amplitude", [])

                            decision_engine.update_csi_sample(
                                presence=presence,
                                motion_level=motion,
                                rssi=rssi,
                                amplitude=amp,
                            )
                    except Exception as ex:
                        logger.debug("Frame decode error: %s", ex)
        except Exception as e:
            decision_engine.sensor_online = False
            logger.debug("RuView sensing server not connected (%s), retrying in 3s...", e)
            await asyncio.sleep(3.0)


async def phone_probe_loop():
    """Probe network for trusted phone presence every 5 seconds."""
    while True:
        try:
            await phone_detector.check_presence()
        except Exception as e:
            logger.error("Phone probe error: %s", e)
        await asyncio.sleep(5.0)


async def decision_and_broadcast_loop():
    """Evaluate decision engine and push state to frontend clients every 1 second."""
    while True:
        try:
            state = decision_engine.evaluate()
            payload = get_full_state()
            msg = json.dumps(payload)

            # Broadcast to UI clients
            dead = set()
            for ws in connected_ws_clients:
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead.add(ws)
            connected_ws_clients.difference_update(dead)
        except Exception as e:
            logger.error("Decision loop error: %s", e)
        await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background loops
    task1 = asyncio.create_task(ruview_stream_consumer())
    task2 = asyncio.create_task(phone_probe_loop())
    task3 = asyncio.create_task(decision_and_broadcast_loop())
    logger.info("Wiiew background tasks launched.")
    yield
    task1.cancel()
    task2.cancel()
    task3.cancel()


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

app = FastAPI(title="Wiiew Room Intrusion Monitor", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://shr3y4n.github.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_origin_regex=r"^https?://(192\.168\.\d+\.\d+|localhost|127\.0\.0\.1|shr3y4n\.github\.io)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def get_full_state() -> Dict:
    """Consolidated state payload for the dashboard."""
    decision_status = decision_engine.get_status()
    phone_status = phone_detector.get_status()

    return {
        "timestamp": decision_status.get("timestamp", 0),
        "room": {
            "state": decision_status["current_state"],
            "message": decision_status["state_message"],
            "sustained_presence": decision_status["sustained_presence"],
            "raw_presence": decision_status["raw_presence"],
            "presence_duration_seconds": decision_status["presence_duration_seconds"],
            "presence_threshold_seconds": decision_status["presence_threshold_seconds"],
            "last_activity_seconds_ago": decision_status["last_activity_seconds_ago"],
            "motion_level": decision_status["motion_level"],
        },
        "sensor": {
            "online": decision_status["sensor_online"],
            "rssi_dbm": decision_status["rssi_dbm"],
            "mean_amplitude": decision_status["mean_amplitude"],
            "subcarriers": last_subcarriers[:64],
        },
        "phone": phone_status,
        "system": {
            "armed": settings.is_armed,
            "sound_enabled": settings.sound_enabled,
        },
    }


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def api_status():
    """Current system status."""
    return get_full_state()


@app.post("/api/arm")
async def api_arm(req: Request):
    """Toggle or set system armed state."""
    body = await req.json() if req.headers.get("content-length", "0") != "0" else {}
    if "armed" in body:
        settings.is_armed = bool(body["armed"])
    else:
        settings.is_armed = not settings.is_armed
    save_settings(settings)
    action = "ARMED" if settings.is_armed else "DISARMED"
    decision_engine.log_event(action, f"System manually {action.lower()} by user.")
    return {"status": "ok", "armed": settings.is_armed}


@app.post("/api/heartbeat")
async def api_heartbeat(req: Request):
    """Client-side heartbeat when PWA is open on trusted phone."""
    phone_detector.heartbeat(source="PWA foreground")
    return {"status": "ok", "phone_home": True}


@app.get("/api/devices/discover")
async def api_discover():
    """Discover candidate LAN devices for 1-click trusted phone setup."""
    devices = await phone_detector.discover_devices()
    return {"devices": devices}


@app.get("/api/settings")
async def api_get_settings():
    return settings.model_dump()


@app.post("/api/settings")
async def api_save_settings(new_settings: WiiewSettings):
    global settings
    settings = new_settings
    phone_detector.settings = settings
    decision_engine.settings = settings
    save_settings(settings)
    return {"status": "ok", "settings": settings.model_dump()}


@app.get("/api/events")
async def api_events():
    """Retrieve recent event history."""
    return {"events": list(decision_engine.events)}


@app.get("/api/push/public-key")
async def api_push_key():
    """VAPID public key for browser PushManager subscription."""
    return {"publicKey": push_service.get_public_key()}


@app.post("/api/push/subscribe")
async def api_push_subscribe(req: Request):
    """Register client push subscription."""
    sub = await req.json()
    success = push_service.add_subscription(sub)
    return {"status": "ok" if success else "error"}


@app.post("/api/push/test")
async def api_push_test():
    """Send a test push notification to verify phone receipt."""
    delivered = push_service.send_notification(
        title="Wiiew",
        body="Test notification — notifications are working.",
        tag="wiiew-test-notification",
    )
    return {"status": "ok", "delivered": delivered}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    """WebSocket stream providing 1 Hz updates to connected dashboards."""
    await websocket.accept()
    connected_ws_clients.add(websocket)
    # Send immediate initial state
    try:
        await websocket.send_text(json.dumps(get_full_state()))
        while True:
            # Keep-alive receive
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        connected_ws_clients.discard(websocket)


# ---------------------------------------------------------------------------
# Static PWA Files
# ---------------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
