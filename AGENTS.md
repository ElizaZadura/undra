# AGENTS.md

Instructions for the coding agent building this repository. Not for Coral — Coral
reads `CHARTER.md`.

**Read first, in this order:** `HANDOFF.md` (what this project is and what has
been decided), `CHARTER.md` (the constitution Coral operates under),
`invariants.toml` (machine-checked limits). This file contains only what those
don't: the build contract.

---

## What you are building

A cycle runner. Everything else already exists.

One cycle, in order:

1. systemd timer fires on the lab box (`red`), every 4 hours
2. Container starts
3. `situation_report.py` runs. Exit 10 → write a state summary, exit, **no model
   call**. Exit 20 → treat as halt. Exit 0 → continue
4. Report stdout is prepended to context, after `CHARTER.md`
5. Coral decides and acts through tools
6. Handoff written to `cycles.handoff`, status written to `cycles.status`
7. Container exits
8. `publish_log.py` renders `docs/`, commits, pushes

**Coral is stateless between cycles.** Do not maintain in-memory state, caches, or
conversation history across cycles. Each cycle is a fresh model instance whose
entire picture of reality comes from the situation report. This is deliberate —
see `HANDOFF.md` §3 for why — and a runner that "helpfully" persists context
defeats the design.

---

## Requirements that are easy to get wrong

Each of these silently breaks something if missed.

1. **`cycles.status` must be honest.** A cycle that woke and achieved nothing
   records `blocked`, `idle` or `halted` — never `ok`. `situation_report.py`
   counts these as `unproductive_cycles_24h`, which is the only signal Eliza gets
   that the limits are the bottleneck. A runner that always writes `ok` destroys
   that signal without any error appearing anywhere.

2. **Telegram messages to Eliza must NOT be written to the `outbound` table.**
   That table feeds `max_outbound_per_hour = 3`, which halts. Approval requests
   and digests are internal. `outbound` means third parties only.

3. **Every money-moving or message-sending action needs an `idempotency_key`.**
   The unique constraint on `actions.idempotency_key` is what makes retries safe.
   Paying an invoice twice is a documented failure mode of long-horizon agents.

4. **The halt flag is checked before every action, not once per cycle.** Read
   `flags.halt` from the ledger immediately before each tool call.

5. **Model routing comes from `invariants.toml`,** not hardcoded. If the
   `planning` model errors or 404s, fall back to `planning_fallback` and log an
   event — do **not** halt. `gemini-3.1-pro-preview` is a preview model and
   preview models get withdrawn.

   **Not every 429 is a rate limit** (verified 2026-08-06). The free key returns
   429 on Pro because the tier has *no* Pro quota, and a depleted prepaid balance
   returns 429 on every model. Neither is retryable — backing off (item 8) waits
   forever on a call that cannot succeed. Distinguish by the error body: a 429
   whose `status` is `RESOURCE_EXHAUSTED` and whose `message` mentions quota,
   plan, billing or prepayment is **permanent** — fall back or park the task. A
   429 without that language is a genuine per-minute limit; back off and retry.

6. **Do not set `temperature`, `top_p` or `top_k`.** Deprecated on Gemini 3.x.
   Use `thinking_level`, not `thinking_budget`.

7. **Use the Gemini Developer API, not Vertex.** `GOOGLE_GENAI_USE_VERTEXAI=FALSE`
   plus an API key. No service account. Verify the exact env var names against
   current ADK docs. Vertex/Agent Platform needs Cloud IAM and is explicitly out
   of scope (`HANDOFF.md` §5).

8. **Serialise model calls.** Free tier is ~15 RPM; you will hit the per-minute
   limit long before the daily one. Exponential backoff on 429.

9. **Log every LLM call to `llm_usage`** with token counts and cost estimate.
   That table is the soft spend cap and the submission's API-usage evidence.

   **Count thinking tokens or the cap is fiction.** Gemini 3.x bills reasoning
   tokens, and they do not appear in `candidatesTokenCount`. Measured 2026-08-06
   on `gemini-3.6-flash`: `promptTokenCount=7`, `candidatesTokenCount=1`,
   `totalTokenCount=109` — the obvious two fields account for 8 of 109 billed
   tokens. Record `usageMetadata.totalTokenCount` and store the reasoning count
   separately (`thinking_tokens`, added to the schema); price it at the output
   rate. A runner that logs only input+output undercounts the *only* spend
   ceiling this system has by roughly an order of magnitude.

