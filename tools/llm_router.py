"""Single entry point for LLM calls. Routes to Anthropic Claude or DeepSeek
based on the LLM_PROVIDER env var (default: deepseek).

Same signature as strategist.claude_call so swap-in is trivial:
    from llm_router import llm_call
    raw = llm_call(system, user_content, max_tokens=1000)

DeepSeek API is OpenAI-compatible (https://api.deepseek.com/v1).
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

PROVIDER = os.environ.get("LLM_PROVIDER", "deepseek").lower()
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

# Model defaults per provider — chosen for parity on structured-JSON tasks
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "deepseek": "deepseek-chat",  # V3; use "deepseek-reasoner" for R1
}


def llm_call(system: str, user_content: str, max_tokens: int = 1000,
             model: str | None = None, provider: str | None = None) -> str:
    """Send one prompt, get one text response. Provider-agnostic."""
    p = (provider or PROVIDER).lower()
    if p == "anthropic":
        return _anthropic_call(system, user_content, max_tokens, model or DEFAULT_MODELS["anthropic"])
    if p == "deepseek":
        return _deepseek_call(system, user_content, max_tokens, model or DEFAULT_MODELS["deepseek"])
    raise ValueError(f"Unknown LLM_PROVIDER: {p!r}")


# ---- Unified retry policy ---------------------------------------------------
# Backoff: 5 attempts, exponential with jitter, total cap ~120s. Same set of
# retryable HTTP status codes across both providers (429 included for Anthropic
# — previously only 529 was retried, which let burst 429s fail immediately).
import random as _random

_RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}
_MAX_ATTEMPTS = 5
_BASE_BACKOFF = 2.0   # seconds, doubles per attempt, plus jitter
_TOTAL_TIME_CAP = 120  # seconds — bail out if cumulative sleep would exceed this


def _backoff_sleep(attempt: int, elapsed: float) -> float:
    """Compute next sleep duration with jitter. Returns 0 if it would exceed the cap."""
    base = _BASE_BACKOFF * (2 ** attempt)
    jitter = _random.uniform(0, base * 0.25)
    delay = base + jitter
    if elapsed + delay > _TOTAL_TIME_CAP:
        return 0
    return delay


def _anthropic_call(system, user_content, max_tokens, model):
    from anthropic import Anthropic, APIStatusError, APIConnectionError, APITimeoutError
    client = Anthropic(api_key=ANTHROPIC_KEY)
    elapsed = 0.0
    for attempt in range(_MAX_ATTEMPTS):
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            )
            return msg.content[0].text.strip()
        except (APIConnectionError, APITimeoutError):
            if attempt >= _MAX_ATTEMPTS - 1:
                raise
            d = _backoff_sleep(attempt, elapsed)
            if d == 0:
                raise
            time.sleep(d); elapsed += d
        except APIStatusError as e:
            status = getattr(e, "status_code", None)
            if status in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS - 1:
                d = _backoff_sleep(attempt, elapsed)
                if d == 0:
                    raise
                time.sleep(d); elapsed += d
            else:
                raise


def _deepseek_call(system, user_content, max_tokens, model):
    from openai import OpenAI, RateLimitError, APIStatusError, APITimeoutError, APIConnectionError
    client = OpenAI(api_key=DEEPSEEK_KEY, base_url=DEEPSEEK_BASE_URL)
    elapsed = 0.0
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
            )
            return resp.choices[0].message.content.strip()
        except (RateLimitError, APITimeoutError, APIConnectionError):
            if attempt >= _MAX_ATTEMPTS - 1:
                raise
            d = _backoff_sleep(attempt, elapsed)
            if d == 0:
                raise
            time.sleep(d); elapsed += d
        except APIStatusError as e:
            status = getattr(e, "status_code", None)
            if status in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS - 1:
                d = _backoff_sleep(attempt, elapsed)
                if d == 0:
                    raise
                time.sleep(d); elapsed += d
            else:
                raise


if __name__ == "__main__":
    # Quick A/B smoke test
    system = "You produce exactly one JSON object, no preamble."
    user = ("Return a JSON object {\"firm\": <string>, \"years\": [<int>...]} "
            "extracted from: 'pitch for Skadden, 2025 and 2026 HK IPOs'")
    for prov in ("anthropic", "deepseek"):
        print(f"\n=== {prov} ===")
        try:
            print(llm_call(system, user, max_tokens=200, provider=prov))
        except Exception as e:
            print("ERROR:", e)
