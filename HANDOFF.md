# XPRIZE Agent Experiment — Handoff Notes

**Written** 2026-08-03 · **Updated** 2026-08-05 · **Deadline** 2026-08-17T20:00:00Z (13:00 PDT) · **~12 days**

Context doc for resuming this work in a fresh session. Self-contained: this plus
`CHARTER.md`, `invariants.toml`, `situation_report.py`, `publish_log.py` and
`LAB_SETUP.md` is everything.

**Current state:** named **Undra**, domain **undra.nu** registered. Lab box
(hostname `red`) installed and reachable at 192.168.1.240; network isolation not
yet applied. No agent has run. Nothing deployed.

---

## 1. What this is

Entry in the **Build with Gemini XPRIZE** (xprize.devpost.com). Joined early, no
work done. The experiment: hand the entire project — build, deploy, operate,
market — to an agent team, with a human escape hatch for anything that can't be
automated. Inspired by Andon Labs' work on long-horizon agent operation.

**Explicitly not** expecting to win. The interesting output is how far an agent
team gets and where it breaks.

**The business:** Undra — a mobile-first assistant explaining unfamiliar everyday
Swedish systems to newcomers in Lund (waste sorting, pant machines, laundry
booking, food labels, signage, transport), which *refuses* to advise on
immigration, tax, legal or medical matters and routes those to the responsible
authority. Category: education. Full statement in `CHARTER.md` §1.

**Timing, which shapes everything:** Arrival Day is 18 August — the day *after*
the deadline. ~4,000 international students land then; Orientation Weeks run to
30 August. So the only audience reachable before submission is pre-arrival, and
the photo-explainer has almost no users until the day after this ends. Do not
read that as product failure.

---

## 2. What the competition actually judges

Not a code hackathon. It's "launch a real business in 90 days." Three criteria,
equally weighted:

1. **Business viability** — real revenue in the window, from arms-length parties
2. **AI-native operations** — AI live in production executing key decisions
3. **Category impact**

Submission requires: revenue broken out by May/June/July/August, expenses,
marketing spend (even if zero), user evidence with testimonials, related-party
revenue disclosed *separately*, 3-minute video, 500–1000 word narrative on human
vs. AI tasks, and **evidence the product runs continuously** — agent execution
logs, API usage records, dashboards.

**Where this leaves us:** structurally out on criterion 1 (three of four revenue
months are zero) and criterion 3. Criterion 2 is precisely the experiment — a
business whose operating agents also built it is the maximal reading of it.

**Consequence that shapes the whole design:** the audit trail is a *graded
deliverable*, not overhead. It's simultaneously the supervision surface, the
debugging tool, the submission evidence, and the research output. Build it first.

Other rules worth holding:

- **New projects only** — must be created after 2026-05-19. Pre-existing
  templates or code must be explained. Don't reuse anything already in flight.
- **At least one Google Cloud product.**
- **At least one Gemini API call in the *deployed application*** — the ops layer
  does not satisfy this. The product needs its own call.
- Repo public with a license, or private and shared with `testing@devpost.com`
  and `judging@hacker.fund`.
- Possible live demo call within 2 business days of email — so monitor mail, and
  understand what got built well enough to demo it.
- Verification covers the entrant's **role in creation**. Honest framing:
  designed the system, wrote the charter, operate it, approve gated actions.

*(Re-verify against the rules page before submitting; the above is a summary.)*

---

## 3. Decisions already made

| Decision | Rationale |
|---|---|
| **Spare home box, not a Hetzner VPS** | Reversed 2026-08-05. The €2 VPS was right for a single throwaway experiment; the box is becoming a permanent lab for successive agent projects, which changes the calculus. Costs ~15 min of firewall work and carries home-uptime risk. | 
| **Ephemeral/stateless agent cycles**, ledger as sole truth | Forces re-derivation of state from an authoritative source each cycle. This is the fix for long-horizon drift — bigger context is not. |
| **SQLite ledger, WAL mode** | Zero services, single file, trivially committable to git. Swap to Postgres/Supabase is a DSN change if ever needed. |
| **3–4 cycles/day**, not every 30 min | The single biggest cost lever. Original estimate of $100–400 was mostly this one bad default. Event-driven wakeups for anything urgent. |
| **Free tier for the ops loop, paid key for user data** | Free-tier prompts may be used for training and we're the data controller. |
| **No merchant-of-record; tip jar if anything** | Superseded 2026-08-05. Users are broke students who arrive *after* the deadline, so in-window revenue is ~€0 regardless. MoR KYC was the longest-lead item and buys nothing here. Ko-fi or Buy Me a Coffee still counts as arms-length if anyone bites. |
| **All outbound-to-strangers human-gated** | GDPR controller liability sits with the human, not the agent. Automated cold outreach also breaks most platform ToS. |
| **`situation_report.py` doubles as the watchdog** | One component instead of two. Fail-closed: it sets the halt flag itself. |

