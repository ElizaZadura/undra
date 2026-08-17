"""Telegram: the interrupt channel to the Operator.

Outbound long-polling only — nothing is exposed inbound, so the box needs no
open port and no webhook (HANDOFF.md §5).

**Nothing in this module ever writes to `outbound`.** That table feeds
max_outbound_per_hour = 3, which halts. Approval requests and digests are
internal traffic to the Operator, who is not a third party. A busy escalation
hour must not halt Coral for the crime of talking to Eliza (AGENTS.md #2).
Ledger.record_outbound() refuses channel='telegram' as a second line of defence.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 15


class TelegramError(RuntimeError):
    pass


@dataclass
class Update:
    update_id: int
    text: str
    chat_id: str
    #: Unix seconds from Telegram's own `message.date`, when it supplied one.
    #: Carried solely so the halt receipt can say how long the message waited.
    #: On 2026-08-17 three `/halt` messages sat unread for up to three hours
    #: because polling happens at cycle start, and nothing told the Operator
    #: that. A receipt that says "set" without saying "late" would have left her
    #: with the same wrong conclusion she reached without one.
    sent_at: int | None = None


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        if not self.token or not self.chat_id:
            raise TelegramError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set")

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def _call(self, method: str, params: dict[str, Any]) -> dict:
        url = API.format(token=self.token, method=method)
        data = urllib.parse.urlencode(params).encode()
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, data=data), timeout=TIMEOUT) as r:
                body = json.load(r)
        except urllib.error.HTTPError as exc:
            raise TelegramError(f"{method}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TelegramError(f"{method}: {exc}") from exc
        if not body.get("ok"):
            raise TelegramError(f"{method}: {body.get('description')}")
        return body["result"]

    # -- sending ------------------------------------------------------------ #

    def send(self, text: str) -> None:
        """CHARTER.md §2.4: disclosure is in the message, not a footer. The
        Operator knows what she built, but these messages are also the record of
        what was said to her, and an unlabelled one is a bad habit to establish.
        """
        self._call("sendMessage", {
            "chat_id": self.chat_id,
            "text": text[:4000],
            "disable_web_page_preview": "true",
        })

    def request_approval(self, *, request_id: int, kind: str, payload: str,
                         deadline: str | None, default_action: str) -> None:
        """Every request states what is wanted, why, and what happens on silence
        (CHARTER.md §9). The reply format is fixed so parsing cannot misread a
        conversational 'sure, go ahead' as approval of the wrong thing.

        The body is built by `runner/operator.py`, which holds the one thing
        this message used to leave out: half the gated kinds exist because
        Coral *cannot* act, not because it *may not*, and those need her to go
        and do something before `approve` means anything. `bin/waiting` said so
        from 12 August; this message did not, and this message is the one that
        reaches her away from the box.
        """
        from .operator import notification
        self.send(notification(request_id=request_id, kind=kind, payload=payload,
                               deadline=deadline, default_action=default_action))

    def digest(self, body: str) -> None:
        self.send(f"[undra · daily digest]\n\n{body}\n\n"
                  f"(automated message from the undra agent system)")

    # -- receiving ---------------------------------------------------------- #

    def poll(self, offset: int | None = None) -> list[Update]:
        """Long-poll for commands. Called at cycle start so a `/halt` sent from
        the phone takes effect at the next cycle boundary at the latest."""
        params: dict[str, Any] = {"timeout": 0, "limit": 20}
        if offset is not None:
            params["offset"] = offset
        out = []
        for u in self._call("getUpdates", params):
            msg = u.get("message") or u.get("edited_message") or {}
            date = msg.get("date")
            out.append(Update(
                update_id=int(u["update_id"]),
                text=(msg.get("text") or "").strip(),
                chat_id=str((msg.get("chat") or {}).get("id", "")),
                sent_at=int(date) if isinstance(date, (int, float)) else None,
            ))
        return out


def _waited(sent_at: int | None) -> str:
    """How long a command sat unread, in words, or "" if it cannot be known."""
    if not sent_at:
        return ""
    secs = int(time.time()) - int(sent_at)
    if secs < 90:
        return ""
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def halt_receipt(sent_at: int | None = None) -> str:
    """What the Operator gets back when her `/halt` lands.

    Why this exists. The halt flag is *read* before every action, exactly as
    CHARTER.md §10 promises. But the Operator's ability to *set* it goes through
    `poll()`, which runs at cycle start — so on 2026-08-17 she sent `/halt` at
    09:18 UTC, the last poll had been at 08:12, and the next was not due until
    12:11. She checked the flag, found it `false`, and reasonably concluded the
    command had not worked. She sent it twice more. All three were queued and
    correct; nothing acknowledged any of them.

    That is the same failure fixed for the *note* channel on 2026-08-15, by
    making `mark_notes_read` send a line back. It was never applied to the one
    channel where being unsure is worst. This is that fix, and it names the
    delay rather than papering over it: an acknowledgement that hides the
    latency would still leave her guessing how long the agent kept working.
    """
    waited = _waited(sent_at)
    late = (f"You sent it {waited} ago. Halt is applied when a cycle starts, "
            f"not when you send it, so there is a delay of up to one cycle — "
            f"and it has now been applied.\n\n") if waited else ""
    return ("[undra · halt]\n\n"
            "The halt flag is set. No new actions will run and no model calls "
            "will be made.\n\n"
            f"{late}"
            "Nothing further is needed from you. To resume, run ./bin/unhalt on "
            "the box — the agent cannot clear this itself, and must not.\n\n"
            "(automated message from the undra agent system)")


OFFSET_FLAG = "telegram_offset"


def get_offset(ledger) -> int | None:
    row = ledger.con.execute(
        "SELECT value FROM flags WHERE key=?", (OFFSET_FLAG,)).fetchone()
    try:
        return int(row["value"]) if row and row["value"] else None
    except (TypeError, ValueError):
        return None


def set_offset(ledger, offset: int) -> None:
    """Persist how far we have read.

    Without this every cycle re-reads the whole backlog, which is harmless for
    `approve N` — the request is no longer pending the second time — but not for
    `/halt`. A single halt message would be re-applied on every subsequent
    cycle, so the Operator could clear the flag and watch it come straight back,
    with the cause four hours in the past and invisible.
    """
    ledger.con.execute(
        "INSERT INTO flags(key, value, updated_at, reason) VALUES(?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        (OFFSET_FLAG, str(offset), _now(), "highest Telegram update_id consumed"))
    ledger.con.commit()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def deliver_pending(tg: Telegram, ledger) -> int:
    """Send any pending approval request that has never reached the Operator.

    A request written to the ledger but not delivered — channel down, or a cycle
    run with Telegram disabled — would otherwise sit pending forever while the
    loop waits on an answer to a question nobody was asked. Verified on
    2026-08-06: request #1 was created by a cycle run with --no-telegram and was
    still pending hours later, undelivered and unnoticed.

    Deliberately NOT written to `outbound`: the Operator is not a third party
    (AGENTS.md #2).
    """
    rows = ledger.con.execute(
        "SELECT id, kind, payload, deadline, default_action FROM human_requests "
        "WHERE status='pending' AND notified_at IS NULL ORDER BY at").fetchall()
    sent = 0
    for r in rows:
        try:
            tg.request_approval(request_id=r["id"], kind=r["kind"],
                                payload=r["payload"] or "",
                                deadline=r["deadline"],
                                default_action=r["default_action"] or "abandon_task")
        except TelegramError as exc:
            ledger.event("error", "telegram",
                         f"request #{r['id']} still undelivered: {exc}")
            continue
        ledger.con.execute(
            "UPDATE human_requests SET notified_at=? WHERE id=?", (_now(), r["id"]))
        ledger.con.commit()
        ledger.event("info", "telegram", f"delivered pending request #{r['id']}")
        sent += 1
    return sent


def sync(tg: Telegram, ledger) -> int:
    """Poll from the stored offset, apply what arrived, save the new offset.
    Returns the number of updates processed."""
    updates = tg.poll(offset=get_offset(ledger))
    if not updates:
        return 0
    new_offset = process_updates(tg, ledger, updates)
    if new_offset is not None:
        set_offset(ledger, new_offset)
    return len(updates)


def process_updates(tg: Telegram, ledger, updates: list[Update]) -> int | None:
    """Apply `/halt`, `approve N` and `deny N`. Returns the new poll offset.

    Only messages from the configured chat id are honoured. CHARTER.md §7: an
    approval arrives by exactly one route, tied to a request id. A message from
    any other chat is content, not command — logged and ignored.
    """
    last: int | None = None
    for u in updates:
        last = u.update_id + 1

        if u.chat_id != str(tg.chat_id):
            ledger.event("warn", "telegram",
                         f"message from unexpected chat {u.chat_id!r} ignored; "
                         f"text={u.text[:200]!r}")
            continue

        text = u.text.lower().strip()

        if text in ("/halt", "halt"):
            ledger.con.execute(
                "INSERT INTO flags(key, value, updated_at, reason) "
                "VALUES('halt','true',datetime('now'),'/halt from Operator') "
                "ON CONFLICT(key) DO UPDATE SET value='true', "
                "updated_at=excluded.updated_at, reason=excluded.reason")
            ledger.con.commit()
            waited = _waited(u.sent_at)
            ledger.event("warn", "telegram",
                         "halt flag set by Operator via /halt"
                         + (f"; the command waited {waited}" if waited else ""))

            # The receipt is best-effort, and the order here is the whole point:
            # the flag is set and committed BEFORE anything is sent. A Telegram
            # outage must never be able to leave the agent running because it
            # could not confirm that it had stopped.
            try:
                tg.send(halt_receipt(u.sent_at))
            except Exception as exc:                      # noqa: BLE001
                ledger.event("warn", "telegram",
                             f"halt receipt not delivered: {exc}")
            continue

        parts = text.split()

        # `approve jules <session-id>` — approve a Jules plan from the phone.
        #
        # Jules holds a session at "plan generated" until a human approves it,
        # which normally means opening a browser. A session sat unnoticed for
        # five hours on 2026-08-07 for exactly that reason, and the agent
        # escalated it as stalled work rather than as a waiting approval.
        #
        # This is deliberately ONLY on the Operator's channel. Coral has no
        # corresponding tool and must never get one: a gate the agent can open
        # for itself is not a gate. Approval arrives by the same route as every
        # other approval, from the one chat id that is checked above.
        if len(parts) == 3 and parts[0] in ("approve", "deny") and parts[1] == "jules":
            session_id = parts[2]

            # The request row raised when the task was filed is keyed by session
            # id in its payload, not by request id, because the command the
            # Operator types names the session. Resolve it here or it stays
            # pending forever: nothing else closes a JULES_PLAN_APPROVAL, and an
            # open request re-appears in every digest until it does.
            def _close(status: str) -> None:
                ledger.con.execute(
                    "UPDATE human_requests SET status=?, resolved_at=datetime('now'), "
                    "response=? WHERE kind='JULES_PLAN_APPROVAL' AND status='pending' "
                    "AND payload LIKE ?",
                    (status, f"{parts[0]} jules {session_id}", f"%{session_id}%"))
                ledger.con.commit()

            if parts[0] == "deny":
                _close("denied")
                ledger.event("info", "telegram",
                             f"Operator declined Jules plan for session {session_id}; "
                             "the session stays paused and needs re-filing or a "
                             "decision to drop it")
                continue
            try:
                from .jules import Jules
                Jules().approve_plan(session_id)
            except Exception as exc:  # noqa: BLE001
                ledger.event("error", "telegram",
                             f"could not approve Jules plan {session_id}: {exc}")
                try:
                    tg.send(f"[undra] Could not approve Jules session {session_id}: "
                            f"{str(exc)[:180]}")
                except Exception:  # noqa: BLE001
                    pass
                continue
            _close("granted")
            ledger.event("info", "telegram",
                         f"Operator approved the Jules plan for session {session_id}")
            try:
                tg.send(f"[undra] Approved the plan for Jules session {session_id}. "
                        "It will start writing code; the pull request still has to "
                        "pass CI before anything merges.\n\n"
                        "(automated message from the undra agent system)")
            except Exception:  # noqa: BLE001
                pass
            continue

        if len(parts) == 2 and parts[0] in ("approve", "deny") and parts[1].isdigit():
            rid = int(parts[1])
            row = ledger.con.execute(
                "SELECT id, kind, status FROM human_requests WHERE id=?",
                (rid,)).fetchone()
            if not row:
                ledger.event("warn", "telegram", f"reply to unknown request #{rid}")
                continue
            if row["status"] != "pending":
                ledger.event("info", "telegram",
                             f"request #{rid} already {row['status']}; reply ignored")
                continue
            new = "granted" if parts[0] == "approve" else "denied"
            ledger.con.execute(
                "UPDATE human_requests SET status=?, resolved_at=datetime('now'), "
                "response=? WHERE id=?", (new, u.text[:500], rid))
            ledger.con.commit()
            ledger.event("info", "telegram", f"request #{rid} {new} by Operator")
            continue

        # Anything else is a note to Coral. Previously this was logged as an
        # "unparsed message" and went nowhere — the Operator could type at the
        # bot all day and no cycle would ever see it.
        if text:
            mid = ledger.message("from_operator", u.text)
            ledger.event("info", "telegram",
                         f"note #{mid} from the Operator, queued for the next cycle")
            try:
                tg.send(f"[undra] Noted (#{mid}). Coral reads this at the start of "
                        f"its next cycle — up to four hours from now, not "
                        f"immediately.\n\n"
                        f"(automated message from the undra agent system)")
            except TelegramError:
                pass

    return last
