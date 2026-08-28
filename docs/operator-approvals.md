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

**Since 13 August the Telegram message says the same things.** It used to be
`#8 DESTRUCTIVE` and the payload, which told you the kind but not what to do
with it; now it leads with whether the request blocks progress, names anything
you have to go and do before answering, and says plainly when replying grants
nothing. Same taxonomy, same source file — `runner/operator.py` renders both,
so they cannot drift apart. `bin/waiting` is still the better view when several
things are waiting at once, because only it can order them.

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
| `/halt` or `halt` | Sets the halt flag. Stops everything. Replies to confirm. |

**`/halt` is not instant, and since 17 August it says so.** The flag is *read*
before every action, but it can only be *set* when a cycle starts and polls
Telegram — so a halt sent at 09:18 with the next cycle due at 12:11 applies at
12:11. On 17 August that happened, nothing acknowledged it, and the reasonable
conclusion — that the command had not worked — was wrong. It had been queued
three times over.

There is now a receipt, and it names the delay rather than hiding it:

```
[undra · halt]

The halt flag is set. No new actions will run and no model calls will be made.

You sent it 3h 2m ago. Halt is applied when a cycle starts, not when you send
it, so there is a delay of up to one cycle — and it has now been applied.

Nothing further is needed from you. To resume, run ./bin/unhalt on the box.
```

**If you need it stopped now**, and you are at a terminal, there are two faster
routes than waiting for a cycle:

| Command | Effect |
|---|---|
| `./bin/halt --reason "..."` | Sets the flag immediately. Cycles still start and record themselves as `halted`. |
| `./bin/unhalt --reason "..."` | Clears it. Refuses without a reason, because the row you overwrite is the only record of why the agent stopped. |
| `sudo systemctl stop undra-cycle.timer` | No cycle runs at all, so no rows. Needs your own terminal. **Does not survive a reboot.** |
| `sudo systemctl disable --now undra-cycle.timer` | The same, and it stays stopped across reboots. This is the one you want for a long stop. |

**`stop` is not `disable`, and the difference is a reboot.** The timer was stopped
on 17 August but left `enabled`. `red` rebooted on 25 August, systemd started the
timer again because that is what `enabled` means, and cycles resumed every four
hours for three days. Nothing bad happened — the flag was set and every one of
them halted before a model call — but nobody had decided the system should be
running, and it was.

Neither `bin/halt` nor `bin/unhalt` is in Coral's tool surface, and a test fails
if anything naming halt ever appears there. CHARTER §10 forbids the agent
clearing the flag; before 17 August it also had no way for *you* to clear it, and
the last clear was done by hand-editing `ledger.db`.

**Stopping the timer and setting the flag are not the same thing**, and resuming
needs whichever you used undone. If a `/halt` is still sitting unread in Telegram
when you restart, the next cycle will consume it and halt again — correct, and
surprising the first time.

### What a halted system sends you

**While the halt stands you get one short notice, not the daily digest.** It names
the reason, when the halt began, and how many cycles have been skipped without a
model call, action or spend — and it repeats every seven days so you can tell a
stopped system from a dead box.

Until 28 August you got the full daily digest instead. `digest.send_if_due()` runs
nine lines before `cycle.py` checks the halt flag, so a system that had made zero
model calls since the 26th filed a normal-looking morning brief every day, ending
with *"reply /halt to stop everything"* — an offer to do the thing already done.
The one message proving the stop had worked was formatted like proof that it had
not.

The fix is deliberately not silence. Four defects in this project were a channel
saying nothing when it should have spoken, and a stopped system that goes quiet
would be the fifth. Read against CHARTER §9 — *what happened, what it cost, what
you decided, what is blocked* — the notice is that digest, honestly rendered when
all four answers are "nothing", and rate-limited to match.

The daily digest's date stamp is not marked while halted, so the first cycle after
you clear the flag files a real digest straight away rather than skipping the day
the work resumed.

**Exact word counts.** The parser matches two or three words and nothing else.
`approve 7 looks good to me` matches neither pattern — it falls through and is
filed as a note to Coral, and request #7 stays pending. If you want to say why,
send the reason as a second message; Coral reads notes at the start of its next
cycle.

**Free-form notes do work, and now say so.** Anything that is not an approval
command becomes a note, appears in the next cycle's briefing, and is acted on.
On 14 August "there is nothing that needs Jules right now, please hold" was read
at the next cycle and obeyed for five cycles — and nothing told you. Since
15 August, marking a note read sends you a line back saying what is being done
about it:

```
[undra · read your note]

#7

Holding off on Jules until there is a step for it.
```

If a note produces no receipt within one cycle, it was not read. That is now a
signal rather than a silence.

**Requests filed before your note do not close themselves.** They keep ageing
and `bin/waiting` keeps reporting them as overdue, which reads as being ignored
when the opposite is happening. Check the timestamps against when you sent the
note before concluding anything.

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
