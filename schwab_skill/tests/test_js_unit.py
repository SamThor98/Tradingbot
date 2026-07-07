"""Bridge the Node-based JS unit tests into pytest.

The dashboard's ES modules under ``webapp/static/`` are tested in-process with
Node's built-in test runner (``tests/js/*.test.mjs``); no bundler or npm
install is required (``webapp/static/package.json`` only marks the tree as
ESM). This wrapper runs the whole JS suite as one pytest case so the standard
``python -m pytest -q`` loop catches frontend regressions, and skips cleanly
on machines without Node.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
JS_TEST_DIR = REPO / "tests" / "js"


def test_js_unit_suite() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; skipping JS unit tests")
    assert node is not None  # narrow Optional for mypy; pytest.skip never returns
    test_files = sorted(JS_TEST_DIR.glob("*.test.mjs"))
    assert test_files, f"no *.test.mjs files found in {JS_TEST_DIR}"
    proc = subprocess.run(
        [node, "--test", *[str(p) for p in test_files]],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(
            "node --test reported failures:\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
