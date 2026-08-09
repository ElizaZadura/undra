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

import inspect
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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

    # -- the Telegram backlog must not replay -------------------------------- #

    def test_telegram_offset_round_trips(self):
        """Without a persisted offset every cycle re-reads the whole backlog.
        Harmless for `approve N`, but a single old `/halt` would be re-applied
        forever: the Operator clears the flag and the next cycle sets it again,
        with the cause hours in the past and invisible."""
        from runner.telegram import get_offset, set_offset
        self.assertIsNone(get_offset(self.led))
        set_offset(self.led, 444293522)
        self.assertEqual(get_offset(self.led), 444293522)
        set_offset(self.led, 444293999)
        self.assertEqual(get_offset(self.led), 444293999)

    def test_telegram_offset_does_not_disturb_the_halt_flag(self):
        from runner.telegram import set_offset
        set_offset(self.led, 12345)
        self.assertFalse(self.led.is_halted(),
                         "writing the Telegram offset altered the halt flag")

    # -- messages are not outbound, and are read once ------------------------ #

    def test_messages_never_touch_the_outbound_rate_limiter(self):
        """The Operator is not a third party. `outbound` halts at three an hour,
        so a conversation with her must not be recorded there (AGENTS.md #2)."""
        self.led.message("to_operator", "the deploy is up")
        self.led.message("from_operator", "focus on the guardrail")
        n = self.led.con.execute("SELECT COUNT(*) FROM outbound").fetchone()[0]
        self.assertEqual(n, 0, "a message to the Operator reached `outbound`")

    def test_a_note_is_surfaced_once_not_forever(self):
        """Unread notes appear in every situation report. A note that is never
        marked read is re-read as new by every subsequent cycle, each of which
        is a different instance with no memory of the last."""
        mid = self.led.message("from_operator", "please check the pant page")
        self.assertEqual(len(self.led.unread_from_operator()), 1)
        self.led.mark_messages_read([mid], cycle_id=1)
        self.assertEqual(self.led.unread_from_operator(), [])

    def test_direction_must_be_one_of_two_values(self):
        with self.assertRaises(ValueError):
            self.led.message("sideways", "hello")

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


class PlanApprovalTest(unittest.TestCase):
    """requirePlanApproval puts a human between the agent and code that touches
    payments, auth or user data. An agent that can approve its own plans has no
    such gate, so the capability must exist only on the Operator's channel."""

    def test_coral_has_no_plan_approval_tool(self):
        from runner import tools
        names = set(tools.TOOL_IMPLS) | {t["name"] for t in tools.declarations()}
        for n in names:
            self.assertNotIn("approve", n.lower(),
                             f"tool {n!r} looks like it could approve something")

    def test_approve_plan_is_called_only_from_the_operator_channel(self):
        root = Path(__file__).resolve().parents[1]
        callers = []
        for f in (root / "runner").glob("*.py"):
            if "approve_plan(" in f.read_text() and f.name != "jules.py":
                callers.append(f.name)
        self.assertEqual(callers, ["telegram.py"],
                         f"approve_plan reachable from {callers}; it must be "
                         "callable only from the Operator's Telegram channel")

    def test_the_operator_channel_checks_the_chat_id_first(self):
        """The command is only as safe as the check that the sender is Eliza."""
        src = (Path(__file__).resolve().parents[1] / "runner" / "telegram.py").read_text()
        guard = src.index('if u.chat_id != str(tg.chat_id)')
        approve = src.index('parts[1] == "jules"')
        self.assertLess(guard, approve,
                        "the chat-id check must precede the Jules approval branch")


