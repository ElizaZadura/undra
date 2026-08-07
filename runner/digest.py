"""The operator's view: what happened, what it cost, what needs her.

CHARTER.md §9: "Once per day, produce the digest: what happened, what it cost,
what you decided, what is blocked, what you need. Honest, short, numbers from the
ledger only."

This is deliberately NOT the public operations log. The two have opposite jobs:

  publish_log.py  derived, unreviewed, written for a stranger who was not here.
                  That property is what lets CHARTER.md §5 authorise publishing
                  it without an approval token, so nothing judgemental may leak
                  into it.

  this file       terse and judgemental, written for the one person who has to
                  decide something. It says "this is broken" and "this needs
                  you", which is exactly what the public log must never say on
                  its own authority.

Every figure here is read from the ledger. No estimates, no rounding presented as
a count, nothing carried forward from a previous run (CHARTER.md §6.5).

Not written to `outbound`: the Operator is not a third party (AGENTS.md #2).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

DIGEST_FLAG = "last_digest_date"


def _rows(con, sql: str, *args) -> list:
    return con.execute(sql, args).fetchall()


def build(ledger, cfg) -> str:
    """Render the digest. Returns text; sending is the caller's business."""
    con = ledger.con
    now = datetime.now(timezone.utc)
    day_ago = (now - timedelta(days=1)).isoformat()

    out: list[str] = []
    hours = cfg.hours_remaining
    out.append(f"[undra · daily digest] {now.strftime('%Y-%m-%d %H:%M')}Z")
    out.append(f"{hours:.0f}h to the deadline." if hours >= 0 else "PAST DEADLINE.")

    # -- what happened ------------------------------------------------------ #
    cycles = _rows(con, "SELECT status, COUNT(*) n FROM cycles "
                        "WHERE started_at > ? GROUP BY status", day_ago)
    total = sum(r["n"] for r in cycles)
    breakdown = ", ".join(f"{r['n']} {r['status']}" for r in cycles) or "none"
    out.append(f"\nCYCLES (24h): {total} — {breakdown}")

    unproductive = sum(r["n"] for r in cycles
                       if r["status"] in ("halted", "blocked", "idle", "unproductive"))
    cap = cfg.rates["max_unproductive_cycles_per_day"]
    if unproductive > cap:
        out.append(f"  ! {unproductive} unproductive, over the limit of {cap}. "
                   "The constraints may be the bottleneck, not the work.")

    # -- what it cost ------------------------------------------------------- #
    spent = ledger.spend_total_usd()
    llm = ledger.llm_spend_usd()
    cap_total = cfg.budget["hard_cap_total"]
    cap_llm = cfg.budget["cap_llm"]
    calls = con.execute("SELECT COUNT(*) FROM llm_usage WHERE at > ?",
                        (day_ago,)).fetchone()[0]
    out.append(f"\nSPEND: ${spent:.2f} of ${cap_total:.0f} total "
               f"(LLM ${llm:.2f} of ${cap_llm:.0f}), {calls} model calls in 24h")

    # Free-tier ceiling, measured rather than assumed. HANDOFF.md §4 budgeted on
    # ~1,500 free requests/day; the observed figure has been an order of
    # magnitude lower, and Google publishes no number and changes it without
    # notice. Reporting what actually happened turns a documentation question
    # into a measurement, so a change in the subscription or the tier shows up
    # here rather than as a surprise fallback.
    today = now.strftime("%Y-%m-%d")
    free = con.execute(
        "SELECT COUNT(*) FROM llm_usage WHERE key_role='ops' AND at LIKE ?",
        (today + "%",)).fetchone()[0]
    paid = con.execute(
        "SELECT COUNT(*) FROM llm_usage WHERE key_role IN ('planning','app') "
        "AND at LIKE ?", (today + "%",)).fetchone()[0]
    unknown = con.execute(
        "SELECT COUNT(*) FROM llm_usage WHERE (key_role IS NULL OR key_role='') "
        "AND at LIKE ?", (today + "%",)).fetchone()[0]
    exhausted = con.execute(
        "SELECT MIN(at) FROM events WHERE at LIKE ? AND "
        "message LIKE '%free-tier quota exhausted%'", (today + "%",)).fetchone()[0]
    if free or paid or unknown:
        line = f"  free tier: {free} call(s) today"
        if exhausted:
            line += f", gave out at {exhausted[11:16]}"
        else:
            line += ", still serving"
        line += f" · paid: {paid}"
        if unknown:
            line += f" · unattributed: {unknown}"
        out.append(line)

    # -- what was decided ---------------------------------------------------- #
    decisions = _rows(con, "SELECT summary FROM decisions WHERE at > ? ORDER BY at",
                      day_ago)
    if decisions:
        out.append(f"\nDECIDED ({len(decisions)}):")
        for d in decisions[:6]:
            out.append(f"  · {d['summary'][:150]}")
        if len(decisions) > 6:
            out.append(f"  … and {len(decisions) - 6} more")

    # -- what needs her ------------------------------------------------------ #
    pending = _rows(con, "SELECT id, kind, priority, default_action, notified_at "
                         "FROM human_requests WHERE status='pending' ORDER BY at")
    if pending:
        out.append(f"\nNEEDS YOU ({len(pending)}):")
        for p in pending:
            mark = "" if p["notified_at"] else "  [never delivered until now]"
            out.append(f"  #{p['id']} {p['kind']} — reply 'approve {p['id']}' or "
                       f"'deny {p['id']}'{mark}")
            out.append(f"      if no answer: {p['default_action']}")

    # -- what is going in circles -------------------------------------------- #
    # Work that succeeds every cycle and moves nothing. The failure counters are
    # blind to this: nothing failed. Three cycles of it means each fresh instance
    # of the agent reached the same conclusion and got the same non-result, so
    # the loop cannot break itself and the recommendation is a human.
    threshold = cfg.rates.get("max_cycles_without_progress", 3)
    stuck = _rows(con,
                  """SELECT kind, COUNT(DISTINCT cycle_id) cycles, COUNT(*) n,
                            MAX(at) last_at, GROUP_CONCAT(DISTINCT target) targets
                     FROM actions
                     WHERE at > ? AND cycle_id IS NOT NULL AND status='ok'
                     GROUP BY kind HAVING cycles >= ?
                     ORDER BY cycles DESC""", day_ago, threshold)
    if stuck:
        out.append(f"\nSTUCK — HUMAN INTERVENTION RECOMMENDED:")
        for s in stuck:
            targets = [t for t in (s["targets"] or "").split(",") if t]
            out.append(f"  {s['kind']}: {s['cycles']} cycles, {s['n']} attempts, "
                       f"no state change. Last at {s['last_at'][11:16]}.")
            for t in targets[-3:]:
                out.append(f"      · {t[:110]}")
            out.append("    Each attempt succeeded and changed nothing. Coral "
                       "cannot break this by retrying.")

    # -- build tasks waiting on a human ------------------------------------- #
    # A Jules session parked at "plan generated" reports COMPLETED and looks
    # finished from every angle except its activity list. One sat unnoticed for
    # five hours on 2026-08-07 while the agent described it as stalled work.
    # Read from the ledger, not the API, so the digest never blocks on network.
    filed = _rows(con, "SELECT target, at FROM actions WHERE kind='JULES_SESSION' "
                       "AND status='ok' AND at > ? ORDER BY at DESC", day_ago)
    if filed:
        out.append(f"\nBUILD TASKS FILED (24h): {len(filed)}")
        out.append("  if one is waiting on plan approval, reply:  approve jules <session-id>")

    # -- what is blocked ----------------------------------------------------- #
    questions = _rows(con, "SELECT id, question, blocking FROM open_questions "
                           "WHERE answer IS NULL ORDER BY blocking DESC, at")
    blocking = [q for q in questions if q["blocking"]]
    if blocking:
        out.append(f"\nBLOCKED ({len(blocking)}):")
        for q in blocking[:4]:
            out.append(f"  #{q['id']} {q['question'][:170]}")
    if questions and not blocking:
        out.append(f"\nOPEN QUESTIONS: {len(questions)} (none blocking)")

    # -- what broke ---------------------------------------------------------- #
    # Scoped to the most recent cycle, not the last 24 hours. An error from six
    # hours ago has usually been fixed since, and listing it as though it were
    # live trains the reader to skim past the section — the same failure as
    # §6.8's comforting narrative, inverted. Older errors are counted, not
    # recited, so a genuinely persistent problem still shows up as a rising
    # number without burying what just happened.
    last_start = con.execute(
        "SELECT MAX(started_at) FROM cycles").fetchone()[0] or day_ago
    recent = _rows(con, "SELECT source, COUNT(*) n, MAX(message) m FROM events "
                        "WHERE level='error' AND at >= ? GROUP BY source "
                        "ORDER BY n DESC", last_start)
    older = con.execute("SELECT COUNT(*) FROM events WHERE level='error' "
                        "AND at > ? AND at < ?", (day_ago, last_start)).fetchone()[0]
    if recent:
        out.append("\nERRORS IN THE LAST CYCLE:")
        for e in recent[:5]:
            times = f" ×{e['n']}" if e["n"] > 1 else ""
            out.append(f"  {e['source']}{times}: {e['m'][:140]}")
    if older:
        out.append(f"\nearlier errors today: {older} "
                   "(may already be fixed — see the ledger)")

    failed = _rows(con, "SELECT kind, target, COUNT(*) n FROM actions "
                        "WHERE status='failed' AND at >= ? GROUP BY kind, target "
                        "ORDER BY n DESC", last_start)
    if failed:
        out.append("\nFAILED ACTIONS IN THE LAST CYCLE:")
        for f in failed[:5]:
            out.append(f"  {f['kind']} -> {f['target'][:60]} ×{f['n']}")

    # -- state --------------------------------------------------------------- #
    if ledger.is_halted():
        out.append(f"\n!! HALTED: {ledger.halt_reason()}")

    out.append("\nreply /halt to stop everything, no explanation needed.")
    out.append("(automated message from the undra agent system)")
    return "\n".join(out)


