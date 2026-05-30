"""Local-dev helper: clone the Kronos ``model/`` package into this directory.

The Docker image vendors this automatically at build time. For running the
service locally without Docker, run::

    python fetch_model_code.py

then start the service with::

    uvicorn app:app --port 8100
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

KRONOS_REPO = "https://github.com/shiyu-coder/Kronos.git"
KRONOS_REF = os.environ.get("KRONOS_REF", "master")
HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "model")


def main() -> int:
    if os.path.isdir(TARGET):
        print(f"model/ already present at {TARGET}; delete it to re-fetch.")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = os.path.join(tmp, "kronos")
        print(f"Cloning {KRONOS_REPO} ({KRONOS_REF})...")
        subprocess.check_call(["git", "clone", KRONOS_REPO, clone_dir])
        subprocess.check_call(["git", "-C", clone_dir, "checkout", KRONOS_REF])
        src = os.path.join(clone_dir, "model")
        if not os.path.isdir(src):
            print("ERROR: model/ not found in cloned repo", file=sys.stderr)
            return 1
        shutil.copytree(src, TARGET)
    print(f"Vendored Kronos model package -> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