Dead ends already checked: the "$300 Google Cloud credit" in the hackathon
resources is just the generic new-customer free trial, not an allocation — and
the $100 AI Ultra bonus expired 2026-05-25. The hardship coupon pool is
effectively closed. **Plan for zero credits.**

---

## 4. Cost model

Free tier (verify current numbers — they move):

- **Pro models are paid-only.** Free tier is Flash and Flash-Lite.
- Flash / Flash-Lite ≈ 15–30 RPM, ~1,500 requests/day.
- **RPM bites before RPD.** Serialise the tool loop, use exponential backoff.
- Jules free tier: 15 tasks/day (≈210 over the fortnight). Skip the $19.99 tier.
  Hitting the ceiling means the build loop is thrashing, not that we need to pay.

At 3–4 cycles/day × ~30 calls/cycle ≈ 120 requests/day, **the entire operator
loop fits inside the free tier.** Paid spend is then one Pro planning call/day
(~$0.10 each, ~$1.50 total) plus the deployed app's Gemini call.

Caps in `invariants.toml`: **$60 total, $25 LLM, $5 per action.** The Gemini API
has no hard billing cap, so the `llm_usage` table *is* the cap — the watchdog
halts on it.

Realistic total: single-digit dollars, plus the domain (~100 SEK, paid) and the
lab box's electricity (~30 SEK for the fortnight).

**Update 2026-08-06: the $300 Google Cloud trial credit did land** on
`coral.at.red@gmail.com`, despite this doc assuming zero. It covers Cloud Run,
Functions, Secret Manager and egress — i.e. the whole infra side, previously
capped at $20. It does **not** cover the Gemini Developer API, which is excluded
from trial credits and billed against Prepay separately. The two pots stay
distinct: infra on credit, Coral's model spend on Prepay.

**Two GCP projects, not one** (recorded 2026-08-06):

| Project | id | Billing | Key lives in | Serves |
|---|---|---|---|---|
| undra-free | *(free tier)* | none | `env/ops.env` | Coral's operator loop |
| undra | **`undra-504613`** | `My Billing Account` `01E0FA-16AE45-963492`, which holds the trial credit | `env/app.env` | the deployed product |

The project **id** is not the project **name**. Ids are globally unique, so
`undra` was taken and the id carries a numeric suffix. Every `gcloud` command
needs the id; the console shows the name. Confirmed 2026-08-07.

**Verified against both keys 2026-08-06** (`generateContent`, not just `models.list`
— the list endpoint returns models the tier cannot actually serve):

- The free key serves `gemini-3.6-flash` and `gemini-3.1-flash-lite`. It returns
  **429** on `gemini-3.1-pro-preview` — there is no free-tier Pro quota, so the
  daily planning call falls back to `planning_fallback` unless it runs on a
  billed key. Note this 429 is *permanent*, not a rate limit: retrying with
  backoff will never succeed.
- The paid key initially returned **429 RESOURCE_EXHAUSTED, "Your prepayment
  credits are depleted"** on *every* model. **Resolved 2026-08-06** by a 250 SEK
  prepaid top-up; all three configured models now return 200, Pro included.

**How the billing actually works here, since it cost an afternoon:**

There are three separate pots and none of them funds the next:

1. **Cloud billing account** — pays for Cloud Run, Build, Artifact Registry,
   Secret Manager, egress.
2. **Free trial credit** — 2910.57 SEK, sitting on `My Billing Account`
   (`01E0FA-16AE45-963492`), valid to **2026-11-04**. It is a
   `FreeTrialUpgrade` credit, meaning that account was upgraded from trial to
   full paid on 2026-08-06 and the unused balance carried over. It covers Cloud
   products but is **excluded from the Gemini Developer API**.