10. **Jules sessions:** `requirePlanApproval` for anything touching payments,
    auth or user data. On CI failure Coral files a follow-up session.
    `max_repeated_action_failures = 5` stops it looping — do not implement a
    separate retry ceiling that disagrees with the watchdog.

    **`autoPr` is not a field this API accepts** (probed 2026-08-06 —
    `autoPr`, `autoPR`, `auto_pr`, `automaticPullRequest` and
    `createPullRequest` are all rejected with HTTP 400). Sending it fails the
    whole request. A finished session with no PR needs a human to click Publish;
    there is no submit endpoint either.

    **Never merge `main` into a long-lived feature branch.** The ops loop
    commits regenerated `docs/` and `reports/` to `main` every four hours, so
    the merge drags generated files onto the branch and they diverge again on
    the next cycle. Five cycles were spent on exactly this on 2026-08-07; every
    task succeeded and nothing moved. Prefer short-lived branches, and re-apply
    work onto a fresh branch rather than reconciling a stale one.

11. **Credentials are per-container.** `env/ops.env` (free key, Telegram token,
    GitHub PAT) and `env/app.env` (paid key only), both mode 600, both
    gitignored. The app container must not be able to see the free key —
    `CHARTER.md` §3 relies on it being absent, not forbidden.

    **This rule is one-directional, and that matters** (amended 2026-08-06).
    Since the free tier returns a permanent 429 for Pro models, the daily
    planning call cannot run on the free key. `env/ops.env` therefore also
    carries `GOOGLE_API_KEY_PAID`, used for exactly one thing: the planning
    call. That does not weaken anything — the constraint is that the container
    handling *user data* must not hold a key whose prompts may be used for
    training. Ops handles no user data. Do not invert this and put the free key
    in the app container for symmetry.

12. **Commit a redacted ledger dump each cycle** alongside the rendered log. The
    raw `ledger.db` is gitignored and lives only on `red`; the dump is the offsite
    backup of the audit trail. Redacted means: no `payments` detail, no
    customer-identifying columns.
13. **Watch for work that succeeds and changes nothing.**
    `max_repeated_action_failures` counts *failures*, and is blind to the
    opposite shape: an action that completes every time while the state it
    targets never moves. `max_cycles_without_progress` counts distinct cycles
    instead, because three attempts inside one cycle is iteration while the same
    job reappearing in three separate cycles means each fresh instance drew the
    same conclusion from the same facts. That cannot be fixed by retrying, and
    the correct output is an escalation.

---

## Do not build

Scope fence. Each of these was considered and deliberately dropped — adding them
back is a regression, not an improvement.

- **IMAP / mailbox integration.** Deferred. `coral@undra.nu` forwards to Eliza.
  cPanel offers no app-specific password, so IMAP would mean Coral holding a
  mailbox password for a channel with no traffic before 18 August.
- **On-device inference.** Build the `classify()` / `answer()` seam with a cloud
  implementation so an on-device one can drop in later. Do not attempt LiteRT-LM:
  the browser build is text-only and 2 GB, so it cannot serve the photo path.
- **Vertex AI Agent Engine / Agent Platform.** Days of wiring for managed
  sessions this design doesn't use.
- **Merchant of record / payments.** Dropped. A tip-jar link if anything.
- **Tailscale.** Not needed; the box is wired and reachable on the LAN.
- **Any second sync mechanism for the docs.** Git is the sync layer.

---

## Layout on `red`

```
/srv/lab/undra/
  compose.yml
  ledger.db            gitignored
  env/ops.env          gitignored, 600
  env/app.env          gitignored, 600
  CHARTER.md  invariants.toml
  situation_report.py  publish_log.py
  runner/              <- what you are building
  docs/                <- published to log.undra.nu
  reports/             <- situation reports, committed
```

`.gitignore` must exist **before** `git init`. `ledger.db`, `ledger.db-wal`,
`ledger.db-shm`, `env/`, `.env*`. A key that has ever been committed is
compromised even after removal.

---

## Verify before declaring done

```bash
python3 situation_report.py --init
python3 situation_report.py --no-enforce     # exit 0 on an empty ledger
python3 publish_log.py --dry-run
```

Then a dry cycle with the model call stubbed, checking that: a halt flag stops it
before any model call; an unproductive cycle records a non-`ok` status; a Telegram
send does not appear in `outbound`.

The three-layer spend protection is already configured (`HANDOFF.md` §4). Do not
add a fourth in code.
