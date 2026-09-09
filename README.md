# Wiiew — Calm Camera-Free Room Intrusion Monitor with Trusted-Device Safe Card

> **Wiiew** is a privacy-first, camera-free room occupancy monitoring Progressive Web Application (PWA). It leverages real-time Channel State Information (CSI) from the onboard ESP32-S3 of an Arduino UNO R4 WiFi. When human presence is detected in the room while the system is armed, Wiiew evaluates a **"Safe Card"** condition: if your trusted smartphone is detected on your home Wi-Fi network, the alert is suppressed ("YOU ARE HOME"); if your phone is away, a calm, single-entry push notification is dispatched to your device ("Someone has entered your room.").

---

## Live Deployments & Endpoints

| Component | Target URL | Description |
|---|---|---|
| **GitHub Pages PWA** | [`https://shr3y4n.github.io/Wiiew/`](https://shr3y4n.github.io/Wiiew/) | Hosted static PWA frontend deployed via GitHub Actions |
| **Local LAN Backend** | `http://192.168.1.100:8000` | FastAPI server running on Windows laptop |
| **Local Host Access** | `http://localhost:8000` | Local development and dashboard access |
| **RuView Sensing WS** | `ws://localhost:8765/ws/live` | Real-time CSI feature extraction stream |
| **ESP32 CSI Stream** | `udp://192.168.1.100:5005` | ADR-018 raw CSI packets from Arduino UNO R4 WiFi |

---

## Architecture Overview

```
                                  +------------------------------+
                                  |     Arduino UNO R4 WiFi      |
                                  |     (ESP32-S3 CSI Sensor)    |
                                  +--------------+---------------+
                                                 |
                                                 | UDP 5005 (ADR-018 Binary CSI)
                                                 v
+-----------------------------------------------------------------------------------------------+
| Windows Host (192.168.1.100)                                                                  |
|                                                                                               |
|  +-----------------------------+               +-------------------------------------------+  |
|  |    RuView Sensing Server    |               |               Wiiew Server                |  |
|  |    (archive/ws_server.py)   |               |            (FastAPI / Uvicorn)            |  |
|  +--------------+--------------+               +---------------------+---------------------+  |
|                 |                                                    |                        |
|                 | WebSocket :8765 / HTTP :8080                       |                        |
|                 +--------------------------------------------------->+                        |
|                                                                      |                        |
|                                 +------------------------------------+                        |
|                                 |                                    |                        |
|                                 v                                    v                        |
|                +---------------------------------+  +---------------------------------------+ |
|                |    Presence Decision Engine     |  |    Trusted Phone Presence Detector    | |
|                |    - Hysteresis & Debounce      |  |    - Windows ARP Cache + Ping Probe   | |
|                |    - 15s Sustained Detection    |  |    - Sleep / Grace Period Tracker     | |
|                |    - Single-Entry Event Alert   |  |    - PWA In-App Heartbeat Ingestion   | |
|                |    - Arm / Disarm State Machine |  +-------------------+-------------------+ |
|                +----------------+----------------+                      |                     |
|                                 |                                       |                     |
|                                 +-------------------+-------------------+                     |
|                                                     |                                         |
|                                                     v                                         |
|                                      +-------------------------------+                        |
|                                      |      Alert & Push Engine      |                        |
|                                      |      (pywebpush + VAPID)      |                        |
|                                      +--------------+----------------+                        |
+-----------------------------------------------------|-----------------------------------------+
                                                      |
                         +----------------------------+----------------------------+
                         |                                                         |
                         v (Local LAN / Same Network)                              v (Static Cloud CDN)
          +-------------------------------+                         +-------------------------------+
          |          Wiiew PWA            |                         |       GitHub Pages PWA        |
          |  (http://192.168.1.100:8000)  |                         | (shr3y4n.github.io/Wiiew/)    |
          |   - Relative Path Assets      |                         |   - Configurable Backend Host |
          |   - SVG Room Visualizer       |                         |   - Offline State Fallback    |
          |   - Human Silhouette & Waves  |                         |   - Auto-Reconnecting Stream  |
          +-------------------------------+                         +-------------------------------+
```

---

## Key Principles & The "Safe Card" Mechanism

### 1. Calm Monitoring Philosophy
Wiiew delivers calm, single-entry push notifications: **"Wiiew: Someone has entered your room."** It dispatches exactly **one** notification per intrusion sequence. It avoids siren words, loud alarms, and repetitive alert spam during continuous room occupancy. Once the room empties and clears for $\ge 10$ seconds, the event resets for subsequent entries.

### 2. No Personal RF Identification Claim
Wiiew **does not claim** that CSI RF disturbances identify the user personally. Wi-Fi subcarrier perturbations reflect physical human motion and multipath field disturbances in the room. Personal identification is achieved strictly through an independent trusted-device signal on the local network (your smartphone).

### 3. Graphical Room Visualization
The central room stage presents a clean, illustrative representation of room occupancy:
* **Empty Room**: Gentle, rhythmic concentric RF wave rings radiating from the wall sensor.
* **Checking Presence**: Pulsing amber waves with a circular countdown ring tracking the sustained debounce threshold ($T_{\text{sustained}} = 15$s).
* **Presence (Safe Card)**: Emerald ambient glow indicating trusted smartphone presence ("YOU ARE HOME").
* **Presence (Untrusted Entry)**: Full-body human silhouette with localized RF multipath disturbance ripples, clearly noted as a CSI disturbance representation (not an optical camera image).

### 4. Decision Logic
$$\text{Presence} \land \neg\text{PhonePresent} \land \text{Armed} \implies \mathbf{ENTRY\ ALERT\ (EXACTLY\ ONCE)}$$
$$\text{Presence} \land \text{PhonePresent} \implies \mathbf{ALERT\ SUPPRESSED\ (SAFE\ CARD)}$$

### 5. Debounce & Hysteresis
To prevent transient false alarms (such as swaying curtains or environmental RF noise), Wiiew requires **sustained presence** (configurable, default 15 seconds) before declaring an entry event.

### 6. Smartphone Sleep Tolerance
Smartphones enter low-power Wi-Fi sleep (DTIM intervals) when locked, temporarily dropping ICMP ping replies. Wiiew's multi-signal engine combines ARP cache inspection, ICMP pings, in-app PWA heartbeats, and a **5-minute sleep grace period** to avoid false "away" triggers.

---

## Hardware Pipeline

* **Board**: Arduino UNO R4 WiFi
* **Module**: ESP32-S3-MINI-1-N8 (QFN56 revision v0.2, 8 MB SPI Flash, No external PSRAM)
* **Firmware**: RuView `esp32-csi-node` v0.8.8
* **Protocol**: ADR-018 Binary Raw CSI (`0xC5110001`), 128 subcarriers per frame
* **Streaming Rate**: ~36 Hz over UDP port 5005 to Windows host `192.168.1.100`

---

## Quickstart

### Prerequisites
- Windows 10/11 host
- Python 3.10+ (tested on Python 3.12) with `fastapi`, `uvicorn`, `websockets`, `cryptography`

### 1. Running Wiiew Backend
Run the master launcher from the repository root:

```powershell
python wiiew/run_wiiew.py
```

The launcher will:
1. Verify and start the local RuView sensing server (WebSocket port `8765`, HTTP port `8080`).
2. Start the Wiiew backend and static file server on port `8000`.
3. Broadcast system status and live CSI telemetry.

### 2. Accessing the App

* **From Local PC**: Open [`http://localhost:8000`](http://localhost:8000)
* **From Mobile Device on Home LAN**: Open `http://192.168.1.100:8000`
* **From GitHub Pages**: Open [`https://shr3y4n.github.io/Wiiew/`](https://shr3y4n.github.io/Wiiew/)
  * Tap **Settings (⚙️)** in the top right.
  * Confirm the Backend Host URL is set to `http://192.168.1.100:8000` and tap **Test & Save Connection**.

### 3. Setup Trusted Phone & Web Push
1. Open Wiiew on your phone.
2. Tap **Settings (⚙️)** $\rightarrow$ tap **Scan Home Network for My Phone** to discover and select your device with 1 click.
3. Tap **Enable Web Push** to allow calm entry notifications on your phone.

---

## Automated Verification Suite (17 Scenarios)

Wiiew includes an end-to-end automated test suite covering all 17 required scenarios:

```powershell
py -3.12 tests/test_wiiew_suite.py
```

### Verified Test Cases:
1. **Empty room state**: Canonical `EMPTY` state, 0 alerts.
2. **Brief false positive (<15s debounce)**: Enters `CHECKING_PRESENCE` without triggering alerts.
3. **Sustained presence (>=15s debounce)**: Confirms `sustained_presence=True`.
4. **Trusted phone + presence**: Resolves `PRESENCE_TRUSTED` ("YOU ARE HOME"), suppresses alerts.
5. **Untrusted phone + presence**: Resolves `PRESENCE_UNTRUSTED` ("SOMEONE IS HERE"), triggers alert callback.
6. **Alert sent exactly once**: Confirms `is_in_untrusted_event=True` and exactly 1 alert sent.
7. **No repeated alert during continuous presence**: Multiple evaluate cycles generate 0 duplicate alerts.
8. **New alert after room clears and re-enters**: Reset after clear threshold enables fresh alert on subsequent entry.
9. **Phone sleep grace period**: Maintains `HOME (SLEEPING)` during deep sleep until timeout.
10. **Disarmed system suppression**: Disarming pauses monitoring and suppresses all alerts.
11. **Sensor offline handling**: CSI stream timeout transitions to `SENSOR_OFFLINE`.
12. **RuView offline handling**: Reconnection backoff handles closed socket without server crash.
13. **Backend unavailable handling**: Frontend renders `BACKEND OFFLINE` status and auto-reconnects.
14. **PWA asset loading**: Validates HTML, CSS, JS, manifest, Service Worker, and PNG icons.
15. **Notification subscription logic**: VAPID public key generation and calm single-entry payload formatting.
16. **WebSocket reconnect resilience**: Broadcast safely evicts dropped clients without interrupting service.
17. **GitHub Pages base path compatibility**: Relative `./` asset paths and GitHub Actions workflow verified.

---

## Project Structure

```
Wiiew/
├── .github/
│   └── workflows/
│       └── deploy-pages.yml    # GitHub Actions workflow for GitHub Pages
├── tests/
│   └── test_wiiew_suite.py     # 17-scenario automated verification suite
├── wiiew/
│   ├── backend/
│   │   ├── config.py           # Settings and persistence
│   │   ├── trusted_device.py   # Windows ARP + Ping + Sleep grace tracker
│   │   ├── decision_engine.py  # Canonical state machine & single-entry alert logic
│   │   ├── push_service.py     # VAPID Web Push delivery
│   │   └── main.py             # FastAPI REST, WebSockets, & CORS configuration
│   ├── frontend/
│   │   ├── index.html          # Responsive dashboard with Room Visualizer
│   │   ├── style.css           # Modern dark-mode UI & desktop grid
│   │   ├── app.js              # Live WebSocket sync & configurable backend client
│   │   ├── sw.js               # Service Worker & calm push handlers
│   │   ├── manifest.json       # W3C PWA installation manifest (relative paths)
│   │   └── icons/              # App icons & badges
│   └── run_wiiew.py            # Unified one-command launcher
├── RuView/                     # Core sensing engine submodule (ADR-018 decoder)
├── WIIEW_HARDWARE_ANALYSIS.md  # Hardware & register analysis
└── README.md
```

---

## License

MIT License — Copyright (c) 2026 Shreyan Dey. Submodules and components retain their respective licenses.