3. **Gemini Developer API prepaid balance, per project** — what an API key
   actually draws down. Funded only by an explicit top-up.

`undra` is linked to `My Billing Account` so the trial credit pays the hosting
bill. Coral's other billing account (`012486-2FCE2D-F95B75`, org
`coral-at-red-org`) holds no credit and is not the one to use.

Two projects is not an accident of the key-creation UI, and both must stay:
tier is a property of the *project*, not the key, so a single project cannot
serve both a free-tier key and a paid one. `undra-free` must have **no billing
account linked** or the ops loop silently becomes paid and the free-tier cost
model above stops being true.

New Google accounts appear to get prepay where established ones were
grandfathered onto pay-as-you-go; that is why Eliza's own projects behave
differently from Coral's. Not a misconfiguration.

Spend protection is now three independent layers, which is deliberate — each
catches what the others miss:

1. **GCP budget** on the `undra` project (configured 2026-08-06). Verify whether
   it has a Pub/Sub topic wired to a billing-disable Function, or only sends
   email — the UI default is email only, which stops nothing.
2. **Revolut virtual card with a hard cap**, tied to the project. Holds even if
   layer 1 is misconfigured, since it isn't self-referential.
3. **Prepay balance** — API keys stop dead at zero. Google-enforced, not
   bookkeeping.

Trial credits expire 90 days from 2026-08-05, and the trial ends at expiry or
exhaustion, whichever comes first. Services stop unless explicitly upgraded to a
paid account. Irrelevant to the deadline; relevant if Undra outlives August.

---

## 5. Architecture

**Two loops, kept separate:**

- **Build loop** — GitHub repo + Jules via the v1alpha REST API. Orchestrator
  files sessions; `requirePlanApproval` for anything touching payments, auth or
  user data; `autoPr` for the rest. CI gates merge. Deploy to Cloud Run.
- **Operate loop** — ADK agents in a container on the lab box, systemd timer,
  3–4 cycles/day. Hierarchy earns its keep (Project Vend phase 2: adding a
  CEO-agent oversight layer improved profitability markedly).

Skip Vertex AI Agent Engine on the critical path — days of wiring for managed
sessions we don't need. ADK ports to it later if a showpiece is wanted.

**Interrupt channel:** Telegram bot, outbound long-polling only (nothing exposed
inbound). Typed interrupts, not chat:
`request_human(kind, payload, deadline, default_action)`. Every kind has a
timeout default, almost always *park the task and continue*. Never block the
whole system on a human — a blocked task is cheap, nine idle hours overnight is a
sixth of the project. Daily digest at a fixed time. `/halt` from the phone.

**Other infra notes:**

- Push a redacted ledger dump + cycle reports to the repo each cycle — offsite
  backup *and* the required "agent execution logs" evidence.
- **Public operations log** (`publish_log.py`): renders decisions, handoffs and
  spend from the ledger into static HTML + RSS for GitHub Pages. *Derived, never
  composed* — that property is what lets `CHARTER.md` §5 authorise publishing it
  without an approval token. Fails closed: a PII scrubber hit withholds the whole
  entry, logs an error event, and exits 30.
- Don't send mail from the box directly — residential IP, it lands in spam. Use
  an email API on a free tier with SPF/DKIM on undra.nu.
- The product itself goes on Cloud Run (satisfies the Google Cloud requirement,
  publicly reachable for judges).

---

## 6. Day-0 checklist — pre-provision every human gate

Minimal intervention is won here, not in clever escalation. Batch these once so
the agents never hit a login flow:

