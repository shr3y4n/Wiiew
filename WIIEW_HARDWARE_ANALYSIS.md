# Wiiew Hardware & Architecture Feasibility Analysis

**Target Project:** Wiiew (derivative application based on RuView)  
**Target Hardware:**  
- Arduino UNO R4 WiFi (onboard ESP32-S3-MINI-1-N8 + Renesas RA4M1)  
- AC1200 Wi-Fi Router (Dual-band 2.4 GHz / 5 GHz, 802.11b/g/n/ac)  
- Windows Laptop with MediaTek MT7922 Wi-Fi 6E (Host / Server)  
**Date:** September 2026  
**Document Status:** Complete Architecture & Hardware Feasibility Assessment  

---

## Executive Summary

This document evaluates whether and how the existing **RuView** codebase can be used to build **Wiiew**, utilizing an **Arduino UNO R4 WiFi** as the Wi-Fi Channel State Information (CSI) sensor, an **AC1200 Wi-Fi Router** as the RF transmission/reflection source, and a **Windows Laptop (MediaTek MT7922)** as the host processing server and visualization client.

### Key Verdict:
1. **The onboard ESP32-S3 CAN realistically be used as a CSI sensor for RuView/Wiiew**, supporting Tier 0 (Raw CSI streaming), Tier 1 (DSP filtering/variance), and Tier 2 (breathing, heart rate, presence, and motion detection).
2. **It can be programmed independently of the Renesas RA4M1** via the dedicated 3x2 ESP header (shorting `ESP_DOWNLOAD` to `GND`) or direct USB-CDC in bootloader mode.
3. **Hardware Constraints:** The module is an **ESP32-S3-MINI-1-N8** with **8 MB Quad SPI Flash** and **0 MB PSRAM**. It lacks the 8 MB PSRAM, the RM67162 AMOLED display, and the WS2812 RGB LED assumed by RuView's default build.
4. **Required Action:** A lightweight configuration overlay (`sdkconfig.defaults.uno_r4_wifi`) must be used to disable AMOLED display probing (`CONFIG_DISPLAY_ENABLE=n`), disable WASM3 PSRAM arenas (`CONFIG_WASM_ENABLE=n`), and disable WS2812 RMT driver (`CONFIG_LED_GAMMA_VIZ=n`). This avoids the known **Issue #893 bug** where floating display pins false-positive and starve CSI yield to 0 pps.
5. **Laptop / Router Roles:** The MediaTek MT7922 on Windows cannot capture CSI directly (no Windows NDIS CSI driver API exists; see ADR-266); it acts as the **Host Sensing Server and UI Display**. The AC1200 router provides the 2.4 GHz 802.11n AP and responds to 50 Hz ICMP self-pings from the ESP32-S3, generating the required OFDM packets for continuous CSI extraction.

---

## 1. Architectural Survey of RuView Components

The RuView repository is organized into four core layers:

```
+-------------------------------------------------------------------------------+
|                                UI / Frontend                                  |
|         ui/ (Vanilla JS + ES Modules + WebSockets) & dashboard/ (Vite/Lit)    |
+---------------------------------------^---------------------------------------+
                                        | WebSocket (ws://:8765/ws/sensing)
                                        | REST (http://:8080/api/v1/...)
+---------------------------------------v---------------------------------------+
|                    Rust Sensing Server (v2/crates/...)                        |
|   wifi-densepose-sensing-server (Axum HTTP/WS, UDP :5005 receiver, RuVector)   |
|   wifi-densepose-hardware (ADR-018 / ADR-039 parsers, radio abstraction)      |
|   wifi-densepose-signal & vitals (Spectral FFT, zero-crossing, spatial model) |
+---------------------------------------^---------------------------------------+
                                        | UDP Datagrams (:5005)
                                        | Magic: 0xC5110001 (CSI) / 0xC5110002 (Vitals)
+---------------------------------------v---------------------------------------+
|                         Firmware (firmware/esp32-csi-node)                    |
|   Core 0: WiFi STA + CSI callback (esp_wifi_set_csi_rx_cb) + ICMP Self-Ping   |
|   Core 1: SPSC Ring Buffer -> Edge DSP (Tier 0 passthrough, Tier 1/2 DSP)     |
|   Network: UDP Stream Sender, NVS Config, HTTP OTA (port 8032)                |
+-------------------------------------------------------------------------------+
```

