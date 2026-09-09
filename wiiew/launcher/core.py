"""
Wiiew Service Manager Core.
Orchestrates the lifecycle of:
1. ESP32 CSI sensor health check (192.168.1.102 / port 5005)
2. RuView WebSocket sensing server (:8765)
3. Wiiew FastAPI backend server (:8000)
4. Cloudflare Quick Tunnel (cloudflared.exe -> trycloudflare.com)
5. PWA Frontend automated launch with dynamic tunnel URL
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set


CLOUDFLARE_URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
PUBLIC_FRONTEND_URL = "https://shr3y4n.github.io/Wiiew/"


@dataclass
class ServiceStatus:
    esp32: str = "CHECKING"       # CONNECTED, OFFLINE, CHECKING
    ruview: str = "STOPPED"       # RUNNING, STOPPED, STARTING, ERROR
    fastapi: str = "STOPPED"      # RUNNING, STOPPED, STARTING, ERROR
    cloudflare: str = "OFFLINE"   # ONLINE, OFFLINE, STARTING, ERROR
    backend_url: str = "http://127.0.0.1:8000"
    public_url: str = ""
    last_error: str = ""


def find_python_executable(explicit: Optional[str] = None) -> str:
    """Find the Python executable, accounting for PyInstaller frozen mode and PATH."""
    if explicit:
        return explicit
    env_py = os.environ.get("WIIEW_PYTHON")
    if env_py and Path(env_py).exists():
        return env_py
    if not getattr(sys, "frozen", False):
        return sys.executable
    # In PyInstaller frozen executable, sys.executable is the launcher exe itself.
    # Search PATH and common Windows installation paths for Python.
    for cmd in ["python", "py", "python3"]:
        found = shutil.which(cmd)
        if found:
            return found
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidates = list(Path(local_app_data).glob("Programs/Python/Python*/python.exe")) + \
                     list(Path(local_app_data).glob("Python/pythoncore-*/python.exe"))
        if candidates:
            return str(candidates[-1])
    return "python"


class WiiewServiceManager:
    """
    Manages background services for Wiiew.
    Guarantees:
    - Never terminates unrelated Python processes or pre-existing instances.
    - Tracks exact PIDs started by this launcher.
    - Dynamically resolves and verifies Cloudflare Quick Tunnel URL.
    """

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        cloudflared_path: Optional[Path] = None,
        python_executable: Optional[str] = None,
    ) -> None:
        if repo_root is not None:
            self.repo_root = Path(repo_root).resolve()
        else:
            if getattr(sys, "frozen", False):
                exe_dir = Path(sys.executable).resolve().parent
                if (exe_dir / "wiiew").is_dir():
                    self.repo_root = exe_dir
                elif (exe_dir.parent / "wiiew").is_dir():
                    self.repo_root = exe_dir.parent
                else:
                    self.repo_root = exe_dir
            else:
                # Default to repo root: 3 levels up from this file (wiiew/launcher/core.py -> repo_root)
                self.repo_root = Path(__file__).resolve().parent.parent.parent

        self.cloudflared_path = (
            Path(cloudflared_path).resolve()
            if cloudflared_path is not None
            else (self.repo_root / "cloudflared.exe")
        )
        self.python_executable = find_python_executable(python_executable)

        self.status = ServiceStatus()
        self.launcher_procs: Dict[str, subprocess.Popen] = {}
        self.launcher_pids: Dict[str, int] = {}
        self.pre_existing: Set[str] = set()

        self._lock = threading.Lock()
        self._start_time: Optional[float] = None
        self._is_starting = False
        self._is_stopping = False

    # -----------------------------------------------------------------------
    # Logging & Formatting
    # -----------------------------------------------------------------------

    def format_log_message(self, message: str) -> str:
        """Format timestamped launcher log entry: [MM:SS] Message."""
        if self._start_time is None:
            elapsed_sec = 0
        else:
            elapsed_sec = int(time.time() - self._start_time)
        mins = elapsed_sec // 60
        secs = elapsed_sec % 60
        return f"[{mins:02d}:{secs:02d}] {message}"

    def _log(self, message: str, callback: Optional[Callable[[str], None]] = None) -> None:
        formatted = self.format_log_message(message)
        if callback:
            try:
                callback(formatted)
            except Exception:
                pass
        print(formatted)

    def _notify_status(self, callback: Optional[Callable[[ServiceStatus], None]] = None) -> None:
        if callback:
            try:
                callback(self.status)
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # Network & Health Check Helpers
    # -----------------------------------------------------------------------

    def check_esp32_reachability(self, host: str = "192.168.1.102", timeout_ms: int = 600) -> bool:
        """Check whether ESP32-S3 sensor is reachable via ICMP ping."""
        try:
            res = subprocess.run(
                ["ping", "-n", "1", "-w", str(timeout_ms), host],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.8,
            )
            return res.returncode == 0
        except Exception:
            return False

    def is_port_open(self, port: int, host: str = "127.0.0.1", timeout: float = 0.8) -> bool:
        """Check whether a TCP port is listening locally."""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def check_http_status(self, url: str, timeout: float = 2.0) -> bool:
        """Check whether an HTTP endpoint returns status 200 OK."""
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "WiiewLauncher/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def extract_cloudflare_url(self, text: str) -> Optional[str]:
        """Extract https://xxxxx.trycloudflare.com from text line."""
        match = CLOUDFLARE_URL_PATTERN.search(text)
        if match:
            return match.group(0)
        return None

    def detect_pre_existing_cloudflare_url(self) -> Optional[str]:
        """Check if an active cloudflared tunnel is already running and query its metric endpoint."""
        for port in range(20240, 20265):
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/metrics", headers={"User-Agent": "Wiiew"})
                with urllib.request.urlopen(req, timeout=0.3) as resp:
                    content = resp.read().decode("utf-8", errors="ignore")
                    for line in content.splitlines():
                        if "userHostname" in line:
                            found = self.extract_cloudflare_url(line)
                            if found:
                                if self.check_http_status(f"{found}/api/status", timeout=2.5):
                                    return found
            except Exception:
                continue
        return None

    # -----------------------------------------------------------------------
    # Lifecycle: Start All
    # -----------------------------------------------------------------------

    def start_all(
        self,
        log_cb: Optional[Callable[[str], None]] = None,
        status_cb: Optional[Callable[[ServiceStatus], None]] = None,
    ) -> bool:
        """
        Orchestrate startup in exact order:
        1. ESP32 reachability check
        2. RuView sensing server (:8765)
        3. Wiiew FastAPI backend (:8000)
        4. Cloudflare Quick Tunnel (:8000 -> https://xxxxx.trycloudflare.com)
        5. Verify public tunnel endpoint
        6. Launch Wiiew PWA with URL query parameter
        """
        with self._lock:
            if self._is_starting:
                return False
            self._is_starting = True

        self._start_time = time.time()
        self.status.last_error = ""

        try:
            # 1. ESP32 Check
            self.status.esp32 = "CHECKING"
            self._notify_status(status_cb)
            self._log("Checking ESP32 CSI sensor at 192.168.1.102...", log_cb)
            esp_ok = self.check_esp32_reachability("192.168.1.102")
            if esp_ok:
                self.status.esp32 = "CONNECTED"
                self._log("ESP32 reachable (192.168.1.102)", log_cb)
            else:
                self.status.esp32 = "OFFLINE"
                self._log("Note: ESP32 sensor not detected on LAN (monitoring can still start)", log_cb)
            self._notify_status(status_cb)

            # 2. RuView Start
            self.status.ruview = "STARTING"
            self._notify_status(status_cb)
            if self.is_port_open(8765):
                self._log("RuView already running on port 8765 (pre-existing)", log_cb)
                self.pre_existing.add("ruview")
                self.status.ruview = "RUNNING"
            else:
                self._log("Starting RuView...", log_cb)
                ruview_dir = self.repo_root / "RuView"
                ruview_archive = ruview_dir / "archive"
                env = os.environ.copy()
                env["PYTHONPATH"] = str(ruview_archive)

                proc_ruview = subprocess.Popen(
                    [self.python_executable, "-m", "v1.src.sensing.ws_server"],
                    cwd=str(ruview_dir),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.launcher_procs["ruview"] = proc_ruview
                self.launcher_pids["ruview"] = proc_ruview.pid

                # Wait for port 8765 (up to 15s)
                started = False
                for _ in range(30):
                    if self.is_port_open(8765):
                        started = True
                        break
                    time.sleep(0.5)

                if not started:
                    self.status.ruview = "ERROR"
                    self.status.last_error = "RuView failed to start"
                    self._log("ERROR: RuView failed to start (port 8765 didn't open)", log_cb)
                    self._notify_status(status_cb)
                    return False

                self.status.ruview = "RUNNING"
                self._log("RuView listening on :8765", log_cb)
            self._notify_status(status_cb)

            # 3. FastAPI Start
            self.status.fastapi = "STARTING"
            self._notify_status(status_cb)
            if self.is_port_open(8000) and self.check_http_status("http://127.0.0.1:8000/api/status"):
                self._log("FastAPI already running on port 8000 (pre-existing)", log_cb)
                self.pre_existing.add("fastapi")
                self.status.fastapi = "RUNNING"
            elif self.is_port_open(8000):
                self.status.fastapi = "ERROR"
                self.status.last_error = "Port 8000 is already in use by another application"
                self._log("ERROR: Port 8000 is already in use by another application", log_cb)
                self._notify_status(status_cb)
                return False
            else:
                self._log("Starting FastAPI...", log_cb)
                proc_fastapi = subprocess.Popen(
                    [
                        self.python_executable,
                        "-m",
                        "uvicorn",
                        "wiiew.backend.main:app",
                        "--host",
                        "0.0.0.0",
                        "--port",
                        "8000",
                    ],
                    cwd=str(self.repo_root),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.launcher_procs["fastapi"] = proc_fastapi
                self.launcher_pids["fastapi"] = proc_fastapi.pid

                # Wait for http://127.0.0.1:8000/api/status (up to 15s)
                api_ok = False
                for _ in range(30):
                    if self.check_http_status("http://127.0.0.1:8000/api/status"):
                        api_ok = True
                        break
                    time.sleep(0.5)

                if not api_ok:
                    self.status.fastapi = "ERROR"
                    self.status.last_error = "FastAPI failed to start"
                    self._log("ERROR: FastAPI failed to start (/api/status did not respond)", log_cb)
                    self._notify_status(status_cb)
                    return False

                self.status.fastapi = "RUNNING"
                self._log("FastAPI online at http://127.0.0.1:8000", log_cb)
            self._notify_status(status_cb)

            # 4. Cloudflare Start
            self.status.cloudflare = "STARTING"
            self._notify_status(status_cb)

            # Check if a working Cloudflare tunnel is already active
            pre_cf_url = self.detect_pre_existing_cloudflare_url()
            if pre_cf_url:
                self._log("Cloudflare tunnel already running (pre-existing)", log_cb)
                self.pre_existing.add("cloudflare")
                self.status.public_url = pre_cf_url
                self.status.cloudflare = "ONLINE"
                self._log("Tunnel online", log_cb)
                self._log(f"Public URL: {pre_cf_url}", log_cb)
                self._notify_status(status_cb)
                self._log("Wiiew ready", log_cb)
                self.open_wiiew(pre_cf_url)
                self._log(f"Opened Wiiew in browser: {PUBLIC_FRONTEND_URL}?api={pre_cf_url}", log_cb)
                return True

            if not self.cloudflared_path.exists():
                # Check PATH as fallback
                which_cf = subprocess.run(["where", "cloudflared"], stdout=subprocess.PIPE, text=True)
                if which_cf.returncode == 0:
                    cf_bin = which_cf.stdout.strip().splitlines()[0]
                else:
                    self.status.cloudflare = "ERROR"
                    self.status.last_error = f"cloudflared.exe not found at {self.cloudflared_path}"
                    self._log(f"ERROR: {self.status.last_error}", log_cb)
                    self._notify_status(status_cb)
                    return False
            else:
                cf_bin = str(self.cloudflared_path)

            self._log("Starting Cloudflare...", log_cb)
            proc_cf = subprocess.Popen(
                [cf_bin, "tunnel", "--url", "http://127.0.0.1:8000"],
                cwd=str(self.repo_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self.launcher_procs["cloudflare"] = proc_cf
            self.launcher_pids["cloudflare"] = proc_cf.pid

            # Parse tunnel URL from output
            extracted_url: Optional[str] = None
            start_cf_time = time.time()

            while time.time() - start_cf_time < 30.0:
                if proc_cf.poll() is not None:
                    break
                line = proc_cf.stdout.readline() if proc_cf.stdout else ""
                if line:
                    found = self.extract_cloudflare_url(line)
                    if found:
                        extracted_url = found
                        break
                else:
                    time.sleep(0.1)

            if not extracted_url:
                self.status.cloudflare = "ERROR"
                self.status.last_error = "Cloudflare tunnel failed to start"
                self._log("ERROR: Cloudflare tunnel failed to start (no URL received)", log_cb)
                self._notify_status(status_cb)
                return False

            self.status.public_url = extracted_url
            self.status.cloudflare = "ONLINE"
            self._log("Tunnel online", log_cb)
            self._log(f"Public URL: {extracted_url}", log_cb)
            self._notify_status(status_cb)

            # 5. Verify public tunnel endpoint (DNS propagation retry up to 15s)
            self._log("Verifying public endpoint reachability...", log_cb)
            verified = False
            for _ in range(15):
                if self.check_http_status(f"{extracted_url}/api/status", timeout=2.0):
                    verified = True
                    break
                time.sleep(1.0)

            if verified:
                self._log("Public tunnel endpoint verified (HTTP 200)", log_cb)
            else:
                self._log("Note: Tunnel active (public DNS may take a few seconds to resolve)", log_cb)

            # 6. Wiiew Ready & Launch
            self._log("Wiiew ready", log_cb)
            self.open_wiiew(extracted_url)
            self._log(f"Opened Wiiew in browser: {PUBLIC_FRONTEND_URL}?api={extracted_url}", log_cb)
            return True

        except Exception as ex:
            self.status.last_error = str(ex)
            self._log(f"Unexpected error during startup: {ex}", log_cb)
            return False
        finally:
            with self._lock:
                self._is_starting = False

    # -----------------------------------------------------------------------
    # Lifecycle: Stop All
    # -----------------------------------------------------------------------

    def stop_all(
        self,
        log_cb: Optional[Callable[[str], None]] = None,
        status_cb: Optional[Callable[[ServiceStatus], None]] = None,
    ) -> None:
        """
        Stop only launcher-owned processes.
        Does NOT terminate pre-existing processes or unrelated Python instances.
        """
        with self._lock:
            if self._is_stopping:
                return
            self._is_stopping = True

        try:
            self._log("Stopping Wiiew services...", log_cb)

            # 1. Stop Cloudflare tunnel
            if "cloudflare" in self.launcher_procs:
                proc = self.launcher_procs.pop("cloudflare")
                pid = self.launcher_pids.pop("cloudflare", None)
                if pid:
                    self._log(f"Stopping Cloudflare tunnel (PID {pid})...", log_cb)
                    self._kill_process_tree(pid)
                self.status.cloudflare = "OFFLINE"
                self.status.public_url = ""
            elif "cloudflare" in self.pre_existing:
                self._log("Preserved pre-existing Cloudflare tunnel (not started by launcher)", log_cb)

            # 2. Stop FastAPI (only if launcher-owned)
            if "fastapi" in self.launcher_procs:
                proc = self.launcher_procs.pop("fastapi")
                pid = self.launcher_pids.pop("fastapi", None)
                if pid:
                    self._log(f"Stopping FastAPI (PID {pid})...", log_cb)
                    self._kill_process_tree(pid)
                self.status.fastapi = "STOPPED"
            elif "fastapi" in self.pre_existing:
                self._log("Preserved pre-existing FastAPI server (not started by launcher)", log_cb)

            # 3. Stop RuView (only if launcher-owned)
            if "ruview" in self.launcher_procs:
                proc = self.launcher_procs.pop("ruview")
                pid = self.launcher_pids.pop("ruview", None)
                if pid:
                    self._log(f"Stopping RuView sensing server (PID {pid})...", log_cb)
                    self._kill_process_tree(pid)
                self.status.ruview = "STOPPED"
            elif "ruview" in self.pre_existing:
                self._log("Preserved pre-existing RuView server (not started by launcher)", log_cb)

            self.pre_existing.clear()
            self._log("All launcher-owned services stopped.", log_cb)
            self._notify_status(status_cb)

        finally:
            with self._lock:
                self._is_stopping = False

    def restart_all(
        self,
        log_cb: Optional[Callable[[str], None]] = None,
        status_cb: Optional[Callable[[ServiceStatus], None]] = None,
    ) -> bool:
        """Stop launcher-owned processes and start everything fresh."""
        self._log("Restarting Wiiew services...", log_cb)
        self.stop_all(log_cb, status_cb)
        time.sleep(1.5)
        return self.start_all(log_cb, status_cb)

    def open_wiiew(self, public_url: Optional[str] = None) -> None:
        """Open the Wiiew PWA in the default browser with the dynamic tunnel URL."""
        url_to_use = public_url or self.status.public_url
        if url_to_use:
            target = f"{PUBLIC_FRONTEND_URL}?api={url_to_use}"
        else:
            target = f"{PUBLIC_FRONTEND_URL}?api=http://127.0.0.1:8000"
        try:
            webbrowser.open(target)
        except Exception:
            pass

    def _kill_process_tree(self, pid: int) -> None:
        """Gracefully terminate a process tree on Windows via taskkill."""
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
            )
        except Exception:
            pass
