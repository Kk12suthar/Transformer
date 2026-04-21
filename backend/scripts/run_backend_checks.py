from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def run_step(*args: str) -> None:
    cmd = [sys.executable, *args]
    print(f"+ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=BACKEND_ROOT, check=True)


def main() -> None:
    run_step("-m", "compileall", "app")
    run_step("-m", "py_compile", "run.py")
    run_step("-m", "pytest", "-q")
    print("Backend verification suite passed.")


if __name__ == "__main__":
    main()
