# Wiiew — Camera-Free Room Intrusion Monitor with Trusted-Device Safe Card

> **Wiiew** is a privacy-first, camera-free room intrusion monitoring Progressive Web Application (PWA). It leverages real-time Channel State Information (CSI) from the onboard ESP32-S3 of an Arduino UNO R4 WiFi. When human presence is detected in the room while the system is armed, Wiiew evaluates a **"Safe Card"** condition: if your trusted smartphone is detected on your home Wi-Fi network, the alert is suppressed ("I am home"); if your phone is away, an urgent Web Push notification is dispatched to your device.

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
|                |    - Arm / Disarm State Machine |  |    - PWA In-App Heartbeat Ingestion   | |
|                +----------------+----------------+  +-------------------+-------------------+ |
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
                                                      | W3C Web Push / HTTPS / Local LAN
                                                      v
                                      +-------------------------------+
                                      |          Wiiew PWA            |
                                      |   (Mobile & Desktop UI)       |
                                      |   - Service Worker (sw.js)    |
                                      |   - W3C Web Push Notifications|
                                      |   - Minimalist Live Dashboard |
                                      +-------------------------------+
```

---

## Key Principles & The "Safe Card" Mechanism

### 1. No Personal RF Identification
Wiiew **does not claim** that CSI RF disturbances identify the user personally. Wi-Fi subcarrier perturbations reflect physical human motion and presence in the RF multipath field. Personal identification is achieved strictly through an independent trusted-device signal on the local network.

### 2. Decision Logic
$$\text{Presence} \land \neg\text{PhonePresent} \land \text{Armed} \implies \mathbf{INTRUSION\ ALERT}$$
$$\text{Presence} \land \text{PhonePresent} \implies \mathbf{ALERT\ SUPPRESSED\ (SAFE\ CARD)}$$

### 3. Debounce & Hysteresis
To eliminate transient false alarms (e.g. curtains swaying or brief RF noise), Wiiew requires **sustained presence** (configurable, default 15 seconds) before triggering an alert state.

### 4. Smartphone Sleep Tolerance
Modern Android and iOS devices enter low-power Wi-Fi sleep (DTIM intervals) when locked, temporarily dropping ICMP ping requests. Wiiew's state machine provides a configurable **sleep grace period** (default 3 minutes) coupled with ARP table analysis and in-app foreground heartbeats to prevent false "away" transitions.

---

## Hardware Pipeline

* **Board**: Arduino UNO R4 WiFi
* **Module**: ESP32-S3-MINI-1-N8 (QFN56 revision v0.2, 8 MB SPI Flash, No external PSRAM)
* **Firmware**: RuView `esp32-csi-node` v0.8.8
* **Protocol**: ADR-018 Binary Raw CSI (`0xC5110001`), 128 subcarriers per frame
* **Streaming Rate**: ~36 Hz over UDP port 5005 to host `192.168.1.100`

---

## Quickstart

### Prerequisites
- Windows 10/11 host
- Python 3.10+ with `fastapi`, `uvicorn`, `websockets`, `pywebpush`, `pillow`, `numpy`, `scipy`

### Running Wiiew
Run the master launcher from the repository root:

```powershell
python wiiew/run_wiiew.py
```

The launcher will:
1. Verify and start the local RuView sensing server (if not already running).
2. Start the Wiiew backend and PWA static file server on port `8000`.
3. Display the network URLs for desktop and mobile access.

---

## Installing the PWA on Your Phone

1. Connect your smartphone to the same Wi-Fi network (e.g., `192.168.1.x`).
2. Open your mobile browser (Chrome / Edge / Safari) and navigate to:
   ```
   http://<YOUR_PC_LAN_IP>:8000
   ```
   *(e.g., `http://192.168.1.100:8000`)*
3. Tap **Install App** in the header (or browser menu $\rightarrow$ **Add to Home Screen**).
4. Tap **Settings (⚙️)** in Wiiew $\rightarrow$ tap **Scan Home Network for My Phone** to select your device with 1 click.
5. Tap **Enable Web Push** to allow intrusion notifications.

---

## Project Structure

```
Wiiew/
├── wiiew/
│   ├── backend/
│   │   ├── config.py           # Settings and persistence
│   │   ├── trusted_device.py   # Windows ARP + Ping + Sleep grace tracker
│   │   ├── decision_engine.py  # Hysteresis state machine & safe card logic
│   │   ├── push_service.py     # VAPID Web Push delivery
│   │   └── main.py             # FastAPI REST, WebSockets, & PWA mounting
│   ├── frontend/
│   │   ├── index.html          # Minimalist responsive dashboard
│   │   ├── style.css           # Modern dark-mode UI
│   │   ├── app.js              # Live WebSocket sync & push client
│   │   ├── sw.js               # Service Worker & push handlers
│   │   ├── manifest.json       # W3C PWA installation manifest
│   │   └── icons/              # App icons & badges
│   └── run_wiiew.py            # Unified one-command launcher
├── RuView/                     # Core sensing engine (ADR-018 decoder)
├── WIIEW_HARDWARE_ANALYSIS.md  # Detailed hardware & register analysis
└── README.md
```
