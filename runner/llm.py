"""Gemini client: serialised, metered, and honest about what a 429 means.

Design notes that are load-bearing:

  - **Serialised.** Free tier is ~15 RPM and RPM bites long before RPD
    (HANDOFF.md §4). There is no concurrency here on purpose; a module-level
    lock makes that true even if a future caller gets clever.

  - **Two kinds of 429.** Verified 2026-08-06: the free key returns 429 on Pro
    because the tier has no Pro quota, and a depleted prepaid balance returns
    429 on every model. Neither is retryable. Backing off on those waits
    forever on a call that cannot succeed. See classify_429().

  - **No sampling parameters.** temperature, top_p and top_k are deprecated on
    Gemini 3.x and are deliberately never set (AGENTS.md #6). Use thinking_level.

  - **Every call is metered**, including reasoning tokens, because llm_usage is
    the only spend cap that exists (the API has no hard billing cap).
"""

from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

_CALL_LOCK = threading.Lock()

# Phrases that mean "this will never succeed on this key", as opposed to "you
# are going too fast". Matched case-insensitively against the 429 body.
PERMANENT_429_MARKERS = (
    "prepayment credits are depleted",
    "check your plan and billing",
    "billing details",
    "quota_limit_value",
    "free_tier",
    "exceeded your current quota",
)

# Transient markers win if both appear — a per-minute limit is the recoverable
# reading, and treating a recoverable error as fatal loses a cycle.
TRANSIENT_429_MARKERS = (
    "per minute",
    "requests per minute",
    "rate limit",
    "try again",
)


class PermanentModelError(RuntimeError):
    """The model cannot be reached with this key, ever. Fall back or park —
    do not retry, do not halt (AGENTS.md #5)."""


class TransientModelError(RuntimeError):
    """Rate limited or a transient 5xx. Backoff and retry is correct."""


def classify_429(body: str) -> type[Exception]:
    low = (body or "").lower()
    if any(m in low for m in TRANSIENT_429_MARKERS):
        return TransientModelError
    if any(m in low for m in PERMANENT_429_MARKERS):
        return PermanentModelError
    # Unrecognised 429: assume transient. Retrying a permanent failure costs a
    # few wasted seconds; treating a rate limit as permanent costs the cycle.
    return TransientModelError


# --------------------------------------------------------------------------- #
# pricing
# --------------------------------------------------------------------------- #

# CHARTER.md §6.5 forbids stating a number not read from a source, and these
# rates are NOT read from a source at runtime — they are operator-supplied
# assumptions used only to estimate spend. They are deliberately set high.
#
# The cap must fail safe: over-estimating trips the watchdog early, which costs
# a halt that can be cleared. Under-estimating spends real money past a ceiling
# that exists precisely to stop that. When a model has no entry, FALLBACK is
# used and an event is logged, so the guess is visible in the audit trail rather
# than silently baked into a total.
#
# USD per million tokens. Verify at ai.google.dev/gemini-api/docs/pricing.
PRICING: dict[str, tuple[float, float]] = {}          # model -> (input, output)
FALLBACK_PRICING: tuple[float, float] = (2.00, 12.00)  # deliberate over-estimate


def estimate_usd(model: str, input_tokens: int, billed_output_tokens: int
                 ) -> tuple[float, bool]:
    """Returns (usd, used_fallback). Reasoning tokens bill at the output rate,
    so callers pass output+thinking as billed_output_tokens."""
    rates = PRICING.get(model)
    used_fallback = rates is None
    inp, out = rates or FALLBACK_PRICING
    usd = (input_tokens / 1_000_000) * inp + (billed_output_tokens / 1_000_000) * out
    return round(usd, 6), used_fallback


# --------------------------------------------------------------------------- #
# usage
# --------------------------------------------------------------------------- #

@dataclass
class Usage:
    model: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    total_tokens: int
    usd_est: float
    priced_by_guess: bool

    @classmethod
    def from_metadata(cls, model: str, md: Any) -> "Usage":
        def g(name: str) -> int:
            v = getattr(md, name, None)
            if v is None and isinstance(md, dict):
                v = md.get(name)
            return int(v or 0)

        inp = g("prompt_token_count") or g("promptTokenCount")
        out = g("candidates_token_count") or g("candidatesTokenCount")
        think = g("thoughts_token_count") or g("thoughtsTokenCount")
        total = g("total_token_count") or g("totalTokenCount")

        # Trust the API's total. If reasoning tokens were not broken out but the
        # total exceeds the parts, the remainder IS reasoning and must be billed.
        if total and not think and total > inp + out:
            think = total - inp - out
        if not total:
            total = inp + out + think

        usd, guessed = estimate_usd(model, inp, out + think)
        return cls(model, inp, out, think, total, usd, guessed)


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #

