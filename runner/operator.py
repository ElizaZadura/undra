"""What is waiting for the Operator, and what she has to do about it.

Everything else in this package is built for agents: structured events, explicit
states, machine-parseable payloads, provenance on every figure. That is the
right shape for `runner/` talking to itself. It is the wrong shape for the one
participant who cannot be re-run, cannot be given a longer context window, and
reads this on a phone.

The gap was not theoretical. On 2026-08-12 four requests were pending at once.
Two described the same stall in different words, one asked her to approve a
plan that did not exist, and the fourth's default action — taken automatically
on timeout — was to re-file the task that caused the stall. Every row was
correct. Read together they said nothing, and the one thing genuinely waiting
for her was in a third-party web UI that nothing had ever mentioned.

So this module answers three questions in the order a person asks them:

    1. How many things are waiting, and do any of them block progress?
    2. Which do I do first?
    3. For this one, what do I actually type — and do I have to do something
       elsewhere before I type it?

Question 3 is the one the system never answered. `approve <id>` and
`approve jules <id>` are different commands in different namespaces, and half
the gated kinds exist because Coral *cannot* act rather than because it *may
not*. For those, approving without doing the thing first just moves the failure
one step along.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

# --- the taxonomy ---------------------------------------------------------- #
#
# The dividing line, and the thing that was never written down anywhere:
#
#   A gate exists because Coral is FORBIDDEN  -> your approval is the whole act.
#   A gate exists because Coral is INCAPABLE  -> you do it, THEN approve.
#
# Both look identical in the ledger. Both arrive as the same Telegram message.
# Only the second requires her to leave the chat, and nothing said which was
# which.

#: Coral physically cannot do these. No credentials (CHARTER.md §3: never a
#: username and password, scoped tokens only), no payment instrument, and no
#: legal personality to agree with anyone (§2.2). Approving one of these
#: without doing it first authorises a step that then fails for the same reason.
NEEDS_YOU_FIRST = {
    "LOGIN": "Sign in / complete the OAuth or 2FA prompt yourself",
    "CAPTCHA": "Solve the challenge yourself",
    "TOS_ACCEPTANCE": "Read and accept it yourself — Coral has no legal personality",
    "NEW_ACCOUNT_CREATION": "Register the account yourself",
    "PAYMENT_AUTH": "Make the payment or enter the card yourself",
}

#: Coral is perfectly able and is waiting only for permission. Approving is the
#: entire action; it does the rest on its next cycle.
PERMISSION_ONLY = {
    "SPEND_OVER_LIMIT", "EXTERNAL_MESSAGE", "PUBLISH",
    "PRICING_CHANGE", "DESTRUCTIVE", "PII_EXPORT",
}

#: CHARTER.md §4 says this should never fire, and its timeout default is `halt`
#: rather than `abandon_task`. If one appears, the interesting question is what
#: produced it, not whether to grant it.
NEVER_GRANT = {"LEGAL_OR_MEDICAL_CLAIM"}

#: Reports, not requests. Nothing is granted; approve/deny only closes the
#: record. Kept out of [gates].require_approval deliberately — a kind listed
#: there can be granted, and a granted row is an approval token that
#: find_approval() will hand back for a matching payload.
NOT_AN_APPROVAL = {
    "STALLED_WORK_ESCALATION":
        "Read it and decide. Approving grants nothing.",
    "PROTECTED_PATH_PATCH":
        "Read the patch and apply it by hand, or discard it. "
        "Approving grants nothing.",
}

#: Released through the Jules API by session id, not through the ledger by
#: request id. The row exists so the work is visible; the command is different.
JULES_PLAN = "JULES_PLAN_APPROVAL"


@dataclass
class Item:
    kind: str
    what: str            # one line, human-first
    detail: str          # the payload, as filed
    command: str         # exactly what to type
    prepare: str         # what to do before typing it, or ""
    blocks: bool         # is the loop actually stopped on this
    age_h: float
    overdue_h: float     # hours past deadline, 0 if not

    @property
    def urgency(self) -> tuple:
        """Sort key. Blocking first, then most overdue, then oldest."""
        return (not self.blocks, -self.overdue_h, -self.age_h)


def _hours(a: str | None, b: datetime) -> float:
    if not a:
        return 0.0
    try:
        t = datetime.fromisoformat(a)
    except ValueError:
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (b - t).total_seconds() / 3600.0


def _blocks(kind: str) -> bool:
    """Does the loop actually stop until she answers?

    Deliberately narrow. CHARTER.md §4 tells Coral to continue with other work
    while a request is pending and never to idle a cycle on one, so a pending
    PUBLISH does not stop anything — it parks one task. Calling everything
    blocking is how a triage view becomes another list nobody reads.

    Three things genuinely stop progress: work Coral cannot perform at all, a
    Jules session that writes no code until released, and a stall the loop has
    already reported it cannot get out of.
    """
    return (kind in NEEDS_YOU_FIRST
            or kind == JULES_PLAN
            or kind == "STALLED_WORK_ESCALATION")


def classify(kind: str) -> str:
    """Which group a request kind falls in. See the taxonomy above."""
    if kind in NEEDS_YOU_FIRST:
        return "act-then-approve"
    if kind in NEVER_GRANT:
        return "never-grant"
    if kind in NOT_AN_APPROVAL:
        return "report"
    if kind == JULES_PLAN:
        return "jules-plan"
    if kind in PERMISSION_ONLY:
        return "approve-only"
    return "unclassified"


def _session_id(payload: str) -> str:
    """The Jules session id out of a JULES_PLAN_APPROVAL payload.

    Filed as "Jules session <id> — <title>". Matching on the longest digit run
    rather than a fixed offset, because the title is free text and has carried
    numbers before ("Fix spend claim to $8.38 USD").
    """
    best = ""
    for chunk in payload.replace("\n", " ").split():
        digits = "".join(c for c in chunk if c.isdigit())
        if len(digits) > len(best):
            best = digits
    return best


def pending(con: sqlite3.Connection, now: datetime | None = None) -> list[Item]:
    """Everything open, most urgent first."""
    now = now or datetime.now(timezone.utc)
    rows = con.execute(
        "SELECT id, at, kind, payload, deadline, default_action "
        "FROM human_requests WHERE status='pending' ORDER BY at").fetchall()

    items = []
    for r in rows:
        kind = r["kind"]
        group = classify(kind)
        payload = (r["payload"] or "").strip()
        first = payload.splitlines()[0] if payload else kind

        if group == "jules-plan":
            sid = _session_id(payload)
            command = f"approve jules {sid}" if sid else "(no session id in payload)"
            prepare = ""
        elif group == "act-then-approve":
            command = f"approve {r['id']}"
            prepare = NEEDS_YOU_FIRST[kind]
        elif group == "report":
            command = f"deny {r['id']}"
            prepare = NOT_AN_APPROVAL[kind]
        elif group == "never-grant":
            command = "(do not approve)"
            prepare = ("CHARTER.md §4 says this should never fire. Find out what "
                       "produced it before answering.")
        else:
            command = f"approve {r['id']}"
            prepare = ""

        deadline_h = _hours(r["deadline"], now)
        items.append(Item(
            kind=kind, what=first, detail=payload,
            command=command, prepare=prepare,
            blocks=_blocks(kind),
            age_h=_hours(r["at"], now),
            overdue_h=max(0.0, deadline_h),
        ))

    return sorted(items, key=lambda i: i.urgency)


def _age(h: float) -> str:
    if h < 1:
        return f"{int(h * 60)}m ago"
    if h < 48:
        return f"{h:.0f}h ago"
    return f"{h / 24:.0f}d ago"


def render(items: list[Item], jules_waiting: list[tuple[str, str]] | None = None,
           jules_error: str = "") -> str:
    """The whole point of the module. Count first, then order, then commands."""
    out: list[str] = []
    blocking = [i for i in items if i.blocks]

    jules_waiting = jules_waiting or []
    if not items and not jules_waiting:
        return ("Nothing is waiting for you.\n"
                + (f"\n(Jules could not be reached: {jules_error})\n"
                   if jules_error else ""))

    # A held Jules session blocks its own work exactly as a NEEDS_YOU_FIRST row
    # does, so it counts. Reporting "0 things waiting" above a list of things
    # waiting is the failure this tool exists to fix.
    n = len(items) + len(jules_waiting)
    stuck = len(blocking) + len(jules_waiting)
    head = f"{n} thing{'s' if n != 1 else ''} waiting for you."
    if stuck:
        head += (f"  {stuck} block{'s' if stuck == 1 else ''} progress — do "
                 f"{'that one' if stuck == 1 else 'those'} first.")
    else:
        head += "  Nothing is blocked; answer these when you have a moment."
    out += [head, ""]

    for section, group in (("DO THESE FIRST", blocking),
                           ("WHEN YOU GET TO IT",
                            [i for i in items if not i.blocks])):
        if not group:
            continue
        out.append(section)
        for i in group:
            when = _age(i.age_h)
            late = f", overdue {i.overdue_h:.0f}h" if i.overdue_h else ""
            out.append(f"  {i.kind}  ({when}{late})")
            out.append(f"    {i.what}")
            if i.prepare:
                out.append(f"    first:  {i.prepare}")
            out.append(f"    reply:  {i.command}")
            out.append("")
        out.append("")

    if jules_waiting:
        out.append("HELD IN JULES")
        out.append("  These write no code until released. They are not in the")
        out.append("  ledger — Jules holds them, and the web UI is the only")
        out.append("  other place they appear.")
        out.append("")
        for sid, title in jules_waiting:
            out.append(f"    {title}")
            out.append(f"    reply:  approve jules {sid}   or   deny jules {sid}")
            out.append("")
    elif jules_error:
        out.append(f"(Jules could not be reached, so anything held there is not "
                   f"listed: {jules_error})")

    return "\n".join(out).rstrip() + "\n"
