#!/usr/bin/env python3
"""
situation_report.py — deterministic pre-cycle briefing and invariant checker.

Run this immediately before every agent cycle. Its output is prepended to the
agent's context and is the *only* place the agent is permitted to learn
operational facts (CHARTER.md §6.2).

There is no LLM in this file, deliberately. The whole point is that the agent's
picture of reality is assembled by code that cannot rationalise, round up, or
carry a stale value forward out of optimism.

Two design rules that matter more than they look:

  1. A collector that fails reports UNKNOWN. It never omits the fact, never
     substitutes a default, and never crashes the report. Silent omission is
     how an agent ends up confidently acting on a state that does not exist.

  2. Every UNKNOWN is repeated in a block at the end of the report, with an
     explicit instruction. Unknowns that are merely absent from the middle of a
     long document get read straight past.

Usage:
    python3 situation_report.py --init        # create the ledger schema
    python3 situation_report.py               # emit report, check invariants
    python3 situation_report.py --json        # machine-readable
    python3 situation_report.py --no-enforce  # report only, do not set halt flag

Exit codes:
    0   proceed
    10  HALTED — an invariant is breached, or the halt flag is set
    20  the report itself could not be produced (treat as halt)

Requires Python 3.11+ (tomllib). No third-party packages.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(os.environ.get("INVARIANTS_PATH", "./invariants.toml"))
REPORT_DIR = Path(os.environ.get("REPORT_DIR", "./reports"))
HTTP_TIMEOUT = 8
MAX_LIST_ITEMS = 8  # token discipline: truncate every list, note the remainder

OK, UNKNOWN, STALE = "OK", "UNKNOWN", "STALE"


# --------------------------------------------------------------------------- #
# facts
# --------------------------------------------------------------------------- #

@dataclass
class Fact:
    """A single piece of state, with provenance and an honest status."""
    key: str
    value: Any
    status: str = OK
    source: str = ""
    note: str = ""

    def render(self) -> str:
        if self.status != OK:
            return f"{self.key}: {self.status}" + (f" ({self.note})" if self.note else "")
        return f"{self.key}: {self.value}" + (f"  [{self.note}]" if self.note else "")


@dataclass
class Breach:
    rule: str
    detail: str
    severity: str  # "halt" | "warn"


@dataclass
class Report:
    now: datetime
    facts: list[Fact] = field(default_factory=list)
    breaches: list[Breach] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)
    flag_already_set: bool = False  # if so, do not rewrite the halt reason

    def add(self, f: Fact) -> Fact:
        self.facts.append(f)
        return f

    @property
    def unknowns(self) -> list[Fact]:
        return [f for f in self.facts if f.status != OK]

    @property
    def halted(self) -> bool:
        return any(b.severity == "halt" for b in self.breaches)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def safe(fn, key: str, source: str, default=None):
    """Run a collector. Any failure becomes an UNKNOWN fact, never an exception."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — deliberate: no collector may abort the report
        return Fact(key, default, UNKNOWN, source, f"{type(exc).__name__}: {exc}"[:160])


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT,
  status TEXT, handoff TEXT);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY, cycle_id INTEGER, at TEXT NOT NULL,
  summary TEXT NOT NULL, rationale TEXT, evidence TEXT,
  reversible INTEGER, falsifier TEXT);

-- Idempotency lives here, not in the model. An action is attempted at most once
-- per key, no matter how many times the agent decides to do it.
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, target TEXT,
  idempotency_key TEXT UNIQUE, status TEXT NOT NULL, attempt INTEGER DEFAULT 1,
  error TEXT, cycle_id INTEGER);

CREATE TABLE IF NOT EXISTS spend (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, category TEXT NOT NULL,
  usd REAL NOT NULL, description TEXT, idempotency_key TEXT UNIQUE);

