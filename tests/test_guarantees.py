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
import re
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


class ProtectedPathTest(unittest.TestCase):
    """An agent-authored patch may not edit the rules it runs under, or the
    checks that decide whether it followed them.

    Observed 2026-08-12. The submission claimed a model no cycle had called;
    Coral commissioned "fix the audit errors"; Jules added the false model to
    invariants.toml [models], because that is the list prose_audit checks
    against. The claim stayed false and the check stopped objecting. It reached
    an open pull request, and nothing in the landing path had an opinion."""

    def test_the_rules_and_the_checks_are_both_protected(self):
        from runner.jules import PROTECTED
        for path in ("CHARTER.md", "invariants.toml", "tests/test_guarantees.py",
                     "runner/prose_audit.py", "situation_report.py"):
            self.assertIn(path, PROTECTED,
                          f"{path} must not be landable by an agent-written patch")

    def test_the_invariants_edit_that_prompted_this_would_be_refused(self):
        from runner.jules import protected_paths
        self.assertEqual(
            protected_paths(["docs/submission.md", "invariants.toml"]),
            ["invariants.toml"])

    def test_ordinary_work_is_not_blocked(self):
        from runner.jules import protected_paths
        self.assertEqual(
            protected_paths(["docs/submission.md", "app/main.py",
                             "app/guardrails.py", "runner/cycle.py"]), [])

    def test_a_directory_guard_covers_what_is_under_it(self):
        from runner.jules import protected_paths
        self.assertEqual(protected_paths([".claude/settings.json"]),
                         [".claude/settings.json"])
        self.assertEqual(protected_paths(["env/keys.env"]), ["env/keys.env"])

    def test_a_leading_dot_slash_does_not_evade_the_guard(self):
        """git diff headers are stripped to a bare path, but the patch is not
        ours and its formatting is not something to rely on."""
        from runner.jules import protected_paths
        self.assertEqual(protected_paths(["./invariants.toml"]), ["invariants.toml"])

    def test_a_near_miss_filename_is_not_protected(self):
        """The guard matches whole paths, not prefixes, or docs/CHARTER-notes.md
        would be unlandable for no reason."""
        from runner.jules import protected_paths
        self.assertEqual(protected_paths(["invariants.toml.bak",
                                          "docs/CHARTER.md"]), [])

    def test_landing_checks_before_it_applies_anything(self):
        """Ordering matters: the refusal must come before the patch is applied
        and before a branch is created, or a rejected patch still leaves a
        branch behind."""
        src = (Path(__file__).resolve().parents[1] / "runner" / "tools.py").read_text()
        body = src[src.index("def t_jules_land_task"):]
        body = body[:body.index("\ndef ")]
        self.assertLess(body.index("protected_paths("),
                        body.index("create_branch_with_files("),
                        "the protected-path check must run before any branch is made")

    def test_a_plan_held_for_approval_reaches_the_operator(self):
        """A task Jules will not start without her is worth nothing if she is
        never told it exists. Sessions sat for days before 2026-08-12 because
        this branch spoke only to Coral."""
        src = (Path(__file__).resolve().parents[1] / "runner" / "tools.py").read_text()
        body = src[src.index("def t_jules_file_task"):]
        body = body[:body.index("\ndef t_jules_land_task")]
        self.assertIn("JULES_PLAN_APPROVAL", body,
                      "filing a plan-gated task must raise a request row")
        self.assertIn("approve jules", body,
                      "the notification must carry the command she has to type")

    def test_the_notification_names_the_session_not_the_request(self):
        """`approve <request_id>` resolves a ledger row; `approve jules <id>`
        calls the Jules API. Printing the first for a plan approval sends her to
        a command that closes the record without releasing the session."""
        src = (Path(__file__).resolve().parents[1] / "runner" / "tools.py").read_text()
        body = src[src.index("def t_jules_file_task"):]
        body = body[:body.index("\ndef t_jules_land_task")]
        # The call site, not any mention: a comment explaining why this branch
        # avoids request_approval() must not itself trip the check.
        self.assertNotIn("ctx.telegram.request_approval(", body)
        self.assertIn("approve jules {session.id}", body)

    def test_releasing_a_plan_closes_its_request(self):
        """Otherwise the row stays pending and every digest re-reports work that
        is already done."""
        src = (Path(__file__).resolve().parents[1] / "runner" / "telegram.py").read_text()
        branch = src[src.index('parts[1] == "jules"'):]
        branch = branch[:branch.index("if len(parts) == 2")]
        self.assertIn("JULES_PLAN_APPROVAL", branch)
        for outcome in ('_close("granted")', '_close("denied")'):
            self.assertIn(outcome, branch,
                          f"{outcome} missing: both replies must resolve the row")

    def test_the_refusal_is_not_a_grantable_approval(self):
        """Kinds in [gates].require_approval can be granted, and a granted row
        is an approval token find_approval() hands back. This escalation reports
        a refusal; there is nothing to grant."""
        import tomllib
        root = Path(__file__).resolve().parents[1]
        cfg = tomllib.loads((root / "invariants.toml").read_text())
        self.assertNotIn("PROTECTED_PATH_PATCH",
                         cfg["gates"]["require_approval"])