class Gemini:
    """Thin wrapper over google-genai. One instance per key.

    `on_usage` is called after every successful call with a Usage; the runner
    wires it to Ledger.llm_usage so that metering cannot be forgotten at a call
    site (AGENTS.md #9).
    """

    def __init__(self, api_key: str, *, on_usage: Callable[[Usage], None] | None = None,
                 on_event: Callable[[str, str], None] | None = None,
                 max_attempts: int = 5):
        from google import genai  # imported here so the module can be unit-tested
        self._genai = genai
        self.client = genai.Client(api_key=api_key)
        self.on_usage = on_usage
        self.on_event = on_event
        self.max_attempts = max_attempts

    def _emit(self, level: str, message: str) -> None:
        if self.on_event:
            self.on_event(level, message)

    def generate(self, *, model: str, contents: Any, system_instruction: str | None = None,
                 tools: Any = None, thinking_level: str | None = None) -> Any:
        """One metered, serialised, backed-off call.

        Deliberately does not accept temperature/top_p/top_k — they are
        deprecated on Gemini 3.x and passing them is a documented mistake.
        """
        from google.genai import types

        cfg_kwargs: dict[str, Any] = {}
        if system_instruction:
            cfg_kwargs["system_instruction"] = system_instruction
        if tools:
            cfg_kwargs["tools"] = tools
        if thinking_level:
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=thinking_level)
        config = types.GenerateContentConfig(**cfg_kwargs) if cfg_kwargs else None

        delay = 2.0
        last_exc: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            with _CALL_LOCK:                      # serialise: ~15 RPM ceiling
                try:
                    resp = self.client.models.generate_content(
                        model=model, contents=contents, config=config)
                except Exception as exc:          # noqa: BLE001
                    last_exc = exc
                    kind = self._classify(exc)
                    if kind is PermanentModelError:
                        raise PermanentModelError(
                            f"{model}: permanent failure, not retryable: "
                            f"{str(exc)[:300]}") from exc
                    if attempt == self.max_attempts:
                        break
                    sleep = delay + random.uniform(0, 1.0)
                    self._emit("warn",
                               f"{model}: attempt {attempt}/{self.max_attempts} failed "
                               f"({type(exc).__name__}); retrying in {sleep:.1f}s")
                    time.sleep(sleep)
                    delay = min(delay * 2, 60.0)
                    continue

            usage = Usage.from_metadata(model, getattr(resp, "usage_metadata", None))
            if usage.priced_by_guess:
                self._emit("warn",
                           f"{model}: no pricing entry; usd_est uses the deliberately "
                           f"high fallback {FALLBACK_PRICING} USD/Mtok. The figure in "
                           "llm_usage is an over-estimate, not a read price.")
            if self.on_usage:
                self.on_usage(usage)
            return resp

        raise TransientModelError(
            f"{model}: {self.max_attempts} attempts failed; last error: "
            f"{str(last_exc)[:300]}")

    @staticmethod
    def _classify(exc: Exception) -> type[Exception]:
        text = str(exc)
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if code == 429 or "429" in text or "RESOURCE_EXHAUSTED" in text:
            return classify_429(text)
        if code in (401, 403) or "PERMISSION_DENIED" in text or "API_KEY_INVALID" in text:
            return PermanentModelError
        if code == 404 or "NOT_FOUND" in text:
            return PermanentModelError
        return TransientModelError


def planning_model(cfg, ledger, gemini: Gemini) -> str:
    """Pick the planning model, honouring the daily cap and the fallback rule.

    AGENTS.md #5: if the planning model errors or 404s, fall back to
    planning_fallback and log an event — do NOT halt. Preview models get pulled,
    and losing the daily plan is not worth stopping the run.
    """
    primary = cfg.model_for("planning")
    fallback = cfg.model_for("planning_fallback")
    if ledger.planning_calls_today(primary) >= cfg.max_planning_calls_per_day:
        ledger.event("info", "llm",
                     f"planning cap reached for {primary} "
                     f"({cfg.max_planning_calls_per_day}/day); using {fallback}")
        return fallback
    return primary


def api_key(role: str = "ops") -> str:
    """Key selection is explicit, never inferred.

    invariants.toml pins user_data_key = "paid": the free tier may use prompts
    for training and the Operator is the data controller (CHARTER.md §3.4). The
    ops container simply does not hold the paid key, and the app container does
    not hold the free one — absent, not forbidden.
    """
    var = {"ops": "GOOGLE_API_KEY", "app": "GOOGLE_API_KEY"}[role]
    key = os.environ.get(var)
    if not key:
        raise RuntimeError(f"{var} is not set in this container's environment")
    return key
