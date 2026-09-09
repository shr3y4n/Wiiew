"""
Wiiew Windows Desktop Launcher GUI.
Modern dark-themed Tkinter interface for starting, stopping, monitoring,
and launching the complete Wiiew CSI intrusion monitoring pipeline.
"""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

try:
    from .core import PUBLIC_FRONTEND_URL, ServiceStatus, WiiewServiceManager
except (ImportError, ValueError):
    from core import PUBLIC_FRONTEND_URL, ServiceStatus, WiiewServiceManager

# Visual Theme Palette (Matches Wiiew Dark Aesthetic)
COLOR_BG = "#0b0e14"
COLOR_CARD = "#12161f"
COLOR_CARD_BORDER = "#1f2633"
COLOR_TEXT_MAIN = "#f5f7fb"
COLOR_TEXT_MUTED = "#8993a3"
COLOR_TEXT_DIM = "#5f6876"
COLOR_ACCENT_GREEN = "#34d399"
COLOR_ACCENT_CYAN = "#6bd5ff"
COLOR_ACCENT_RED = "#ff6675"
COLOR_ACCENT_AMBER = "#fbbf24"
COLOR_CONSOLE_BG = "#07090e"
COLOR_BTN_PRIMARY = "#10b981"
COLOR_BTN_SECONDARY = "#1e2430"


