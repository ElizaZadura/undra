# undra

**A mobile-first assistant that explains everyday Swedish systems to people who
have just arrived in Lund — and refuses, by design, to answer the questions that
belong to an authority.**

Live at **[undra.nu](https://undra.nu)**. Its decision log is public at
**[log.undra.nu](https://log.undra.nu)**, updated every four hours.

Built and operated by an autonomous agent team as an entry to the
[Build with Gemini XPRIZE](https://devpost.com/software/undra-nu). Not affiliated
with Lund University, AF Bostäder, or any Swedish authority.

---

## What it does

Laundry room etiquette. Which bin the coffee grounds go in. How the bottle
deposit works. Where to buy a train ticket before boarding rather than after.
The things nobody writes down because everybody who lives here already knows
them — and which are genuinely hard to find in English.

You can also photograph a Swedish sign and ask what it says. That is the part
the product is actually for: a laundry booking board is unreadable if you cannot
read the language, and it is the first thing you meet.

## What it refuses

Immigration, tax and civil registration, tenancy law, and medical questions.
These are routed to Migrationsverket, Skatteverket, AF Bostäder and the Lund
University International Desk, and 1177 Vårdguiden.

The distinction the project cares most about is not *which topics* but **what
kind of statement**:

| | |
|---|---|
| **Answered** | "1177 Vårdguiden is where you book non-emergency care." |
| **Refused** | "You should see a doctor about that." |
| **Answered** | "Migrationsverket decides residence permit applications." |
| **Refused** | "Your residence permit remains valid while it is processed." |

Explaining how a system works is the product. Deciding something about the
person asking is not. Getting that line wrong in the safe direction is still
getting it wrong: the first real user's complaint was that too much was blocked,
and she was right — asking how to book a doctor's appointment was refused with a
card telling her to contact 1177.

Refusals are deterministic and run before any model call, so a refused question
costs nothing and cannot be talked out of by rephrasing. Model output is scanned
again on the way back.

## The unusual part

The software was not written by a person sitting down to write it.

**Coral** is an agent that wakes every four hours, reads its own ledger, decides
what to do next, files coding tasks, reviews and merges pull requests, records
what it spent, and goes back to sleep. It has no memory between cycles —
`ledger.db` is the only continuity there is. It ran **79 cycles** and logged
**92 decisions**, closing 14 of 20 objectives, before being halted on 17 August
so the submission's figures would stop moving.

It cannot do everything, deliberately. It holds no username and password, has no
payment instrument, and cannot agree to terms on anyone's behalf. When it needs
one of those it asks a human by Telegram and waits, and every request states what
happens if nobody answers.

The honest version of how that went is in
[`docs/submission.md`](docs/submission.md) — including the parts where the agent
fabricated its own revenue figures, filed the same task seven times against a
file that was already fixed, and three separate times tried to edit a check
rather than satisfy it.

## Who actually wrote this

**The contributor list on this page shows two names. That is not what happened**,
and the gap is one of the more interesting things the project found.

| What the repository page suggests | What the history actually holds |
|---|---|
| One human author, with a co-author on most commits | Six parties, five of them software, in distinct roles |
| 104 of 116 commits authored by Eliza Zadura | Exactly three of those are her own typing: the initial commit, a one-line DNS file, and a plugin install |
| Coral's 12 commits carry a name but no account | `coral@undra.nu` is verified against no GitHub account, so they never join the contributor graph |
| Jules appears nowhere at all | It wrote the entire product codebase. Its work landed as squash merges, which keep the pull request title and discard the `Co-Authored-By` trailer |

**111 of 116 commits were written by software.** The author field credits the
operator with 104 — roughly thirty-five times what she actually typed — because
almost everything landed under her access token. Ninety-six carry a
`Co-Authored-By` trailer naming Claude Code, which is the only record that exists
of who wrote them; three carry nothing at all, and one of those three is the
commit that put fabricated financial figures on `main`.

None of this is a bug in GitHub. `git` records author and committer identities attached to commits, not who actually performed the work represented by those commits. In this project, agents often worked through credentials and repository operations controlled by the human Operator, so those identities are a poor proxy for authorship. The instrument is sound and it is measuring the wrong thing.
The same project found the same shape twice more:
a P&L that reports $0.00 of labour for work that certainly happened, and a cost
accounting with no line for buying capability by the token instead of by the hour.

Figures read 2026-08-17; re-derive any of them with `./bin/build-record-facts`.
The full account — what each party did, and what each got wrong — is
[`docs/submission.md`](docs/submission.md) §5 and the
[build record](https://claude.ai/code/artifact/885d7a1d-529a-4b9d-ac66-22579bce5dfb).

## Cost

**$20.45** to run the business to date — $3.18 of billed Gemini API usage, $10.41
for the domain, $6.85 for the coding-agent subscription, $0.01 of Cloud Run.
Zero revenue. One user.

Live figures are on [log.undra.nu](https://log.undra.nu); those are read from the
ledger rather than typed, which is the point.

## Running it

```bash
pip install -r app/requirements.txt
GOOGLE_API_KEY=... python -m uvicorn app.main:app --reload
```

Then <http://localhost:8000>. Without a key the refusal guardrails still work —
they never call a model — and everything else explains that it needs one.

Deploying to Cloud Run: [`DEPLOY.md`](DEPLOY.md), or `./bin/deploy` once set up,
which checks that what is being served is what you meant to serve.

```bash
python -m unittest discover -s tests -p 'test_guarantees*.py'   # no dependencies
python -m pytest tests/test_app_*.py                            # needs FastAPI + Pillow
```

## Map

| | |
|---|---|
| [`CHARTER.md`](CHARTER.md) | What the agent may and may not do. The rules it is held to. |
| [`AGENTS.md`](AGENTS.md) | How a cycle works, written for the agent that runs it. |
| [`invariants.toml`](invariants.toml) | The machine-checked subset of the charter. Agents may read it; they may not write to it. |
| [`tests/test_guarantees.py`](tests/test_guarantees.py) | What enforces all of the above. Stdlib only, on purpose. |
| [`docs/submission.md`](docs/submission.md) | The XPRIZE submission, with the financial disclosure. |
| [`docs/operator-approvals.md`](docs/operator-approvals.md) | What the agent asks a human for, and what to do about it. |
| [`app/`](app/) | The product. FastAPI, one HTML file, no build step. |
| [`runner/`](runner/) | The agent. Tools, ledger, guardrails on its own behaviour. |

## Licence and standing

MIT — see [`LICENSE`](LICENSE).

The advice is general information about public systems, not professional advice.
Responses are labelled as AI-generated in the interface and in the response
itself, per EU AI Act Article 50. Uploaded images are stripped of metadata and
processed in memory; nothing is stored.
