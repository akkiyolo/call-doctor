"""The ONE Gemini wrapper. Every LLM call in Call Doctor goes through `complete`."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import TypeVar

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential_jitter,
)

from .config import ROLES, get_settings

T = TypeVar("T", bound=BaseModel)

# Estimated USD per 1M tokens (input, output), matched by substring of the model ID, first hit wins.
# Thinking tokens are billed as output. These are estimates for the budget guard, not invoices.
PRICING: list[tuple[str, float, float]] = [
    ("flash-lite", 0.10, 0.40),
    ("flash", 0.50, 3.00),
    ("pro", 2.00, 12.00),
]
DEFAULT_PRICE = (2.00, 12.00)  # unknown models priced like pro, so the guard errs on the safe side


class BudgetExceeded(RuntimeError):
    pass


class EmptyResponse(RuntimeError):
    """Safety-blocked or empty candidate. Retried once."""


_lock = threading.Lock()
_spend_usd = 0.0
_client: genai.Client | None = None


def spend() -> float:
    return _spend_usd


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=get_settings().gemini_api_key)
    return _client


def price_for(model: str) -> tuple[float, float]:
    for key, pin, pout in PRICING:
        if key in model:
            return pin, pout
    return DEFAULT_PRICE


def _cost(model: str, in_tok: int, out_tok: int) -> float:
    pin, pout = price_for(model)
    return (in_tok * pin + out_tok * pout) / 1_000_000


def _is_hard_quota(exc: BaseException) -> bool:
    """A 429 that no amount of backoff will fix (zero quota, or the daily cap is spent)."""
    msg = str(exc)
    return "limit: 0" in msg or "PerDay" in msg


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, genai_errors.APIError):
        if exc.code == 429:
            return not _is_hard_quota(exc)
        return (exc.code or 0) >= 500
    return False


def _log(row: dict) -> None:
    path: Path = get_settings().runs_dir / "llm_log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _to_contents(messages: list[dict]) -> list[types.Content]:
    """messages: [{"role": "user"|"model", "text": str}, ...]"""
    return [
        types.Content(role=m["role"], parts=[types.Part.from_text(text=m["text"])])
        for m in messages
    ]


@retry(
    retry=retry_if_exception(_is_transient),
    wait=wait_exponential_jitter(initial=2, max=60),
    stop=stop_after_attempt(12) | stop_after_delay(300),  # Gemini 503 spikes can last minutes
    reraise=True,
)
def _call(model: str, contents: list[types.Content], cfg: types.GenerateContentConfig):
    return _get_client().models.generate_content(model=model, contents=contents, config=cfg)


def _generate(role: str, system: str, messages: list[dict], schema, seed) -> str:
    global _spend_usd
    s = get_settings()
    if _spend_usd >= s.max_spend_usd:
        raise BudgetExceeded(f"estimated spend ${_spend_usd:.2f} >= MAX_SPEND_USD ${s.max_spend_usd}")

    model = s.models[role]
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        temperature=s.temperatures[role],
        seed=seed,
    )
    if schema is not None:
        cfg.response_mime_type = "application/json"
        cfg.response_schema = schema

    contents = _to_contents(messages)
    prompt_hash = hashlib.sha256(
        (system + json.dumps(messages, ensure_ascii=False)).encode()
    ).hexdigest()[:16]

    text = ""
    for attempt in range(2):  # one extra try for safety-blocked / empty responses
        t0 = time.time()
        resp = _call(model, contents, cfg)
        latency = time.time() - t0
        um = resp.usage_metadata
        in_tok = (um.prompt_token_count or 0) if um else 0
        out_tok = ((um.candidates_token_count or 0) + (um.thoughts_token_count or 0)) if um else 0
        cost = _cost(model, in_tok, out_tok)
        with _lock:
            _spend_usd += cost
        text = (resp.text or "").strip()
        _log({
            "ts": time.time(), "role": role, "model": model, "prompt_hash": prompt_hash,
            "seed": seed, "in_tokens": in_tok, "out_tokens": out_tok,
            "cost_usd": round(cost, 6), "latency_s": round(latency, 2),
            "empty": not text, "attempt": attempt,
        })
        if text:
            return text
    raise EmptyResponse(f"{role}: empty or safety-blocked response from {model}")


def complete(
    role: str,
    system: str,
    messages: list[dict],
    schema: type[T] | None = None,
    seed: int | None = None,
) -> str | T:
    """Run one Gemini call for `role`. Returns text, or a validated `schema` instance.

    On pydantic validation failure, retries once with the error fed back for repair.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}, expected one of {ROLES}")
    text = _generate(role, system, messages, schema, seed)
    if schema is None:
        return text
    try:
        return schema.model_validate_json(text)
    except ValidationError as e:
        repair = messages + [
            {"role": "model", "text": text},
            {"role": "user", "text": (
                "Your JSON failed validation:\n" + str(e)
                + "\nReturn the corrected JSON only, matching the schema exactly."
            )},
        ]
        return schema.model_validate_json(_generate(role, system, repair, schema, seed))