class CiVerdictTest(unittest.TestCase):
    """CHARTER.md §5 authorises merging PRs *that pass CI*. Absence of CI is not
    a pass — a repository with no checks has demonstrated nothing, and treating
    that as permission would let an agent merge unverified code into main."""

    def _verdict(self, runs, combined_state, has_statuses=True):
        from unittest.mock import patch
        from runner.github import GitHub
        gh = GitHub.__new__(GitHub)
        gh.repo, gh.token = "x/y", "t"
        payloads = [{"check_runs": runs},
                    {"state": combined_state,
                     "statuses": [{}] if has_statuses else []}]
        with patch.object(GitHub, "_call", side_effect=payloads):
            return gh.checks("deadbeef")["verdict"]

    def test_no_checks_is_not_a_pass(self):
        self.assertEqual(self._verdict([], None, has_statuses=False), "none")

    def test_failing_check_is_fail(self):
        self.assertEqual(
            self._verdict([{"name": "t", "status": "completed",
                            "conclusion": "failure"}], None), "fail")

    def test_running_check_is_pending(self):
        self.assertEqual(
            self._verdict([{"name": "t", "status": "in_progress"}], None), "pending")

    def test_all_green_is_pass(self):
        self.assertEqual(
            self._verdict([{"name": "t", "status": "completed",
                            "conclusion": "success"}], "success"), "pass")

    def test_one_failure_among_passes_is_fail(self):
        self.assertEqual(
            self._verdict([{"name": "a", "status": "completed", "conclusion": "success"},
                           {"name": "b", "status": "completed", "conclusion": "failure"}],
                          "success"), "fail")


class ScrubberTest(unittest.TestCase):
    """The scrubber must catch real card numbers and stop eating the audit trail.

    Three consecutive decisions were withheld from the public log on 2026-08-06
    because the card pattern matched a 19-digit Jules session id. The public log
    is a graded deliverable, so a systematic false positive is not a free
    trade-off — but a missed card would be worse, hence both directions here.
    """

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from publish_log import scan
        self.scan = scan

    def test_real_cards_are_still_caught(self):
        for label, number in (("visa", "4111111111111111"),
                              ("visa spaced", "4111 1111 1111 1111"),
                              ("mastercard", "5500005555555559"),
                              ("amex", "378282246310005"),
                              ("discover", "6011111111111117")):
            with self.subTest(label):
                self.assertIn("card", self.scan(f"paid with {number} today"))

    def test_jules_session_ids_are_not_cards(self):
        """This exact id passes Luhn — checksum 100 — so the leading-digit check
        is what saves it. Do not remove one and keep the other."""
        self.assertEqual(self.scan("session 1652844863819652924 completed"), [])
        self.assertEqual(self.scan("branch jules-1652844863819652924-781c3b6b"), [])

    def test_other_patterns_are_untouched(self):
        self.assertIn("personnummer", self.scan("born 890101-1234"))
        self.assertIn("email", self.scan("mail to sam@example.com"))


class LlmTest(unittest.TestCase):
    """Encodes what the live API actually did on 2026-08-06."""

    def test_depleted_prepay_429_is_permanent(self):
        body = ("Your prepayment credits are depleted. Please go to AI Studio "
                "to manage your project and billing.")
        self.assertIs(classify_429(body), PermanentModelError)

    def test_quota_wording_is_transient_because_it_is_ambiguous(self):
        """CORRECTED after this misclassification aborted live cycle #4.

        The free tier returns this identical wording both for "Pro is not on
        your tier" (permanent) and for "you exceeded the per-minute limit"
        (transient). It therefore carries no information, and the safe reading
        is transient: retrying a permanent failure wastes seconds, while
        treating a rate limit as permanent loses the cycle.
        """
        body = ("You exceeded your current quota, please check your plan and "
                "billing details. For more information on this error, head to: "
                "https://ai.google.dev/gemini-api/docs/rate-limits.")
        self.assertIs(classify_429(body), TransientModelError)

    def test_per_minute_429_is_transient(self):
        self.assertIs(classify_429("Quota exceeded: requests per minute"),
                      TransientModelError)

    def test_api_supplied_retry_delay_is_used(self):
        from runner.llm import retry_delay_seconds
        body = '{"error":{"details":[{"@type":"...RetryInfo","retryDelay":"31s"}]}}'
        self.assertEqual(retry_delay_seconds(body), 31.0)
        self.assertIsNone(retry_delay_seconds("no retry info here"))

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

    def test_known_model_is_priced_from_the_table(self):
        usd, guessed = estimate_usd("gemini-3.6-flash", 1_000_000, 1_000_000)
        self.assertFalse(guessed)
        self.assertAlmostEqual(usd, 1.50 + 7.50, places=4)

    def test_large_prompt_tier_is_applied(self):
        """Ignoring the >200k tier would undercount exactly when a cycle is at
        its most expensive."""
        small, _ = estimate_usd("gemini-3.1-pro-preview", 100_000, 1000)
        big, _ = estimate_usd("gemini-3.1-pro-preview", 300_000, 1000)
        self.assertAlmostEqual(small, (100_000 / 1e6) * 2.00 + (1000 / 1e6) * 12.00, places=6)
        self.assertAlmostEqual(big, (300_000 / 1e6) * 4.00 + (1000 / 1e6) * 18.00, places=6)

    def test_fallback_is_the_most_expensive_rate_not_an_average(self):
        from runner.llm import FALLBACK_PRICING, PRICING
        worst_in = max(p[0] for p in PRICING.values())
        worst_out = max(p[1] for p in PRICING.values())
        self.assertGreaterEqual(FALLBACK_PRICING[0], worst_in)
        self.assertGreaterEqual(FALLBACK_PRICING[1], worst_out)


