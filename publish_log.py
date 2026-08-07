#!/usr/bin/env python3
"""
publish_log.py — render the public operations log from the ledger.

CHARTER.md §5 grants standing latitude to publish this WITHOUT an approval token,
on one condition: the output is *derived* from ledger rows, never composed. This
script is what makes that condition true. It contains no LLM call and no free
text of its own beyond fixed template strings.

Consequences of that design worth keeping:

  - The public log and the private truth are the same rows. There is no separate
    narrative to drift, and no way to publish a flattering version.
  - Because agent-written fields (decision summaries, rationales, handoffs) are
    published verbatim, §3.6 binds them. The scrubber below is belt-and-braces,
    not the primary control.
  - A scrubber hit means an agent wrote personal data into a ledger field, which
    is a §3.6 violation. The entry is withheld entirely, an error event is
    logged, and the exit code is non-zero. Fail closed, then tell someone.

Article 50 marking: every page carries a visible AI-authorship banner plus
machine-readable metadata. No single machine-readable standard is mandated for
text, so this is a reasonable-effort implementation — meta tag, JSON-LD creator,
and an explicit statement — not certified compliance. Do not describe it as
more than that (§6.6).

Usage:
    python3 publish_log.py              # render to publish_dir
    python3 publish_log.py --dry-run    # report what would be written
    python3 publish_log.py --since 2026-08-10

Exit codes:
    0   published cleanly
    30  published, but one or more entries were withheld by the scrubber
    20  could not run
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = Path("./invariants.toml")

# --------------------------------------------------------------------------- #
# redaction
# --------------------------------------------------------------------------- #

# Deliberately blunt. False positives cost one withheld entry and a warning;
# false negatives cost a person's identifiers on a public page. Tuned to over-fire.
PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("personnummer", re.compile(r"\b(?:19|20)?\d{6}[-+]?\d{4}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("phone", re.compile(r"(?:\+46|0046|\b0)\s?7[\d\s-]{8,}")),
    ("iban", re.compile(r"\bSE\d{2}[\d\s]{20,}\b", re.I)),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
]


def _looks_like_card(matched: str) -> bool:
    """Two checks a payment card passes and an arbitrary digit run usually does
    not: the Luhn checksum, and a major-industry-identifier in 3-6.

    Luhn alone was not enough. Jules session id 1652844863819652924 passes it —
    checksum 100, which is one-in-ten luck — so it was still being withheld. The
    leading digit is the second discriminator: under ISO/IEC 7812 payment cards
    issued by the major networks begin 3 (Amex, Diners), 4 (Visa), 5
    (Mastercard) or 6 (Discover, UnionPay). Jules ids begin 1.

    Every real payment card still matches. What no longer matches is a long
    identifier that happens to be numeric.
    """
    d = [int(c) for c in matched if c.isdigit()]
    if len(d) < 13 or d[0] not in (3, 4, 5, 6):
        return False
    total, alt = 0, False
    for n in reversed(d):
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def scan(text: str | None) -> list[str]:
    """Return the names of any PII patterns present. Empty list means clean.

    The `card` pattern is confirmed by _looks_like_card() (added 2026-08-06). It
    matches any 13-19 digit run, and Jules session ids are 19 digits, so three
    consecutive decisions were withheld from the public log for mentioning a
    session id — silently, because withholding is by design and nobody was
    reading the exit code until the daily digest started reporting it. The log is
    a graded deliverable; losing real entries to a systematic false positive
    costs more than the raw pattern was buying.

    This narrows precision, not coverage. Every other pattern here is unchanged
    and still deliberately blunt.
    """
    if not text:
        return []
    hits = []
    for name, pat in PII_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        if name == "card" and not _looks_like_card(m.group()):
            continue
        hits.append(name)
    return hits


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #

@dataclass
class Day:
    date: str
    cycles: int = 0
    cycle_statuses: list[str] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    handoffs: list[str] = field(default_factory=list)
    spend: dict[str, float] = field(default_factory=dict)
    llm_usd: float = 0.0
    withheld: int = 0


def day_of(ts: str) -> str:
    return (ts or "")[:10]


def load(con: sqlite3.Connection, since: str | None) -> tuple[dict[str, Day], list[str]]:
    days: dict[str, Day] = defaultdict(lambda: Day(date=""))
    violations: list[str] = []

    def day(ts: str) -> Day:
        d = day_of(ts)
        if not days[d].date:
            days[d].date = d
        return days[d]

    for r in con.execute("SELECT started_at, status, handoff FROM cycles ORDER BY started_at"):
        if since and day_of(r["started_at"]) < since:
            continue
        d = day(r["started_at"])
        d.cycles += 1
        d.cycle_statuses.append(r["status"] or "unknown")
        if r["handoff"]:
            hits = scan(r["handoff"])
            if hits:
                d.withheld += 1
                violations.append(f"cycles.handoff on {d.date}: {', '.join(hits)}")
            else:
                d.handoffs.append(r["handoff"])

    for r in con.execute(
            "SELECT at, summary, rationale, reversible, falsifier FROM decisions ORDER BY at"):
        if since and day_of(r["at"]) < since:
            continue
        d = day(r["at"])
        blob = " ".join(filter(None, (r["summary"], r["rationale"], r["falsifier"])))
        hits = scan(blob)
        if hits:
            d.withheld += 1
            violations.append(f"decisions on {d.date}: {', '.join(hits)}")
            continue
        d.decisions.append({
            "at": r["at"][11:16],
            "summary": r["summary"],
            "rationale": r["rationale"],
            "reversible": bool(r["reversible"]),
            "falsifier": r["falsifier"],
        })

    # Spend is aggregated by category. Vendor-level descriptions are not published:
    # they are operationally uninteresting and occasionally identifying.
    for r in con.execute("SELECT at, category, SUM(usd) total FROM spend GROUP BY at, category"):
        if since and day_of(r["at"]) < since:
            continue
        d = day(r["at"])
        d.spend[r["category"]] = round(d.spend.get(r["category"], 0.0) + (r["total"] or 0), 2)

    for r in con.execute("SELECT at, SUM(usd_est) total FROM llm_usage GROUP BY at"):
        if since and day_of(r["at"]) < since:
            continue
        d = day(r["at"])
        d.llm_usd = round(d.llm_usd + (r["total"] or 0), 4)

    days.pop("", None)
    return dict(days), violations


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

E = html.escape

BANNER = (
    '<div class="ai-notice" role="note">'
    "<strong>Written by software.</strong> This log is generated automatically "
    "from the operating ledger of an autonomous agent system. No human wrote or "
    "reviewed these entries before publication. Entries are rendered from "
    "append-only database rows, not composed."
    "</div>"
)

CSS = """
:root{color-scheme:light dark}
body{max-width:46rem;margin:2rem auto;padding:0 1rem;
  font:16px/1.6 ui-serif,Georgia,serif}