class OperatorViewTest(unittest.TestCase):
    """`bin/waiting` is the only thing in this repository written for the one
    participant who cannot be re-run or given a longer context window."""

    def test_every_gated_kind_is_classified(self):
        """The guarantee that keeps the taxonomy from going stale. Adding a kind
        to invariants.toml without classifying it here means bin/waiting tells
        her to approve something it does not understand."""
        import tomllib
        from runner.operator import classify
        root = Path(__file__).resolve().parents[1]
        cfg = tomllib.loads((root / "invariants.toml").read_text())
        for kind in cfg["gates"]["require_approval"]:
            self.assertNotEqual(
                classify(kind), "unclassified",
                f"{kind} is gated but bin/waiting cannot say what to do about it")

    def test_the_two_reasons_a_gate_exists_are_kept_apart(self):
        """Forbidden means approving is the whole act. Incapable means she has
        to do it first. Collapsing them is the confusion this module exists for."""
        from runner.operator import classify
        self.assertEqual(classify("LOGIN"), "act-then-approve")
        self.assertEqual(classify("TOS_ACCEPTANCE"), "act-then-approve")
        self.assertEqual(classify("PUBLISH"), "approve-only")
        self.assertEqual(classify("LEGAL_OR_MEDICAL_CLAIM"), "never-grant")

    def test_a_report_is_not_offered_as_an_approval(self):
        from runner.operator import pending
        con = self._db([(1, "PROTECTED_PATH_PATCH", "touched invariants.toml")])
        item, = pending(con)
        self.assertNotIn("approve", item.command)
        self.assertIn("by hand", item.prepare)

    def test_blocking_is_narrow(self):
        """CHARTER.md §4 tells Coral to keep working while a request is pending.
        A view that calls everything urgent is one nobody reads."""
        from runner.operator import pending
        con = self._db([(1, "PUBLISH", "ship the log"),
                        (2, "LOGIN", "re-auth the billing console")])
        blocks = {i.kind: i.blocks for i in pending(con)}
        self.assertFalse(blocks["PUBLISH"])
        self.assertTrue(blocks["LOGIN"])

    def test_a_session_id_survives_a_title_containing_money(self):
        """Titles are free text and have carried figures before — "Fix spend
        claim to $8.38 USD" is a real one."""
        from runner.operator import pending
        con = self._db([(1, "JULES_PLAN_APPROVAL",
                         "Jules session 5686851207941248156 — "
                         "Fix spend claim to $8.38 USD")])
        item, = pending(con)
        self.assertEqual(item.command, "approve jules 5686851207941248156")

    def test_held_jules_sessions_are_counted(self):
        """First run of bin/waiting printed "0 things waiting for you" directly
        above a session that was waiting. Jules holds them; the ledger does not
        know they exist."""
        from runner.operator import render
        out = render([], jules_waiting=[("123", "Fix the thing")])
        self.assertIn("1 thing waiting for you", out)
        self.assertIn("blocks progress", out)

    def test_silence_is_reported_as_silence(self):
        from runner.operator import render
        self.assertIn("Nothing is waiting", render([], jules_waiting=[]))

    def test_an_unreachable_jules_is_said_out_loud(self):
        """Failing closed would tell her nothing is waiting when it cannot know."""
        from runner.operator import render
        out = render([], jules_waiting=[], jules_error="no JULES_API_KEY")
        self.assertIn("could not be reached", out)

    # -- the same taxonomy, pushed instead of pulled ----------------------- #
    #
    # bin/waiting is a pull tool on the box. Every one of these guarantees was
    # true there from 12 August and false in the message that actually reaches
    # her phone, which is the failure the module's own docstring describes.

    def _push(self, kind, payload="something happened", rid=8):
        from runner.operator import notification
        return notification(request_id=rid, kind=kind, payload=payload,
                            deadline="2026-08-13T20:13Z",
                            default_action="carry on without it")

    def test_the_push_message_says_what_to_do_first(self):
        body = self._push("LOGIN")
        self.assertIn("first:", body)
        self.assertIn("2FA", body)
        self.assertIn("moves the failure one step along", body)

    def test_a_permission_only_kind_asks_for_nothing_first(self):
        self.assertNotIn("first:", self._push("PUBLISH"))

    def test_the_push_message_leads_with_whether_it_blocks(self):
        self.assertTrue(self._push("LOGIN").startswith("[undra · blocks progress]"))
        self.assertTrue(self._push("PUBLISH").startswith("[undra · when you have"))

    def test_a_report_says_it_grants_nothing(self):
        self.assertIn("grants nothing", self._push("STALLED_WORK_ESCALATION"))

    def test_the_one_kind_she_must_not_grant_is_not_offered_a_command(self):
        """Printing `approve 8` under a line saying do not approve this is the
        exact shape of message this rewrite exists to stop sending."""
        body = self._push("LEGAL_OR_MEDICAL_CLAIM")
        self.assertNotIn("approve 8", body)
        self.assertIn("do not approve", body)

    def test_the_reply_format_is_still_exactly_two_words(self):
        """The parser matches two or three tokens and nothing else. Whatever
        else this message gained, it cannot have lost the literal command."""
        self.assertIn("approve 8   or   deny 8", self._push("SPEND_OVER_LIMIT"))

    def test_telegram_does_not_keep_its_own_copy_of_the_taxonomy(self):
        """Two renderings of the same taxonomy is how they drift apart, which
        is what happened between 12 and 13 August."""
        body = (Path(__file__).resolve().parents[1]
                / "runner" / "telegram.py").read_text()
        self.assertIn("from .operator import notification", body)
        self.assertNotIn("approval needed", body)

    @staticmethod
    def _db(rows):
        import sqlite3
        from datetime import datetime, timezone
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE human_requests(id INTEGER PRIMARY KEY, at TEXT, "
                    "kind TEXT, payload TEXT, priority TEXT, deadline TEXT, "
                    "default_action TEXT, status TEXT)")
        now = datetime.now(timezone.utc).isoformat()
        for rid, kind, payload in rows:
            con.execute("INSERT INTO human_requests VALUES(?,?,?,?,'digest',?,'x',"
                        "'pending')", (rid, now, kind, payload, now))
        return con


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

    # -- itemised costs, once they can be recorded at all -------------------- #

    def _with_cost(self, usd, desc="Hostup invoice #12345 dated 2026-08-05"):
        self.con.execute(
            "INSERT INTO spend(at, category, usd, description, idempotency_key) "
            "VALUES('2026-08-05','domain',?,?,'k1')", (usd, desc))
        self.con.commit()

    def test_a_recorded_line_item_becomes_quotable(self):
        """A financial section is itemised. An auditor that accepts only the
        grand total forces a choice between an itemised table and a checkable
        one."""
        self._with_cost(10.40)
        self.assertEqual(self._kinds("The domain cost $10.40."), set())

    def test_an_unrecorded_line_item_is_still_refused(self):
        self._with_cost(10.40)
        self.assertIn("money", self._kinds("The domain cost $47.00."))

    def test_a_budget_cap_is_not_evidence_of_spending(self):
        """cap_marketing is $10.00 and the original fabrication was '$10.00 USD'
        for a domain. Treating a config value as evidence of expenditure opens a
        hole at exactly the round numbers invented figures favour."""
        cfg = dict(self.CFG, budget={"sek_per_usd": 10.0, "cap_marketing": 10.0})
        from runner import prose_audit
        found = prose_audit.audit("The domain cost $10.00.", self.con, cfg)
        self.assertEqual([f.severity for f in found], ["warn"],
                         "a cap must neither pass silently nor read as a cost")

    def test_stating_that_the_month_predates_the_project_is_accepted(self):
        """The finding demands "say which, rather than narrating activity". A
        document that says it is not narrating activity has complied, and
        refusing it anyway makes the required disclosure impossible to write."""
        self.assertEqual(self._kinds(
            "| **July 2026** | $0.00 |\n\nUndra did not exist before 3 August 2026."),
            set())

    def test_a_zero_is_not_a_disclaimer(self):
        """The fabricated table reported July revenue as $0.00 — which was true
        — and then narrated a setup phase for a month in which nothing existed.
        The zero was never the lie, so it cannot be what clears the check."""
        self.assertIn("month", self._kinds(
            "| **July 2026** | $0.00 | Charter design phase. |"))

    # -- adding up recorded costs ------------------------------------------- #

    def _two_costs(self):
        for i, usd in enumerate((10.41, 6.85)):
            self.con.execute(
                "INSERT INTO spend(at, category, usd, description, idempotency_key) "
                "VALUES('2026-08-11','domain',?,?,?)",
                (usd, "Hostup invoice 202680231 dated 2026-08-04", f"k{i}"))
        self.con.commit()

    def test_a_total_of_recorded_line_items_is_quotable(self):
        """A breakdown that lists invoices and then totals them is the normal
        shape of a financial section. Accepting each row but not their sum
        forces a choice between the total and the itemisation."""
        self._two_costs()
        self.assertEqual(self._kinds("Domain $10.41 and tooling $6.85, $17.26 in all."),
                         set())

    def test_a_sum_that_uses_an_unrecorded_figure_is_still_refused(self):
        """The exemption is for combinations of evidenced rows, not for
        arithmetic that quietly introduces a new one."""
        self._two_costs()
        self.assertIn("money", self._kinds("Costs came to $30.00."))

    def test_sums_are_matched_to_the_cent(self):
        """Single rows carry a 2% tolerance. Applying that across every
        combination would start accepting arbitrary numbers: with rows at 10.41
        and 6.85 a 2% band on 17.26 would swallow anything up to ~17.60."""
        self._two_costs()
        self.assertIn("money", self._kinds("Costs came to $17.50."))

    # -- disclosing a figure known to be false ------------------------------- #

    def test_a_fabrication_can_be_quoted_in_order_to_retract_it(self):
        """A document disclosing its own past fabrication has to restate the
        false number. Refusing that makes an honest retraction impossible to
        write, which is how a gate against fabrication suppresses the
        correction of one."""
        text = ("We wrongly claimed "
                "<!-- audit:disclosed -->the domain cost $10.00 and power $3.00"
                "<!-- /audit:disclosed --> — neither figure had a source.")
        from runner import prose_audit
        self.assertEqual(prose_audit.errors(self._audit(text)), [])

    def test_using_the_fence_is_always_reported(self):
        """Silent suppression would make the fence a hole. Every use is counted
        and surfaced so a reviewer knows to go read the passage."""
        text = "<!-- audit:disclosed -->$10.00<!-- /audit:disclosed -->"
        found = self._audit(text)
        self.assertEqual([f.kind for f in found], ["disclosed"])
        self.assertEqual(found[0].severity, "warn")

    def test_the_fence_does_not_leak_past_its_close(self):
        text = ("<!-- audit:disclosed -->$10.00<!-- /audit:disclosed --> "
                "and then we spent $99.00.")
        self.assertIn("money", self._kinds(text))

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