class ProseAuditTest(unittest.TestCase):
    """Claims must be as checkable as actions.

    PR #7 passed every check and put invented financials on main: a registrar
    and an electricity bill in no ledger row, a retired model named as the one
    in service, and revenue narrated for three months preceding the project.
    It landed as JULES_LAND -> ok, which was true and was all the ledger could
    say. CI verifies that code runs; nothing verified that prose is true.
    """

    CFG = {"models": {"work": "gemini-3.6-flash"},
           "budget": {"sek_per_usd": 10.0},
           "scope": {"allowed_hosts": ["undra-abc-lz.a.run.app"],
                     "allowed_domains": ["undra.nu"],
                     "publish_base_url": "https://log.undra.nu"}}

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(_schema())
        self.con.execute(
            "INSERT INTO cycles(started_at, status) VALUES('2026-08-06T13:33','ok')")
        self.con.execute(
            "INSERT INTO llm_usage(at, model, usd_est) "
            "VALUES('2026-08-06T13:40','gemini-3.6-flash', 5.97)")
        self.con.commit()

    def tearDown(self):
        self.con.close()

    def _audit(self, text):
        from runner import prose_audit
        return prose_audit.audit(text, self.con, self.CFG)

    def _kinds(self, text):
        return {f.kind for f in self._audit(text) if f.severity == "error"}

    # -- the four fabrications from the real document ----------------------- #

    def test_money_with_no_ledger_row_is_refused(self):
        self.assertIn("money", self._kinds(
            "Domain registration cost ~$10.00 USD (100 SEK)."))

    def test_a_model_never_called_is_refused(self):
        self.assertIn("model", self._kinds(
            "The assistant is powered by `gemini-2.5-flash` via the SDK."))

    def test_the_spelled_out_model_name_is_caught_too(self):
        """Marketing prose writes 'Gemini 2.5 Flash', not the model id. The
        first version of the auditor matched only the id and missed this."""
        self.assertIn("model", self._kinds(
            "Undra is powered by the **Gemini 2.5 Flash** model."))

    def test_a_month_before_the_project_is_refused(self):
        self.assertIn("month", self._kinds(
            "| **July 2026** | $0.00 | Local infrastructure setup phase. |"))

    def test_backticked_claims_are_not_skipped(self):
        """Inline spans are how markdown writes model ids and hostnames. An
        earlier version stripped them and missed two of four fabrications while
        reporting ten findings — confidently, which is worse."""
        self.assertIn("model", self._kinds("We use `gemini-2.5-flash` here."))

    # -- what must NOT fire -------------------------------------------------- #

    def test_the_real_figures_pass(self):
        text = ("Spend to date is $5.97, an estimate from transcribed rates. "
                "Arms-length revenue is $0.00. The loop runs on "
                "`gemini-3.6-flash`. First activity: August 2026.")
        self.assertEqual([f for f in self._audit(text) if f.severity == "error"], [])

    def test_fenced_examples_are_not_claims(self):
        self.assertEqual(self._kinds(
            "Example:\n```\ncurl -d 'price=$999.00' https://example.com\n```\n"), set())

    def test_a_configured_but_unused_model_is_allowed(self):
        """invariants.toml may name a model no cycle has reached yet. That is a
        plan, not a fabrication."""
        self.assertNotIn("model", self._kinds("Planning uses gemini-3.6-flash."))

    def test_owning_a_domain_is_not_serving_from_it(self):
        f = [x for x in self._audit("Open https://undra.nu on your phone.")
             if x.kind == "host"]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].severity, "warn",
                         "an owned domain is a weaker claim than an invented host")

    def test_an_invented_host_is_an_error(self):
        f = [x for x in self._audit("Live at https://undra-app-99.europe-west1.run.app")
             if x.kind == "host"]
        self.assertEqual([x.severity for x in f], ["error"])

    def test_repeated_fabrications_are_reported_once(self):
        found = self._audit("$10.00 here. And $10.00 again. And $10.00.")
        self.assertEqual(len(found), 1)

    # -- the gate ------------------------------------------------------------ #

    def test_merge_is_refused_when_a_claim_is_unsupported(self):
        from runner import prose_audit
        errs = prose_audit.errors(self._audit("Total spend was ~$15.00 USD."))
        self.assertTrue(errs, "the gate has nothing to refuse on")

    def test_the_tool_is_reachable_before_merging(self):
        """A check only available as a refusal at merge time is a check you hit
        after doing the work."""
        from runner import tools
        self.assertIn("audit_document", tools.TOOL_IMPLS)
        self.assertIn("audit_document", {d["name"] for d in tools.declarations()})


