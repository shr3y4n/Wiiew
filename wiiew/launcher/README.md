# Wiiew Launcher

A modern, native Windows desktop application for managing and orchestrating the complete **Wiiew CSI Room Intrusion Monitoring Pipeline**.

Instead of opening multiple PowerShell terminal windows and manually executing commands, Wiiew Launcher provides a single-click control panel to start, stop, monitor, and open the system.

---

## Managed Pipeline Architecture

```
[ Arduino UNO R4 WiFi ] (ESP32-S3 @ 192.168.1.102)
        │ UDP Port 5005 (CSI Packets)
        ▼
[ RuView Sensing Server ] (ws://localhost:8765/ws/sensing)
        │ Local WebSocket
        ▼
[ Wiiew FastAPI Backend ] (http://127.0.0.1:8000)
        │ Local HTTP / WSS
        ▼
[ Cloudflare Quick Tunnel ] (cloudflared.exe -> trycloudflare.com)
        │ Public HTTPS / WSS
        ▼
[ Wiiew PWA Frontend ] (https://shr3y4n.github.io/Wiiew/?api=https://xxxxx.trycloudflare.com)
```

1. **ESP32 CSI Sensor**: Verifies network reachability (`192.168.1.102`) via ICMP echo.
2. **RuView Sensing Server**: Runs `v1.src.sensing.ws_server` with `RuView/archive` in `PYTHONPATH`, exposing WebSocket port `8765`.
3. **FastAPI Backend**: Runs `wiiew.backend.main:app` via Uvicorn on port `8000`, monitoring `/api/status`.
4. **Cloudflare Quick Tunnel**: Runs `cloudflared.exe tunnel --url http://127.0.0.1:8000`, dynamically parses the generated `https://xxxxx.trycloudflare.com` URL, and verifies public DNS endpoint resolution.
5. **Wiiew PWA**: Launches the default web browser to the GitHub Pages PWA with the `?api=` query parameter, which is automatically saved to `localStorage`.

---

## Running from Source

Requirements: Python 3.10+ installed with dependencies from `requirements.txt`.

```powershell
cd "D:\COLLEGE\GitHub Projects\Wiiew"
python -m wiiew.launcher
```

---

## Building the Standalone Executable (.exe)

Wiiew Launcher can be compiled into a standalone Windows `.exe` using PyInstaller.

### 1. Install PyInstaller
```powershell
pip install pyinstaller
```

### 2. Build via Script
```powershell
python -m wiiew.launcher.build_exe
```

Or run directly with PyInstaller:
```powershell
pyinstaller --name=WiiewLauncher --onefile --noconsole --clean --paths="." --paths="wiiew/launcher" --hidden-import=wiiew --hidden-import=wiiew.launcher --hidden-import=wiiew.launcher.core --hidden-import=wiiew.launcher.gui --collect-submodules=wiiew.launcher --icon="RuView/v2/crates/wifi-densepose-desktop/icons/icon.ico" wiiew/launcher/__main__.py
```

### 3. Binary Location
The compiled executable will be located at:
```
dist/WiiewLauncher.exe
```

> [!NOTE]
> The compiled executable (`WiiewLauncher.exe`), `build/`, `dist/`, and `*.spec` are excluded in `.gitignore` to keep the Git repository clean.

---

## Key Safety & Process Management Guarantees

- **Exact PID Tracking**: Only processes started directly by this launcher session are tracked in `launcher_pids` and terminated when clicking **STOP WIIEW** or exiting.
- **Pre-Existing Service Preservation**: If RuView (:8765) or FastAPI (:8000) was already running before the launcher was opened, the launcher detects it, marks it as `RUNNING`, and will **NOT** terminate it when stopping services.
- **Zero Unrelated Process Impact**: Never kills unrelated Python processes or other system utilities.
- **Clean Windows Process Tree Termination**: Child processes (e.g. Uvicorn worker threads) are terminated cleanly using `taskkill /F /T /PID`.
- **Dynamic Quick Tunnel Parsing**: The Cloudflare Quick Tunnel URL is parsed dynamically via regex (`https://[a-zA-Z0-9-]+\.trycloudflare\.com`). It is never hardcoded.
