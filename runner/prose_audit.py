"""Check agent-written prose against the ledger before it lands.

Everything in this system is instrumented for ACTIONS — idempotency keys, action
rows, statuses, gates. Nothing was instrumented for CLAIMS, and on 2026-08-09
that gap put fabricated financials into `main`.

What happened: Jules was commissioned to draft the competition submission. It
produced `docs/submission.md`, which is largely accurate — the guides it
describes exist, the guardrails it describes are implemented — and which also
states that the domain was registered "via Swedish registrar Hostup" for 100 SEK,
that the lab box consumed roughly 30 SEK of electricity, that total out-of-pocket
spend was about $15, that the product runs on `gemini-2.5-flash`, and that
revenue for May, June and July 2026 was zero because the project was in "local
infrastructure setup and operational charter design phase". None of those figures
exists in the ledger. The registrar and the electricity were invented outright.
The model was retired before the product shipped. The project began on 5 August,
so three of those four months precede it entirely.

The session landed as `JULES_LAND -> ok`, which is true and which is all the
ledger could say. Coral reviewed it, merged it, and logged a decision marking the
objective complete. Every mechanism worked as specified. A completed task is not
something the design knew how to doubt.

So this module doubts it. It is deterministic and has no model in it, for the
same reason `situation_report.py` has none: a checker that can be talked round is
not a checker.

**What it can and cannot do.** It verifies claims of a kind the ledger can
settle — money, model names, dates, hosts. It cannot judge prose, tone, or
whether an argument is sound, and it does not try. A document that passes has not
been proved honest; it has been proved free of the four fabrications that are
mechanically detectable. Absence of findings is not a certificate.

Usage:
    python3 -m runner.prose_audit docs/submission.md
    python3 -m runner.prose_audit --json docs/submission.md
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import tomllib
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# Fenced blocks are examples and commands, not claims about this business, so
# they are stripped.
#
# Inline spans are NOT stripped, and the first version of this file got that
# wrong. Backticks are exactly how markdown writes a model id or a hostname in
# running prose — `gemini-2.5-flash`, `https://undra.nu` — so stripping them
# blinded the checker to two of the four fabrications it was written to catch.
# It reported ten findings on the submission and confidently missed both.
_FENCE = re.compile(r"```.*?```", re.S)

# An explicit, visible marker for figures a document quotes in order to disclose
# them as false. See _split_disclosed: every use is counted and reported.
_DISCLOSED = re.compile(
    r"<!--\s*audit:disclosed\s*-->.*?<!--\s*/audit:disclosed\s*-->", re.S | re.I)

# A trailing comma must not be swallowed as a thousands separator: "invoice
# 202680231, kr 79.00" otherwise reads as a claim of 202,680,231 kr. And a
# currency followed by "=" is an exchange rate, not an amount spent — "1 SEK =
# 0.10538 USD" is how an honest conversion states its source.
_MONEY = re.compile(r"""
    (?:
        (?P<usd>\$\s?(?P<usd_n>\d[\d,]*\d|\d)(?P<usd_d>\.\d+)?)
      | (?P<sek>(?P<sek_n>\d[\d,]*\d|\d)(?P<sek_d>\.\d+)?\s?(?:SEK|kr\b)(?!\s*=))
    )""", re.X | re.I)

_MODEL = re.compile(r"\bgemini-[a-z0-9.\-]+", re.I)
# Prose spells them out: "the Gemini 2.5 Flash model". Same claim, different
# shape, and the shape a marketing document reaches for first.
_MODEL_PROSE = re.compile(
    r"\bGemini[\s\-]+(\d+\.\d+)[\s\-]+(Flash[\s\-]Lite|Flash|Pro|Ultra|Nano)\b", re.I)
_URL = re.compile(r"https?://([A-Za-z0-9.\-]+)")
_MONTH = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(20\d{2})\b", re.I)

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}

# Round numbers that are almost never a claim about spend: version strings,
# percentages already matched elsewhere, and zero.
_MONEY_IGNORE = {"0", "0.00", "0.0"}

# Phrases that answer "was there nothing here, or did nobody measure?" — the
# question _check_months exists to force. Matched near the month, not anywhere.
# NOT "$0.00" or "zero". The fabrication this check exists to catch reported
# July revenue as $0.00 — which was true — and then narrated a "local
# infrastructure setup and operational charter design phase" for a month in
# which the project did not exist. The zero was never the lie. Only an explicit
# statement about existence answers the question.
_MONTH_DISCLAIMED = (
    "did not exist", "didn't exist", "did not yet exist", "no business",
    "before the project", "predates", "precedes the project",
    "no work of any kind", "nothing was recorded", "nothing existed",
    "predate", "predating",
)


@dataclass
class Finding:
    severity: str        # "error" | "warn"
    kind: str            # money | model | month | host
    claim: str           # the exact text found
    detail: str          # why it is a problem, and what the ledger says

    def render(self) -> str:
        return f"[{self.severity.upper():5}] {self.kind}: {self.claim} — {self.detail}"


# --------------------------------------------------------------------------- #
# what the ledger actually knows
# --------------------------------------------------------------------------- #

@dataclass
class Ground:
    """The verifiable facts, read from the ledger and invariants. No estimates
    beyond the one the ledger itself labels as an estimate."""
    spend_usd: float
    llm_usd: float
    line_items: set[float]     # each recorded cost, so itemised tables work
    llm_by_key: set[float]     # model spend split by the key that served it
    caps: set[float]           # budget ceilings are legitimate to quote
    models: set[str]
    configured_models: set[str]
    first_activity: datetime | None
    hosts: set[str]          # where something is actually served
    owned: set[str]          # domains we control, which is not the same thing
    revenue_usd: float

    @classmethod
    def read(cls, con: sqlite3.Connection, cfg: dict) -> "Ground":
        def q1(sql: str, default=0):
            try:
                v = con.execute(sql).fetchone()[0]
            except sqlite3.Error:
                return default
            return default if v is None else v

        models = set()
        try:
            models = {r[0] for r in con.execute(
                "SELECT DISTINCT model FROM llm_usage WHERE model IS NOT NULL")}
        except sqlite3.Error:
            pass

        first = None
        raw = q1("SELECT MIN(started_at) FROM cycles", None)
        if raw:
            try:
                first = datetime.fromisoformat(raw)
            except ValueError:
                first = None

        # "We own this domain" and "the product is served here" are different
        # facts, and conflating them is what let a document put the product at
        # https://undra.nu — a domain we do own, on which nothing serves it.
        scope = cfg.get("scope", {}) or {}
        serving = set(scope.get("allowed_hosts") or [])
        base = scope.get("publish_base_url") or ""
        m = _URL.match(base)
        if m:
            serving.add(m.group(1))
        owned = set(scope.get("allowed_domains") or [])

        cfgmodels = {str(v) for k, v in (cfg.get("models") or {}).items()
                     if isinstance(v, str) and v.startswith("gemini")}

        llm = float(q1("SELECT COALESCE(SUM(usd_est),0) FROM llm_usage"))
        other = float(q1("SELECT COALESCE(SUM(usd),0) FROM spend"))

        # Individual rows, not only the total. A cost breakdown is the normal
        # shape of a financial section, and an auditor that accepts only the
        # grand total forces the writer to choose between an itemised table and
        # a checkable one.
        items = set()
        try:
            items = {round(float(r[0]), 2) for r in
                     con.execute("SELECT usd FROM spend")}
        except sqlite3.Error:
            pass

        # Model spend split by the key that served each call. Quoting the split
        # is how a document explains why an estimate and an invoice differ —
        # the free key's calls are metered and never billed — and refusing it
        # forces the writer back to a single total that hides the distinction.
        # Ledger-derived like every other figure here.
        by_key = set()
        try:
            by_key = {round(float(r[0]), 2) for r in con.execute(
                "SELECT SUM(usd_est) FROM llm_usage GROUP BY "
                "COALESCE(NULLIF(key_role,''),'?')") if r[0]}
        except sqlite3.Error:
            pass

        caps = {float(v) for k, v in (cfg.get("budget") or {}).items()
                if isinstance(v, (int, float)) and str(k).startswith(("cap", "hard"))}

        return cls(
            spend_usd=llm + other,
            llm_usd=llm,
            line_items=items,
            llm_by_key=by_key,
            caps=caps,
            models=models,
            configured_models=cfgmodels,
            first_activity=first,
            hosts=serving,
            owned=owned,
            revenue_usd=float(q1("SELECT COALESCE(SUM(gross_usd),0) FROM payments")),
        )


def _strip_code(text: str) -> str:
    return _FENCE.sub(" ", text)


def _split_disclosed(text: str) -> tuple[str, int]:
    """Separate text a document explicitly marks as quoting a known-false figure.

    A document that discloses its own past fabrication has to restate the false
    numbers to disclose them. This checker cannot tell "the domain cost $10.00"
    from "we wrongly claimed the domain cost $10.00", and refusing both makes an
    honest retraction impossible to write — which is how a gate against
    fabrication ends up suppressing the correction of one.

    The fence is deliberately ugly and deliberately counted. Its use is reported
    every time, so suppression is visible in the output rather than silent, and
    a reviewer can see exactly how much text was exempted and go read it.

        <!-- audit:disclosed --> ...known-false figures... <!-- /audit:disclosed -->
    """
    n = len(_DISCLOSED.findall(text))
    return _DISCLOSED.sub(" ", text), n


def _subset_sums(items: set[float], limit: int = 14) -> set[float]:
    """Every total reachable by adding recorded line items together.

    A cost breakdown that lists four invoices and then totals them is the normal
    shape of a financial section, and a checker that accepts each row but not
    their sum forces the writer to drop either the total or the itemisation.
    Every value here is derived from rows that already carry evidence, so this
    widens what is quotable without admitting anything unrecorded.

    Matched to the cent rather than the 2% used for single rows: a sum of exact
    figures is exact, and loose tolerance over 2^n combinations would start
    accepting arbitrary numbers.
    """
    vals = sorted(items)[:limit]
    sums = {0.0}
    for v in vals:
        sums |= {round(s + v, 2) for s in sums}
    sums.discard(0.0)
    return sums


def _num(s: str) -> float:
    return float(s.replace(",", ""))


# --------------------------------------------------------------------------- #
# the checks
# --------------------------------------------------------------------------- #

def _check_money(text: str, g: Ground, sek_per_usd: float) -> Iterable[Finding]:
    """Every currency figure must be derivable from the ledger.

    Deliberately strict, and deliberately not clever about tolerance. The
    submission's invented figures were $10 for a domain and $3 for electricity —
    both plausible, neither recorded. There is no threshold that admits a
    plausible invention and rejects an implausible one, so the test is
    provenance, not plausibility: if no row supports it, it is unsourced.
    """
    # Caps are deliberately NOT in here.
    #
    # An earlier version accepted any figure equal to a configured budget
    # ceiling, on the grounds that quoting the ceiling is legitimate. It is —
    # but cap_marketing is $10.00, and the fabrication this module was written
    # to catch was "$10.00 USD" for a domain registration. Treating a config
    # value as evidence of expenditure opens a hole at exactly the round numbers
    # invented figures favour. Caps get a warning of their own below instead.
    known = ({round(g.spend_usd, 2), round(g.llm_usd, 2),
              round(g.revenue_usd, 2), 0.0} | g.line_items | g.llm_by_key)
    # Sums of recorded rows, matched exactly. Kept separate from `known` because
    # `known` carries a 2% tolerance that must not be applied to combinations.
    sums = _subset_sums(g.line_items)
    for m in _MONEY.finditer(text):
        raw = m.group(0).strip()
        if m.group("usd_n"):
            n = _num(m.group("usd_n") + (m.group("usd_d") or ""))
        else:
            n = _num(m.group("sek_n") + (m.group("sek_d") or "")) / sek_per_usd
        if raw.lstrip("$ ").rstrip() in _MONEY_IGNORE or n == 0:
            continue
        if any(abs(n - k) <= max(0.01, k * 0.02) for k in known):
            continue
        if any(abs(n - t) <= 0.01 for t in sums):
            continue
        if any(abs(n - c) <= 0.01 for c in g.caps):
            yield Finding(
                "warn", "money", raw,
                f"this equals a configured budget ceiling in invariants.toml, "
                f"not a recorded cost. Quoting the cap is fine — say it is a "
                f"cap. Quoting it as money spent is not: nothing was spent "
                f"here. Recorded expenditure is ${g.spend_usd:.2f}.")
            continue
        yield Finding(
            "error", "money", raw,
            f"no ledger row supports this. Recorded: API usage "
            f"${g.llm_usd:.2f} (estimated from transcribed rates), other costs "
            f"{('$%.2f' % sum(g.line_items)) if g.line_items else 'none recorded'}"
            f", combined ${g.spend_usd:.2f}, revenue ${g.revenue_usd:.2f}. "
            f"If the cost was real, record it with its evidence — "
            f"`bin/record-cost` — and it becomes quotable. If it was not, "
            f"remove the claim.")


def _check_models(text: str, g: Ground) -> Iterable[Finding]:
    known = {m.lower() for m in (g.models | g.configured_models)}

    def spelled(m):
        """"Gemini 2.5 Flash" -> "gemini-2.5-flash"."""
        return f"gemini-{m.group(1)}-{re.sub(r'[\s-]+', '-', m.group(2)).lower()}"

    seen = set()
    for m in list(_MODEL.finditer(text)) + list(_MODEL_PROSE.finditer(text)):
        name = (m.group(0).lower().rstrip(".,;:") if m.re is _MODEL
                else spelled(m))
        if name in known or name in seen:
            continue
        seen.add(name)
        yield Finding(
            "error", "model", m.group(0),
            f"never called by this project and not in invariants.toml [models]. "
            f"Models actually used: {', '.join(sorted(g.models)) or 'none yet'}. "
            f"Naming a model the product does not run is the same class of error "
            f"as the retired gemini-2.5-flash outage on 2026-08-07.")


def _check_months(text: str, g: Ground) -> Iterable[Finding]:
    if not g.first_activity:
        return
    start = g.first_activity
    for m in _MONTH.finditer(text):
        month, year = _MONTHS[m.group(1).lower()], int(m.group(2))
        if (year, month) >= (start.year, start.month):
            continue
        # The finding's own demand is "say which, rather than narrating
        # activity". A document that answers it — stating a zero, or that the
        # project did not exist — has complied, and refusing it anyway would
        # make the required disclosure impossible to write. The fabrication this
        # catches read "July: local infrastructure setup and operational charter
        # design phase": activity, no zero, no denial.
        window = text[max(0, m.start() - 300):m.end() + 300].lower()
        if any(p in window for p in _MONTH_DISCLAIMED):
            continue
        yield Finding(
            "error", "month", m.group(0),
            f"precedes the project. The first ledger row is "
            f"{start.date().isoformat()}, so there is nothing recorded for this "
            f"month. A month with no rows because nothing existed is a different "
            f"statement from a month with no rows because nothing was measured — "
            f"say which, rather than narrating activity.")


def _check_hosts(text: str, g: Ground) -> Iterable[Finding]:
    for m in _URL.finditer(text):
        host = m.group(1).lower().rstrip(".")
        if host in g.hosts:
            continue

        # Owned, but nothing serves the product here. Weaker than an invented
        # host and still worth saying: this is the exact shape of the claim that
        # sent the agent to a 404 and produced a false outage report.
        if any(host == d or host.endswith("." + d) for d in g.owned):
            yield Finding(
                "warn", "host", m.group(0),
                f"this domain is ours, but nothing serves the product on it. "
                f"The application is at "
                f"{', '.join(sorted(g.hosts)) or '(no host configured)'}. "
                f"Owning a domain and serving from it are different claims.")
            continue
        # Third-party references are normal in prose; only flag hosts that look
        # like a claim about *our* deployment.
        if not re.search(r"\b(undra|run\.app)\b", host):
            continue
        yield Finding(
            "error", "host", m.group(0),
            f"not a host this project serves. Configured: "
            f"{', '.join(sorted(g.hosts))}. On 2026-08-09 a document naming "
            f"undra.nu as the product URL sent the agent to a 404 and produced a "
            f"false outage report.")


def audit(text: str, con: sqlite3.Connection, cfg: dict) -> list[Finding]:
    g = Ground.read(con, cfg)
    sek = float((cfg.get("budget") or {}).get("sek_per_usd", 10.0))
    body, disclosed = _split_disclosed(_strip_code(text))
    out: list[Finding] = []
    if disclosed:
        out.append(Finding(
            "warn", "disclosed", f"{disclosed} region(s)",
            "figures inside an `audit:disclosed` fence were not checked. That "
            "fence is for quoting a known-false number in order to retract it. "
            "Read those passages: nothing else in this report covers them."))
    for check in (lambda: _check_money(body, g, sek),
                  lambda: _check_models(body, g),
                  lambda: _check_months(body, g),
                  lambda: _check_hosts(body, g)):
        out.extend(check())
    # Stable, de-duplicated: the same invented figure repeated eight times is one
    # problem, and a findings list longer than the document helps nobody.
    seen, uniq = set(), []
    for f in out:
        k = (f.kind, f.claim)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(f)
    return uniq


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def _load(path: str = "./invariants.toml") -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except OSError:
        return {}


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    as_json = "--json" in argv
    if not args:
        print(__doc__, file=sys.stderr)
        return 2

    cfg = _load()
    db = (cfg.get("scope") or {}).get("ledger_path", "./ledger.db")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    rc = 0
    for path in args:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        found = audit(text, con, cfg)
        if as_json:
            print(json.dumps({"file": path,
                              "findings": [asdict(f) for f in found]}, indent=2))
        else:
            print(f"\n=== {path} — {len(found)} finding(s) ===")
            for f in found:
                print("  " + f.render())
            if not found:
                print("  no mechanically detectable fabrications. This is not a "
                      "certificate of honesty — see the module docstring.")
        if errors(found):
            rc = 1
    con.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
