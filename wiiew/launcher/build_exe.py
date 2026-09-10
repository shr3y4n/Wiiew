"""
Build script for Wiiew Launcher standalone Windows executable.
Uses PyInstaller to compile WiiewLauncher.exe without console window.
"""

from __future__ import annotations

import os
import sys
import shutil
from pathlib import Path
import subprocess


def build_launcher() -> Path:
    repo_root = Path(__file__).resolve().parent.parent.parent
    entry_point = repo_root / "wiiew" / "launcher" / "__main__.py"
    icon_ico = repo_root / "RuView" / "v2" / "crates" / "wifi-densepose-desktop" / "icons" / "icon.ico"
    dist_dir = repo_root / "dist"
    build_dir = repo_root / "build"

    print("==================================================")
    print("Building Wiiew Launcher Windows Standalone Executable")
    print("==================================================")
    print(f"Repository Root: {repo_root}")
    print(f"Entry Point:     {entry_point}")
    print(f"Output Directory: {dist_dir}")

    pyinstaller_bin = shutil.which("pyinstaller")
    if pyinstaller_bin:
        cmd = [pyinstaller_bin]
    else:
        cmd = [sys.executable, "-m", "PyInstaller"]

    cmd.extend([
        "--name=WiiewLauncher",
        "--onefile",
        "--noconsole",
        "--clean",
        f"--paths={repo_root}",
        f"--paths={repo_root / 'wiiew' / 'launcher'}",
        "--hidden-import=wiiew",
        "--hidden-import=wiiew.launcher",
        "--hidden-import=wiiew.launcher.core",
        "--hidden-import=wiiew.launcher.gui",
        "--collect-submodules=wiiew.launcher",
        f"--distpath={dist_dir}",
        f"--workpath={build_dir}",
        f"--specpath={repo_root}",
    ])

    if icon_ico.exists():
        print(f"Window Icon:     {icon_ico}")
        cmd.append(f"--icon={icon_ico}")

    cmd.append(str(entry_point))

    print(f"Running command:\n{' '.join(cmd)}\n")
    res = subprocess.run(cmd, cwd=str(repo_root))
    if res.returncode != 0:
        raise RuntimeError(f"PyInstaller build failed with return code {res.returncode}")

    target_exe = dist_dir / "WiiewLauncher.exe"
    if not target_exe.exists():
        raise FileNotFoundError(f"Expected output binary not found at {target_exe}")

    # Persist repo path to AppData and home
    try:
        from wiiew.launcher.core import save_persisted_repo_root
        save_persisted_repo_root(repo_root)
    except Exception:
        pass

    # If desktop copy exists, synchronize it with the fresh build
    desktop_dir = Path(os.path.expanduser("~/Desktop"))
    desktop_exe = desktop_dir / "WiiewLauncher.exe"
    if desktop_exe.exists():
        try:
            shutil.copy2(target_exe, desktop_exe)
            print(f"Updated Desktop Executable: {desktop_exe}")
        except Exception as ex:
            print(f"Notice: Could not copy to desktop exe: {ex}")

    # Create/update clean Windows desktop shortcut
    try:
        ps_cmd = f"""
        $WshShell = New-Object -ComObject WScript.Shell
        $Shortcut = $WshShell.CreateShortcut('{desktop_dir / "Wiiew Launcher.lnk"}')
        $Shortcut.TargetPath = '{target_exe}'
        $Shortcut.WorkingDirectory = '{repo_root}'
        $Shortcut.Description = 'Wiiew Room Intrusion Monitor Launcher'
        if (Test-Path '{icon_ico}') {{ $Shortcut.IconLocation = '{icon_ico}' }}
        $Shortcut.Save()
        """
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], check=False)
        print(f"Created/Updated Desktop Shortcut: {desktop_dir / 'Wiiew Launcher.lnk'}")
    except Exception as ex:
        print(f"Notice: Could not create desktop shortcut: {ex}")

    size_mb = target_exe.stat().st_size / (1024 * 1024)
    print("==================================================")
    print("BUILD SUCCESSFUL!")
    print(f"Executable: {target_exe} ({size_mb:.2f} MB)")
    print("==================================================")
    return target_exe


if __name__ == "__main__":
    build_launcher()
