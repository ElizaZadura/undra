"""One cycle, start to finish.

Order is fixed by AGENTS.md:

  1. situation_report.py runs.
       exit 10 -> write a state summary, exit, NO MODEL CALL
       exit 20 -> treat as halt
       exit  0 -> continue
  2. Its stdout is prepended to context, after CHARTER.md
  3. Coral decides and acts through tools
  4. Handoff -> cycles.handoff, status -> cycles.status
  5. Process exits

Coral is stateless between cycles. Nothing in this module may persist state to
memory, a cache, or a conversation history that outlives the process. The entire
picture of reality is the situation report, every time. A runner that helpfully
carried context forward would defeat the design (HANDOFF.md §3).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, llm, tools
from .ledger import CycleRecorder, Halted, Ledger

MAX_TOOL_TURNS = 24        # bounded: a cycle that cannot finish is a failed cycle
SITUATION_REPORT = "situation_report.py"
DUMP_PATH = Path("reports/ledger-dump.json")

CHARTER_PATH = Path("CHARTER.md")


TASK_PROMPT = """\
You are Coral, the operating agent for undra.

The situation report below is your ONLY source of operational fact for this
cycle (CHARTER.md §6.2). If something is not in it, you do not know it. An
UNKNOWN is not zero, not healthy, and not the value it had last cycle.

You have no memory of previous cycles. What you know about the past is what the
handoff and the ledger sections below say.

Work the objectives in priority order. Most of what you need is already
authorised by CHARTER.md §5 — read it before asking for permission you already
have, because cycles spent asking are the most expensive mistake available.

Call finish_cycle exactly once, last, with a handoff written for a stranger.
If you do not understand the state, say precisely what is unclear and finish —
that is a good cycle. Confident action on a misread state is how this fails.

