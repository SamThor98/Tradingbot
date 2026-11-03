from __future__ import annotations

from pathlib import Path

from core.env_local import upsert_env_file
from core.hardware_profile import (
    HostFacts,
    assess_ops_box,
    recommend_profile,
    recommend_workers,
    write_profile_artifact,
)


def test_recommend_workers_um790_class() -> None:
    scan_a, scan_b, research = recommend_workers(physical_cores=8)
    assert scan_a == 6
    assert scan_b == 6
    assert research == 6


def test_recommend_workers_caps_scan_below_thread_count() -> None:
    scan_a, scan_b, research = recommend_workers(physical_cores=16)
    assert scan_a == 8
    assert scan_b == 6
    assert research == 12


def test_recommend_workers_small_box() -> None:
    scan_a, scan_b, research = recommend_workers(physical_cores=2)
    assert scan_a == 2
    assert scan_b == 2
    assert research == 1


def _facts(**overrides: object) -> HostFacts:
    base = dict(
        cpu_name="AMD Ryzen 9 7940HS w/ Radeon 780M Graphics",
        physical_cores=8,
        logical_cpus=16,
        ram_gb=61.8,
        repo_root=r"C:\src\TradingBot",
        repo_bus_type="NVMe",
        ethernet_up=True,
        wifi_up=False,
        git_available=True,
        env_file_present=True,
        token_files_present=True,
    )
    base.update(overrides)
    return HostFacts(**base)  # type: ignore[arg-type]


def test_recommend_profile_warns_on_usb_wifi_only_accepted() -> None:
    profile = recommend_profile(
        _facts(
            repo_root=r"D:\TradingBot",
            repo_bus_type="USB",
            ethernet_up=False,
            wifi_up=True,
        )
    )
    joined = " ".join(profile.warnings)
    notes = " ".join(profile.notes)
    assert "USB" in joined
    assert "Plug the 2.5G" not in joined
    assert "Wi-Fi" in notes
    assert "power saving" in notes
    assert profile.env_updates() == {
        "SCAN_STAGE_A_MAX_WORKERS": "6",
        "SCAN_STAGE_B_MAX_WORKERS": "6",
    }


def test_recommend_profile_offline_warns() -> None:
    profile = recommend_profile(_facts(ethernet_up=False, wifi_up=False))
    assert any("No Ethernet or Wi-Fi" in item for item in profile.warnings)


def test_assess_ops_box_wifi_only_skips_ethernet_cable() -> None:
    readiness = assess_ops_box(_facts(ethernet_up=False, wifi_up=True))
    assert readiness.blockers == []
    joined = " ".join(readiness.next_steps)
    assert "Plug" not in joined
    assert "RJ45" not in joined
    assert "harden_ops_box_wifi.ps1" in joined


def test_assess_ops_box_offline_is_blocker() -> None:
    readiness = assess_ops_box(_facts(ethernet_up=False, wifi_up=False))
    assert "no network" in readiness.blockers


def test_assess_ops_box_blockers_without_secrets() -> None:
    readiness = assess_ops_box(
        _facts(env_file_present=False, token_files_present=False, git_available=False)
    )
    assert "missing .env" in readiness.blockers
    assert "missing Schwab tokens" in readiness.blockers
    assert any("SCAN_STAGE_" in step for step in readiness.next_steps) is False
    assert any("fix_schwab_auth" in step for step in readiness.next_steps)


def test_assess_ops_box_ready_lists_dashboard_and_overnight_job() -> None:
    readiness = assess_ops_box(_facts())
    assert readiness.blockers == []
    joined = " ".join(readiness.next_steps)
    assert "start_local_dashboard.py" in joined
    assert "--max-workers 6" in joined


def test_write_profile_artifact_roundtrip(tmp_path: Path) -> None:
    readiness = assess_ops_box(_facts())
    path = write_profile_artifact(readiness, tmp_path / "hardware_profile.json")
    text = path.read_text(encoding="utf-8")
    assert '"scan_stage_a_workers": 6' in text
    assert "7940HS" in text


def test_apply_ops_box_env_upsert(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=bar\nSCAN_STAGE_A_MAX_WORKERS=4\n", encoding="utf-8")
    profile = recommend_profile(_facts())
    changed = upsert_env_file(env_path, profile.env_updates())
    assert "SCAN_STAGE_A_MAX_WORKERS" in changed
    assert "SCAN_STAGE_B_MAX_WORKERS" in changed
    text = env_path.read_text(encoding="utf-8")
    assert "SCAN_STAGE_A_MAX_WORKERS=6" in text
    assert "SCAN_STAGE_B_MAX_WORKERS=6" in text
    assert "FOO=bar" in text