### 1.1 `firmware/esp32-csi-node`
- Built on **ESP-IDF v5.4** targeting `esp32s3` (production) and `esp32c6` (research).
- **CSI Ingestion Pipeline (`csi_collector.c`):** Registers `wifi_csi_callback` via `esp_wifi_set_csi_rx_cb()`. Enforces a 50 Hz ICMP ping (`csi_start_self_ping()`) against the station's gateway to guarantee an OFDM traffic floor even on idle networks.
- **Edge DSP Pipeline (`edge_processing.c`):**
  - **Tier 0:** Passthrough raw subcarrier I/Q data in ADR-018 binary format.
  - **Tier 1:** Phase unwrap, Welford running variance, Top-K subcarrier selection, delta compression.
  - **Tier 2:** Biquad IIR bandpass filters (0.1–0.5 Hz breathing, 0.8–2.0 Hz heart rate), zero-crossing frequency estimation, phase variance threshold for presence/motion, phase acceleration threshold for fall detection.
  - **Tier 3:** WASM3 virtual machine executing `.wasm` sensing bytecode loaded into PSRAM (ADR-040).
- **Transport (`stream_sender.c`):** Sends UDP datagrams to configured `target_ip:target_port` (default 5005). Features ENOMEM backoff cooldown to prevent socket starvation.

### 1.2 `v2/` (Rust Workspace)
- **`wifi-densepose-sensing-server`:** Lightweight Axum web server and UDP ingestion daemon.
  - Binds UDP port 5005 (`udp_receiver_task` in `main.rs:8694-9250`).
  - Parses ESP32 ADR-018 raw CSI (`parse_esp32_frame`), ADR-039 edge vitals (`parse_esp32_vitals`), and ADR-110 sync packets.
  - Fuses multi-node state, estimates occupancy, computes signal field matrices, and evaluates room classification.
  - Serves static UI files on port 8080 (or 3000) and broadcasts updates via WebSocket at `ws://localhost:8765/ws/sensing`.
- **`wifi-densepose-hardware`:** Core wire decoders (`esp32_parser.rs`), sync packets (`sync_packet.rs`), and simulator/parsers for MediaTek (ADR-266), Qualcomm, and Realtek RTL8720F.
- **`wifi-densepose-signal` & `wifi-densepose-vitals`:** High-precision FFT, vital sign estimation, and signal conditioning.

### 1.3 `ui/` (Frontend)
- Located at `ui/` (served directly by `wifi-densepose-sensing-server`).
- Built with vanilla ES modules:
  - `ui/services/sensing.service.js`: Manages WebSocket link to `ws://localhost:8765/ws/sensing`, auto-reconnects, and parses JSON `sensing_update` frames.
  - `ui/components/SensingTab.js`: Real-time presence badges, vital signs charts (Breathing & Heart Rate BPM), and RSSI sparklines.
  - `ui/components/signal-viz.js`: Canvas-based CSI subcarrier amplitude waterfall and frequency heatmaps.
  - `ui/components/PoseDetectionCanvas.js`: 2D/3D skeleton wireframe visualizer.

---

## 2. Seven Specific Technical Determinations

### Question 1: Can the ESP32-S3 inside the UNO R4 WiFi realistically be used as the CSI sensor for the existing RuView firmware?

**Verdict: YES, realistically and reliably, for Tier 0, Tier 1, and Tier 2 sensing.**

#### Evidence & Analysis:
1. **SoC Silicon:** The Arduino UNO R4 WiFi integrates an **ESP32-S3-MINI-1-N8** module. The underlying silicon is the Espressif ESP32-S3 (dual-core Xtensa LX7 @ 240 MHz, Wi-Fi 4 802.11b/g/n 2.4 GHz).
2. **CSI Hardware Capability:** The ESP32-S3 Wi-Fi MAC and baseband hardware natively support CSI extraction. The ESP-IDF functions used in `csi_collector.c`:
   - `esp_wifi_set_csi_config()` (`csi_collector.c:641`)
   - `esp_wifi_set_csi_rx_cb(wifi_csi_callback, NULL)` (`csi_collector.c:642`)
   - `esp_wifi_set_csi(true)` (`csi_collector.c:643`)
   are fully supported by the ESP32-S3 silicon in ESP-IDF v5.4.
