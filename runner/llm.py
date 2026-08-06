"""Gemini client: serialised, metered, and honest about what a 429 means.

Design notes that are load-bearing:

  - **Serialised AND paced.** Free tier is ~15 RPM and RPM bites long before RPD
    (HANDOFF.md §4). A module-level lock makes serialisation true even if a
    future caller gets clever — but serialising alone was not enough: six agent
    turns fired back-to-back exceeded the limit and aborted live cycle #4. The
    calls are therefore spaced by MIN_CALL_INTERVAL_S as well.

  - **429s are transient unless proven otherwise.** The free tier returns
    identical wording — "exceeded your current quota, please check your plan and
    billing details" — both when a model is off-tier entirely and when the
    per-minute limit is hit. That string is therefore useless for classifying,
    and an earlier version of this file that trusted it killed a working cycle.
    Only a depleted prepaid balance is treated as permanent. See classify_429().

  - **No sampling parameters.** temperature, top_p and top_k are deprecated on
    Gemini 3.x and are deliberately never set (AGENTS.md #6). Use thinking_level.

  - **Every call is metered**, including reasoning tokens, because llm_usage is
    the only spend cap that exists (the API has no hard billing cap).
"""

from __future__ import annotations

import os
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

_CALL_LOCK = threading.Lock()

# Free tier is ~15 RPM, and RPM bites long before RPD (HANDOFF.md §4).
# Serialising the calls is not enough on its own: six agent turns fired
# back-to-back blew the limit and aborted a live cycle on 2026-08-06. Pace them.
# 15 RPM is one per 4s; 4.5s leaves headroom for clock skew and the planning
# call sharing the same minute.
MIN_CALL_INTERVAL_S = 4.5
_last_call_at = 0.0

# Models we have already warned about pricing for, so the warning does not
# repeat on every call and bury the events that only happen once.
_warned_unpriced: set[str] = set()


def _pace() -> None:
    """Block until at least MIN_CALL_INTERVAL_S has passed since the last call.
    Called with _CALL_LOCK held, so this is the only pacer."""
    global _last_call_at
    wait = MIN_CALL_INTERVAL_S - (time.monotonic() - _last_call_at)
    if wait > 0:
        time.sleep(wait)
    _last_call_at = time.monotonic()

# Phrases that mean "this will never succeed on this key", as opposed to "you
# are going too fast". Matched case-insensitively against the 429 body.
#
# CORRECTED 2026-08-06, after this misclassification aborted a live cycle.
# The free tier returns the SAME text — "You exceeded your current quota, please
# check your plan and billing details" — for two completely different things:
#
#   (a) this model is not available on your tier at all   -> permanent
#   (b) you have exceeded the per-minute request limit    -> transient
#
# So that wording carries no information and must not appear below. Only the
# prepaid-balance message is unambiguous, because a depleted balance genuinely
# cannot be retried around.
#
# Everything else defaults to transient, deliberately. The costs are asymmetric:
# retrying a permanent failure wastes a few seconds of backoff, while treating a
# rate limit as permanent loses the whole cycle — which is exactly what happened.
PERMANENT_429_MARKERS = (
    "prepayment credits are depleted",
    "prepayment credits",
)


class PermanentModelError(RuntimeError):
    """The model cannot be reached with this key, ever. Fall back or park —
    do not retry, do not halt (AGENTS.md #5)."""


class TransientModelError(RuntimeError):
    """Rate limited or a transient 5xx. Backoff and retry is correct."""


def classify_429(body: str) -> type[Exception]:
    low = (body or "").lower()
    if any(m in low for m in PERMANENT_429_MARKERS):
        return PermanentModelError
    return TransientModelError