-- The API has no hard billing cap, so this table *is* the cap.
--
-- thinking_tokens is not decoration. Gemini 3.x bills reasoning tokens and they
-- appear in neither promptTokenCount nor candidatesTokenCount: a 7-in/1-out call
-- measured 109 total. Logging only input+output undercounts the cap ~12x.
-- total_tokens is what the API actually billed; keep it as the source of truth.
CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, model TEXT NOT NULL,
  input_tokens INTEGER, output_tokens INTEGER, thinking_tokens INTEGER,
  total_tokens INTEGER, usd_est REAL, cycle_id INTEGER);

CREATE TABLE IF NOT EXISTS objectives (
  id INTEGER PRIMARY KEY, priority INTEGER NOT NULL, title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', created_at TEXT, done_at TEXT);

CREATE TABLE IF NOT EXISTS open_questions (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, question TEXT NOT NULL,
  blocking INTEGER DEFAULT 0, answer TEXT, answered_at TEXT);

-- notified_at is NULL until the request has actually reached the Operator.
-- A request written to the ledger but never delivered — the channel was down,
-- or the cycle ran with it disabled — would otherwise sit pending forever with
-- nothing retrying it, and the loop would wait on an answer to a question
-- nobody was asked.
CREATE TABLE IF NOT EXISTS human_requests (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT,
  priority TEXT DEFAULT 'digest', deadline TEXT, default_action TEXT,
  status TEXT NOT NULL DEFAULT 'pending', resolved_at TEXT, response TEXT,
  notified_at TEXT);

-- recipient_hash, not recipient: no customer PII in a repo-committed table.
CREATE TABLE IF NOT EXISTS outbound (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, channel TEXT, recipient_hash TEXT,
  subject TEXT, status TEXT, approval_request_id INTEGER);

CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, provider TEXT, external_id TEXT UNIQUE,
  gross_usd REAL, net_usd REAL, related_party INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, level TEXT, source TEXT, message TEXT);

CREATE TABLE IF NOT EXISTS flags (
  key TEXT PRIMARY KEY, value TEXT, updated_at TEXT, reason TEXT);
