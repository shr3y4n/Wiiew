"""
Wiiew Master Launcher.
Automatically verifies/starts the RuView sensing server, then starts the Wiiew PWA server.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUVIEW_DIR = REPO_ROOT / "RuView"
RUVIEW_ARCHIVE = RUVIEW_DIR / "archive"
PYTHON_EXE = sys.executable

RUVIEW_WS_PORT = 8765
RUVIEW_HTTP_PORT = 8080
WIIEW_PORT = 8000


def get_lan_ip() -> str:
    """Detect LAN IP address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.1.1", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a local TCP port is already listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def main():
    print("=" * 60)
    print("                WIIEW — ROOM INTRUSION MONITOR")
    print("=" * 60)

    lan_ip = get_lan_ip()
    ruview_proc = None

    # 1. Check if RuView Sensing Server is already running
    if is_port_open(RUVIEW_WS_PORT):
        print(f"[RuView] Sensing server already running on port {RUVIEW_WS_PORT}.")
    else:
        print(f"[RuView] Starting sensing server on port {RUVIEW_WS_PORT} / {RUVIEW_HTTP_PORT}...")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(RUVIEW_ARCHIVE)

        ruview_proc = subprocess.Popen(
            [PYTHON_EXE, "-m", "v1.src.sensing.ws_server"],
            cwd=str(RUVIEW_DIR),
            env=env,
        )

        # Wait up to 5 seconds for it to bind
        for _ in range(10):
            time.sleep(0.5)
            if is_port_open(RUVIEW_WS_PORT):
                print("[RuView] Sensing server successfully started and listening.")
                break
        else:
            print("[RuView] Warning: Sensing server port not detected yet, continuing...")

    # 2. Add repo root to python path for wiiew module
    sys.path.insert(0, str(REPO_ROOT))

    print("\n" + "-" * 60)
    print(f"  Wiiew PWA is running!")
    print(f"  Desktop PC:       http://localhost:{WIIEW_PORT}")
    print(f"  Mobile Phone:     http://{lan_ip}:{WIIEW_PORT}")
    print("-" * 60)
    print("  Instructions for Phone:")
    print("  1. Connect your phone to your home Wi-Fi (e.g. TP-Link_3648)")
    print(f"  2. Open Chrome/Safari and visit: http://{lan_ip}:{WIIEW_PORT}")
    print("  3. Tap 'Install App' (or 'Add to Home Screen') to install the PWA")
    print("  4. Open Settings to set your phone as the Trusted Device")
    print("-" * 60)
    print("  Press Ctrl+C to stop.\n")

    def handle_signal(sig, frame):
        print("\nShutting down Wiiew...")
        if ruview_proc:
            print("Terminating RuView sensing server...")
            ruview_proc.terminate()
            try:
                ruview_proc.wait(timeout=3)
            except Exception:
                ruview_proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)

    try:
        import uvicorn
        uvicorn.run("wiiew.backend.main:app", host="0.0.0.0", port=WIIEW_PORT, log_level="info")
    except KeyboardInterrupt:
        handle_signal(None, None)
    finally:
        if ruview_proc:
            ruview_proc.terminate()


if __name__ == "__main__":
    main()
