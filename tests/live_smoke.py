"""Live smoke test: one real Gemini call, end to end through the runner.

Deliberately NOT named test_* — `unittest discover` must not pick it up, because
it spends money and needs a key. Run it by hand after changing runner/llm.py:

    docker compose run --rm --no-deps --entrypoint python3 ops tests/live_smoke.py

What it proves that the unit tests cannot: that the SDK surface we call is real,
that usage metadata parses, and that a call lands in llm_usage with reasoning
tokens counted.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner import config, llm          # noqa: E402
from runner.ledger import Ledger        # noqa: E402

SCHEMA_SRC = Path(__file__).resolve().parents[1] / "situation_report.py"
text = SCHEMA_SRC.read_text()
_start = text.index('SCHEMA = """') + len('SCHEMA = """')
SCHEMA = text[_start:text.index('"""', _start)]

cfg = config.load()
tmp = tempfile.TemporaryDirectory()
path = str(Path(tmp.name) / "ledger.db")
con = sqlite3.connect(path)
con.executescript(SCHEMA)
con.commit()
con.close()

led = Ledger(path)
model = cfg.model_for("work")

client = llm.Gemini(
    llm.api_key("ops"),
    on_usage=lambda u: led.llm_usage(
        model=u.model, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
        thinking_tokens=u.thinking_tokens, total_tokens=u.total_tokens,
        usd_est=u.usd_est, cycle_id=None),
    on_event=lambda level, msg: print(f"  [{level}] {msg}"),
)

print(f"calling {model} ...")
resp = client.generate(
    model=model,
    contents="Name the capital of Sweden. One word.",
    system_instruction="You are terse.",
    thinking_level="low",
)
print("  reply:", (resp.text or "").strip()[:80])

row = led.con.execute(
    "SELECT model, input_tokens, output_tokens, thinking_tokens, total_tokens, "
    "usd_est FROM llm_usage ORDER BY id DESC LIMIT 1").fetchone()

print("\nllm_usage row written:")
if row is None:
    print("  *** NOTHING LOGGED — metering is broken ***")
    sys.exit(1)
for k in row.keys():
    print(f"  {k:16} {row[k]}")

parts = row["input_tokens"] + row["output_tokens"] + row["thinking_tokens"]
print(f"\n  in+out+thinking = {parts}   total = {row['total_tokens']}")
if parts != row["total_tokens"]:
    print("  *** parts do not reconcile with the billed total ***")
    sys.exit(1)
print("  reconciles.")

print(f"\nledger spend so far: ${led.spend_total_usd():.6f}")
led.close()
tmp.cleanup()