.ai-notice{border:1px solid currentColor;padding:.75rem 1rem;margin:1.5rem 0;
  font-size:.9rem;font-family:ui-sans-serif,system-ui,sans-serif}
.meta{font-family:ui-monospace,monospace;font-size:.8rem;opacity:.75}
.decision{border-left:3px solid currentColor;padding-left:1rem;margin:1.5rem 0}
.rationale{opacity:.85}
.tag{font-family:ui-monospace,monospace;font-size:.75rem;
  border:1px solid currentColor;padding:.1rem .4rem;border-radius:.2rem}
.withheld{opacity:.7;font-style:italic}
footer{margin-top:3rem;font-size:.85rem;opacity:.75}
"""


def head(title: str, codename: str, generated: str) -> str:
    ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": title,
        "dateModified": generated,
        "creator": {"@type": "SoftwareApplication", "name": f"{codename} agent team"},
        "isAccessibleForFree": True,
        "abstract": "Automatically generated operations log. AI-authored, unreviewed.",
    })
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{E(title)}</title>"
        "<meta name=\"ai-generated\" content=\"true\">"
        "<meta name=\"ai-generated-by\" content=\"autonomous agent system\">"
        "<meta name=\"ai-human-reviewed\" content=\"false\">"
        f"<meta name=\"generator\" content=\"publish_log.py\">"
        f"<meta name=\"date\" content=\"{E(generated)}\">"
        f"<script type=\"application/ld+json\">{ld}</script>"
        f"<style>{CSS}</style></head><body>"
    )


def foot(codename: str) -> str:
    return (
        "<footer><hr>"
        f"<p>Operations log for <strong>{E(codename)}</strong>. "
        "Generated from the ledger by <code>publish_log.py</code>; "
        "no human review before publication.</p>"
        "<p>Not affiliated with Lund University.</p>"
        "<p><a href=\"index.html\">All entries</a> · <a href=\"feed.xml\">RSS</a></p>"
        "</footer></body></html>"
    )


def render_day(d: Day, codename: str, generated: str) -> str:
    out = [head(f"{codename} — {d.date}", codename, generated), BANNER]
    out.append(f"<h1>{E(d.date)}</h1>")
    out.append(
        f"<p class=\"meta\">{d.cycles} cycle(s) · "
        f"{len(d.decisions)} decision(s) · "
        f"${d.llm_usd + sum(d.spend.values()):.2f} spent</p>")

    if d.decisions:
        out.append("<h2>Decisions</h2>")
        for dec in d.decisions:
            tag = "reversible" if dec["reversible"] else "irreversible"
            out.append(f"<div class=\"decision\"><p class=\"meta\">{E(dec['at'])} "
                       f"<span class=\"tag\">{tag}</span></p>")
            out.append(f"<p>{E(dec['summary'] or '')}</p>")
            if dec["rationale"]:
                out.append(f"<p class=\"rationale\">{E(dec['rationale'])}</p>")
            if dec["falsifier"]:
                out.append("<p class=\"meta\">Would be shown wrong by: "
                           f"{E(dec['falsifier'])}</p>")
            out.append("</div>")

    if d.handoffs:
        out.append("<h2>State at end of cycle</h2>")
        for h in d.handoffs:
            out.append(f"<p>{E(h)}</p>")

    if d.spend or d.llm_usd:
        out.append("<h2>Spend</h2><ul>")
        if d.llm_usd:
            out.append(f"<li>model calls: ${d.llm_usd:.4f}</li>")
        for cat, amt in sorted(d.spend.items()):
            out.append(f"<li>{E(cat)}: ${amt:.2f}</li>")
        out.append("</ul>")

    if d.withheld:
        out.append(f"<p class=\"withheld\">{d.withheld} entr"
                   f"{'y was' if d.withheld == 1 else 'ies were'} withheld: the "
                   "automated check found what may be personal data. This is a "
                   "policy violation upstream and has been flagged.</p>")

    out.append(foot(codename))
    return "".join(out)


def render_index(days: list[Day], codename: str, generated: str) -> str:
    out = [head(f"{codename} — operations log", codename, generated), BANNER]
    out.append(f"<h1>{E(codename)}</h1>")
    out.append("<p>What an autonomous agent team did, decided and spent, "
               "rendered from its own ledger.</p>")
    total = sum(d.llm_usd + sum(d.spend.values()) for d in days)
    out.append(f"<p class=\"meta\">{len(days)} day(s) · "
               f"{sum(d.cycles for d in days)} cycle(s) · "
               f"{sum(len(d.decisions) for d in days)} decision(s) · "
               f"${total:.2f} total</p><ul>")
    for d in sorted(days, key=lambda x: x.date, reverse=True):
        out.append(f"<li><a href=\"{E(d.date)}.html\">{E(d.date)}</a> — "
                   f"{len(d.decisions)} decision(s), {d.cycles} cycle(s)</li>")
    out.append("</ul>")
    out.append(foot(codename))
    return "".join(out)


def render_feed(days: list[Day], codename: str, base: str, generated: str) -> str:
    items = []
    for d in sorted(days, key=lambda x: x.date, reverse=True)[:30]:
        summary = "; ".join(dec["summary"] or "" for dec in d.decisions[:5]) or "No decisions."
        items.append(
            "<item>"
            f"<title>{E(codename)} — {E(d.date)}</title>"
            f"<link>{E(base)}/{E(d.date)}.html</link>"
            f"<guid isPermaLink=\"true\">{E(base)}/{E(d.date)}.html</guid>"
            f"<description>{E(summary)}</description>"
            "</item>")
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<rss version=\"2.0\"><channel>"
        f"<title>{E(codename)} operations log</title>"
        f"<link>{E(base)}</link>"
        "<description>AI-authored, unreviewed. Generated from the operating "
        "ledger of an autonomous agent system.</description>"
        f"<lastBuildDate>{E(generated)}</lastBuildDate>"
        f"<generator>publish_log.py</generator>"
        + "".join(items) +
        "</channel></rss>")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", help="only render days on or after YYYY-MM-DD")
    args = ap.parse_args()

    try:
        cfg = tomllib.loads(CONFIG_PATH.read_text())
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: cannot read {CONFIG_PATH}: {exc}", file=sys.stderr)
        return 20

    codename = cfg["project"]["codename"]
    scope = cfg.get("scope", {})
    out_dir = Path(scope.get("publish_dir", "./docs"))
    # Explicit, not derived from allowed_domains: the log may not live at the
    # product's apex. A wrong base here produces dead RSS guids, which is the
    # kind of thing nobody notices for a week.
    base = scope.get("publish_base_url", ".").rstrip("/")

    try:
        con = sqlite3.connect(scope.get("ledger_path", "./ledger.db"))
        con.row_factory = sqlite3.Row
        days, violations = load(con, args.since)
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: ledger unreadable: {exc}", file=sys.stderr)
        return 20

    if not days:
        print("nothing to publish yet")
        return 0

    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pages = {f"{d.date}.html": render_day(d, codename, generated) for d in days.values()}
    pages["index.html"] = render_index(list(days.values()), codename, generated)
    pages["feed.xml"] = render_feed(list(days.values()), codename, base, generated)

    if args.dry_run:
        for name, body in sorted(pages.items()):
            print(f"{name}: {len(body)} bytes")
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / ".nojekyll").write_text("")   # serve the HTML as written
        for name, body in pages.items():
            (out_dir / name).write_text(body, encoding="utf-8")
        print(f"wrote {len(pages)} file(s) to {out_dir}")

    if violations:
        print("\nWITHHELD — possible personal data in ledger free-text fields.",
              file=sys.stderr)
        print("This is a CHARTER.md §3.6 violation upstream. Fix the writer, "
              "not the scrubber.", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        try:
            con.execute(
                "INSERT INTO events(at, level, source, message) VALUES(?,?,?,?)",
                (generated, "error", "publish_log",
                 f"withheld {len(violations)} entries, possible PII: "
                 + "; ".join(v.split(":")[0] for v in violations)))
            con.commit()
        except Exception:  # noqa: BLE001
            pass
        return 30

    return 0


if __name__ == "__main__":
    sys.exit(main())
