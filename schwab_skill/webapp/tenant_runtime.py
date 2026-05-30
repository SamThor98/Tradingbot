"""
Per-tenant skill directory for SaaS workers.

Materializes a temporary directory with .env and Schwab token files so existing
modules (DualSchwabAuth, signal_scanner, execution) work unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy.orm import Session

from schwab_auth import read_encrypted_token_file, write_encrypted_token_file

from .db import SessionLocal
from .models import User, UserCredential
from .security import decrypt_secret, encrypt_secret

LOG = logging.getLogger(__name__)

_LLM_KEY_ENV_ALIASES = (
    "MIROFISH_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_KEY",
)

# Optional platform overrides forwarded into each tenant .env when set in process env.
_ENV_OPTIONAL_FOR_TENANT = (
    "PAPER_TRADING_ENABLED",
    "EXECUTION_SHADOW_MODE",
    "MAX_SECTOR_ACCOUNT_FRACTION",
    "HYPOTHESIS_LEDGER_ENABLED",
    "HYPOTHESIS_SELF_STUDY_MERGE",
    # MiroFish/LLM keys + routing options used by engine_analysis._call_llm().
    "MIROFISH_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL_NAME",
    # Advisory model runtime knobs.
    "ADVISORY_MODEL_ENABLED",
    "ADVISORY_MODEL_PATH",
    "ADVISORY_CONFIDENCE_HIGH",
    "ADVISORY_CONFIDENCE_LOW",
    "ADVISORY_REQUIRE_MODEL",
)

# Env keys written into tenant .env (platform must supply Schwab app registration).
_ENV_KEYS_FOR_TENANT = (
    "SCHWAB_MARKET_APP_KEY",
    "SCHWAB_MARKET_APP_SECRET",
    "SCHWAB_MARKET_CALLBACK_URL",
    "SCHWAB_ACCOUNT_APP_KEY",
    "SCHWAB_ACCOUNT_APP_SECRET",
    "SCHWAB_CALLBACK_URL",
    "SCHWAB_TOKEN_ENCRYPTION_KEY",
    "DISCORD_WEBHOOK_URL",
    "DISCORD_USER_ID",
)


def _decrypt_json_payload(enc: str | None) -> dict[str, Any] | None:
    if not enc:
        return None
    raw = decrypt_secret(enc)
    if not raw:
        return None
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


def _account_token_dict(row: UserCredential) -> dict[str, Any] | None:
    blob = _decrypt_json_payload(row.account_token_payload_enc)
    if blob and blob.get("access_token") and blob.get("refresh_token"):
        return blob
    access = decrypt_secret(row.access_token_enc)
    refresh = decrypt_secret(row.refresh_token_enc)
    if access and refresh:
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": (row.token_type or "Bearer").strip() or "Bearer",
        }
    return None


def _market_token_dict(row: UserCredential) -> dict[str, Any] | None:
    blob = _decrypt_json_payload(row.market_token_payload_enc)
    if blob and blob.get("access_token") and blob.get("refresh_token"):
        return blob
    return None


def user_has_account_session(db: Session, user_id: str) -> bool:
    row = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
    if not row:
        return False
    return _account_token_dict(row) is not None


def user_schwab_ready_for_live_trading(db: Session, user_id: str) -> tuple[bool, str]:
    """Account + market data path available (same bar as running a scan / placing guarded orders)."""
    if not user_has_account_session(db, user_id):
        return False, "Schwab account tokens are not linked."
    ok, reason = user_can_materialize_for_scan(db, user_id)
    if not ok:
        return False, reason
    return True, ""


def user_can_materialize_for_scan(db: Session, user_id: str) -> tuple[bool, str]:
    if not user_has_account_session(db, user_id):
        return False, "Schwab account tokens are not linked."
    row = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
    assert row is not None
    if _market_token_dict(row):
        return True, ""
    if _platform_market_fallback_disabled():
        return (
            False,
            "Per-user market OAuth required: SAAS_DISABLE_PLATFORM_MARKET_FALLBACK is enabled.",
        )
    if _platform_market_token_file() is not None:
        return True, ""
    return (
        False,
        "Market session missing: provide market_oauth_json on credentials or set "
        "SAAS_PLATFORM_MARKET_SKILL_DIR to a skill dir containing tokens_market.enc.",
    )


def _platform_market_skill_dir() -> Path | None:
    raw = (os.getenv("SAAS_PLATFORM_MARKET_SKILL_DIR") or "").strip()
    return Path(raw) if raw else None


def _platform_market_fallback_disabled() -> bool:
    return (os.getenv("SAAS_DISABLE_PLATFORM_MARKET_FALLBACK", "0") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _platform_market_token_file() -> Path | None:
    skill_dir = _platform_market_skill_dir()
    if skill_dir is None:
        return None
    token_path = skill_dir / "tokens_market.enc"
    if token_path.is_file():
        return token_path
    return None


def _platform_skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _copy_platform_advisory_model_if_available(skill_dir: Path) -> bool:
    """
    Materialize advisory model artifact into tenant runtime when available.

    Tenant scans resolve ADVISORY_MODEL_PATH relative to the tenant skill dir,
    so a platform-level artifact at schwab_skill/artifacts/... must be copied
    into each temp skill dir to keep advisory scoring available in SaaS mode.
    """
    raw = (os.getenv("ADVISORY_MODEL_PATH") or "").strip() or "advisory_model_v1.json"
    configured = Path(raw)
    if configured.is_absolute():
        # Absolute model paths remain valid without copying.
        return configured.is_file()

    root = _platform_skill_root()
    candidates = [
        configured,
        Path("advisory_model_v1.json"),
        Path("artifacts") / "advisory_model_v1.json",
    ]
    for rel in candidates:
        src = root / rel
        if not src.is_file():
            continue
        dst = skill_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
        return True
    return False


def scan_runtime_prerequisite_errors(
    skill_dir: Path | None = None,
    *,
    require_llm_key: bool = True,
) -> list[str]:
    """
    Platform-level prerequisites required for SaaS scan enrichment.

    Returns a list of human-readable error strings. Empty list means runtime is
    ready to produce real MiroFish/advisory outputs.
    """
    errors: list[str] = []
    if require_llm_key and not any((os.getenv(k) or "").strip() for k in _LLM_KEY_ENV_ALIASES):
        errors.append("LLM key missing: set MIROFISH_API_KEY, OPENAI_API_KEY, or OPENAI_KEY on the worker.")

    advisory_enabled = (os.getenv("ADVISORY_MODEL_ENABLED") or "1").strip().lower() in ("1", "true", "yes", "on")
    if advisory_enabled:
        raw = (os.getenv("ADVISORY_MODEL_PATH") or "").strip() or "advisory_model_v1.json"
        model_path = Path(raw)
        if not model_path.is_absolute():
            base = skill_dir if skill_dir is not None else _platform_skill_root()
            model_path = base / model_path
        if not model_path.is_file():
            errors.append(
                "Advisory model artifact missing: expected ADVISORY_MODEL_PATH "
                f"at {model_path}."
            )
    return errors


def _required_platform_schwab_env() -> dict[str, str]:
    required = {
        "SCHWAB_MARKET_APP_KEY": (os.environ.get("SCHWAB_MARKET_APP_KEY") or "").strip(),
        "SCHWAB_MARKET_APP_SECRET": (os.environ.get("SCHWAB_MARKET_APP_SECRET") or "").strip(),
        "SCHWAB_ACCOUNT_APP_KEY": (os.environ.get("SCHWAB_ACCOUNT_APP_KEY") or "").strip(),
        "SCHWAB_ACCOUNT_APP_SECRET": (os.environ.get("SCHWAB_ACCOUNT_APP_SECRET") or "").strip(),
    }
    if all(required.values()):
        return required
    raise RuntimeError(
        "Platform Schwab app env missing: set SCHWAB_MARKET_APP_KEY/SECRET and "
        "SCHWAB_ACCOUNT_APP_KEY/SECRET on the API and worker processes."
    )


def _write_tenant_env(skill_dir: Path) -> None:
    lines: list[str] = []
    for key in _ENV_KEYS_FOR_TENANT:
        val = os.environ.get(key)
        if val is not None and str(val).strip() != "":
            lines.append(f"{key}={val}")
    for key in _ENV_OPTIONAL_FOR_TENANT:
        val = os.environ.get(key)
        if val is not None and str(val).strip() != "":
            lines.append(f"{key}={val}")
    if not any(line.startswith("SCHWAB_CALLBACK_URL=") for line in lines):
        lines.append("SCHWAB_CALLBACK_URL=https://127.0.0.1:8182/")
    (skill_dir / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_tenant_execution_overrides(skill_dir: Path, db: Session, user_id: str) -> None:
    extra: list[str] = []
    plat = (os.getenv("LIVE_TRADING_KILL_SWITCH") or "").strip().lower()
    if plat in ("1", "true", "yes", "on"):
        extra.append("LIVE_TRADING_KILL_SWITCH=1")
    be = (os.getenv("LIVE_TRADING_KILL_SWITCH_BLOCKS_EXITS") or "").strip().lower()
    if be in ("1", "true", "yes", "on"):
        extra.append("LIVE_TRADING_KILL_SWITCH_BLOCKS_EXITS=1")
    row = db.query(User).filter(User.id == user_id).first()
    if row and getattr(row, "trading_halted", False):
        extra.append("USER_TRADING_HALTED=1")
    if not extra:
        return
    path = skill_dir / ".env"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(existing.rstrip() + "\n" + "\n".join(extra) + "\n", encoding="utf-8")


def materialize_tenant_skill_dir(db: Session, user_id: str, skill_dir: Path) -> None:
    """Populate skill_dir with .env and token files. Raises RuntimeError on misconfiguration."""
    row = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
    if not row:
        raise RuntimeError("No credentials row for user.")

    env_cfg = _required_platform_schwab_env()
    market_secret = env_cfg["SCHWAB_MARKET_APP_SECRET"]
    account_secret = env_cfg["SCHWAB_ACCOUNT_APP_SECRET"]

    skill_dir.mkdir(parents=True, exist_ok=True)
    _write_tenant_env(skill_dir)
    _append_tenant_execution_overrides(skill_dir, db, user_id)

    account = _account_token_dict(row)
    if not account:
        raise RuntimeError("Schwab account OAuth tokens are missing or incomplete.")
    write_encrypted_token_file(skill_dir / "tokens_account.enc", account, account_secret)

    market = _market_token_dict(row)
    if market:
        write_encrypted_token_file(skill_dir / "tokens_market.enc", market, market_secret)
    elif _platform_market_skill_dir() is not None:
        if _platform_market_fallback_disabled():
            raise RuntimeError(
                "Per-user market OAuth required: SAAS_DISABLE_PLATFORM_MARKET_FALLBACK is enabled."
            )
        src = _platform_market_token_file()
        if src is None:
            base = _platform_market_skill_dir()
            assert base is not None
            raise RuntimeError(
                f"SAAS_PLATFORM_MARKET_SKILL_DIR set but tokens_market.enc missing: {base / 'tokens_market.enc'}"
            )
        LOG.warning(
            "Using legacy platform market token fallback for user_id=%s; migrate to per-user market_oauth_json.",
            user_id,
        )
        shutil.copy(src, skill_dir / "tokens_market.enc")
    else:
        raise RuntimeError(
            "Market OAuth not configured: upload market_oauth_json or set SAAS_PLATFORM_MARKET_SKILL_DIR."
        )

    advisory_enabled = (os.getenv("ADVISORY_MODEL_ENABLED") or "1").strip().lower() in ("1", "true", "yes", "on")
    if advisory_enabled:
        copied = _copy_platform_advisory_model_if_available(skill_dir)
        if not copied:
            LOG.warning(
                "Advisory model enabled but artifact unavailable for tenant runtime "
                "(expected ADVISORY_MODEL_PATH or advisory_model_v1.json)."
            )


def _refresh_at_value(tokens: dict[str, Any] | None) -> str:
    """Comparable `_last_refresh_at` stamp (ISO string) for freshness ordering."""
    if not isinstance(tokens, dict):
        return ""
    return str(tokens.get("_last_refresh_at") or "")


def _tokens_refreshed(fresh: dict[str, Any], current: dict[str, Any] | None) -> bool:
    """True if ``fresh`` (read from the skill dir) is a real refresh of ``current``.

    Change is detected by token *value* (the access/refresh token actually
    rotated), not by the `_last_refresh_at` stamp — DB payloads written by the
    OAuth callback are unstamped, so a stamp-only comparison would falsely fire
    on every operation. The stamp is then used only as an anti-regression guard
    so a slower process cannot overwrite a newer token written concurrently.
    """
    cur = current or {}
    value_changed = (
        fresh.get("access_token") != cur.get("access_token")
        or fresh.get("refresh_token") != cur.get("refresh_token")
    )
    if not value_changed:
        return False
    # Never regress to an older token than what's already stored.
    return _refresh_at_value(fresh) >= _refresh_at_value(cur)


def _capture_materialized_access(skill_dir: Path) -> dict[str, str]:
    """Snapshot the access tokens just materialized into the skill dir.

    Used as a baseline so ``persist_tenant_tokens_back`` only writes a token
    back to the DB when it actually changed during the operation (a real
    in-process refresh) — never when an unrefreshed token would otherwise
    clobber a token a concurrent request/OAuth callback updated in the DB.
    """
    out: dict[str, str] = {}
    a_sec = (os.environ.get("SCHWAB_ACCOUNT_APP_SECRET") or "").strip()
    m_sec = (os.environ.get("SCHWAB_MARKET_APP_SECRET") or "").strip()
    try:
        if a_sec:
            t = read_encrypted_token_file(Path(skill_dir) / "tokens_account.enc", a_sec)
            if t and t.get("access_token"):
                out["account"] = str(t["access_token"])
        if m_sec:
            t = read_encrypted_token_file(Path(skill_dir) / "tokens_market.enc", m_sec)
            if t and t.get("access_token"):
                out["market"] = str(t["access_token"])
    except Exception as exc:
        LOG.debug("baseline capture failed for %s: %s", skill_dir, exc)
    return out


def _refreshed_in_process(fresh: dict[str, Any], baseline_access: str | None) -> bool:
    """True if ``fresh`` represents a real in-process refresh.

    ``baseline_access`` is the access token materialized at the start of the
    operation. If it changed, a refresh happened during the operation and the
    new token should be persisted. ``None`` means the baseline is unknown
    (legacy callers / tests) — fall back to permissive behavior.
    """
    if baseline_access is None:
        return True
    fresh_access = fresh.get("access_token")
    return bool(fresh_access and fresh_access != baseline_access)


def persist_tenant_tokens_back(
    db: Session, user_id: str, skill_dir: Path, baseline: dict[str, str] | None = None
) -> None:
    """Write Schwab tokens refreshed during an operation back into the DB.

    Tokens are materialized into an ephemeral per-tenant skill dir; any refresh
    that happens mid-operation (e.g. on a 401 during market-data loading) is
    written to that dir by ``SchwabSession`` and would otherwise be lost when
    the dir is deleted — leaving the DB (the cross-process source of truth) with
    a stale refresh token that eventually fails with ``400 unsupported_token_type``.

    A ``_last_refresh_at`` freshness guard ensures a slower/older operation can
    never overwrite a newer token written by a concurrent process. The market
    token is only persisted for tenants that already use per-user market OAuth
    (never for those on the shared platform-market fallback).

    Token persistence runs in its OWN session: the caller's ``db`` may already be
    in a failed-transaction state from an unrelated flush error during the
    operation, which would otherwise drop the refreshed token here and force a
    repeated 401 -> re-auth loop. ``db`` is accepted for API compatibility but
    only used to release the caller's poisoned transaction.
    """
    try:
        db.rollback()
    except Exception:
        pass

    session = SessionLocal()
    try:
        row = session.query(UserCredential).filter(UserCredential.user_id == user_id).first()
        if not row:
            return

        account_secret = (os.environ.get("SCHWAB_ACCOUNT_APP_SECRET") or "").strip()
        market_secret = (os.environ.get("SCHWAB_MARKET_APP_SECRET") or "").strip()
        had_user_market = bool((row.market_token_payload_enc or "").strip())
        changed = False

        if account_secret:
            fresh = read_encrypted_token_file(Path(skill_dir) / "tokens_account.enc", account_secret)
            if fresh and fresh.get("access_token") and fresh.get("refresh_token"):
                current = _account_token_dict(row)
                base_access = (baseline or {}).get("account")
                if _refreshed_in_process(fresh, base_access) and _tokens_refreshed(fresh, current):
                    row.account_token_payload_enc = encrypt_secret(json.dumps(fresh, default=str))
                    row.access_token_enc = encrypt_secret(str(fresh["access_token"]))
                    row.refresh_token_enc = encrypt_secret(str(fresh["refresh_token"]))
                    row.token_type = (str(fresh.get("token_type") or "Bearer").strip() or "Bearer")
                    changed = True

        if market_secret and had_user_market:
            fresh_m = read_encrypted_token_file(Path(skill_dir) / "tokens_market.enc", market_secret)
            if fresh_m and fresh_m.get("access_token") and fresh_m.get("refresh_token"):
                current_m = _market_token_dict(row)
                base_access_m = (baseline or {}).get("market")
                if _refreshed_in_process(fresh_m, base_access_m) and _tokens_refreshed(fresh_m, current_m):
                    row.market_token_payload_enc = encrypt_secret(json.dumps(fresh_m, default=str))
                    changed = True

        if changed:
            session.commit()
    except Exception as exc:
        LOG.warning("persist_tenant_tokens_back failed for user_id=%s: %s", user_id, exc)
        try:
            session.rollback()
        except Exception:
            pass
    finally:
        session.close()


@contextmanager
def tenant_skill_dir(db: Session, user_id: str) -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix=f"tb_saas_{user_id[:24]}_"))
    baseline: dict[str, str] = {}
    try:
        materialize_tenant_skill_dir(db, user_id, root)
        # Snapshot what we materialized so persist_tenant_tokens_back only writes
        # back tokens that actually rotated during this operation — never an
        # unrefreshed token that would clobber a freshly re-authed DB token.
        baseline = _capture_materialized_access(root)
        yield root
    finally:
        # Persist any in-operation token refresh back to the DB before the
        # ephemeral dir is deleted, so the shared source of truth stays fresh.
        persist_tenant_tokens_back(db, user_id, root, baseline=baseline)
        shutil.rmtree(root, ignore_errors=True)
