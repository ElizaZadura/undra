"""The checks AGENTS.md requires before declaring the runner done.

  "a halt flag stops it before any model call; an unproductive cycle records a
   non-`ok` status; a Telegram send does not appear in `outbound`."

Plus the two facts found by probing the live API on 2026-08-06: that a 429 can
be permanent, and that reasoning tokens are billed but appear in neither
promptTokenCount nor candidatesTokenCount.

stdlib unittest, no pytest — one fewer dependency in the image.
Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner.ledger import (  # noqa: E402
    AlreadyDone, CycleRecorder, Halted, Ledger, UNPRODUCTIVE,
)
from runner.llm import (  # noqa: E402
    PermanentModelError, TransientModelError, Usage, classify_429, estimate_usd,
)

SCHEMA_SOURCE = Path(__file__).resolve().parents[1] / "situation_report.py"


def _schema() -> str:
    """Use the real schema from situation_report.py, not a copy. A test that
    drifts from the shipped schema tests nothing."""
    text = SCHEMA_SOURCE.read_text()
    start = text.index('SCHEMA = """') + len('SCHEMA = """')
    return text[start:text.index('"""', start)]


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "ledger.db")
        con = sqlite3.connect(self.path)
        con.executescript(_schema())
        con.commit()
        con.close()
        self.led = Ledger(self.path)

    def tearDown(self):
        self.led.close()
        self.tmp.cleanup()

    # -- AGENTS.md #4: halt is checked before every action ------------------ #

    def test_halt_flag_blocks_action_before_it_happens(self):
        self.led.con.execute(
            "INSERT INTO flags(key, value, updated_at, reason) "
            "VALUES('halt','true',datetime('now'),'test')")
        self.led.con.commit()

        performed = []
        with self.assertRaises(Halted):
            with self.led.action(kind="DEPLOY", target="svc", idempotency_key="k1"):
                performed.append("side effect")

        self.assertEqual(performed, [], "action body ran despite the halt flag")
        n = self.led.con.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
        self.assertEqual(n, 0, "a halted action was still recorded as attempted")

    def test_halt_is_reread_not_cached(self):
        """The Operator sets the flag from her phone mid-cycle. A cached read
        would let the rest of the cycle keep acting (CHARTER.md §10)."""
        with self.led.action(kind="NOOP", target="a", idempotency_key="k1"):
            pass
        self.led.con.execute(
            "INSERT INTO flags(key, value, updated_at, reason) "
            "VALUES('halt','true',datetime('now'),'set mid-cycle')")
        self.led.con.commit()
        with self.assertRaises(Halted):
            with self.led.action(kind="NOOP", target="b", idempotency_key="k2"):
                pass

    # -- AGENTS.md #3: idempotency ------------------------------------------ #

    def test_replayed_idempotency_key_does_not_act_twice(self):
        calls = []
        with self.led.action(kind="PAYMENT", target="inv-7", idempotency_key="pay-7"):
            calls.append(1)
        with self.assertRaises(AlreadyDone):
            with self.led.action(kind="PAYMENT", target="inv-7", idempotency_key="pay-7"):
                calls.append(2)
        self.assertEqual(calls, [1], "the same idempotency key paid twice")

    def test_missing_idempotency_key_is_refused(self):
        with self.assertRaises(ValueError):
            with self.led.action(kind="PAYMENT", target="inv-8", idempotency_key=""):
                pass

    def test_failure_is_recorded_and_reraised(self):
        with self.assertRaises(ZeroDivisionError):
            with self.led.action(kind="DEPLOY", target="svc", idempotency_key="k9"):
                _ = 1 / 0
        row = self.led.con.execute(
            "SELECT status, error FROM actions WHERE idempotency_key='k9'").fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("ZeroDivisionError", row["error"])

    # -- AGENTS.md #2: Telegram must not reach `outbound` ------------------- #

    def test_telegram_to_operator_is_refused_by_outbound(self):
        with self.assertRaises(ValueError):
            self.led.record_outbound(channel="telegram", recipient_hash="x",
                                     subject="approval needed",
                                     approval_request_id=None)
        n = self.led.con.execute("SELECT COUNT(*) FROM outbound").fetchone()[0]
        self.assertEqual(n, 0, "a Telegram message reached the outbound rate limiter")

    def test_third_party_email_does_reach_outbound(self):
        self.led.record_outbound(channel="email", recipient_hash="deadbeef",
                                 subject="re: your question",
                                 approval_request_id=None)
        n = self.led.con.execute("SELECT COUNT(*) FROM outbound").fetchone()[0]
        self.assertEqual(n, 1)

    # -- AGENTS.md #1: cycles.status must be honest ------------------------- #

    def test_unproductive_cycle_is_not_ok(self):
        cyc = CycleRecorder.start(self.led)
        status = cyc.end("woke, found nothing actionable")
        self.assertIn(status, UNPRODUCTIVE)
        self.assertNotEqual(status, "ok")

    def test_blocked_cycle_records_blocked(self):
        cyc = CycleRecorder.start(self.led)
        cyc.note_blocked()
        self.assertEqual(cyc.end("waiting on approval"), "blocked")

    def test_productive_cycle_may_be_ok(self):
        cyc = CycleRecorder.start(self.led)
        cyc.note_productive()
        self.assertEqual(cyc.end("shipped the healthz route"), "ok")

    def test_caller_cannot_force_a_dishonest_ok(self):
        """The signal this protects is the only way Eliza learns the limits are
        the bottleneck, and losing it produces no error anywhere."""
        cyc = CycleRecorder.start(self.led)
        self.assertEqual(cyc.end("nothing happened", status="ok"), "idle")
        warn = self.led.con.execute(
            "SELECT COUNT(*) FROM events WHERE level='warn' AND source='cycle'"
        ).fetchone()[0]
        self.assertEqual(warn, 1, "the forced 'ok' was downgraded silently")

    # -- CHARTER.md §9: a request without a default action is malformed ----- #

    def test_request_without_default_action_is_refused(self):
        with self.assertRaises(ValueError):
            self.led.request_human(kind="PUBLISH", payload="landing copy",
                                   priority="digest", deadline=None,
                                   default_action="")

    # -- AGENTS.md #12: the committed dump is redacted ---------------------- #

    def test_dump_omits_payment_detail_and_recipients(self):
        self.led.con.execute(
            "INSERT INTO payments(at, provider, external_id, gross_usd, net_usd, "
            "related_party) VALUES(datetime('now'),'kofi','ext-1',5.0,4.5,0)")
        self.led.record_outbound(channel="email", recipient_hash="abc123",
                                 subject="a subject line",
                                 approval_request_id=None)
        self.led.con.commit()

        dest = Path(self.tmp.name) / "dump.json"
        self.led.dump_redacted(dest)
        text = dest.read_text()

        self.assertNotIn("ext-1", text, "payment detail leaked into the public dump")
        self.assertNotIn("abc123", text, "recipient hash leaked into the public dump")
        self.assertNotIn("a subject line", text, "subject leaked into the public dump")
        self.assertIn('"row_count": 1', text, "payments should still be counted")


