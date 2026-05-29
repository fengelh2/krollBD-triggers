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
from dotenv import load_dotenv

load_dotenv()

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


def _anthropic_call(system, user_content, max_tokens, model):
    from anthropic import Anthropic, APIStatusError
    client = Anthropic(api_key=ANTHROPIC_KEY)
    for attempt in range(5):
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            )
            return msg.content[0].text.strip()
        except APIStatusError as e:
            if e.status_code == 529 and attempt < 4:
                time.sleep(10 * (attempt + 1))
            else:
                raise


def _deepseek_call(system, user_content, max_tokens, model):
    from openai import OpenAI, RateLimitError, APIStatusError, APITimeoutError, APIConnectionError
    client = OpenAI(api_key=DEEPSEEK_KEY, base_url=DEEPSEEK_BASE_URL)
    retryable_status = {429, 500, 502, 503, 504, 529}
    for attempt in range(5):
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
        except (RateLimitError, APITimeoutError, APIConnectionError) as e:
            if attempt < 4:
                time.sleep(10 * (attempt + 1))
                continue
            raise
        except APIStatusError as e:
            if getattr(e, "status_code", None) in retryable_status and attempt < 4:
                time.sleep(10 * (attempt + 1))
                continue
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