def due(ledger, now: datetime | None = None) -> bool:
    """One digest per calendar day. Cheap and stateless — the ledger remembers,
    not the process, because the process does not survive the cycle."""
    now = now or datetime.now(timezone.utc)
    row = ledger.con.execute(
        "SELECT value FROM flags WHERE key=?", (DIGEST_FLAG,)).fetchone()
    return not row or row["value"] != now.strftime("%Y-%m-%d")


def mark_sent(ledger, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    ledger.con.execute(
        "INSERT INTO flags(key, value, updated_at, reason) VALUES(?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        (DIGEST_FLAG, now.strftime("%Y-%m-%d"), now.isoformat(),
         "last date a digest was sent to the Operator"))
    ledger.con.commit()


def send_if_due(ledger, cfg, telegram, *, hour_utc: int = 4) -> bool:
    """Send once per day, on the first cycle at or after `hour_utc`.

    The threshold is not the delivery time — the timer is, and it fires at
    00:11, 04:11, 08:11, 12:11, 16:11 and 20:11 UTC. A threshold of 6 therefore
    delivered on the 08:11 cycle, which is 10:11 in Stockholm: too late to be a
    morning briefing. 4 lands it on the 04:11 cycle instead, 06:11 local in
    summer and 05:11 in winter.

    Winter is the thing to watch. If a 05:11 buzz is unwelcome, raise this to 6
    rather than trying to be clever about local time: the box is deliberately on
    UTC (LAB_SETUP.md §2.3) precisely so that scheduled work does not run twice
    or not at all across a DST changeover.
    """
    now = datetime.now(timezone.utc)
    if now.hour < hour_utc or not due(ledger, now):
        return False
    try:
        telegram.digest(build(ledger, cfg))
    except Exception as exc:  # noqa: BLE001
        ledger.event("error", "digest", f"could not send digest: {exc}")
        return False
    mark_sent(ledger, now)
    ledger.event("info", "digest", "daily digest sent to the Operator")
    return True
