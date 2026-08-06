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
        conversational 'sure, go ahead' as approval of the wrong thing."""
        self.send(
            f"[undra · approval needed]\n\n"
            f"#{request_id}  {kind}\n\n"
            f"{payload}\n\n"
            f"deadline: {deadline or 'none'}\n"
            f"if no answer: {default_action}\n\n"
            f"reply exactly:  approve {request_id}   or   deny {request_id}\n"
            f"(automated message from the undra agent system)"
        )

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
            out.append(Update(
                update_id=int(u["update_id"]),
                text=(msg.get("text") or "").strip(),
                chat_id=str((msg.get("chat") or {}).get("id", "")),
            ))
        return out


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
            ledger.event("warn", "telegram", "halt flag set by Operator via /halt")
            continue

        parts = text.split()
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

        if text:
            ledger.event("info", "telegram",
                         f"unparsed message from Operator: {u.text[:300]!r}")

    return last