class LedgerSpendTest(unittest.TestCase):
    """`spend` had a reader and no writer, which is why the costs were invented.

    The watchdog totalled this table and prose_audit checked figures against it,
    and no code path could put a row in it. Asked for a cost breakdown, the
    drafter found nothing to read for the domain or the electricity and made
    both up. A figure has to be possible to source before anyone can be blamed
    for not sourcing it.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = str(Path(self.tmp.name) / "ledger.db")
        con = sqlite3.connect(path)
        con.executescript(_schema())
        con.commit()
        con.close()
        self.led = Ledger(path)

    def tearDown(self):
        self.led.close()
        self.tmp.cleanup()

    def test_a_cost_can_be_recorded_at_all(self):
        rid = self.led.record_spend(
            category="domain", usd=10.40,
            description="undra.nu 1yr, Hostup invoice #12345 dated 2026-08-05",
            idempotency_key="spend:a")
        self.assertTrue(rid)
        self.assertAlmostEqual(self.led.spend_total_usd(), 10.40, places=2)

    def test_evidence_is_mandatory(self):
        """The number was always guessable. The point of the row is that someone
        who doubts it can check it."""
        for desc in ("", "   ", "10 bucks"):
            with self.assertRaises(ValueError):
                self.led.record_spend(category="domain", usd=10.40,
                                      description=desc, idempotency_key="spend:" + desc)
        self.assertEqual(self.led.spend_total_usd(), 0.0)

    def test_the_same_cost_cannot_be_recorded_twice(self):
        kw = dict(category="domain", usd=10.40,
                  description="Hostup invoice #12345 dated 2026-08-05",
                  idempotency_key="spend:dup")
        self.led.record_spend(**kw)
        with self.assertRaises(sqlite3.IntegrityError):
            self.led.record_spend(**kw)
        self.assertAlmostEqual(self.led.spend_total_usd(), 10.40, places=2)

    def test_a_refund_is_its_own_row_not_a_negative(self):
        with self.assertRaises(ValueError):
            self.led.record_spend(category="domain", usd=-5.0,
                                  description="refund for the duplicate order",
                                  idempotency_key="spend:neg")

    # -- what the agent spent versus what the project cost ------------------ #

    def _sub(self, **kw):
        base = dict(category="subscription", usd=26.58,
                    description="ChatGPT Plus, OpenAI order sub_1Rlu59, shared",
                    idempotency_key="spend:sub")
        base.update(kw)
        return self.led.record_spend(**base)

    def test_a_cost_counts_against_the_cap_unless_exempted(self):
        """Default 1. An exemption has to be asked for, so the failure mode is
        an over-counted cap rather than a silently uncapped one."""
        self._sub()
        self.assertAlmostEqual(self.led.spend_total_usd(), 26.58, places=2)

    def test_human_tooling_can_be_recorded_without_reaching_the_cap(self):
        """`spend` feeds the watchdog that halts the agent. A subscription
        bought before the business existed is a real cost and belongs in the
        record, but halting the agent cannot un-buy it. Recording one at face
        value on 2026-08-11 would have stopped the loop six days before the
        deadline."""
        self._sub(counts_against_cap=False)
        self.assertEqual(self.led.spend_total_usd(), 0.0,
                         "a cost the agent cannot control reached the halt guard")
        self.assertAlmostEqual(self.led.spend_recorded_usd(), 26.58, places=2)

    def test_the_watchdog_reads_the_same_distinction(self):
        """The exemption is worthless if situation_report totals the raw column:
        the guard would still halt on it."""
        import situation_report as sr
        src = inspect.getsource(sr.collect_budget)
        self.assertIn("counts_against_cap=1", src)
        self.assertIn("spend_not_agent_usd", src)

    def test_the_exempt_fact_is_actually_rendered(self):
        """A fact absent from render()'s group table is computed and dropped —
        the same defect found on 8 August with git_unpushed_commits."""
        import situation_report as sr
        self.assertIn("spend_not_agent_usd", inspect.getsource(sr.render))


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


class MobileViewportTest(unittest.TestCase):
    """The first defect a real user reported, 2026-08-13.

    The bottom navigation was invisible on her phone in Chrome and Firefox
    both, so the chat tab could not be reached at all — the product's main
    feature, unreachable on the only class of device it is built for, for
    seven days. Nothing in the ledger, the invariants or the test suite could
    have noticed: everything here checks what the agent believes, and the
    smoke test asks for HTTP 200, which the page was returning perfectly while
    a third of it sat under the address bar.

    Stdlib-only, so it runs in the `runner guarantees` CI job with no
    dependencies — the app job needs FastAPI and cannot run on the box.
    """

    @property
    def html(self) -> str:
        return (Path(__file__).resolve().parents[1]
                / "app" / "static" / "index.html").read_text()

    def test_the_page_is_sized_to_the_visible_viewport(self):
        """`100vh` on mobile is the height the page would have if the address
        bar were retracted, not the height it has. `dvh` is the one that
        tracks reality."""
        self.assertIn("100dvh", self.html)

    def test_the_fallback_comes_before_the_rule_that_replaces_it(self):
        """Two declarations of the same property: a browser that does not know
        `dvh` drops the second and keeps the first. Reversed, every modern
        browser would take the fallback and the fix would do nothing."""
        body = self.html
        self.assertLess(body.index("height: 100vh"), body.index("height: 100dvh"))

    def test_no_utility_class_overrides_it(self):
        """A class beats an element selector. While `h-screen` was on <body>,
        any `body { height: ... }` rule lost silently — which is the worst
        possible outcome: a fix in the file, absent from the page.

        Matched against the body tag itself, not the file: the comment
        explaining the fix names the class it removed, and an assertion that
        trips over its own explanation is a test nobody can keep."""
        m = re.search(r"<body[^>]*>", self.html)
        self.assertIsNotNone(m, "no <body> tag")
        self.assertNotIn("h-screen", m.group(0))

    def test_the_safe_area_inset_can_resolve(self):
        """env(safe-area-inset-*) is zero unless the viewport meta opts in, so
        the padding below the tab labels would quietly do nothing."""
        self.assertIn("viewport-fit=cover", self.html)
        self.assertIn("safe-area-inset-bottom", self.html)

    def test_the_navigation_still_carries_all_three_tabs(self):
        """Whatever else moves, the chat tab is the product."""
        for tab in ("nav-guide", "nav-chat", "nav-authorities"):
            self.assertIn(tab, self.html)


class ImageMemoryTest(unittest.TestCase):
    """The second defect a real user reported, 2026-08-13, and the one that
    stopped the demo video being recorded.

    `clean_img.putdata(list(img_rgb.getdata()))` stripped metadata by
    materialising one Python tuple per pixel. For the 50MP photo the reporter's
    phone takes, that is ~50 million 64-byte tuples — roughly 3GB — inside a
    512MiB container. The instance was OOM-killed mid-request, so there was no
    response at all, and the browser told her to check her connection.

    Reproduced against production before the fix: 0.5MP and 12MP returned 200,
    8160x6120 returned HTTP 503 with an empty body in 4.7 seconds.

    Source-level, because the app suite needs FastAPI and Pillow and cannot run
    on the operator box — the check that would have caught this has to live
    where it runs.
    """

    @property
    def source(self) -> str:
        return (Path(__file__).resolve().parents[1]
                / "app" / "main.py").read_text()

    def test_pixels_are_not_copied_through_python_objects(self):
        src = re.sub(r"#.*", "", self.source)     # comments describe the bug
        self.assertNotIn("getdata()", src)
        self.assertNotIn("putdata(", src)

    def test_the_image_is_bounded_before_it_is_processed(self):
        """Downscaling is not an optimisation here. It is what keeps peak
        memory independent of what camera the user happens to own."""
        src = self.source
        self.assertIn("MAX_IMAGE_EDGE", src)
        self.assertIn("thumbnail(", src)

    def test_a_jpeg_is_decoded_at_reduced_scale(self):
        """draft() has to be called before the pixels are loaded, or libjpeg
        has already done the expensive thing."""
        src = self.source
        self.assertIn('draft(', src)
        self.assertLess(src.index("draft("), src.index("thumbnail("))

    def test_metadata_is_still_stripped_by_reconstruction(self):
        """The cheap version of this fix is `.copy()`, which carries the
        original's `info` dict — EXIF included — straight across."""
        src = self.source
        self.assertIn("Image.new(", src)
        self.assertIn(".paste(", src)

    def test_an_oversized_upload_is_answered_rather_than_died_on(self):
        src = self.source
        self.assertIn("MAX_IMAGE_BYTES", src)
        self.assertIn("413", src)

    def test_the_size_refusal_survives_the_blanket_handler(self):
        """`except Exception` sits directly below and would rewrite the 413 as
        'Invalid image file or format', sending the user to look for a fault
        in a file that is fine."""
        # Scoped to the image handler: main.py catches Exception elsewhere too,
        # and a bare index() over the file finds whichever comes first.
        block = self.source.split("3. Handle Image Upload")[1]
        self.assertIn("except HTTPException:", block)
        self.assertLess(block.index("except HTTPException:"),
                        block.index("except Exception as e:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