"""


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")   # several writers, short transactions
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_schema(path: str) -> None:
    con = connect(path)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    print(f"ledger initialised at {path}")


# --------------------------------------------------------------------------- #
# collectors
# --------------------------------------------------------------------------- #

def q1(con, sql: str, *args) -> Any:
    row = con.execute(sql, args).fetchone()
    return row[0] if row and row[0] is not None else 0


def collect_time(rep: Report, cfg: dict) -> None:
    deadline = datetime.fromisoformat(cfg["project"]["deadline_utc"].replace("Z", "+00:00"))
    left = deadline - rep.now
    hours = left.total_seconds() / 3600
    rep.add(Fact("deadline_utc", deadline.isoformat(), source="invariants.toml"))
    rep.add(Fact("hours_remaining", f"{hours:.1f}", source="computed",
                 note="PAST DEADLINE" if hours < 0 else ""))
    if hours < 0:
        rep.breaches.append(Breach("deadline", "deadline has passed", "halt"))


def collect_budget(rep: Report, con, cfg: dict) -> None:
    b = cfg["budget"]
    llm = q1(con, "SELECT SUM(usd_est) FROM llm_usage")
    other = q1(con, "SELECT SUM(usd) FROM spend")
    total = llm + other
    rep.add(Fact("spend_llm_usd", f"{llm:.2f}", source="ledger.llm_usage"))
    rep.add(Fact("spend_other_usd", f"{other:.2f}", source="ledger.spend"))
    rep.add(Fact("spend_total_usd", f"{total:.2f}", source="computed",
                 note=f"cap {b['hard_cap_total']:.2f}"))
    rep.add(Fact("budget_remaining_usd", f"{b['hard_cap_total'] - total:.2f}", source="computed"))

    threshold = b["halt_at_pct"] / 100
    for label, spent, cap in (("total", total, b["hard_cap_total"]),
                              ("llm", llm, b["cap_llm"])):
        if cap and spent >= cap * threshold:
            sev = "halt" if spent >= cap else "warn"
            rep.breaches.append(Breach(
                f"budget.{label}", f"{spent:.2f} of {cap:.2f} cap ({spent/cap:.0%})", sev))


def collect_rates(rep: Report, con, cfg: dict) -> None:
    r = cfg["rates"]
    hour_ago = (rep.now - timedelta(hours=1)).isoformat()
    day_ago = (rep.now - timedelta(days=1)).isoformat()

    out_hr = q1(con, "SELECT COUNT(*) FROM outbound WHERE at > ?", hour_ago)
    out_all = q1(con, "SELECT COUNT(*) FROM outbound")
    deploys = q1(con, "SELECT COUNT(*) FROM actions WHERE kind='DEPLOY' AND at > ?", hour_ago)
    cycles = q1(con, "SELECT COUNT(*) FROM cycles WHERE started_at > ?", day_ago)

    # Cycles that woke but achieved nothing. Distinguishes *blocked* idle from
    # *scheduled* idle — without this, a rate-limited cycle that did nothing looks
    # identical to a productive one in every other counter here.
    unprod = q1(con, "SELECT COUNT(*) FROM cycles WHERE started_at > ? AND "
                     "status IN ('halted','blocked','idle','unproductive')", day_ago)

    rep.add(Fact("outbound_last_hour", out_hr, source="ledger.outbound",
                 note=f"max {r['max_outbound_per_hour']}"))
    rep.add(Fact("outbound_total", out_all, source="ledger.outbound",
                 note=f"max {r['max_outbound_total']}"))
    rep.add(Fact("deploys_last_hour", deploys, source="ledger.actions"))
    rep.add(Fact("cycles_last_24h", cycles, source="ledger.cycles"))
    rep.add(Fact("unproductive_cycles_24h", unprod, source="ledger.cycles",
                 note=f"max {r['max_unproductive_cycles_per_day']} — limits may be "
                      "the bottleneck" if unprod else ""))

    for name, val, cap, sev in (
        ("rates.outbound_per_hour", out_hr, r["max_outbound_per_hour"], "halt"),
        ("rates.outbound_total", out_all, r["max_outbound_total"], "halt"),
        ("rates.deploys_per_hour", deploys, r["max_deploys_per_hour"], "warn"),
        ("rates.cycles_per_day", cycles, r["max_cycles_per_day"], "warn"),
        ("rates.unproductive_cycles", unprod, r["max_unproductive_cycles_per_day"], "warn"),
    ):
        if val > cap:
            rep.breaches.append(Breach(name, f"{val} > {cap}", sev))

    # Repeated identical failures — the signature of a stuck loop rather than a
    # single bad call. This is the check that catches the classic long-horizon
    # meltdown before it burns the budget.
    streaks = con.execute(
        """SELECT kind, target, COUNT(*) n FROM actions
           WHERE status='failed' AND at > ?
           GROUP BY kind, target HAVING n >= ?
           ORDER BY n DESC""",
        (day_ago, r["max_repeated_action_failures"])).fetchall()
    if streaks:
        rep.sections["FAILURE STREAKS"] = [
            f"{s['kind']} -> {s['target']}: {s['n']} consecutive failures" for s in streaks]
        rep.breaches.append(Breach(
            "rates.repeated_failures",
            f"{len(streaks)} action(s) failing repeatedly: " +
            ", ".join(f"{s['kind']}/{s['target']}" for s in streaks[:3]), "halt"))


def collect_stuck_work(rep: Report, con, cfg: dict) -> None:
    """Work that keeps succeeding and keeps achieving nothing.

    `max_repeated_action_failures` catches actions that FAIL repeatedly. It is
    blind to the opposite and more insidious shape: an action that succeeds every
    time while the underlying state never moves. Observed 2026-08-07 — three
    consecutive cycles each filed a Jules task against the same pull request
    conflict, each filing succeeded, and the conflict was still there at the end.
    Nothing failed, so nothing warned.

    The signal is distinct cycles, not attempt count. Three tries inside one
    cycle is iteration; the same work reappearing in three separate cycles means
    each fresh instance of the agent looked at the state, drew the same
    conclusion, and got the same non-result. That is the stateless-agent version
    of a stuck loop, and no amount of retrying inside a cycle will break it.
    """
    threshold = cfg["rates"].get("max_cycles_without_progress", 3)
    day_ago = (rep.now - timedelta(days=1)).isoformat()

    # Group by kind and a coarse target, because the same underlying job gets
    # described differently each cycle — "resolve conflicts", "merge main in",
    # "push a commit to trigger CI" were all the same stuck work.
    rows = con.execute(
        """SELECT kind, target, COUNT(DISTINCT cycle_id) cycles,
                  COUNT(*) attempts, MIN(at) first_at, MAX(at) last_at
           FROM actions
           WHERE at > ? AND cycle_id IS NOT NULL
           GROUP BY kind, target
           HAVING cycles >= ?
           ORDER BY cycles DESC""", (day_ago, threshold)).fetchall()

    # Same kind, different wording each cycle: group by kind alone as a second
    # pass, so re-described work is still caught.
    by_kind = con.execute(
        """SELECT kind, COUNT(DISTINCT cycle_id) cycles, COUNT(*) n,
                  GROUP_CONCAT(DISTINCT target) targets
           FROM actions
           WHERE at > ? AND cycle_id IS NOT NULL AND status='ok'
           GROUP BY kind
           HAVING cycles >= ?""", (day_ago, threshold)).fetchall()

    lines: list[str] = []
    for r in rows:
        lines.append(f"{r['kind']} -> {r['target']}: same target in "
                     f"{r['cycles']} cycles ({r['attempts']} attempts), "
                     f"first {r['first_at'][11:16]} last {r['last_at'][11:16]}")
    for r in by_kind:
        targets = [t for t in (r["targets"] or "").split(",") if t]
        if len(targets) > 1:
            lines.append(
                f"{r['kind']}: {r['cycles']} consecutive cycles, {len(targets)} "
                f"differently-worded targets — likely the same work re-described. "
                f"Latest: {targets[-1][:70]}")

    if lines:
        rep.sections["REPEATED WORK WITHOUT PROGRESS"] = lines + [
            "",
            "Each of these succeeded every time and changed nothing. Re-filing it "
            "will not help — a different instance of you already tried that.",
            "Escalate with request_human, state exactly what is stuck and where, "
            "and move to other objectives (CHARTER.md §8.1).",
        ]
        rep.breaches.append(Breach(
            "progress.stalled",
            f"{len(lines)} piece(s) of work repeated across "
            f"{threshold}+ cycles with no state change", "warn"))


def collect_staleness(rep: Report, con, cfg: dict) -> None:
    s = cfg["staleness"]
    last_evt = con.execute("SELECT MAX(at) FROM events").fetchone()[0]
    last_cycle = con.execute("SELECT MAX(started_at) FROM cycles").fetchone()[0]

    def age_hours(ts: str | None) -> float | None:
        if not ts:
            return None
        try:
            return (rep.now - datetime.fromisoformat(ts)).total_seconds() / 3600
        except ValueError:
            return None

    for key, ts, cap in (("hours_since_ledger_write", last_evt, s["max_hours_without_ledger_write"]),
                         ("hours_since_cycle", last_cycle, s["max_hours_without_cycle"])):
        h = age_hours(ts)
        if h is None:
            rep.add(Fact(key, None, UNKNOWN, "ledger", "no rows yet — first run?"))
            continue
        rep.add(Fact(key, f"{h:.1f}", source="ledger", note=f"max {cap}"))
        if h > cap:
            rep.breaches.append(Breach(f"staleness.{key}", f"{h:.1f}h > {cap}h", "warn"))


def collect_halt_flag(rep: Report, con) -> None:
    row = con.execute("SELECT value, reason, updated_at FROM flags WHERE key='halt'").fetchone()
    if row and str(row["value"]).lower() in ("1", "true", "yes"):
        rep.flag_already_set = True
        rep.add(Fact("halt_flag", True, source="ledger.flags",
                     note=f"set {row['updated_at']}: {row['reason']}"))
        rep.breaches.append(Breach("halt_flag", row["reason"] or "set manually", "halt"))
    else:
        rep.add(Fact("halt_flag", False, source="ledger.flags"))


def collect_work(rep: Report, con) -> None:
    def rows(sql, *a):
        return con.execute(sql, a).fetchall()

    objs = rows("SELECT priority, title, status FROM objectives "
                "WHERE status='open' ORDER BY priority LIMIT ?", MAX_LIST_ITEMS + 1)
    rep.sections["OPEN OBJECTIVES (priority order)"] = (
        [f"P{o['priority']} {o['title']}" for o in objs[:MAX_LIST_ITEMS]] or
        ["none — the first task of this cycle is to propose an objective list"])

    pend = rows("SELECT id, kind, priority, deadline, default_action FROM human_requests "
                "WHERE status='pending' ORDER BY at LIMIT ?", MAX_LIST_ITEMS)
    rep.sections["PENDING APPROVALS (do not block on these)"] = (
        [f"#{p['id']} {p['kind']} [{p['priority']}] deadline={p['deadline'] or '-'} "
         f"on_timeout={p['default_action']}" for p in pend] or ["none"])

    qs = rows("SELECT id, question, blocking FROM open_questions "
              "WHERE answer IS NULL ORDER BY blocking DESC, at LIMIT ?", MAX_LIST_ITEMS)
    rep.sections["OPEN QUESTIONS"] = (
        [f"#{q['id']}{' [BLOCKING]' if q['blocking'] else ''} {q['question']}" for q in qs]
        or ["none"])

    dec = rows("SELECT at, summary, reversible FROM decisions ORDER BY at DESC LIMIT ?", 5)
    rep.sections["LAST 5 DECISIONS (newest first)"] = (
        [f"{d['at'][:16]} {'[irreversible] ' if not d['reversible'] else ''}{d['summary']}"
         for d in dec] or ["none"])

    last = con.execute("SELECT id, started_at, status, handoff FROM cycles "
                       "ORDER BY started_at DESC LIMIT 1").fetchone()
    rep.sections["PREVIOUS CYCLE HANDOFF"] = (
        [f"cycle #{last['id']} ({last['status']}) {last['started_at'][:16]}",
         last["handoff"] or "(no handoff written — treat prior state as unverified)"]
        if last else ["no previous cycle — this is cycle 1"])


def collect_revenue(rep: Report, con) -> None:
    # Populated by the payment provider's webhook, not by the agents. If the
    # webhook is not wired yet the count is genuinely zero, which is a fact.
    n = q1(con, "SELECT COUNT(*) FROM payments WHERE related_party=0")
    gross = q1(con, "SELECT SUM(gross_usd) FROM payments WHERE related_party=0")
    rp = q1(con, "SELECT COUNT(*) FROM payments WHERE related_party=1")
    rep.add(Fact("arms_length_payments", n, source="ledger.payments"))
    rep.add(Fact("arms_length_gross_usd", f"{gross:.2f}", source="ledger.payments"))
    rep.add(Fact("related_party_payments", rp, source="ledger.payments",
                 note="must be reported separately in the submission"))


def collect_deploy(rep: Report, con, cfg: dict) -> None:
    cap = cfg["staleness"]["max_hours_without_healthy_deploy"]
    hosts = cfg.get("scope", {}).get("allowed_hosts", [])
    if not hosts or "FILL_ME" in hosts[0]:
        rep.add(Fact("deploy_health", None, UNKNOWN, "config",
                     "no host configured yet — nothing is deployed"))
        return
    url = os.environ.get("HEALTHCHECK_URL") or f"https://{hosts[0]}/healthz"
    healthy = False
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "situation-report/1"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            healthy = r.status == 200
            rep.add(Fact("deploy_health", f"HTTP {r.status}", source=url,
                         note="" if healthy else "NOT HEALTHY"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        rep.add(Fact("deploy_health", None, UNKNOWN, url, str(exc)[:120]))

    if healthy:
        # Record the heartbeat so downtime duration is measurable next cycle.
        con.execute("INSERT INTO events(at, level, source, message) VALUES(?,?,?,?)",
                    (rep.now.isoformat(), "info", "deploy_health", f"200 {url}"))
        con.commit()
        return

    # Unhealthy. Severity depends on how long it has been down, not the fact of it:
    # a failed deploy mid-iteration is routine, four hours of downtime is not.
    last = con.execute("SELECT MAX(at) FROM events WHERE source='deploy_health'").fetchone()[0]
    if not last:
        rep.breaches.append(Breach("deploy.health", f"{url} not healthy, never has been",
                                   "warn"))
        return
    try:
        down = (rep.now - datetime.fromisoformat(last)).total_seconds() / 3600
    except ValueError:
        rep.add(Fact("hours_since_healthy_deploy", None, UNKNOWN, "ledger.events",
                     "unparseable timestamp"))
        return
    rep.add(Fact("hours_since_healthy_deploy", f"{down:.1f}", source="ledger.events",
                 note=f"max {cap}"))
    rep.breaches.append(Breach("deploy.health", f"{url} down {down:.1f}h",
                               "halt" if down > cap else "warn"))


def collect_git(rep: Report) -> None:
    def git(*args: str) -> str:
        return subprocess.run(("git", *args), capture_output=True, text=True,
                              timeout=10, check=True).stdout.strip()
    try:
        rep.add(Fact("git_head", git("rev-parse", "--short", "HEAD"), source="git"))
        rep.add(Fact("git_branch", git("rev-parse", "--abbrev-ref", "HEAD"), source="git"))
        dirty = git("status", "--porcelain")
        rep.add(Fact("git_uncommitted_files", len(dirty.splitlines()), source="git",
                     note="uncommitted work is invisible to the audit trail" if dirty else ""))
    except Exception as exc:  # noqa: BLE001
        rep.add(Fact("git_head", None, UNKNOWN, "git", str(exc)[:120]))


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

BANNER_HALT = """
################################################################################
#  HALTED. Take no action. Write a state summary to the ledger and exit.       #
#  Do not investigate why. Do not attempt to clear the flag. (CHARTER.md §10)  #
################################################################################
"""


def render(rep: Report, cfg: dict) -> str:
    out: list[str] = []
    out.append("=" * 78)
    out.append(f"SITUATION REPORT — {rep.now.isoformat(timespec='seconds')}")
    out.append(f"project: {cfg['project']['codename']}   invariants v{cfg['version']}")
    out.append("This is the only source of operational fact for this cycle. "
               "(CHARTER.md §6.2)")
    out.append("=" * 78)

    if rep.halted:
        out.append(BANNER_HALT.strip())

    if rep.breaches:
        out.append("\n-- INVARIANT BREACHES " + "-" * 56)
        for b in sorted(rep.breaches, key=lambda x: x.severity != "halt"):
            out.append(f"  [{b.severity.upper():4}] {b.rule}: {b.detail}")
    else:
        out.append("\n-- INVARIANTS: all clear " + "-" * 53)

    groups = {
        "CLOCK": ("deadline_utc", "hours_remaining"),
        "BUDGET": ("spend_total_usd", "spend_llm_usd", "spend_other_usd",
                   "budget_remaining_usd"),
        "RATE COUNTERS": ("outbound_last_hour", "outbound_total",
                          "deploys_last_hour", "cycles_last_24h",
                          "unproductive_cycles_24h"),
        "LIVENESS": ("halt_flag", "hours_since_ledger_write", "hours_since_cycle",
                     "deploy_health", "hours_since_healthy_deploy"),
        "REVENUE": ("arms_length_payments", "arms_length_gross_usd",
                    "related_party_payments"),
        "CODE": ("git_head", "git_branch", "git_uncommitted_files"),
    }
    by_key = {f.key: f for f in rep.facts}
    for title, keys in groups.items():
        present = [by_key[k] for k in keys if k in by_key]
        if not present:
            continue
        out.append(f"\n-- {title} " + "-" * (74 - len(title)))
        for f in present:
            out.append(f"  {f.render()}")

    for title, lines in rep.sections.items():
        out.append(f"\n-- {title} " + "-" * max(3, 74 - len(title)))
        for line in lines:
            out.append(f"  {line}")

    if rep.unknowns:
        out.append("\n" + "!" * 78)
        out.append("UNKNOWN FACTS — you do not know these. Do not infer them, do not")
        out.append("assume the favourable case, do not reuse a value from a past cycle.")
        out.append("An unknown revenue figure is not zero. (CHARTER.md §6.3)")
        out.append("!" * 78)
        for f in rep.unknowns:
            out.append(f"  {f.key}: {f.status} — {f.note or 'no detail'} (source: {f.source})")

    out.append("\n" + "=" * 78)
    out.append("END OF REPORT. Anything not above is not known to you.")
    out.append("=" * 78)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def set_halt(con, reason: str) -> None:
    con.execute(
        "INSERT INTO flags(key, value, updated_at, reason) VALUES('halt','true',?,?) "
        "ON CONFLICT(key) DO UPDATE SET value='true', updated_at=excluded.updated_at, "
        "reason=excluded.reason",
        (utcnow().isoformat(), reason[:500]))
    con.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--init", action="store_true", help="create ledger schema and exit")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--no-enforce", action="store_true",
                    help="report only; do not set the halt flag on breach")
    args = ap.parse_args()

    try:
        cfg = tomllib.loads(CONFIG_PATH.read_text())
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: cannot read {CONFIG_PATH}: {exc}", file=sys.stderr)
        return 20

    ledger = cfg.get("scope", {}).get("ledger_path", "./ledger.db")
    if args.init:
        init_schema(ledger)
        return 0

    rep = Report(now=utcnow())
    try:
        con = connect(ledger)
        con.executescript(SCHEMA)  # idempotent; a missing table must not halt the run
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: ledger unavailable at {ledger}: {exc}", file=sys.stderr)
        return 20

    for fn in (lambda: collect_time(rep, cfg),
               lambda: collect_halt_flag(rep, con),
               lambda: collect_budget(rep, con, cfg),
               lambda: collect_rates(rep, con, cfg),
               lambda: collect_stuck_work(rep, con, cfg),
               lambda: collect_staleness(rep, con, cfg),
               lambda: collect_work(rep, con),
               lambda: collect_revenue(rep, con),
               lambda: collect_deploy(rep, con, cfg),
               lambda: collect_git(rep)):
        result = safe(fn, "collector", "internal")
        if isinstance(result, Fact):          # collector blew up entirely
            rep.add(result)

    # Only record a reason for a *newly* detected breach. Re-deriving it from an
    # already-set flag would append the old reason to itself every cycle.
    if rep.halted and not args.no_enforce and not rep.flag_already_set:
        set_halt(con, "; ".join(f"{b.rule}: {b.detail}" for b in rep.breaches
                                if b.severity == "halt" and b.rule != "halt_flag"))

    text = render(rep, cfg)
    if args.json:
        print(json.dumps({
            "generated_at": rep.now.isoformat(),
            "halted": rep.halted,
            "facts": [f.__dict__ for f in rep.facts],
            "breaches": [b.__dict__ for b in rep.breaches],
            "sections": rep.sections,
            "unknowns": [f.key for f in rep.unknowns],
        }, indent=2, default=str))
    else:
        print(text)

    # Persist for the audit trail. This file is what gets committed to the repo
    # each cycle and is the competition's "agent execution log" evidence.
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = rep.now.strftime("%Y%m%dT%H%M%SZ")
        (REPORT_DIR / f"situation-{stamp}.txt").write_text(text)
        con.execute("INSERT INTO events(at, level, source, message) VALUES(?,?,?,?)",
                    (rep.now.isoformat(), "warn" if rep.breaches else "info",
                     "situation_report",
                     f"halted={rep.halted} breaches={len(rep.breaches)} "
                     f"unknowns={len(rep.unknowns)}"))
        con.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not persist report: {exc}", file=sys.stderr)

    con.close()
    return 10 if rep.halted else 0


if __name__ == "__main__":
    sys.exit(main())