class LlmTest(unittest.TestCase):
    """Encodes what the live API actually did on 2026-08-06."""

    def test_depleted_prepay_429_is_permanent(self):
        body = ("Your prepayment credits are depleted. Please go to AI Studio "
                "to manage your project and billing.")
        self.assertIs(classify_429(body), PermanentModelError)

    def test_free_tier_pro_429_is_permanent(self):
        body = ("You exceeded your current quota, please check your plan and "
                "billing details.")
        self.assertIs(classify_429(body), PermanentModelError)

    def test_per_minute_429_is_transient(self):
        self.assertIs(classify_429("Quota exceeded: requests per minute"),
                      TransientModelError)

    def test_unrecognised_429_is_treated_as_transient(self):
        """Retrying a permanent failure wastes seconds; treating a rate limit
        as permanent wastes the cycle."""
        self.assertIs(classify_429("something new and unhelpful"),
                      TransientModelError)

    def test_thinking_tokens_are_billed_even_when_not_broken_out(self):
        """The measured case: 7 in, 1 out, 109 total. The 101 unexplained
        tokens are reasoning and must be billed, or the cap undercounts ~12x."""
        u = Usage.from_metadata("gemini-3.6-flash", {
            "promptTokenCount": 7, "candidatesTokenCount": 1, "totalTokenCount": 109})
        self.assertEqual(u.thinking_tokens, 101)
        self.assertEqual(u.total_tokens, 109)

        naive, _ = estimate_usd("gemini-3.6-flash", 7, 1)
        self.assertGreater(u.usd_est, naive * 10,
                           "reasoning tokens are not reaching the cost estimate")

    def test_explicit_thoughts_field_is_respected(self):
        u = Usage.from_metadata("gemini-3.6-flash", {
            "promptTokenCount": 10, "candidatesTokenCount": 5,
            "thoughtsTokenCount": 40, "totalTokenCount": 55})
        self.assertEqual(u.thinking_tokens, 40)

    def test_unpriced_model_is_flagged_not_silently_zero(self):
        _, guessed = estimate_usd("some-unreleased-model", 1000, 1000)
        self.assertTrue(guessed, "an unpriced model silently produced a cost figure")


if __name__ == "__main__":
    unittest.main(verbosity=2)