class DeployIdentityTest(unittest.TestCase):
    """A health grade with no host is not an operational fact.

    On 2026-08-09 the report said `deploy_health: HTTP 200` and named nothing.
    Asked to smoke-test the deployment, the agent had no host, took a URL from a
    draft document that was never the live service, got 404s and escalated the
    product as down while it was serving normally. CHARTER.md §6.2 makes this
    report the agent's only source of operational truth, so whatever it must act
    on has to be stated, not implied.
    """

    CFG = {"staleness": {"max_hours_without_healthy_deploy": 4},
           "scope": {"allowed_hosts": ["example-abc-lz.a.run.app"],
                     "health_path": "/api/health"}}

    def _facts(self):
        from unittest.mock import patch
        import situation_report as sr
        rep = sr.Report(now=datetime.now(timezone.utc))
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(_schema())
        with patch.object(sr.urllib.request, "urlopen", side_effect=OSError("down")):
            sr.collect_deploy(rep, con, self.CFG)
        con.close()
        return {f.key: f for f in rep.facts}

    def test_the_host_is_named_even_when_the_check_fails(self):
        """Especially then: an unreachable deployment is exactly when someone
        goes hunting for the right URL."""
        f = self._facts()
        self.assertIn("deploy_url", f)
        self.assertEqual(f["deploy_url"].value, "https://example-abc-lz.a.run.app")

    def test_the_host_is_actually_rendered(self):
        """Facts absent from render()'s group table are computed and dropped."""
        import situation_report as sr
        self.assertIn("deploy_url", inspect.getsource(sr.render))