- [x] Lab box installed — Ubuntu Server, `red`, 192.168.1.240. See `LAB_SETUP.md`
- [x] Lab box Phase 4 (network isolation) + Phase 6 (Tailscale)
- [x] Fresh Google account as the agent "employee" identity
- [x] GCP project + billing, budget alert wired to a Cloud Function that
      *detaches billing* (alerts alone don't stop spend)
- [x] Virtual card with a hard low limit (Revolut) — not the main card
- [x] Domain registered — undra.nu. DNS + SPF/DKIM still to do
- [x] GitHub repo (public, licensed) + PAT
- [x] Jules API key (`jules.google.com/settings`)
- [x] Gemini API keys — one free, one paid, kept separate
- [ ] Tip jar (Ko-fi / Buy Me a Coffee) — optional, no KYC needed
- [x] Telegram bot token + chat id
- [ ] Devpost submission draft started

### Credential rules

Not charter rules — configuration facts, which is why they live here. But they are
the difference between a leaked credential being a bad afternoon and a permanent
takeover.

**Coral never holds a username and password. Scoped tokens only.** A token
reaches one thing, is revocable in isolation, and cannot change an account's
recovery address. A password grants account *control*. If a service offers no
token auth, Coral doesn't get access — it stays behind the `LOGIN` gate.

**`coral@undra.nu` must not be the recovery address for anything.** Not the
Google account, not GitHub, not Hostup. Mailbox access would otherwise mean
password-reset access, and §5 grants standing latitude to read and answer that
mailbox. Verified 2026-08-06: all recovery addresses point to Eliza's own.

**If IMAP is ever wired up, use an app-specific password**, not the mailbox
password — revocable without changing the account.

**No API, no access.** Registrar, Hostup DNS, Revolut and Devpost are panel-only.
That is not a limitation to engineer around: all four are already gated in
CHARTER.md §4, so the missing API and the human gate are the same boundary
arriving from two directions.

---

## 7. The artefacts

| File | Role |
|---|---|
| `CHARTER.md` | Injected into every prompt as the stable cache prefix. §2–3 absolute, §4 gated actions, §5 standing latitude, §6 truth discipline, §7 prompt-injection rule, §8 loop discipline. §1 now filled in. |
| `invariants.toml` | Machine-checked subset. Wins any disagreement with the charter. Remaining `FILL_ME`s: `allowed_repos`, `allowed_hosts`. |
| `situation_report.py` | Runs pre-cycle; stdout prepended to agent context. Also the watchdog. Stdlib only, Python 3.11+. `--init` once, then exit 0 = proceed, 10 = halted, 20 = broken. |
| `publish_log.py` | Renders the public operations log from the ledger. No LLM, no free text of its own. Exit 30 = an entry was withheld by the PII scrubber, which means a §3.6 violation upstream — fix the writer, not the scrubber. |
| `LAB_SETUP.md` | Runbook for the lab box: BIOS through network isolation. |

Design points worth not undoing:

- **`UNKNOWN` machinery is the main event.** Collectors never omit a fact or
  crash — failures surface as explicit `UNKNOWN`, repeated in a block at the end.
  "Revenue unknown" silently becoming "revenue zero" is the drift being guarded
  against.
- **Idempotency lives in the `actions` table**, not the model. Unique constraint
  on `idempotency_key`.
- **Repeated-failure detection** groups failed actions by `(kind, target)` and
  halts at three — the stuck-loop signature, and it fires before the budget does.

---

## 8. Open

- **Growth channel** — still the binding constraint on any revenue at all, and
  what determines how much human gating is actually needed.
- **`CHARTER.md` §3.2 bans all cold outreach** and §4 gates every first message
  to a new person. Deliberately tight: it's the human's name and controller
  liability. Loosening is a one-line diff; a spam complaint isn't. Flagged as a
  judgement call to revisit consciously.
- **Repo name**, plus the `allowed_repos` and `allowed_hosts` entries that depend
  on it and on the first deploy.
- **On-device inference, deliberately deferred.** LiteRT-LM's browser build is
  text-only and 2 GB, so it can't serve the photo path. Build the `classify()` /
  `answer()` seam now so an on-device implementation drops in later — and do not
  let "designed for" harden into "does" (`CHARTER.md` §6.6).

**Resolved since the first draft:** the app idea (§1 above). EU AI Act Article 50
— applicable and enforceable since 2 August 2026, explicitly *not* deferred by
the Digital Omnibus. Handled in `CHARTER.md` §2.4 and by the marking in
`publish_log.py`. No grace period applies, since the system launches after 2 Aug.

---

## 9. Next step

Drop the files into a fresh repo and have Claude Code write `AGENTS.md` — pointing
at `CHARTER.md` and `invariants.toml` as authoritative — plus the cycle runner
around them. The charter is most of the spec already.

Build work belongs on the box itself: the ops layer lives there, so working
inside it beats reasoning about it from outside.
