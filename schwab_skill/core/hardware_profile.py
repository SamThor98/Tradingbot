"""Recommend local ops-box knobs from host facts (no secrets, no live trading).

Scan workers stay capped because Stage A/B are Schwab-rate-limited, not CPU-limited.
Research workers scale with physical cores for overnight pandas/subprocess backtests.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

SKILL_DIR = Path(__file__).resolve().parent.parent

# Live scanner: extra threads past this mostly produce Schwab 429s.
SCAN_STAGE_A_WORKER_CAP = 8
SCAN_STAGE_B_WORKER_CAP = 6
# Overnight chunk subprocesses: each is a heavy pandas process.
RESEARCH_WORKER_CAP = 12
SCAN_WORKER_FLOOR = 2


@dataclass(frozen=True)
class HostFacts:
    cpu_name: str
    physical_cores: int
    logical_cpus: int
    ram_gb: float
    repo_root: str
    repo_bus_type: str
    ethernet_up: bool
    wifi_up: bool
    git_available: bool
    env_file_present: bool
    token_files_present: bool


@dataclass(frozen=True)
class HardwareProfile:
    scan_stage_a_workers: int
    scan_stage_b_workers: int
    research_max_workers: int
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    facts: HostFacts | None = None

    def env_updates(self) -> dict[str, str]:
        return {
            "SCAN_STAGE_A_MAX_WORKERS": str(self.scan_stage_a_workers),
            "SCAN_STAGE_B_MAX_WORKERS": str(self.scan_stage_b_workers),
        }


@dataclass
class OpsBoxReadiness:
    profile: HardwareProfile
    blockers: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "scan_stage_a_workers": self.profile.scan_stage_a_workers,
            "scan_stage_b_workers": self.profile.scan_stage_b_workers,
            "research_max_workers": self.profile.research_max_workers,
            "warnings": list(self.profile.warnings),
            "notes": list(self.profile.notes),
            "blockers": list(self.blockers),
            "next_steps": list(self.next_steps),
        }
        if self.profile.facts is not None:
            payload["facts"] = asdict(self.profile.facts)
        return payload


def recommend_workers(*, physical_cores: int) -> tuple[int, int, int]:
    """Map physical cores to scan A/B and research worker counts.

    Leaves two cores for Windows + the live dashboard. Caps scan workers so
    a 16-thread iGPU box does not stampede Schwab.
    """
    cores = max(1, int(physical_cores))
    if cores >= 4:
        headroom = cores - 2
    else:
        headroom = 1
    if cores < 2:
        scan_a = 1
    elif cores < 4:
        scan_a = SCAN_WORKER_FLOOR
    else:
        scan_a = min(SCAN_STAGE_A_WORKER_CAP, max(SCAN_WORKER_FLOOR, headroom))
    scan_b = min(SCAN_STAGE_B_WORKER_CAP, scan_a)
    research = min(RESEARCH_WORKER_CAP, headroom)
    return scan_a, scan_b, research


def recommend_profile(facts: HostFacts) -> HardwareProfile:
    scan_a, scan_b, research = recommend_workers(physical_cores=facts.physical_cores)
    warnings: list[str] = []
    notes: list[str] = [
        "Do not run a full-universe multi-era backtest in the same process as the dashboard.",
        "Schedule research jobs after the close; keep SCAN_STAGE_* modest during RTH.",
        "A GPU does not speed pandas backtests. Keep PROB_RANK and plugins in shadow.",
    ]
    bus = (facts.repo_bus_type or "unknown").strip().upper()
    if bus in {"USB", "USB3", "USB4", "SD", "FILE BACKED VIRTUAL"}:
        warnings.append(
            f"Repo is on a {facts.repo_bus_type} volume ({facts.repo_root}). "
            "Copy it to the internal NVMe (e.g. C:\\src\\TradingBot) before treating this as the ops box."
        )
    if not facts.ethernet_up and facts.wifi_up:
        notes.append(
            "Ops network is Wi-Fi (Ethernet unused on this host). Keep both antennas on and "
            "disable Wi-Fi adapter power saving so the always-on bot does not drop overnight."
        )
    elif not facts.ethernet_up and not facts.wifi_up:
        warnings.append("No Ethernet or Wi-Fi is up. The dashboard cannot reach Schwab.")
    if facts.ram_gb and facts.ram_gb < 16:
        warnings.append(f"RAM is {facts.ram_gb:.0f} GB. Overnight multi-era chunks prefer 32 GB+.")
    if not facts.git_available:
        warnings.append("Git is not on PATH. Install Git for Windows so Cursor source control works.")
    if not facts.env_file_present:
        warnings.append("schwab_skill/.env is missing. Copy it from the old PC (do not commit it).")
    if not facts.token_files_present:
        warnings.append(
            "Encrypted Schwab token files are missing. Copy tokens_market.enc and tokens_account.enc "
            "or re-run python scripts/fix_schwab_auth.py."
        )
    return HardwareProfile(
        scan_stage_a_workers=scan_a,
        scan_stage_b_workers=scan_b,
        research_max_workers=research,
        warnings=tuple(warnings),
        notes=tuple(notes),
        facts=facts,
    )


def assess_ops_box(facts: HostFacts, *, skill_dir: Path | None = None) -> OpsBoxReadiness:
    profile = recommend_profile(facts)
    skill = skill_dir or SKILL_DIR
    blockers: list[str] = []
    next_steps: list[str] = []
    if not facts.env_file_present:
        blockers.append("missing .env")
        next_steps.append(f"Copy schwab_skill/.env onto {skill / '.env'}")
    if not facts.token_files_present:
        blockers.append("missing Schwab tokens")
        next_steps.append("Copy token files or run python scripts/fix_schwab_auth.py")
    if not facts.git_available:
        next_steps.append("Install Git for Windows (winget install Git.Git) and reopen the terminal")
    if not facts.ethernet_up and not facts.wifi_up:
        blockers.append("no network")
        next_steps.append("Connect Wi-Fi (this host has no Ethernet path)")
    elif facts.wifi_up and not facts.ethernet_up:
        next_steps.append(
            "powershell -File scripts/harden_ops_box_wifi.ps1  "
            "(disable Wi-Fi adapter power saving; this host has no wired path)"
        )
    bus = (facts.repo_bus_type or "").strip().upper()
    if bus.startswith("USB"):
        next_steps.append(r"Robocopy the repo to C:\src\TradingBot and open that folder in Cursor")
    if facts.env_file_present:
        next_steps.append(
            "python scripts/setup_local_ops_box.py --apply-env  "
            "(upserts SCAN_STAGE_* workers only)"
        )
        next_steps.append("python scripts/start_local_dashboard.py")
        next_steps.append(
            "After the close: python scripts/run_multi_era_backtest_schwab_only.py "
            f"--run-tag um790_ops --max-workers {profile.research_max_workers}"
        )
    return OpsBoxReadiness(profile=profile, blockers=blockers, next_steps=next_steps)


def write_profile_artifact(readiness: OpsBoxReadiness, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(readiness.as_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def collect_host_facts(repo_root: Path, skill_dir: Path | None = None) -> HostFacts:
    """Best-effort Windows/Linux facts. Unit tests should inject HostFacts instead."""
    skill = skill_dir or SKILL_DIR
    logical = os.cpu_count() or 1
    physical = _physical_cores(logical)
    ram_gb = _ram_gb()
    cpu_name = _cpu_name()
    bus = _volume_bus_type(repo_root)
    ethernet_up, wifi_up = _network_up()
    env_path = skill / ".env"
    return HostFacts(
        cpu_name=cpu_name,
        physical_cores=physical,
        logical_cpus=logical,
        ram_gb=ram_gb,
        repo_root=str(repo_root),
        repo_bus_type=bus,
        ethernet_up=ethernet_up,
        wifi_up=wifi_up,
        git_available=shutil.which("git") is not None,
        env_file_present=env_path.is_file(),
        token_files_present=(skill / "tokens_market.enc").is_file()
        and (skill / "tokens_account.enc").is_file(),
    )


def _ps(command: str, timeout: int = 20) -> str:
    if os.name != "nt":
        return ""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        LOG.warning("PowerShell fact probe failed: %s", exc)
        return ""
    return (completed.stdout or "").strip()


def _physical_cores(fallback: int) -> int:
    if os.name == "nt":
        raw = _ps("(Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum")
        try:
            value = int(float(raw))
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    try:
        count = os.cpu_count() or fallback
        return max(1, int(count))
    except (TypeError, ValueError):
        return max(1, fallback)


def _ram_gb() -> float:
    if os.name == "nt":
        raw = _ps("[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)")
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        phys = os.sysconf("SC_PHYS_PAGES")
        return round((page * phys) / (1024**3), 1)
    except (AttributeError, ValueError, OSError):
        return 0.0


def _cpu_name() -> str:
    if os.name == "nt":
        raw = _ps("(Get-CimInstance Win32_Processor | Select-Object -First 1).Name")
        if raw:
            return raw
    return os.environ.get("PROCESSOR_IDENTIFIER") or "unknown"


def _volume_bus_type(path: Path) -> str:
    if os.name != "nt":
        return "unknown"
    try:
        drive = path.resolve().drive.rstrip(":\\")
    except (OSError, ValueError):
        return "unknown"
    if not drive:
        return "unknown"
    raw = _ps(
        f"(Get-Partition -DriveLetter '{drive}' | Get-Disk | Select-Object -First 1).BusType"
    )
    return raw or "unknown"


def _network_up() -> tuple[bool, bool]:
    if os.name != "nt":
        return False, False
    ethernet = _ps(
        "@((Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and $_.Name -match 'Ethernet' }).Name) -join ','"
    )
    wifi = _ps(
        "@((Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and $_.Name -match 'Wi-?Fi' }).Name) -join ','"
    )
    return bool(ethernet.strip()), bool(wifi.strip())
