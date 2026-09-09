"""
Wiiew Launcher Package Entrypoint.
Allows running the launcher with: python -m wiiew.launcher or standalone executable.
"""

import sys
from pathlib import Path

# Ensure repo root and launcher directory are on sys.path in both normal and PyInstaller modes
_launcher_dir = Path(__file__).resolve().parent
_repo_root = _launcher_dir.parent.parent

for _p in [str(_repo_root), str(_launcher_dir)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from wiiew.launcher.gui import run_app
except ModuleNotFoundError:
    from gui import run_app

if __name__ == "__main__":
    run_app()

