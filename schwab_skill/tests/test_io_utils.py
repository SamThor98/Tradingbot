from __future__ import annotations

from pathlib import Path
from unittest import mock

from _io_utils import atomic_write_json


def test_atomic_write_json_retries_replace_on_permission_error(tmp_path: Path) -> None:
    target = tmp_path / "metrics.json"
    calls = {"n": 0}
    real_replace = __import__("os").replace

    def flaky_replace(src: Path, dst: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(13, "Access is denied")
        real_replace(src, dst)

    with mock.patch("_io_utils.os.replace", side_effect=flaky_replace):
        atomic_write_json(target, {"ok": True})

    assert target.exists()
    assert calls["n"] == 2
