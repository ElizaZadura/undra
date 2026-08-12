# What Coral asks you for, and what to do about it

For Eliza. Everything else written down here is for agents — structured events,
explicit states, machine-parseable payloads. This page is the one part of the
protocol you are inside, so it is written for a person on a phone.

**If you read nothing else:** run `bin/waiting`. It answers this whole document
for the situation you are actually in, in the order you should do things.

```
$ ./bin/with-env ./bin/waiting

5 things waiting for you.  3 block progress — do those first.

DO THESE FIRST
  LOGIN  (5h ago)
    Google Cloud billing export needs re-auth before the invoice can be read.
    first:  Sign in / complete the OAuth or 2FA prompt yourself
    reply:  approve 1
...
```

`--no-jules` skips the API call if you are offline or in a hurry.

---

## The one distinction nobody wrote down

Every gated action arrives looking the same. They are not the same, and the
difference is *why* the gate exists:

> **Coral is forbidden** → your approval is the entire act. It does the rest.
>
> **Coral is incapable** → you have to do the thing yourself, *then* approve.
> Approving first just moves the failure one step along.

That is the whole thing. Everything below is that sentence applied.

### You act, then approve

Coral cannot do these at all. It holds no username and password (only scoped
tokens), has no payment instrument, and has no legal personality to agree with
anyone (CHARTER §2.2).

| Kind | What you do first |
|---|---|
| `LOGIN` | Sign in, complete the OAuth consent or the 2FA prompt |
| `CAPTCHA` | Solve it |
| `TOS_ACCEPTANCE` | Read and accept it — Coral cannot agree to anything |
| `NEW_ACCOUNT_CREATION` | Register the account |
| `PAYMENT_AUTH` | Make the payment, enter the card |

Then `approve <id>`, which tells Coral the way is clear.

### You approve, Coral acts

It is able and is waiting only for permission. Replying is the whole action.

`SPEND_OVER_LIMIT` · `EXTERNAL_MESSAGE` · `PUBLISH` · `PRICING_CHANGE` ·
`DESTRUCTIVE` · `PII_EXPORT`

### Do not approve

`LEGAL_OR_MEDICAL_CLAIM` — CHARTER §4 says this should never fire, and its
timeout default is `halt`, not `abandon_task`. If one appears, the question is
what produced it. Find that out before answering.

### Not approvals at all

Nothing is granted. `approve`/`deny` only closes the record.

- **`STALLED_WORK_ESCALATION`** — the loop is telling you it is stuck. Read it
  and decide. Note that its `default_action` fires on timeout, and on
  12 August one of those defaults was *"file another Jules task"*, which was
  the behaviour causing the stall. Read the default, not just the question.
- **`PROTECTED_PATH_PATCH`** — an agent-written patch tried to change the rules
  or the checks and was refused before it could open a pull request. Read the
  patch. Apply it by hand if it is right; otherwise discard it.
- **`JULES_PLAN_APPROVAL`** — the row exists so the work is visible. The actual
  release uses a different command; see below.

---

## What to type

Two commands, two namespaces. They are not interchangeable.

| Type this | Effect |
|---|---|
| `approve 7` / `deny 7` | Resolves ledger request #7 |
| `approve jules <session_id>` | Calls the Jules API and releases that plan |
| `deny jules <session_id>` | Records your refusal; the session stays paused |
| `/halt` or `halt` | Sets the halt flag. Stops everything. |

**Exact word counts.** The parser matches two or three words and nothing else.
`approve 7 looks good to me` matches neither pattern — it falls through and is
filed as a note to Coral, and request #7 stays pending. If you want to say why,
send the reason as a second message; Coral reads notes at the start of its next
cycle.

Approval is only accepted from your chat id, checked before anything else is
parsed. Coral has no tool that can approve anything, and three tests enforce
that a gate it could open for itself is not a gate.

**A token is valid for one action**, the one in the payload. It does not
generalise — approving a support reply yesterday does not authorise one today.

**Silence is an answer.** Every request carries a `default_action` that fires at
its deadline, usually 12 hours. Almost always it is *abandon the task and carry
on*. A blocked task is cheap; a blocked system while you sleep is not.

---

## Where things hide

Requests reach you by Telegram and live in `ledger.db`. Two things do not:

**Jules holds sessions of its own.** A session at `AWAITING_USER_FEEDBACK`
writes no code until released, and until 12 August nothing told you one existed
— `t_jules_file_task` created it, explained the consequence to Coral, and sent
you nothing. That is fixed, but `bin/waiting` also asks Jules directly, because
a notification that was never sent cannot be re-read.

**"Ready for review" in the Jules web UI is not an approval queue.** It means
Jules finished and left a patch nobody published. Publishing one now opens a
pull request against a `main` that has moved on. As of 12 August, 15 sessions
hold unpublished patches and every one is either already shipped by another
route or superseded — there is nothing in there to rescue.

---

## Keeping this true

The taxonomy above is not prose. It lives in `runner/operator.py`, `bin/waiting`
reads it, and `test_every_gated_kind_is_classified` fails if a kind is added to
`invariants.toml` without a classification. If this page and the tool ever
disagree, **the tool is right** — it is the one under test.
