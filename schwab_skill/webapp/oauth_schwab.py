"""Signed OAuth state for Schwab browser callback (no JWT on redirect)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from typing import Any

from auth import AUTH_URL, exchange_code_for_tokens

_STATE_TTL_SEC = 600


def _state_secret() -> bytes:
    raw = (
        (os.getenv("OAUTH_STATE_SECRET") or "").strip()
        or (os.getenv("SUPABASE_JWT_SECRET") or "").strip()
        or (os.getenv("CREDENTIAL_ENCRYPTION_KEY") or "").strip()
    )
    if not raw:
        raise RuntimeError(
            "Set OAUTH_STATE_SECRET (or SUPABASE_JWT_SECRET / CREDENTIAL_ENCRYPTION_KEY) for Schwab OAuth state signing."
        )
    return hashlib.sha256(raw.encode("utf-8")).digest()


def sign_schwab_oauth_state(user_id: str) -> str:
    exp = int(time.time()) + _STATE_TTL_SEC
    payload = {"uid": user_id, "exp": exp}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_state_secret(), body, hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(body).decode("utf-8").rstrip("=") + "." + sig
    return token


def verify_schwab_oauth_state(token: str) -> str | None:
    if not token or "." not in token:
        return None
    enc, sig = token.rsplit(".", 1)
    pad = "=" * ((4 - len(enc) % 4) % 4)
    try:
        body = base64.urlsafe_b64decode(enc + pad)
        expect = hmac.new(_state_secret(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, sig):
            return None
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            return None
        uid = str(data.get("uid") or "").strip()
        exp = int(data.get("exp") or 0)
        if not uid or exp < int(time.time()):
            return None
        return uid
    except Exception:
        return None


def schwab_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params: dict[str, str] = {
        "client_id": client_id.strip(),
        "redirect_uri": redirect_uri.strip(),
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_schwab_code_for_tokens(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    return exchange_code_for_tokens(
        client_id.strip(),
        client_secret.strip(),
        code.strip(),
        redirect_uri=redirect_uri.strip(),
    )
