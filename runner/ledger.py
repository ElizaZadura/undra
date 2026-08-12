"""All ledger access. Nothing else in the runner touches sqlite directly.

This module is where four of AGENTS.md's "easy to get wrong" requirements are
made structural rather than remembered:

  1. cycles.status is honest        -> CycleRecorder derives it from what
                                       actually happened; "ok" is not the default
                                       and cannot be set by wishing for it.
  2. Telegram never hits `outbound` -> record_outbound() refuses a channel of
                                       "telegram" outright. The Operator is not
                                       a third party, and outbound feeds the
                                       rate limit that halts.
  3. Every action has an idempotency key -> action() requires one, and the
                                       UNIQUE constraint makes a replay a no-op.
  4. Halt is checked before every action -> action() re-reads flags.halt from
                                       the database every single time. No cache,
                                       no per-cycle snapshot (CHARTER.md §10).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Halted(RuntimeError):
    """flags.halt is set. CHARTER.md §10: complete no new actions, take no
    external action, spend nothing. Do not investigate, do not clear it."""


class AlreadyDone(RuntimeError):
    """This idempotency key has been used. The action already happened once;
    that is the entire point of the key. Not an error condition."""


# Statuses that situation_report.py counts as unproductive_cycles_24h. Keeping
# the list here and in one place stops a typo from silently destroying the only
# signal Eliza gets that the limits are the bottleneck.
UNPRODUCTIVE = ("halted", "blocked", "idle", "unproductive")


class Ledger:
    def __init__(self, path: str):
        self.path = path
        self.con = sqlite3.connect(path, timeout=10)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        self.con.close()

    # -- halt --------------------------------------------------------------- #

    def is_halted(self) -> bool:
        """Read the flag from disk. Never cached: the Operator can set this from
        her phone mid-cycle and it must take effect on the next action."""
        row = self.con.execute(
            "SELECT value FROM flags WHERE key='halt'").fetchone()
        return bool(row) and str(row["value"]).lower() in ("1", "true", "yes")

    def halt_reason(self) -> str | None:
        row = self.con.execute(
            "SELECT reason FROM flags WHERE key='halt'").fetchone()
        return row["reason"] if row else None

    def assert_live(self) -> None:
        if self.is_halted():
            raise Halted(self.halt_reason() or "halt flag set")

    # -- events, decisions, questions --------------------------------------- #

    def event(self, level: str, source: str, message: str) -> None:
        self.con.execute(
            "INSERT INTO events(at, level, source, message) VALUES(?,?,?,?)",
            (utcnow(), level, source, message[:4000]))
        self.con.commit()

    def decision(self, *, cycle_id: int | None, summary: str, rationale: str,
                 evidence: str = "", reversible: bool = True,
                 falsifier: str = "") -> int:
        """CHARTER.md §6.4 requires all five fields. Published verbatim by
        publish_log.py, so §3.5 binds this text: no personal data, no quoting."""
        cur = self.con.execute(
            "INSERT INTO decisions(cycle_id, at, summary, rationale, evidence, "
            "reversible, falsifier) VALUES(?,?,?,?,?,?,?)",
            (cycle_id, utcnow(), summary, rationale, evidence,
             1 if reversible else 0, falsifier))
        self.con.commit()
        return int(cur.lastrowid)

    def open_question(self, question: str, blocking: bool = False) -> int:
        cur = self.con.execute(
            "INSERT INTO open_questions(at, question, blocking) VALUES(?,?,?)",
            (utcnow(), question, 1 if blocking else 0))
        self.con.commit()
        return int(cur.lastrowid)

    # -- llm usage ---------------------------------------------------------- #

    def llm_usage(self, *, model: str, input_tokens: int, output_tokens: int,
                  thinking_tokens: int, total_tokens: int, usd_est: float,
                  cycle_id: int | None, key_role: str = "") -> None:
        """This table IS the spend cap — the Gemini API has no hard billing cap.
        total_tokens is what was billed; input+output alone undercounts badly
        because Gemini 3.x reasoning tokens appear in neither."""
        self.con.execute(
            "INSERT INTO llm_usage(at, model, input_tokens, output_tokens, "
            "thinking_tokens, total_tokens, usd_est, cycle_id, key_role) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (utcnow(), model, input_tokens, output_tokens, thinking_tokens,
             total_tokens, usd_est, cycle_id, key_role))
        self.con.commit()

    # -- non-LLM expenditure ------------------------------------------------ #

    def record_spend(self, *, category: str, usd: float, description: str,
                     idempotency_key: str, counts_against_cap: bool = True) -> int:
        """Record a real cost that is not an API call. Evidence is mandatory.

        Until 2026-08-10 this table had a reader — the watchdog totals it, and
        prose_audit checks figures against it — and no writer anywhere. Zero
        rows, and no code path that could add one. Every non-API cost the
        business incurred was therefore structurally unrecordable.

        That is not a cosmetic gap. It is the direct cause of the fabricated
        financials in the submission draft: asked for a cost breakdown, the
        drafter found nothing to read for the domain or the electricity and
        invented both, at plausible-looking values. A figure has to be
        *possible* to source before anyone can be blamed for not sourcing it.

        `description` must carry the evidence — an invoice number, a receipt
        URL, how an estimate was arrived at. It is refused otherwise. The point
        of this row is not the number; the number was always guessable. The
        point is that the number can be checked by someone who doubts it, which
        is the only property that distinguishes a figure from a claim.

        `counts_against_cap=False` records a real, evidenced cost that the agent
        did not incur and cannot stop incurring — a personal subscription that
        predates the business, tooling the humans used to build it. Such a cost
        belongs in the record, because a submission has to report total expenses
        honestly, and must not reach the watchdog, because that guard exists to
        halt the AGENT and halting it cannot un-buy a subscription. Recording one
        at face value on 2026-08-11 put the total $1.84 from a spurious warning
        and would have stopped the loop six days before the deadline.

        It defaults to True. An exemption has to be asked for, so the failure
        mode is an over-counted cap rather than a silently uncapped one.
        """
        if not description or len(description.strip()) < 12:
            raise ValueError(
                "description must state the evidence for this cost — an invoice "
                "or receipt reference, or how the estimate was derived. A bare "
                "amount is the thing this table exists to prevent.")
        if usd < 0:
            raise ValueError("negative spend: record a refund as its own row "
                             "with the reason, do not net it off")
        cur = self.con.execute(
            "INSERT INTO spend(at, category, usd, description, idempotency_key, "
            "counts_against_cap) VALUES(?,?,?,?,?,?)",
            (utcnow(), category, float(usd), description.strip(), idempotency_key,
             1 if counts_against_cap else 0))
        self.con.commit()
        return int(cur.lastrowid)

    def spend_rows(self) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT * FROM spend ORDER BY at").fetchall()

    def spend_total_usd(self) -> float:
        """What counts against the cap. Cap-exempt rows are deliberately absent:
        this feeds the halt decision, and `spend_recorded_usd` is what a document
        should quote for total project cost."""
        llm = self.con.execute("SELECT COALESCE(SUM(usd_est),0) FROM llm_usage").fetchone()[0]
        other = self.con.execute(
            "SELECT COALESCE(SUM(usd),0) FROM spend WHERE counts_against_cap=1").fetchone()[0]
        return float(llm) + float(other)

    def spend_recorded_usd(self) -> float:
        """Every evidenced cost, cap-exempt or not. For reporting, never for gating."""
        llm = self.con.execute("SELECT COALESCE(SUM(usd_est),0) FROM llm_usage").fetchone()[0]
        other = self.con.execute("SELECT COALESCE(SUM(usd),0) FROM spend").fetchone()[0]
        return float(llm) + float(other)

    def llm_spend_usd(self) -> float:
        return float(self.con.execute(
            "SELECT COALESCE(SUM(usd_est),0) FROM llm_usage").fetchone()[0])

    def planning_calls_today(self, model: str) -> int:
        day_ago = (datetime.now(timezone.utc).timestamp() - 86400)
        cutoff = datetime.fromtimestamp(day_ago, timezone.utc).isoformat()
        return int(self.con.execute(
            "SELECT COUNT(*) FROM llm_usage WHERE model=? AND at > ?",
            (model, cutoff)).fetchone()[0])

    # -- actions ------------------------------------------------------------ #

    @contextmanager
    def action(self, *, kind: str, target: str, idempotency_key: str,
               cycle_id: int | None = None) -> Iterator["ActionHandle"]:
        """Every state-changing action goes through here.

        Checks the halt flag first, every time (CHARTER.md §10) — not once per
        cycle. Then claims the idempotency key: if it is already present, the
        action has happened and AlreadyDone is raised before anything external
        occurs. Paying an invoice twice is a documented failure mode of
        long-horizon agents, and the UNIQUE constraint is what prevents it.
        """
        self.assert_live()

        if not idempotency_key:
            raise ValueError(
                f"{kind} -> {target}: idempotency_key is mandatory (AGENTS.md #3)")

        try:
            cur = self.con.execute(
                "INSERT INTO actions(at, kind, target, idempotency_key, status, "
                "cycle_id) VALUES(?,?,?,?,'pending',?)",
                (utcnow(), kind, target, idempotency_key, cycle_id))
            self.con.commit()
        except sqlite3.IntegrityError:
            raise AlreadyDone(
                f"{kind} -> {target}: idempotency_key {idempotency_key!r} "
                "already used; this action has already happened") from None

        handle = ActionHandle(self, int(cur.lastrowid), kind, target)
        try:
            yield handle
        except Exception as exc:
            handle.fail(f"{type(exc).__name__}: {exc}")
            raise
        else:
            if not handle.resolved:
                handle.succeed()

    def failure_streak(self, kind: str, target: str) -> int:
        return int(self.con.execute(
            "SELECT COUNT(*) FROM actions WHERE kind=? AND target=? AND status='failed'",
            (kind, target)).fetchone()[0])

    # -- outbound ----------------------------------------------------------- #

    def record_outbound(self, *, channel: str, recipient_hash: str,
                        subject: str, approval_request_id: int | None) -> None:
        """THIRD PARTIES ONLY.

        invariants.toml [rates] max_outbound_per_hour = 3 and it halts. Messages
        to the Operator — approval requests, digests — are internal and must
        never land here, or a busy escalation hour halts Coral for the crime of
        talking to Eliza. This is AGENTS.md requirement #2 and it is the kind of
        bug that produces no error anywhere, so it is enforced rather than
        documented.
        """
        if channel.lower() in ("telegram", "operator", "digest", "approval"):
            raise ValueError(
                f"channel {channel!r} is the Operator channel, not a third party. "
                "Messages to Eliza must not be written to `outbound` "
                "(AGENTS.md #2, invariants.toml [rates])")
        self.con.execute(
            "INSERT INTO outbound(at, channel, recipient_hash, subject, status, "
            "approval_request_id) VALUES(?,?,?,?,'sent',?)",
            (utcnow(), channel, recipient_hash, subject, approval_request_id))
        self.con.commit()

    # -- messages ------------------------------------------------------------ #
    #
    # Free text between the Operator and Coral. Never `outbound`, which counts
    # third parties and halts at three an hour — the Operator is not a third
    # party. Never `human_requests` either: every kind in invariants.toml is
    # gated, so an agent with something to say and nothing to ask had to file a
    # fake approval request to say it.

    def message(self, direction: str, body: str,
                cycle_id: int | None = None) -> int:
        if direction not in ("to_operator", "from_operator"):
            raise ValueError(f"direction must be to_operator or from_operator, "
                             f"not {direction!r}")
        cur = self.con.execute(
            "INSERT INTO messages(at, direction, body, cycle_id) VALUES(?,?,?,?)",
            (utcnow(), direction, body[:4000], cycle_id))
        self.con.commit()
        return int(cur.lastrowid)

    def unread_from_operator(self) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT id, at, body FROM messages "
            "WHERE direction='from_operator' AND read_at IS NULL "
            "ORDER BY at").fetchall()

    def mark_messages_read(self, ids: list[int], cycle_id: int | None) -> None:
        """Mark inbound notes seen, so a message is surfaced once rather than in
        every situation report forever."""
        if not ids:
            return
        self.con.executemany(
            "UPDATE messages SET read_at=?, cycle_id=? WHERE id=?",
            [(utcnow(), cycle_id, i) for i in ids])
        self.con.commit()

    # -- human requests ----------------------------------------------------- #

    def request_human(self, *, kind: str, payload: str, priority: str,
                      deadline: str | None, default_action: str) -> int:
        """CHARTER.md §9: a request without a default action is malformed."""
        if not default_action:
            raise ValueError(f"{kind}: default_action is mandatory (CHARTER.md §9)")
        cur = self.con.execute(
            "INSERT INTO human_requests(at, kind, payload, priority, deadline, "
            "default_action) VALUES(?,?,?,?,?,?)",
            (utcnow(), kind, payload, priority, deadline, default_action))
        self.con.commit()
        return int(cur.lastrowid)

    def pending_approval(self, kind: str, payload: str) -> sqlite3.Row | None:
        """A granted token is valid for ONE action, the one in the payload
        (CHARTER.md §4). Matching on payload is what stops it generalising."""
        return self.con.execute(
            "SELECT * FROM human_requests WHERE kind=? AND payload=? "
            "AND status='granted' ORDER BY at DESC LIMIT 1",
            (kind, payload)).fetchone()

    def consume_approval(self, request_id: int) -> None:
        self.con.execute(
            "UPDATE human_requests SET status='consumed', resolved_at=? WHERE id=?",
            (utcnow(), request_id))
        self.con.commit()

    def open_requests(self) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT * FROM human_requests WHERE status='pending' ORDER BY at").fetchall()

    # -- objectives ---------------------------------------------------------- #

    def objectives(self) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT * FROM objectives WHERE status='open' ORDER BY priority").fetchall()

    def add_objective(self, priority: int, title: str) -> int:
        cur = self.con.execute(
            "INSERT INTO objectives(priority, title, status, created_at) "
            "VALUES(?,?,'open',?)", (priority, title, utcnow()))
        self.con.commit()
        return int(cur.lastrowid)

    def objective(self, objective_id: int) -> sqlite3.Row | None:
        return self.con.execute(
            "SELECT * FROM objectives WHERE id=?", (objective_id,)).fetchone()

    def close_objective(self, objective_id: int, status: str) -> bool:
        """Mark an objective done or retired. Returns False if it was not open.

        Until 2026-08-07 nothing could do this: add_objective existed and no
        counterpart did, so the list only ever grew. CHARTER.md §5 grants the
        latitude — "retire objectives that no longer serve the mission, with a
        logged rationale" — and the tool surface withheld it, which left the
        agent re-reading shipped work every four hours while §8.3 told it that
        anything off the list was not work.
        """
        row = self.objective(objective_id)
        if row is None or row["status"] != "open":
            return False
        self.con.execute(
            "UPDATE objectives SET status=?, done_at=? WHERE id=?",
            (status, utcnow(), objective_id))
        self.con.commit()
        return True

    def set_objective_priority(self, objective_id: int, priority: int) -> bool:
        row = self.objective(objective_id)
        if row is None or row["status"] != "open":
            return False
        self.con.execute("UPDATE objectives SET priority=? WHERE id=?",
                         (priority, objective_id))
        self.con.commit()
        return True

    # -- redacted dump ------------------------------------------------------- #

    # AGENTS.md #12: the raw ledger.db is gitignored and lives only on `red`.
    # This dump is the offsite backup of the audit trail and the competition's
    # "agent execution logs" evidence, so it is committed each cycle.
    DUMP_SKIP_TABLES = ("payments",)          # no payments detail
    DUMP_SKIP_COLUMNS = {                     # no customer-identifying columns
        "outbound": ("recipient_hash", "subject"),
    }

    def dump_redacted(self, dest: Path) -> dict[str, int]:
        tables = [r["name"] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        out: dict[str, Any] = {
            "generated_at": utcnow(),
            "note": ("Redacted dump of the operating ledger. Payments detail and "
                     "recipient columns are omitted; see runner/ledger.py. The "
                     "authoritative ledger lives only on the lab box."),
            "tables": {},
        }
        counts: dict[str, int] = {}
        for t in tables:
            if t in self.DUMP_SKIP_TABLES:
                n = int(self.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                out["tables"][t] = {"redacted": True, "row_count": n}
                counts[t] = n
                continue
            skip = set(self.DUMP_SKIP_COLUMNS.get(t, ()))
            cols = [r[1] for r in self.con.execute(f"PRAGMA table_info({t})")
                    if r[1] not in skip]
            if not cols:
                continue
            rows = [dict(zip(cols, r)) for r in self.con.execute(
                f"SELECT {', '.join(cols)} FROM {t}")]
            out["tables"][t] = rows
            counts[t] = len(rows)

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        return counts


@dataclass
class ActionHandle:
    ledger: Ledger
    id: int
    kind: str
    target: str
    resolved: bool = False

    def succeed(self) -> None:
        self.ledger.con.execute(
            "UPDATE actions SET status='ok' WHERE id=?", (self.id,))
        self.ledger.con.commit()
        self.resolved = True

    def fail(self, error: str) -> None:
        self.ledger.con.execute(
            "UPDATE actions SET status='failed', error=? WHERE id=?",
            (error[:1000], self.id))
        self.ledger.con.commit()
        self.resolved = True


@dataclass
class CycleRecorder:
    """Owns cycles.status, and will not let it be dishonest.

    AGENTS.md #1: a cycle that woke and achieved nothing records blocked, idle
    or halted — never ok. situation_report.py counts those as
    unproductive_cycles_24h, which is the only signal Eliza gets that the limits
    are the bottleneck. A runner that always writes "ok" destroys that signal
    without any error appearing anywhere, so `ok` is not reachable by default:
    it requires at least one successful action or written decision.
    """
    ledger: Ledger
    id: int = 0
    productive: int = 0
    blocked: bool = False
    halted: bool = False
    _closed: bool = field(default=False, repr=False)

    @classmethod
    def start(cls, ledger: Ledger) -> "CycleRecorder":
        cur = ledger.con.execute(
            "INSERT INTO cycles(started_at, status) VALUES(?, 'running')",
            (utcnow(),))
        ledger.con.commit()
        return cls(ledger=ledger, id=int(cur.lastrowid))

    def note_productive(self) -> None:
        """Called when something real happened: an action succeeded, a decision
        was recorded, code was merged, a deploy went out."""
        self.productive += 1

    def note_blocked(self) -> None:
        self.blocked = True

    def note_halted(self) -> None:
        self.halted = True

    def derive_status(self) -> str:
        if self.halted:
            return "halted"
        if self.productive > 0:
            return "ok"
        return "blocked" if self.blocked else "idle"

    def end(self, handoff: str, status: str | None = None) -> str:
        """CHARTER.md §8.5: end every cycle with a written handoff. The next
        cycle is a different instance with no memory — write for a stranger."""
        if self._closed:
            return "already-closed"
        final = status or self.derive_status()
        if final == "ok" and self.productive == 0:
            # Belt and braces: refuse a caller-supplied dishonest "ok".
            final = "idle"
            self.ledger.event(
                "warn", "cycle",
                "caller passed status='ok' for a cycle with no productive work; "
                "recorded as 'idle' (AGENTS.md #1)")
        self.ledger.con.execute(
            "UPDATE cycles SET ended_at=?, status=?, handoff=? WHERE id=?",
            (utcnow(), final, handoff, self.id))
        self.ledger.con.commit()
        self._closed = True
        return final