class WiiewLauncherApp:
    """Tkinter-based GUI for Wiiew Launcher."""

    def __init__(self, root: tk.Tk, manager: Optional[WiiewServiceManager] = None) -> None:
        self.root = root
        self.manager = manager or WiiewServiceManager()

        self.root.title("Wiiew Launcher")
        self.root.geometry("640x740")
        self.root.minsize(560, 680)
        self.root.configure(bg=COLOR_BG)

        # Set taskbar / window icon if available
        try:
            icon_ico = self.manager.repo_root / "RuView" / "v2" / "crates" / "wifi-densepose-desktop" / "icons" / "icon.ico"
            if icon_ico.exists():
                self.root.iconbitmap(str(icon_ico))
        except Exception:
            pass

        self._busy = False
        self._init_styles()
        self._build_ui()

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Initial log
        self._append_log("Wiiew Launcher initialized.")
        self._append_log(f"Repository: {self.manager.repo_root}")

    def _init_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(
            "TFrame",
            background=COLOR_BG,
        )
        style.configure(
            "Card.TFrame",
            background=COLOR_CARD,
            relief="solid",
            borderwidth=1,
        )

    def _build_ui(self) -> None:
        # Outer padding frame
        container = tk.Frame(self.root, bg=COLOR_BG, padx=22, pady=18)
        container.pack(fill=tk.BOTH, expand=True)

        # -------------------------------------------------------------------
        # Header Section
        # -------------------------------------------------------------------
        header_frame = tk.Frame(container, bg=COLOR_BG)
        header_frame.pack(fill=tk.X, pady=(0, 16))

        title_lbl = tk.Label(
            header_frame,
            text="WIIEW LAUNCHER",
            font=("Segoe UI", 18, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_BG,
        )
        title_lbl.pack(anchor="w")

        sub_lbl = tk.Label(
            header_frame,
            text="Arduino UNO R4 WiFi & RuView CSI Intrusion Pipeline",
            font=("Segoe UI", 9),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_BG,
        )
        sub_lbl.pack(anchor="w", pady=(2, 0))

        # -------------------------------------------------------------------
        # System Status Cards (2x2 Grid)
        # -------------------------------------------------------------------
        status_box = tk.Frame(container, bg=COLOR_CARD, highlightbackground=COLOR_CARD_BORDER, highlightthickness=1, padx=14, pady=14)
        status_box.pack(fill=tk.X, pady=(0, 14))

        status_heading = tk.Label(
            status_box,
            text="SYSTEM STATUS",
            font=("Segoe UI", 8, "bold"),
            fg=COLOR_TEXT_DIM,
            bg=COLOR_CARD,
        )
        status_heading.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        # Row 1: ESP32 CSI & RuView
        self.lbl_dot_esp32, self.lbl_val_esp32 = self._create_status_row(status_box, row=1, col=0, label="ESP32 CSI", val="Checking...")
        self.lbl_dot_ruview, self.lbl_val_ruview = self._create_status_row(status_box, row=1, col=1, label="RuView", val="Stopped")

        # Row 2: FastAPI & Cloudflare
        self.lbl_dot_fastapi, self.lbl_val_fastapi = self._create_status_row(status_box, row=2, col=0, label="FastAPI", val="Stopped")
        self.lbl_dot_cf, self.lbl_val_cf = self._create_status_row(status_box, row=2, col=1, label="Cloudflare", val="Offline")

        status_box.grid_columnconfigure(0, weight=1)
        status_box.grid_columnconfigure(1, weight=1)

        # -------------------------------------------------------------------
        # Endpoints Panel
        # -------------------------------------------------------------------
        endpoints_box = tk.Frame(container, bg=COLOR_CARD, highlightbackground=COLOR_CARD_BORDER, highlightthickness=1, padx=14, pady=12)
        endpoints_box.pack(fill=tk.X, pady=(0, 14))

        # Backend URL
        b_frame = tk.Frame(endpoints_box, bg=COLOR_CARD)
        b_frame.pack(fill=tk.X, pady=2)
        tk.Label(b_frame, text="Backend:", font=("Segoe UI", 9, "bold"), fg=COLOR_TEXT_MUTED, bg=COLOR_CARD, width=10, anchor="w").pack(side=tk.LEFT)
        self.ent_backend = tk.Entry(b_frame, font=("Consolas", 9), bg=COLOR_CONSOLE_BG, fg=COLOR_TEXT_MAIN, bd=0, relief=tk.FLAT)
        self.ent_backend.insert(0, "http://127.0.0.1:8000")
        self.ent_backend.config(state="readonly")
        self.ent_backend.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        btn_copy_backend = tk.Button(b_frame, text="Copy", font=("Segoe UI", 8), bg=COLOR_BTN_SECONDARY, fg=COLOR_TEXT_MAIN, bd=0, padx=8, pady=2, command=self._copy_backend)
        btn_copy_backend.pack(side=tk.RIGHT)

        # Public URL
        p_frame = tk.Frame(endpoints_box, bg=COLOR_CARD)
        p_frame.pack(fill=tk.X, pady=(6, 2))
        tk.Label(p_frame, text="Public URL:", font=("Segoe UI", 9, "bold"), fg=COLOR_TEXT_MUTED, bg=COLOR_CARD, width=10, anchor="w").pack(side=tk.LEFT)
        self.ent_public = tk.Entry(p_frame, font=("Consolas", 9), bg=COLOR_CONSOLE_BG, fg=COLOR_ACCENT_CYAN, bd=0, relief=tk.FLAT)
        self.ent_public.insert(0, "Not running")
        self.ent_public.config(state="readonly")
        self.ent_public.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.btn_copy_public = tk.Button(p_frame, text="Copy", font=("Segoe UI", 8), bg=COLOR_BTN_SECONDARY, fg=COLOR_TEXT_MAIN, bd=0, padx=8, pady=2, command=self._copy_public)
        self.btn_copy_public.pack(side=tk.RIGHT)

        # -------------------------------------------------------------------
        # Error Banner (Hidden by default)
        # -------------------------------------------------------------------
        self.error_frame = tk.Frame(container, bg="#2b1115", highlightbackground=COLOR_ACCENT_RED, highlightthickness=1, padx=12, pady=8)
        self.lbl_error = tk.Label(self.error_frame, text="", font=("Segoe UI", 9, "bold"), fg=COLOR_ACCENT_RED, bg="#2b1115", wraplength=500, justify="left")
        self.lbl_error.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.btn_retry = tk.Button(self.error_frame, text="Retry", font=("Segoe UI", 8, "bold"), bg=COLOR_ACCENT_RED, fg="#0b0e14", bd=0, padx=10, command=self._on_start)
        self.btn_retry.pack(side=tk.RIGHT)

        # -------------------------------------------------------------------
        # Action Buttons Row
        # -------------------------------------------------------------------
        btn_row = tk.Frame(container, bg=COLOR_BG)
        btn_row.pack(fill=tk.X, pady=(0, 14))

        self.btn_start = tk.Button(
            btn_row,
            text="▶  START WIIEW",
            font=("Segoe UI", 10, "bold"),
            bg=COLOR_BTN_PRIMARY,
            fg="#071b12",
            activebackground="#059669",
            bd=0,
            padx=16,
            pady=10,
            cursor="hand2",
            command=self._on_start,
        )
        self.btn_start.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.btn_stop = tk.Button(
            btn_row,
            text="⏹  STOP WIIEW",
            font=("Segoe UI", 10, "bold"),
            bg=COLOR_BTN_SECONDARY,
            fg=COLOR_TEXT_MAIN,
            activebackground="#2a3346",
            bd=0,
            padx=14,
            pady=10,
            cursor="hand2",
            command=self._on_stop,
        )
        self.btn_stop.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3)

        self.btn_restart = tk.Button(
            btn_row,
            text="⟳  RESTART",
            font=("Segoe UI", 10, "bold"),
            bg=COLOR_BTN_SECONDARY,
            fg=COLOR_TEXT_MAIN,
            activebackground="#2a3346",
            bd=0,
            padx=14,
            pady=10,
            cursor="hand2",
            command=self._on_restart,
        )
        self.btn_restart.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3)

        self.btn_open = tk.Button(
            btn_row,
            text="🌐  OPEN WIIEW",
            font=("Segoe UI", 10, "bold"),
            bg="#1e3a8a",
            fg="#93c5fd",
            activebackground="#1d4ed8",
            bd=0,
            padx=14,
            pady=10,
            cursor="hand2",
            command=self._on_open,
        )
        self.btn_open.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        # -------------------------------------------------------------------
        # Activity & Log Console
        # -------------------------------------------------------------------
        log_header = tk.Frame(container, bg=COLOR_BG)
        log_header.pack(fill=tk.X, pady=(0, 6))
        tk.Label(log_header, text="ACTIVITY LOG", font=("Segoe UI", 8, "bold"), fg=COLOR_TEXT_DIM, bg=COLOR_BG).pack(side=tk.LEFT)
        btn_clear = tk.Button(log_header, text="Clear", font=("Segoe UI", 8), fg=COLOR_TEXT_DIM, bg=COLOR_BG, bd=0, activebackground=COLOR_BG, command=self._clear_log)
        btn_clear.pack(side=tk.RIGHT)

        log_frame = tk.Frame(container, bg=COLOR_CONSOLE_BG, highlightbackground=COLOR_CARD_BORDER, highlightthickness=1)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.txt_log = tk.Text(
            log_frame,
            font=("Consolas", 9),
            bg=COLOR_CONSOLE_BG,
            fg="#94a3b8",
            insertbackground=COLOR_TEXT_MAIN,
            bd=0,
            padx=10,
            pady=10,
            wrap=tk.WORD,
        )
        scrollbar = tk.Scrollbar(log_frame, command=self.txt_log.yview, bg=COLOR_CONSOLE_BG)
        self.txt_log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def _create_status_row(self, parent: tk.Widget, row: int, col: int, label: str, val: str):
        subframe = tk.Frame(parent, bg=COLOR_CARD, pady=4)
        subframe.grid(row=row, column=col, sticky="w", padx=10)

        dot = tk.Label(subframe, text="●", font=("Segoe UI", 11), fg=COLOR_TEXT_DIM, bg=COLOR_CARD)
        dot.pack(side=tk.LEFT, padx=(0, 6))

        lbl = tk.Label(subframe, text=f"{label}:", font=("Segoe UI", 9, "bold"), fg=COLOR_TEXT_MUTED, bg=COLOR_CARD)
        lbl.pack(side=tk.LEFT, padx=(0, 6))

        val_lbl = tk.Label(subframe, text=val, font=("Segoe UI", 9), fg=COLOR_TEXT_MAIN, bg=COLOR_CARD)
        val_lbl.pack(side=tk.LEFT)

        return dot, val_lbl

    # -----------------------------------------------------------------------
    # Logging & Status Updates (Thread-Safe)
    # -----------------------------------------------------------------------

    def _append_log(self, message: str) -> None:
        def _insert():
            self.txt_log.insert(tk.END, message + "\n")
            self.txt_log.see(tk.END)
        self.root.after(0, _insert)

    def _clear_log(self) -> None:
        self.txt_log.delete("1.0", tk.END)

    def _update_ui_status(self, status: ServiceStatus) -> None:
        def _apply():
            # ESP32
            if status.esp32 == "CONNECTED":
                self.lbl_dot_esp32.config(fg=COLOR_ACCENT_GREEN)
                self.lbl_val_esp32.config(text="Connected", fg=COLOR_ACCENT_GREEN)
            elif status.esp32 == "OFFLINE":
                self.lbl_dot_esp32.config(fg=COLOR_ACCENT_RED)
                self.lbl_val_esp32.config(text="Offline", fg=COLOR_ACCENT_RED)
            else:
                self.lbl_dot_esp32.config(fg=COLOR_ACCENT_AMBER)
                self.lbl_val_esp32.config(text="Checking...", fg=COLOR_ACCENT_AMBER)

            # RuView
            if status.ruview == "RUNNING":
                self.lbl_dot_ruview.config(fg=COLOR_ACCENT_GREEN)
                self.lbl_val_ruview.config(text="Running (:8765)", fg=COLOR_ACCENT_GREEN)
            elif status.ruview == "STARTING":
                self.lbl_dot_ruview.config(fg=COLOR_ACCENT_AMBER)
                self.lbl_val_ruview.config(text="Starting...", fg=COLOR_ACCENT_AMBER)
            elif status.ruview == "ERROR":
                self.lbl_dot_ruview.config(fg=COLOR_ACCENT_RED)
                self.lbl_val_ruview.config(text="Error", fg=COLOR_ACCENT_RED)
            else:
                self.lbl_dot_ruview.config(fg=COLOR_TEXT_DIM)
                self.lbl_val_ruview.config(text="Stopped", fg=COLOR_TEXT_MUTED)

            # FastAPI
            if status.fastapi == "RUNNING":
                self.lbl_dot_fastapi.config(fg=COLOR_ACCENT_GREEN)
                self.lbl_val_fastapi.config(text="Running (:8000)", fg=COLOR_ACCENT_GREEN)
            elif status.fastapi == "STARTING":
                self.lbl_dot_fastapi.config(fg=COLOR_ACCENT_AMBER)
                self.lbl_val_fastapi.config(text="Starting...", fg=COLOR_ACCENT_AMBER)
            elif status.fastapi == "ERROR":
                self.lbl_dot_fastapi.config(fg=COLOR_ACCENT_RED)
                self.lbl_val_fastapi.config(text="Error", fg=COLOR_ACCENT_RED)
            else:
                self.lbl_dot_fastapi.config(fg=COLOR_TEXT_DIM)
                self.lbl_val_fastapi.config(text="Stopped", fg=COLOR_TEXT_MUTED)

            # Cloudflare
            if status.cloudflare == "ONLINE":
                self.lbl_dot_cf.config(fg=COLOR_ACCENT_GREEN)
                self.lbl_val_cf.config(text="Online", fg=COLOR_ACCENT_GREEN)
            elif status.cloudflare == "STARTING":
                self.lbl_dot_cf.config(fg=COLOR_ACCENT_AMBER)
                self.lbl_val_cf.config(text="Starting...", fg=COLOR_ACCENT_AMBER)
            elif status.cloudflare == "ERROR":
                self.lbl_dot_cf.config(fg=COLOR_ACCENT_RED)
                self.lbl_val_cf.config(text="Error", fg=COLOR_ACCENT_RED)
            else:
                self.lbl_dot_cf.config(fg=COLOR_TEXT_DIM)
                self.lbl_val_cf.config(text="Offline", fg=COLOR_TEXT_MUTED)

            # Public URL
            self.ent_public.config(state="normal")
            self.ent_public.delete(0, tk.END)
            self.ent_public.insert(0, status.public_url or "Not running")
            self.ent_public.config(state="readonly")

            # Error banner
            if status.last_error:
                self.lbl_error.config(text=f"⚠ {status.last_error}")
                self.error_frame.pack(fill=tk.X, pady=(0, 10), before=self.btn_start.master)
            else:
                self.error_frame.pack_forget()

        self.root.after(0, _apply)

    # -----------------------------------------------------------------------
    # User Actions (Async Worker Threads)
    # -----------------------------------------------------------------------

    def _on_start(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_start.config(state="disabled")

        def _worker():
            try:
                self.manager.start_all(log_cb=self._append_log, status_cb=self._update_ui_status)
            finally:
                self._busy = False
                self.root.after(0, lambda: self.btn_start.config(state="normal"))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_stop(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_stop.config(state="disabled")

        def _worker():
            try:
                self.manager.stop_all(log_cb=self._append_log, status_cb=self._update_ui_status)
            finally:
                self._busy = False
                self.root.after(0, lambda: self.btn_stop.config(state="normal"))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_restart(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_restart.config(state="disabled")

        def _worker():
            try:
                self.manager.restart_all(log_cb=self._append_log, status_cb=self._update_ui_status)
            finally:
                self._busy = False
                self.root.after(0, lambda: self.btn_restart.config(state="normal"))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_open(self) -> None:
        self.manager.open_wiiew()

    def _copy_backend(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append("http://127.0.0.1:8000")
        self._append_log("Copied backend URL to clipboard.")

    def _copy_public(self) -> None:
        pub = self.manager.status.public_url
        if pub:
            self.root.clipboard_clear()
            self.root.clipboard_append(pub)
            self._append_log(f"Copied public URL to clipboard: {pub}")
        else:
            self._append_log("No public URL available yet (tunnel offline).")

    def _on_close(self) -> None:
        # If launcher started processes, stop them before exiting
        if self.manager.launcher_procs:
            if messagebox.askyesno("Exit Wiiew Launcher", "Stop running Wiiew services before exiting?"):
                self.manager.stop_all()
        self.root.destroy()


def run_app() -> None:
    root = tk.Tk()
    app = WiiewLauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    run_app()
