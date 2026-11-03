#!/usr/bin/env python3
"""Probe xAI with XAI_API_KEY from the environment, then persist it to .env.

Never prints the key. Usage (from schwab_skill):
  $env:XAI_API_KEY='xai-...'
  python scripts/probe_xai_grok.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from core.env_local import apply_mirofish_grok_shadow_env, upsert_env_file  # noqa: E402
from core.llm_endpoint import resolve_llm_endpoint  # noqa: E402
from engine_analysis import _call_llm, _load_env  # noqa: E402

PROMPT = (
    "Fix this function and explain the bug: "
    "function median(a){a.sort();return a[a.length/2]}"
)


def _post(url: str, api_key: str, payload: dict) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return int(resp.status), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")


def _summarize_json(text: str, limit: int = 800) -> str:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text[:limit]
    if isinstance(parsed, dict) and parsed.get("error"):
        err = parsed["error"]
        if isinstance(err, dict):
            return str(err.get("message") or err)[:limit]
        return str(err)[:limit]
    output = parsed.get("output") if isinstance(parsed, dict) else None
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("text"):
                    chunks.append(str(content["text"]))
        if chunks:
            return "\n".join(chunks)[:limit]
    choices = parsed.get("choices") if isinstance(parsed, dict) else None
    if isinstance(choices, list) and choices:
        msg = (choices[0] or {}).get("message") or {}
        if isinstance(msg, dict) and msg.get("content"):
            return str(msg["content"])[:limit]
    return text[:limit]


def main() -> int:
    from core.env_local import parse_env_file

    api_key = (os.environ.get("XAI_API_KEY") or "").strip()
    if not api_key:
        api_key = (parse_env_file(SKILL_DIR / ".env").get("XAI_API_KEY") or "").strip()
        if api_key:
            os.environ["XAI_API_KEY"] = api_key
    if not api_key:
        print("FAIL: XAI_API_KEY is not set in the process environment or .env")
        return 1
    if not api_key.lower().startswith("xai-"):
        print("FAIL: XAI_API_KEY does not look like an xAI key")
        return 1

    env_path = SKILL_DIR / ".env"
    apply_mirofish_grok_shadow_env(env_path)
    upsert_env_file(env_path, {"XAI_API_KEY": api_key, "LLM_MODEL_NAME": "grok-4.6"})
    print("wrote XAI_API_KEY and LLM_MODEL_NAME=grok-4.6 to .env")

    os.environ["LLM_MODEL_NAME"] = "grok-4.6"
    os.environ["LLM_BASE_URL"] = "https://api.x.ai/v1"
    status, raw = _post(
        "https://api.x.ai/v1/responses",
        api_key,
        {"model": "grok-4.6", "input": PROMPT},
    )
    print("responses_status", status)
    print("responses_excerpt")
    print(_summarize_json(raw))

    chat_status, chat_raw = _post(
        "https://api.x.ai/v1/chat/completions",
        api_key,
        {
            "model": "grok-4.6",
            "messages": [{"role": "user", "content": "Reply with the single word pong."}],
            "max_tokens": 16,
        },
    )
    print("chat_completions_status", chat_status)
    print("chat_excerpt")
    print(_summarize_json(chat_raw))

    os.environ["XAI_API_KEY"] = api_key
    miro = _call_llm(
        'Return exactly {"ok":true}',
        "Return one JSON object only.",
        _load_env(),
    )
    print("mirofish_probe_empty", not bool((miro or "").strip()), "len", len(miro or ""))
    ep = resolve_llm_endpoint(_load_env())
    print("resolver provider", ep.provider, "model", ep.model, "key_class", "xai-prefix")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