--- SITUATION REPORT ---
{report}
--- END SITUATION REPORT ---
"""


def run_situation_report(python: str = sys.executable) -> tuple[int, str]:
    """Deterministic pre-cycle briefing. Assembled by code that cannot
    rationalise, round up, or carry a stale value forward out of optimism."""
    proc = subprocess.run(
        [python, SITUATION_REPORT], capture_output=True, text=True, timeout=120)
    if proc.stderr.strip():
        print(f"[situation_report stderr] {proc.stderr.strip()[:2000]}",
              file=sys.stderr)
    return proc.returncode, proc.stdout


def _charter() -> str:
    try:
        return CHARTER_PATH.read_text()
    except OSError:
        return ""


class StubModel:
    """Used by --stub-model to exercise the whole cycle without spending money
    or needing a key. Returns one finish_cycle call, nothing else."""

    def __init__(self, handoff: str = "Stub cycle: no model was called. The "
                                      "runner path was exercised end to end and "
                                      "the ledger was written."):
        self.handoff = handoff
        self.calls = 0

    def generate(self, **kwargs) -> Any:
        self.calls += 1
        return _StubResponse(self.handoff)


class _StubResponse:
    def __init__(self, handoff: str):
        self.function_calls = [_StubCall("finish_cycle", {"handoff": handoff})]
        self.text = ""
        self.usage_metadata = None


class _StubCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args


def run(*, stub_model: bool = False, use_telegram: bool = True) -> int:
    """Returns the process exit code."""
    cfg = config.load()

    rc, report = run_situation_report()

    if rc == 20:
        # The report itself could not be produced. Treat as halt: acting on a
        # picture of reality that failed to assemble is the failure mode this
        # whole design exists to prevent.
        print("situation_report.py exited 20 — the report could not be "
              "produced. Treating as halt; no model call.", file=sys.stderr)
        try:
            led = Ledger(cfg.ledger_path)
            led.event("error", "cycle",
                      "situation_report exit 20: report unavailable, cycle aborted")
            led.close()
        except Exception:  # noqa: BLE001
            pass
        return 20

    led = Ledger(cfg.ledger_path)
    cyc = CycleRecorder.start(led)

    tg = None
    if use_telegram:
        try:
            from .telegram import Telegram, process_updates
            tg = Telegram()
            process_updates(tg, led, tg.poll())
        except Exception as exc:  # noqa: BLE001
            led.event("warn", "telegram", f"channel unavailable: {exc}")
            tg = None

    # Halt is checked here AND before every action inside dispatch(). This one
    # exists so that a halted cycle never reaches a model call at all.
    if rc == 10 or led.is_halted():
        reason = led.halt_reason() or "invariant breach reported by the watchdog"
        summary = (
            f"HALTED at {datetime.now(timezone.utc).isoformat(timespec='seconds')}. "
            f"Reason: {reason}. No action taken, no model call made. "
            "Per CHARTER.md §10 this cycle did not investigate the cause and did "
            "not attempt to clear the flag.")
        cyc.note_halted()
        status = cyc.end(summary)
        led.event("warn", "cycle", f"cycle {cyc.id} halted before any model call")
        _dump(led)
        led.close()
        print(f"cycle {cyc.id}: {status} — no model call")
        return 10

    ctx = tools.ToolContext(cfg=cfg, ledger=led, cycle=cyc, telegram=tg)

    if stub_model:
        client: Any = StubModel()
        model = "stub"
    else:
        client = llm.Gemini(
            llm.api_key("ops"),
            on_usage=lambda u: led.llm_usage(
                model=u.model, input_tokens=u.input_tokens,
                output_tokens=u.output_tokens, thinking_tokens=u.thinking_tokens,
                total_tokens=u.total_tokens, usd_est=u.usd_est, cycle_id=cyc.id),
            on_event=lambda level, msg: led.event(level, "llm", msg),
        )
        model = cfg.model_for("work")

    handoff = ""
    try:
        handoff = _agent_loop(ctx, client, model, report, stub_model=stub_model)
    except Halted:
        cyc.note_halted()
        handoff = ("Halt flag was set mid-cycle. Stopped immediately without "
                   "completing further actions (CHARTER.md §10).")
    except llm.PermanentModelError as exc:
        led.event("error", "llm", f"permanent model failure: {exc}")
        handoff = (f"Could not run: the model was permanently unavailable with "
                   f"this key ({exc}). This is not a rate limit and retrying will "
                   "not help. Escalated as an open question.")
        led.open_question(f"Model permanently unavailable: {exc}", blocking=True)
    except Exception as exc:  # noqa: BLE001
        led.event("error", "cycle", f"cycle failed: {type(exc).__name__}: {exc}")
        handoff = (f"Cycle aborted by an unhandled error: {type(exc).__name__}: "
                   f"{exc}. State should be treated as unverified by the next "
                   "cycle.")

    status = cyc.end(handoff or "No handoff was written; treat prior state as "
                                "unverified.")
    _dump(led)
    spend = led.spend_total_usd()
    led.close()

    print(f"cycle {cyc.id}: {status}  model={model}  spend_total=${spend:.4f}")
    return 0


def _agent_loop(ctx: tools.ToolContext, client: Any, model: str, report: str,
                *, stub_model: bool) -> str:
    """Run the model until it calls finish_cycle or the turn budget runs out."""
    from_sdk = not stub_model
    if from_sdk:
        from google.genai import types
        tool_defs = [types.Tool(function_declarations=tools.declarations())]
        contents: Any = [types.Content(
            role="user",
            parts=[types.Part(text=TASK_PROMPT.format(report=report))])]
    else:
        tool_defs = None
        contents = TASK_PROMPT.format(report=report)

    charter = _charter()
    handoff = ""

    for turn in range(1, MAX_TOOL_TURNS + 1):
        resp = client.generate(model=model, contents=contents,
                               system_instruction=charter, tools=tool_defs,
                               thinking_level="low")

        calls = list(getattr(resp, "function_calls", None) or [])
        if not calls:
            # No tool call: the model answered in prose. That is not a cycle
            # outcome, so nudge once and then give up rather than looping.
            text = (getattr(resp, "text", "") or "").strip()
            ctx.ledger.event("warn", "cycle",
                             f"turn {turn}: model produced prose, not a tool call: "
                             f"{text[:400]!r}")
            if turn >= 2:
                return handoff or (
                    "Cycle ended without a handoff: the model stopped calling "
                    f"tools. Last text: {text[:500]}")
            if from_sdk:
                from google.genai import types
                contents.append(types.Content(role="model",
                                              parts=[types.Part(text=text or " ")]))
                contents.append(types.Content(role="user", parts=[types.Part(
                    text="Call a tool. End with finish_cycle.")]))
            continue

        if from_sdk:
            from google.genai import types
            contents.append(resp.candidates[0].content)
            parts = []

        for call in calls:
            args = dict(call.args or {})
            result = tools.dispatch(ctx, call.name, args)
            ctx.ledger.event("info", "tool",
                             f"{call.name}({json_preview(args)}) -> "
                             f"{json_preview(result)}")
            if call.name == "finish_cycle" and "error" not in result:
                return args.get("handoff", "")
            if from_sdk:
                parts.append(types.Part.from_function_response(
                    name=call.name, response=result))

        if from_sdk:
            contents.append(types.Content(role="user", parts=parts))

    ctx.ledger.event("warn", "cycle",
                     f"turn budget of {MAX_TOOL_TURNS} exhausted without finish_cycle")
    return handoff or ("Cycle hit its turn budget without calling finish_cycle. "
                       "Whatever it was doing was not converging; the next cycle "
                       "should re-read the objectives before continuing.")


def json_preview(obj: Any, limit: int = 300) -> str:
    import json
    try:
        s = json.dumps(obj, default=str)
    except Exception:  # noqa: BLE001
        s = str(obj)
    return s[:limit]


def _dump(led: Ledger) -> None:
    """AGENTS.md #12: the raw ledger lives only on `red`; this redacted dump is
    the offsite backup of the audit trail and the competition's execution-log
    evidence."""
    try:
        counts = led.dump_redacted(DUMP_PATH)
        led.event("info", "dump", f"redacted ledger dump written: {counts}")
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not write ledger dump: {exc}", file=sys.stderr)