def retry_delay_seconds(body: str) -> float | None:
    """Honour the API's own RetryInfo when it supplies one, rather than guessing.
    Matches `"retryDelay": "31s"` in the error payload."""
    m = re.search(r'"retrydelay"\s*:\s*"(\d+(?:\.\d+)?)s"', (body or "").lower())
    return float(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# pricing
# --------------------------------------------------------------------------- #

# USD per million tokens, paid tier, standard (not batch).
#
# READ FROM https://ai.google.dev/gemini-api/docs/pricing on 2026-08-06. These
# are transcribed figures, not estimates — which is what CHARTER.md §6.5
# requires before a number reaches the Operator or the submission. Re-read the
# page rather than adjusting these by intuition; prices move.
#
# Thinking tokens bill at the OUTPUT rate on every model here, which the page
# states explicitly. Usage.from_metadata therefore passes output+thinking.
PRICING: dict[str, tuple[float, float]] = {          # model -> (input, output)
    "gemini-3.6-flash":        (1.50, 7.50),
    "gemini-3.5-flash":        (1.50, 9.00),
    "gemini-3.5-flash-lite":   (0.30, 2.50),
    "gemini-3.1-flash-lite":   (0.25, 1.50),
    "gemini-3.1-pro-preview":  (2.00, 12.00),
    "gemini-3-flash-preview":  (0.50, 3.00),
    "gemini-3-pro-preview":    (2.00, 12.00),
}

# Some models charge more above a prompt-size threshold. Ignoring this would
# undercount exactly when a cycle is at its most expensive — a long situation
# report plus a full charter is how a prompt gets big.
LARGE_PROMPT_THRESHOLD = 200_000
LARGE_PROMPT_PRICING: dict[str, tuple[float, float]] = {
    "gemini-3.1-pro-preview": (4.00, 18.00),
    "gemini-3-pro-preview":   (4.00, 18.00),
}

# Used only for a model absent from the table above — a new release, or a
# rename. Deliberately set to the most expensive entry rather than an average:
# the cap must fail safe. Over-estimating trips the watchdog early, which costs
# a halt that can be cleared; under-estimating spends real money past a ceiling
# that exists precisely to stop that. Its use is always logged, so a guess is
# visible in the audit trail rather than silently baked into a total.
FALLBACK_PRICING: tuple[float, float] = (4.00, 18.00)


def estimate_usd(model: str, input_tokens: int, billed_output_tokens: int
                 ) -> tuple[float, bool]:
    """Returns (usd, used_fallback). Reasoning tokens bill at the output rate,
    so callers pass output+thinking as billed_output_tokens."""
    if input_tokens > LARGE_PROMPT_THRESHOLD and model in LARGE_PROMPT_PRICING:
        rates: tuple[float, float] | None = LARGE_PROMPT_PRICING[model]
    else:
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
    cached_tokens: int = 0

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

        # Implicit caching is documented as on by default for Gemini 2.5+, but
        # measured zero on 2026-08-06 at a 4,365-token prompt with a stable
        # 4,358-token system instruction, on both the free and paid keys.
        # Captured rather than assumed: if it starts working, spend drops and we
        # should be able to see that rather than infer it. Cached tokens bill at
        # a discount, so counting them at full rate over-estimates — the safe
        # direction for a figure that drives a halt.
        cached = g("cached_content_token_count") or g("cachedContentTokenCount")

        # Trust the API's total. If reasoning tokens were not broken out but the
        # total exceeds the parts, the remainder IS reasoning and must be billed.
        if total and not think and total > inp + out:
            think = total - inp - out
        if not total:
            total = inp + out + think

        usd, guessed = estimate_usd(model, inp, out + think)
        return cls(model, inp, out, think, total, usd, guessed, cached)


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
                _pace()
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
                    # Prefer the API's own RetryInfo over our guess.
                    suggested = retry_delay_seconds(str(exc))
                    sleep = suggested if suggested else delay + random.uniform(0, 1.0)
                    self._emit("warn",
                               f"{model}: attempt {attempt}/{self.max_attempts} failed "
                               f"({type(exc).__name__}); retrying in {sleep:.1f}s"
                               + (" (API-supplied delay)" if suggested else ""))
                    time.sleep(sleep)
                    delay = min(delay * 2, 60.0)
                    continue

            usage = Usage.from_metadata(model, getattr(resp, "usage_metadata", None))
            if usage.priced_by_guess and model not in _warned_unpriced:
                # Once per model per process. The warning matters, but repeating
                # it on every call buries the events that only happen once.
                _warned_unpriced.add(model)
                self._emit("warn",
                           f"{model}: no pricing entry; usd_est uses the deliberately "
                           f"high fallback {FALLBACK_PRICING} USD/Mtok. The figure in "
                           "llm_usage is an over-estimate, not a read price. "
                           "(Logged once per model per cycle.)")
            if usage.cached_tokens:
                self._emit("info",
                           f"{model}: {usage.cached_tokens} of {usage.input_tokens} "
                           "prompt tokens served from cache")
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
    app container does not hold the free key at all — absent, not forbidden.

    The ops container holds both, which is not a contradiction: AGENTS.md #11 is
    one-directional. The APP must not see the FREE key, because that is the
    container handling user data. Nothing forbids ops holding the paid one, and
    it needs to, because the daily planning call uses a Pro model that the free
    tier returns a permanent 429 for.

      "ops"      free key  — the cycle loop, ~180 calls/day, costs nothing
      "planning" paid key  — one Pro call/day over the situation report
      "app"      paid key  — the deployed product (different container)
    """
    var = {"ops": "GOOGLE_API_KEY",
           "planning": "GOOGLE_API_KEY_PAID",
           "app": "GOOGLE_API_KEY"}[role]
    key = os.environ.get(var)
    if not key:
        raise RuntimeError(f"{var} is not set in this container's environment")
    return key


def has_key(role: str) -> bool:
    try:
        api_key(role)
        return True
    except (KeyError, RuntimeError):
        return False