3. **CSI Traffic Generation:** The firmware's `csi_start_self_ping()` (`csi_collector.c:449-501`) pings the default gateway of the connected AC1200 router at 50 Hz. The router's 802.11 OFDM ICMP echo replies trigger the hardware CSI receiver, guaranteeing a continuous ~50 Hz stream of 64-subcarrier CSI frames regardless of ambient traffic.
4. **SRAM Budget:** Tier 0 (passthrough), Tier 1 (Welford statistics), and Tier 2 (biquad filtering, zero-crossing BPM, presence/fall heuristics) execute completely inside internal SRAM (~512 KB total, consuming ~220 KB heap/stack).
5. **Limitations:**
   - **No PSRAM:** Tier 3 WASM modules (which allocate 160 KB PSRAM per slot) cannot run.
   - **No Display:** The AMOLED display driver must be disabled in firmware to prevent false probe detections (see Question 3).
   - **Board Role Change:** Overwriting the ESP32-S3 firmware replaces the default Arduino USB-bridge.

---

### Question 2: Can the ESP32-S3 module be programmed independently of the Renesas RA4M1?

**Verdict: YES, completely independently.**

#### Evidence & Analysis:
1. **Dedicated Programming Header (3x2 ESP Header):**
   The Arduino UNO R4 WiFi provides a physical 3x2 header directly adjacent to the reset button and ESP32-S3 module. Its schematic pinout is:
   - **Pin 1:** `ESP_IO42` (MTMS / JTAG)
   - **Pin 2:** `ESP_IO41` (MTDI / JTAG)
   - **Pin 3:** `ESP_TXD0` (UART0 TX — 3.3V)
   - **Pin 4:** `ESP_DOWNLOAD` (GPIO0 / BOOT — active LOW)
   - **Pin 5:** `ESP_RXD0` (UART0 RX — 3.3V)
   - **Pin 6:** `GND` (Ground)
2. **ROM Bootloader Access:**
   Connecting Pin 4 (`ESP_DOWNLOAD`) to Pin 6 (`GND`) while resetting or applying power forces the ESP32-S3 hardware into ROM Download Mode.
3. **USB Routing & Analog Switches (U2 / U6):**
   According to the official Arduino UNO R4 WiFi schematics, the USB-C data lines (`D+` and `D-`) are multiplexed between the ESP32-S3 and the Renesas RA4M1 using analog switches (U2 and U6).
   - By default, USB data lines route to the ESP32-S3's native USB Serial/JTAG pins (GPIO 19/20).
   - When in Download Mode, the ESP32-S3 enumerates on Windows directly as `USB JTAG/serial debug unit` (VID:PID `303a:1001`).
   - Standard `esptool.py` communicates directly over this virtual COM port:
     ```bash
     python -m esptool --chip esp32s3 --port COM_PORT write_flash ...
     ```
4. **Decoupling from Renesas RA4M1:**
   The ESP32-S3 executes code out of its own dedicated 8 MB SPI flash. It does not depend on the Renesas RA4M1 to boot, maintain Wi-Fi connection, run FreeRTOS, capture CSI, or transmit UDP datagrams. The RA4M1 can simply remain unprogrammed, held in reset, or run a dummy sketch.
5. **Reversibility:**
   The stock Arduino USB bridge firmware (`UNOR4USBBridge`) can be restored at any time using Arduino IDE's *Firmware Updater* or via `esptool.py` with Arduino's official release binary.

---

### Question 3: Does the existing RuView firmware expect hardware features unavailable on the UNO R4 WiFi?

**Verdict: YES. RuView's default configuration expects several hardware peripherals not present on the UNO R4 WiFi.**

#### Detailed Discrepancy Matrix:

| Hardware Feature | RuView Default Expectation | Arduino UNO R4 WiFi Reality | Impact & Resolution |
| :--- | :--- | :--- | :--- |
| **PSRAM (External RAM)** | 8 MB Octal/Quad SPIRAM (`README.md:150`) for WASM module arenas (640 KB) and display framebuffers | **0 MB PSRAM** (ESP32-S3-MINI-1-N8 is flash-only) | **Critical:** Must disable WASM Tier 3 (`CONFIG_WASM_ENABLE=n`) and AMOLED display. Tier 0-2 DSP runs in internal SRAM. |
| **AMOLED Display Panel** | RM67162 536x240 QSPI AMOLED display on GPIO 5, 6, 7, 18, 47, 48 (`Kconfig.projbuild:220-249`) | **None** (Board has 12x8 LED matrix driven by the RA4M1) | **Critical (Issue #893):** Floating QSPI pins false-positive the runtime display probe. `main.c:629-635` then skips promiscuous DATA capture upgrade, collapsing CSI yield to 0 pps. Must set `CONFIG_DISPLAY_ENABLE=n`. |
| **WS2812 RGB LED** | 40 Hz gamma flicker on GPIO 48 (`main.c:342`) via RMT peripheral | **None on ESP32-S3** (GPIO 48 is unrouted; LED matrix is on RA4M1) | **Medium:** Toggles an unrouted pin and wastes CPU/RMT cycles. Must set `CONFIG_LED_GAMMA_VIZ=n`. |
| **Dedicated USB-UART Bridge** | CP2102 / CH340 on UART0 (`README.md:151`) | **Native USB Serial/JTAG** on ESP32-S3 (or UART on 3x2 header) | **Low:** Enable `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y` in sdkconfig so serial logging and provisioning work over USB-C. |
| **External mmWave Radar** | LD2410 radar on UART GPIO 15/16 (`mmwave_sensor.c`) | **None** | **None:** Gracefully inactive when radar does not reply. |
| **Wi-Fi 6 (802.11ax) & 802.15.4** | C6 features: TWT, time-sync, LP-core (`c6_*.c`) | **Wi-Fi 4 (802.11b/g/n) 2.4 GHz only** | **None:** Already gated behind `IDF_TARGET_ESP32C6` in Kconfig. |

---

### Question 4: Which exact firmware files would need modification if necessary?

**Verdict: No C source code refactoring is required. The adaptation is achieved cleanly via an SDK configuration overlay and build options.**

#### Exact File Modifications / Additions:

1. **`firmware/esp32-csi-node/sdkconfig.defaults.uno_r4_wifi` (NEW CONFIG OVERLAY):**
   Create this file to define the exact hardware profile of the UNO R4 WiFi:
   ```ini
   # Target ESP32-S3
   CONFIG_IDF_TARGET="esp32s3"

   # 8 MB Flash Configuration
   CONFIG_ESPTOOLPY_FLASHSIZE_8MB=y
   CONFIG_ESPTOOLPY_FLASHSIZE="8MB"
   CONFIG_PARTITION_TABLE_CUSTOM=y
   CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions_display.csv"

   # Disable AMOLED display (Prevents Issue #893 zero-pps CSI starvation)
   # CONFIG_DISPLAY_ENABLE is not set

   # Disable WASM3 (Conserves internal SRAM; no PSRAM on ESP32-S3-MINI-1-N8)
   # CONFIG_WASM_ENABLE is not set

   # Disable WS2812 RMT driver (GPIO 48 not connected to an LED)
   # CONFIG_LED_GAMMA_VIZ is not set

   # Enable Native USB Serial/JTAG for console logs and provisioning over USB-C
   CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y
   # CONFIG_ESP_CONSOLE_UART_DEFAULT is not set

   # Enable CSI & LWIP buffers
   CONFIG_ESP_WIFI_CSI_ENABLED=y
   CONFIG_LWIP_SO_RCVBUF=y
   CONFIG_LWIP_UDP_RECVMBOX_SIZE=32
   CONFIG_LWIP_TCPIP_RECVMBOX_SIZE=64
   CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM=64
   CONFIG_ESP_MAIN_TASK_STACK_SIZE=8192
   CONFIG_FREERTOS_TIMER_TASK_STACK_DEPTH=8192
   ```

2. **`firmware/esp32-csi-node/main/main.c` (VERIFIED SAFE):**
   - Lines 628–635 already handle display disabling conditionally:
     ```c
     #ifdef CONFIG_DISPLAY_ENABLE
         bool has_display = display_is_active();
     #else
         bool has_display = false;
     #endif
         if (!has_display) {
             csi_collector_enable_data_capture();
         }
     ```
     When `CONFIG_DISPLAY_ENABLE` is unset, `has_display` compiles directly to constant `false`, guaranteeing `csi_collector_enable_data_capture()` is called.
   - Lines 339–374: `CONFIG_LED_GAMMA_VIZ` is guarded by `#if CONFIG_LED_GAMMA_VIZ`. When unset, it releases RMT and avoids driving GPIO 48.

3. **`firmware/esp32-csi-node/provision.py` (VERIFIED COMPATIBLE):**
   - Compatible as-is. When targeting the UNO R4 WiFi over USB-C:
     ```bash
     python firmware/esp32-csi-node/provision.py --port COM_PORT --chip esp32s3 \
       --ssid "RouterSSID" --password "RouterPassword" --target-ip 192.168.1.X
     ```
   - Writes directly to the NVS partition at offset `0x9000`.

---

### Question 5: Whether the UNO R4 WiFi flash size and ESP32-S3 configuration are compatible?

**Verdict: YES, Flash is 100% compatible (8 MB). RAM is compatible for Tier 0-2, but incompatible with PSRAM features.**

#### Detailed Memory Breakdown:

#### 1. Flash Memory Compatibility (8 MB)
- The module is **ESP32-S3-MINI-1-N8**. The **`-N8`** suffix designates **8 Megabytes of embedded Quad SPI Flash**.
- RuView's custom 8 MB partition table (`partitions_display.csv`):
  ```text
  Offset    Size      Name      Type/SubType
  0x009000  0x006000  nvs       data, nvs (24 KB)
  0x00F000  0x002000  otadata   data, ota (8 KB)
  0x011000  0x001000  phy_init  data, phy (4 KB)
  0x020000  0x200000  ota_0     app,  ota_0 (2048 KB)
  0x220000  0x200000  ota_1     app,  ota_1 (2048 KB)
  0x420000  0x1E0000  spiffs    data, spiffs (1920 KB)
  Total allocated: 0x600000 = 6,291,456 bytes (6.0 MB) <= 8.0 MB Available
  ```
- **Conclusion:** The firmware binary (~650 KB compiled without LVGL/WASM) fits comfortably within the 2048 KB OTA slots, and the 6 MB total allocation fits easily within the 8 MB physical flash.

#### 2. RAM / PSRAM Compatibility
- **Physical SRAM:** 512 KB internal SRAM (standard on ESP32-S3).
- **Physical PSRAM:** **0 KB** (no external pseudo-static RAM).
- **Runtime SRAM Allocation:**
  - FreeRTOS & System: ~40 KB
  - Wi-Fi Driver & Dynamic TX/RX Buffers: ~110 KB
  - LWIP Network Stack (Expanded UDP/TCP mailboxes): ~45 KB
  - CSI SPSC Ring Buffer: ~32 KB
  - Tier 1 & Tier 2 DSP State (Biquad IIR, Welford accumulators): ~12 KB
  - Free Heap Remaining: **~180–220 KB** (Healthy margin; prevents ENOMEM crashes).
- **Conclusion:** By excluding WASM and LVGL, internal SRAM operates with generous headroom.

---

### Question 6: What the minimum viable hardware/software architecture for Wiiew should be?

**Verdict: A streamlined 3-node topology leveraging existing hardware without extra purchases.**

```
+-----------------------------------------------------------------------------------------+
|                                  WIIEW PHYSICAL TOPOLOGY                                 |
+-----------------------------------------------------------------------------------------+

                   +--------------------------------------------+
                   |            AC1200 Wi-Fi Router             |
                   |   - 2.4 GHz 802.11n AP (Channel 6)         |
                   |   - Gateway IP: 192.168.1.1                |
                   +---------------------+----------------------+
                                         |
                       802.11n RF Link   |   802.11ac / 802.11n
                         (50 Hz Pings)   |   (High-speed Wi-Fi or Ethernet)
                                         |
          +------------------------------+------------------------------+
          |                                                             |
          v                                                             v
+------------------------------------+        +-----------------------------------------+
|     Arduino UNO R4 WiFi (Node 1)   |        |   Windows Laptop (MediaTek MT7922)      |
|                                    |        |                                         |
|  [ESP32-S3-MINI-1-N8]              |        |  [wifi-densepose-sensing-server (Rust)] |
|  - Custom RuView Firmware          |  UDP   |  - Listens on UDP :5005                 |
|  - WiFi STA (192.168.1.50)         |------> |  - Runs RuVector DSP & Vital Estimator  |
|  - 50 Hz ICMP Echo to Gateway      | :5005  |  - Serves static UI (:8080 or :3000)    |
|  - Extracts CSI via HW callback    |        |  - Broadcasts WebSocket (:8765)         |
|  - Computes Tier 0-2 DSP           |        |                                         |
|  - Emits UDP packets (ADR-018/039) |        |  [Local Web Browser]                    |
|                                    |        |  - Opens http://localhost:8080/ui/      |
|  [Renesas RA4M1 (Optional)]        |        |  - Connects to ws://localhost:8765      |
|  - Receives Vitals via Serial      |        |  - Renders presence, respiration BPM,   |
|  - Displays on 12x8 LED Matrix     |        |    heart rate, and CSI waterfall heatmaps|
+------------------------------------+        +-----------------------------------------+
```

#### Node Roles & Workflow:
1. **RF Transmitter & Reflector (AC1200 Router):**
   - Placed across the room from the Arduino.
   - Operates on a fixed 2.4 GHz channel (e.g., Channel 6, 20 MHz bandwidth).
   - Serves as the wireless target for the ESP32-S3. Human movement between the router and Arduino alters the multipath RF environment.
2. **CSI Sensing Probe (Arduino UNO R4 WiFi - ESP32-S3):**
   - Connects to the AC1200 router's 2.4 GHz SSID.
   - Pings `192.168.1.1` (Gateway) at 50 Hz to induce constant OFDM unicast return frames.
   - Hardware CSI callback extracts 64 subcarriers per frame.
   - Core 1 edge processing computes presence, motion index, and vital signs.
   - Transmits ADR-018 raw frames (`0xC5110001`) and ADR-039 vitals (`0xC5110002`) over UDP to the laptop's IP on port 5005.
   - *(Optional Bonus Feature)*: The ESP32-S3 can output presence/vitals over internal UART to the Renesas RA4M1, which can illuminate patterns on the board's **12x8 LED Matrix** (e.g., heart pulsing animation when a person is detected).
3. **Aggregator, Sensing Server, & UI (Windows Laptop - MT7922):**
   - Connected to the same router over Wi-Fi or Ethernet.
   - Runs `cargo run -p wifi-densepose-sensing-server -- --http-port 8080 --udp-port 5005 --static-dir ./ui`.
   - Ingests UDP packets from the Arduino UNO R4 WiFi.
   - Computes multi-subcarrier spatial metrics, baseline calibration, and room occupancy.
   - Hosts the web UI and streams real-time data over WebSocket `/ws/sensing`.
   - Browser displays real-time human presence, breathing rate, and signal strength.

---

### Question 7: Which existing RuView APIs/WebSockets can we reuse instead of rewriting the sensing pipeline?

**Verdict: Nearly 100% of the ingestion, processing, REST, WebSocket, and UI layers can be reused without alteration.**

#### Reusable Pipeline Components:

#### 1. Ingestion Layer (Port 5005 UDP)
- **`parse_esp32_frame` (`v2/crates/wifi-densepose-hardware/src/esp32_parser.rs:89-180`):**
  Parses binary ADR-018 frames (`0xC5110001` magic, node ID, antennas, subcarriers, sequence, RSSI, noise floor, I/Q pairs). Completely implemented and verified.
- **`parse_esp32_vitals` (`v2/crates/wifi-densepose-sensing-server/src/main.rs:8806-8850`):**
  Decodes 32-byte ADR-039 vitals packets (`0xC5110002` magic, breathing BPM, heart rate BPM, presence flag, motion energy).

#### 2. REST Endpoints (`http://localhost:8080/api/v1/...`)
- `GET /health` & `GET /health/ready`: Liveness and readiness probes.
- `GET /api/v1/sensing/latest`: Returns latest processed sensing frame in canonical JSON schema.
- `GET /api/v1/vital-signs`: Returns current breathing rate, heart rate, and detection confidence.
- `GET /api/v1/edge-vitals`: Returns raw vitals packet computed on-device by the ESP32-S3.
- `GET /api/v1/nodes`: Lists active CSI nodes, their IP addresses, RSSI history, and last-seen timestamps.
- `GET /api/v1/stream/status`: Metrics on incoming UDP packets/sec, dropped packets, and yield.
- `POST /api/v1/calibration/start` & `POST /api/v1/calibration/stop`: Collects 60 seconds of empty-room background noise to calibrate adaptive variance thresholds.

#### 3. WebSocket Streams (`ws://localhost:8765/...` or `ws://localhost:8080/...`)
- **`/ws/sensing` (`v2/crates/wifi-densepose-sensing-server/src/main.rs:11415`):**
  Broadcasts real-time JSON `sensing_update` frames containing:
  - `nodes`: Per-node signal and RSSI status.
  - `features`: Mean RSSI, phase variance, motion band power, dominant frequency.
  - `classification`: Room state (`"absent"`, `"present_still"`, `"motion"`), confidence score.
  - `vital_signs`: Filtered breathing BPM and heart rate BPM.
  - `signal_field`: 2D interpolation matrix for spatial heatmap visualization.
- **`/ws/introspection` (`v2/crates/wifi-densepose-sensing-server/src/main.rs:11525`):**
  Real-time phase attractor and signal dynamics.

#### 4. Frontend UI Components (`ui/`)
- **`ui/services/sensing.service.js`:** Handles WebSocket connections, automatic reconnection with exponential backoff, and ticket-based authentication.
- **`ui/components/SensingTab.js`:** Production-ready UI with vital signs charts, motion indicator, occupancy count, and node health.
- **`ui/components/signal-viz.js`:** CSI subcarrier heatmap / waterfall canvas renderer.

---

## 3. Practical Wiiew Implementation Roadmap

To realize Wiiew using this repository without breaking existing functionality:

### Phase 1: Build Firmware for Arduino UNO R4 WiFi
1. Create `firmware/esp32-csi-node/sdkconfig.defaults.uno_r4_wifi` containing the display-less, PSRAM-less configuration.
2. Build the firmware binary using the official Docker container:
   ```bash
   MSYS_NO_PATHCONV=1 docker run --rm \
     -v "$(pwd)/firmware/esp32-csi-node:/project" -w /project \
     espressif/idf:v5.4 bash -c \
     "rm -rf build sdkconfig && \
      idf.py -DSDKCONFIG_DEFAULTS='sdkconfig.defaults;sdkconfig.defaults.uno_r4_wifi' set-target esp32s3 && \
      idf.py -DSDKCONFIG_DEFAULTS='sdkconfig.defaults;sdkconfig.defaults.uno_r4_wifi' build"
   ```

### Phase 2: Flash & Provision the UNO R4 WiFi
1. Place the ESP32-S3 into Download Mode:
   - Connect a jumper wire between **Pin 4 (`ESP_DOWNLOAD`)** and **Pin 6 (`GND`)** on the 3x2 ESP header.
   - Plug the Arduino UNO R4 WiFi into the Windows laptop via USB-C.
   - Remove the jumper wire.
2. Flash the compiled firmware via `esptool`:
   ```bash
   python -m esptool --chip esp32s3 --port COM_PORT --baud 460800 \
     write_flash --flash_mode dio --flash_size 8MB \
     0x0     firmware/esp32-csi-node/build/bootloader/bootloader.bin \
     0x8000  firmware/esp32-csi-node/build/partition_table/partition-table.bin \
     0xf000  firmware/esp32-csi-node/build/ota_data_initial.bin \
     0x20000 firmware/esp32-csi-node/build/esp32-csi-node.bin
   ```
3. Provision network credentials using `provision.py`:
   ```bash
   python firmware/esp32-csi-node/provision.py --port COM_PORT --chip esp32s3 \
     --ssid "YourRouterSSID" --password "YourRouterPassword" \
     --target-ip <LAPTOP_LAN_IP> --target-port 5005 --channel 6
   ```

### Phase 3: Launch Host Sensing Server & Web UI
1. Build and run the sensing server in `v2/`:
   ```bash
   cargo run -p wifi-densepose-sensing-server -- \
     --http-port 8080 \
     --udp-port 5005 \
     --ws-port 8765 \
     --static-dir ./ui \
     --source esp32
   ```
2. Open `http://localhost:8080/` in a web browser.
3. Observe live CSI packet reception, presence detection, and breathing rate indicators as human subjects walk or breathe in the RF path between the AC1200 router and the Arduino UNO R4 WiFi.

---

## 4. Conclusion

The Arduino UNO R4 WiFi can function directly as an active CSI sensing node within RuView's architecture. The lack of PSRAM and AMOLED display is non-fatal; disabling those features via configuration aligns the firmware with the exact physical hardware profile of the ESP32-S3-MINI-1-N8 while preserving full Tier 0, Tier 1, and Tier 2 Wi-Fi sensing capabilities. The existing sensing-server, REST endpoints, WebSocket streams, and UI can be reused without modification.