class QuotaCauseTest(unittest.TestCase):
    """The loop may say the free key stopped. It may not say why.

    The API returns the same 429 body for a model being off-tier, a per-minute
    limit and a spent daily quota, so nothing downstream can recover the cause.
    Until 2026-08-08 the fallback path asserted an exhausted daily allowance
    anyway; that reached the Operator as measurement and the build record as
    fact, and the ledger contradicts it — cycles 22-26 served 2, 2, 1, 13 and 9
    free-key calls before falling back.
    """

    def test_detector_reports_only_that_backoff_failed(self):
        from runner.llm import exhausted_after_backoff
        self.assertTrue(exhausted_after_backoff(Exception("429 RESOURCE_EXHAUSTED")))
        self.assertTrue(exhausted_after_backoff(Exception("resource_exhausted")))
        self.assertFalse(exhausted_after_backoff(Exception("500 internal")))

    def test_the_name_that_claimed_a_cause_is_gone(self):
        """`is_quota_exhausted` read as a finding at every call site. A function
        whose name states a conclusion it cannot reach will be believed."""
        import runner.llm as llm
        self.assertFalse(hasattr(llm, "is_quota_exhausted"))

    def test_no_dead_constant_advertising_a_distinction_never_made(self):
        """_DAILY_QUOTA_HINTS was defined, documented as separating daily limits
        from per-minute ones, and referenced by nothing. It is the most likely
        origin of the belief this class exists to prevent."""
        import runner.llm as llm
        self.assertFalse(hasattr(llm, "_DAILY_QUOTA_HINTS"))

    def test_the_digest_can_still_find_the_event_it_reports_on(self):
        """The digest used to match the literal phrase 'free-tier quota
        exhausted'. Correcting that wording would have silently stopped the
        match, and the digest would have reported 'still serving' through every
        fallback with no error anywhere. Match on what the emitter actually
        writes, and keep the two in step."""
        digest_src = (Path(__file__).resolve().parents[1] / "runner/digest.py").read_text()
        cycle_src = (Path(__file__).resolve().parents[1] / "runner/cycle.py").read_text()

        start = digest_src.index("falling back to the paid key")
        needle = digest_src[start:digest_src.index("%", start)]

        collapsed = " ".join(cycle_src.split())
        # assertTrue, not assertIn: assertIn prints both operands on failure,
        # and one of them is the whole of cycle.py.
        self.assertTrue(
            needle in collapsed,
            f"digest.py searches events for {needle!r}, but cycle.py no longer "
            f"emits that phrase. Fallbacks would vanish from the digest with no "
            f"error anywhere. Keep the two in step or match on something stabler.")


class OffsiteTrailTest(unittest.TestCase):
    """The audit trail is a deliverable, and committing it is not saving it.

    On 2026-08-08 a merged pull request moved origin/main ahead, every
    subsequent push was rejected as non-fast-forward, and four consecutive
    cycles were told "git_uncommitted_files: 0" — true, and no help at all.
    Nothing in the report described the distance to the remote, so nothing
    could breach.
    """

    def _report(self, ahead):
        from unittest.mock import patch
        import subprocess as sp
        import situation_report as sr

        rep = sr.Report(now=datetime.now(timezone.utc))
        outs = {"rev-parse": "abc1234", "status": "",
                "rev-list": str(ahead), "abbrev-ref": "main"}

        def fake_run(args, **kw):
            key = next((k for k in outs if k in args), "rev-parse")
            return sp.CompletedProcess(args, 0, stdout=outs[key] + "\n", stderr="")

        with patch.object(sr.subprocess, "run", side_effect=fake_run):
            sr.collect_git(rep, {"scope": {"publish_branch": "ops-log"}})
        return rep

    def test_unpushed_commits_are_reported_as_a_fact(self):
        facts = {f.key: f.value for f in self._report(4).facts}
        self.assertEqual(facts["git_unpushed_commits"], 4)

    def test_a_synced_repo_raises_nothing(self):
        self.assertEqual(self._report(0).breaches, [])

    def test_divergence_raises_a_breach(self):
        rules = [b.rule for b in self._report(2).breaches]
        self.assertIn("trail.offsite", rules)

    def test_divergence_never_halts_the_loop(self):
        """The agent holds no push token, so halting it cannot fix this and
        would stop all product work until the Operator cleared the flag by
        hand. `Report.halted` is true for any halt-severity breach and
        situation_report.py acts on it, so severity here is load-bearing."""
        for ahead in (1, 3, 50):
            rep = self._report(ahead)
            self.assertFalse(rep.halted, f"{ahead} unpushed commits set the halt flag")

    def test_the_fact_is_actually_rendered(self):
        """A fact absent from render()'s group table is computed and dropped."""
        import situation_report as sr
        src = inspect.getsource(sr.render)
        self.assertIn("git_unpushed_commits", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
