# Undra: Lund Pre-Arrival Student Assistant
## Build with Gemini XPRIZE — Official Submission Package

> Every figure in this document is read from `ledger.db`, from `git log`, or from an
> invoice, and says which. Where a number is an estimate rather than a billed amount, it is
> labelled. Where something is zero, it says zero. An earlier draft of this document, written
> by an agent and merged on 9 August 2026, contained invented financial figures; that failure
> and its cause are described in §5.3, because it is the most instructive thing this project
> produced.

---

## 1. Project Narrative

### 1.1 The Problem: Navigating Swedish Systems Under Pre-Arrival Anxiety

Each year Lund University admits a substantial international intake to southern Sweden.
The most recent published figure is **1,468 incoming exchange students in the 2022/23
academic year, with 1,507 expected in 2023/24** ([Lund University staff pages, published
2024-01-27](https://www.staff.lu.se/article/increase-both-incoming-and-outgoing-exchange-students)) —
exchange students alone, excluding full-degree international students, so the population
this product addresses is larger than that figure and we have not found a sourced number
for the whole of it. For many it is a first time living abroad, and they are immediately
confronted with a set of complex, highly structured and unfamiliar local systems.

Students must navigate:

1. **The "Pant" Recycling System**: Reverse-vending machines that refund deposits on
   aluminium cans and PET bottles — routine for residents, opaque to newcomers.
2. **Waste Sorting (Miljöhus)**: Corridor waste rooms require sorting into multiple specific
   fractions — paper packaging, plastic, organic, metal, coloured glass, clear glass,
   residual — with social and occasionally financial consequences for getting it wrong.
3. **The Unwritten Rules of the Laundry Room (Tvättstuga)**: A genuine cultural flashpoint.
   Digital tag bookings, strict timing windows, and cleanup etiquette that nobody writes
   down.
4. **Public Transport (Skånetrafiken)**: Student discounts, app registration, and how
   regional buses and trains actually work.
5. **Pre-Arrival Housing and Scams**: AF Bostäder's Novisch lottery and LU Accommodation are
   high-stakes processes, and the surrounding private market attracts rental fraud.

Because students face these systems **before physically arriving** — Arrival Day for autumn
2026 is 18 August 2026 — they are anxious, abroad, and without local support. Signage and
official instructions are in Swedish. General-purpose chatbots lack Lund-specific context
and, worse, will confidently answer legal, tax, immigration or medical questions where a
wrong answer can jeopardise a student's right to stay.

### 1.2 The Solution: Undra Mobile-First Pre-Arrival Portal and Assistant

**Undra** (Swedish for "to wonder") is a mobile-first web assistant for pre-arrival
international students in Lund. It has two layers:

1. **Curated Visual Guides** — mobile-first guides covering housing and scams, Arrival Day
   logistics from Copenhagen Airport to Lund Central, the pant and sorting systems, and
   communal laundry etiquette.
2. **Interactive Multimodal Q&A Assistant** — a chat interface where students ask
   open-ended questions in plain English and can upload photos of signs, Swedish letters,
   recycling symbols or laundry control panels for translation and explanation.

### 1.3 Target Audience

Pre-arrival international students admitted to Lund University: making high-stakes decisions
such as signing leases and paying deposits from their home countries, not yet speaking
Swedish, phone-dependent, and disproportionately exposed to rental scams and bad advice on
visa and registration requirements.

### 1.4 Gemini Integration

The assistant layer is powered by **`gemini-3.6-flash`** through the official Google GenAI
SDK (`google-genai`), called from `app/main.py` in the deployed application. This satisfies
the competition requirement that "Projects that include LLM functionality must use the Gemini
API for at least one LLM call in the deployed application" — the Gemini call is in the
product a user talks to, not only in the operating layer described in §4.

- **Multimodal reasoning**: the same model handles text queries and image uploads, reading
  Swedish text in a photographed sign or booking panel and explaining it in the context of
  Lund student life.
- **System instructions**: injected via SDK configuration. They define the assistant's
  persona and scope and forbid it claiming to be human.
- **AI-authorship disclosure**: every model-generated response is prefixed with a visible
  badge, `🤖 [AI-Generated Response by Undra Assistant]`, emitted at `app/main.py:223`. This
  is implemented to align with the EU AI Act's Article 50 transparency obligations, which
  apply from 2 August 2026. We describe what the application does; we do not assert a legal
  compliance determination, which is not ours to make.
- **In-memory image handling**: uploaded images have EXIF and metadata stripped in memory on
  receipt and are held only as ephemeral buffers for the duration of the API call. They are
  never written to persistent disk or object storage. **Undra never logs or persists user
  question text** — application logging records the size and format of an image and nothing
  a user typed or named a file, because Cloud Run logs are durable storage and a filename
  like `uppehallstillstand_anna.jpg` is personal data.

  **The platform does retain it, and we would rather say so than let the sentence above be
  read as an absolute.** On 15 August we found that Google AI Studio has gained a logs and
  datasets view covering the API project that serves our users, holding 381 requests. It
  contains user prompts verbatim, and we exported two of our own — *"How does the pant
  machine work?"* — from a request Undra itself keeps nothing about. The retention is the
  platform's and predates our knowledge of it; the claim we can stand behind is about our
  own systems, which is why it is now written that way. Recorded, with two further findings
  about that surface, in our [developer-experience field
  notes](https://claude.ai/code/artifact/64b64876-97fc-4788-9825-9353aa302c37) — published to
  the competition's own discussion page and to the Google Labs Discord, so the report went to
  the platform as well as into this submission.

### 1.5 Refusal Guardrails: Deterministic "Refuse and Route"

In a student-advisory context the most dangerous failure is a confident, incorrect answer
about a visa, a tax obligation or a medical symptom. Undra therefore refuses four categories
outright rather than relying on prompt instructions alone.

**The four restricted categories** (CHARTER §3.3): Immigration and Visas; Taxes and Civil
Registration; Legal Contracts and Tenancy Disputes; Medical and Safety.

**Implementation** — `app/guardrails.py`, 523 lines, **144 regular-expression patterns**
matching Swedish and English regulatory terminology (*Migrationsverket*, *Skatteverket*,
*personnummer*, *uppehållstillstånd*, *residence permit*, *deposit dispute*, *112*, and so
on), in three sets that do different jobs:

- **Pre-generation — 112 patterns.** The query is scanned locally before anything is sent to
  the Gemini API. A match blocks it deterministically, no call is made, and the refusal
  returns in about 0.2 seconds. This is the check that earns the word *deterministic*: it
  cannot be argued out of by rephrasing, retrying or instructing the model.
- **Post-generation — 28 patterns.** The model's output is scanned for a determination about
  the reader: a dosage, a diagnosis, an assertion about their eligibility or liability. It is
  deliberately *not* the pre-generation set. Running the question's patterns over an answer
  refuses the product's own explanations, which is what happened between 12 and 13 August and
  is documented in §3.1. This check is weaker than the first by nature rather than by
  omission, and §3.2 says why.
- **Image path — 4 further patterns.** `check_query_guardrails` reads typed text and cannot
  read a photograph, so when an image is attached nothing has examined the subject of the
  request by the time the model answers. The output scan widens there to cover the model
  identifying the photograph as an official document — a permit decision, a tax letter, a
  tenancy contract — because a summary of the reader's own permit decision is immigration
  advice however it is phrased.
- **The "Route" UX**: a triggered guardrail returns a structured refusal card naming the
  responsible authority and linking to it directly — Migrationsverket, Skatteverket, AF
  Bostäder and the LU International Desk, 1177 Vårdguiden and 112.

Both paths are verifiable with two requests against the live service:

```
$ curl -s -X POST https://undra.nu/api/chat -F 'message=how does the laundry booking work?'
{"refused":false,"message":"🤖 [AI-Generated Response by Undra Assistant]\n\nHej! Laundry
room booking in Sweden—especially in student housing like AF Bostäder..."}

$ curl -s -X POST https://undra.nu/api/chat -F 'message=how do I apply for a residence permit?'
{"refused":true,"category":"immigration","title":"Immigration & Visas",
 "authority":"Migrationsverket","message":"I cannot advise on immigration, visa, or
 residence permit matters...","routing":[{"name":"Migrationsverket (Swedish Migration
 Agency)","url":"https://www.migrationsverket.se"}]}
```

Both outputs above were captured from `https://undra.nu` on 11 August 2026.

---

## 2. Financial Summary

### 2.1 Total Expenses

All costs are recorded in `ledger.db` with the evidence for each row, and every figure below
is traceable to an invoice or a billing console. Converted at the ECB reference rate for
10 August 2026: **1 SEK = 0.10538 USD**, **1 EUR = 1.1555 USD**.

**Costs incurred by the business itself — $20.45:**

| Category | Description | Cost (USD) | Evidence |
|---|---|---:|---|
| Gemini API | Model calls, projects `undra-504613` and `undra-free` | **$3.18** | Google Cloud billing, 1 Jul – 10 Aug 2026 (kr 30.16), billed |
| Cloud Run | Application hosting; Cloud Storage and Cloud Build both kr 0.00 | **$0.01** | Same billing report (kr 0.09), billed |
| Domain registration | `undra.nu`, 2026-08-04 to 2027-08-04 | **$10.41** | Hostup AB invoice 202680231, kr 79.00 + kr 19.75 VAT = kr 98.75 |
| Google AI Pro | One month; the agent's coding tool (Jules) | **$6.85** | kr 65.00 charged 2026-08-07, promotional rate. Google payments order history and the confirmation email, both retained as PDFs |
| **Total** | | **$20.45** | |

**Marketing and customer acquisition spend: $0.00.** No advertising, no paid placement, no
promotional spend of any kind. Disclosed as required even though it is zero.

**Human tooling — $48.25, disclosed separately and deliberately.** The people supervising the
build used AI subscriptions during August: Claude Pro at €225.00/year (Klarna order 1V4LABCO,
€18.75/month = $21.67) and ChatGPT Plus at €23.00/month incl. VAT (OpenAI order
`sub_1Rlu59KsIHRdbaPgNlHOfsAZ` = $26.58). Both predate this project — ChatGPT since April
2025, Claude since March 2026 — and are not exclusive to it. Charging a full month against
nine days of work overstates the true attributable cost, which we prefer to understating it.
These are excluded from the $20.45 because that figure answers "what does this business
cost to run", and a personal subscription bought before the business existed does not.

<!-- audit:disclosed -->
**A note on the estimate in our own logs.** The ledger's internal estimate of total model
spend is **$17.85** across 781 calls, computed from per-token rates transcribed from Google's
pricing page and applied to metered token counts. That figure covers every call the agent
made, on both API projects. Only one of those projects is billable: `undra-504613` carries the
paid key, and `undra-free` carries a free-tier key whose calls cost nothing.
<!-- /audit:disclosed -->

**We cannot split that estimate between the two keys, and until 13 August this document
claimed we could.** The ledger records the *role* a call was made in — `planning` or `ops` —
not the key that served it. The two were meant to line up, but the runner falls back to the
paid key when the free key stops serving mid-cycle, and 45 of 79 cycles logged doing exactly
that; 91 of the 257 `ops` calls were made inside one. A dollar column headed "free" would
therefore have been a guess wearing the clothes of a reading, which is the specific failure
this section was rewritten to remove in the first place. It has been taken out rather than
restated more carefully, because there is no more careful version of a number the ledger
does not hold.

What the ledger does support, read at 2026-08-17T09:28Z:

| Recorded as | Calls |
|---|---:|
| `planning` — paid key by design | 449 |
| `ops` — free key by design, fell back to paid on 45 of 79 cycles | 257 |
| Recorded before the ledger tracked the role at all | 75 |
| **Total** | **781** |

The agent is still running, so the call counts are a reading rather than a final figure; they
are regenerated from the ledger before submission. Google billed **$3.18** against the paid
key through 10 August, the last day the invoice covers. Measured on 11 August, the ledger's
estimate for that same key and period ran about 7% above the invoice — ordinary estimation
error, in the conservative direction. That comparison rests on the same role-versus-key
attribution described above, so treat it as an order of magnitude rather than a precise
reconciliation; only the billed figure is fixed.
**$3.18 is the billed figure and the one we quote.** The estimate is what the same volume of
work would have cost had none of it run on the free tier, and it is the number our spend
ceiling is enforced against, deliberately, because a budget guard should err toward stopping
early.

**What has accrued since the invoice closed, and why it is not in the P&L.** The $20.45 above
is what has actually been paid. The invoice stops at 10 August and the agent kept running, so
more has been incurred since: our ledger estimates the usage from 11 August onward at an upper
bound of $10.21 across 473 calls — upper, because most of those calls ran on the free key and
the ledger cannot separate them. Google's billing console, read on 17 August, puts the
billable part far lower.

None of that appears in the P&L statement, because the competition's template requires the
**cash basis** — expenses recorded when cash is paid out. Unbilled usage is not a cash
outflow, so it is excluded there and disclosed here instead. This is the one place in this
document where our convention of overstating costs is overridden by an instruction, and we
would rather point at the gap than let a reader find it.

### 2.2 Revenue by Month (May – August 2026)

| Month | Revenue (USD) |
|---|---:|
| May 2026 | $0.00 |
| June 2026 | $0.00 |
| July 2026 | $0.00 |
| August 2026 | $0.00 |

**Undra did not exist before 3 August 2026.** The repository's first commit is dated
5 August 2026 and the operating ledger's first row is 6 August 2026 at 13:33 UTC. There was
no business in May, June or July to earn revenue, and no work of any kind was done on this
project in those months. The hackathon period opening on 19 May does not imply the project
existed then.

For August 2026 the figure is zero for a different and simpler reason: the product has been
live since 7 August and has never charged anyone. It is free to use. Lund University's
Arrival Day is 18 August 2026, one day after the submission deadline, so the population this
product is built for has not yet arrived in Sweden.

We are not claiming a strategic rationale for zero revenue. The honest account is that we
built an operating system for an autonomous agent first, the product second, and ran out of
calendar before either could meet a paying customer.

### 2.3 Related-Party Disclosure

- **Related-party revenue: none.** Total revenue is $0.00, so there is nothing to separate.
  No transactions of any kind have taken place.
- **Related-party user: one, disclosed.** The product's first and currently only user is the
  partner of the Operator's brother, an exchange student in Lund who began beta testing on
  11 August 2026. She is a member of the target population, but she is not an arms-length
  user and we do not present her as one. See §3.
- **No artificial activity.** No dummy transactions, simulated payments, or synthetic usage
  were generated at any point.
- **Funding.** All costs were paid personally by the Operator, Eliza Zadura. No corporate
  sponsorship, grant, or related-party funding.

---

## 3. User Evidence

**Number of individual users: one.**

Undra's first real user began testing on 11 August 2026. She is an exchange student at Lund
University — precisely the population the product was specified for — and she is the partner
of the Operator's brother, which we disclose in §2.3 rather than leave for a judge to
discover. She has been asked whether other exchange students at her university would like to
test; any who do would be arms-length users, and we will report them as such if they
materialise before the deadline.

**Feedback received: one substantive comment, 12 August 2026, and a defect report on
13 August.** The user reported that the assistant's refusals felt broad — that she had tried
a range of prompts and most were declined. We are not reproducing her words here: consent to
quote her has been requested but not yet returned, and we would rather submit no testimonial
than one obtained without it. She is travelling until 19 August, after this deadline.

**The comment was acted on, and asking her for specifics overturned our first conclusion.**
An initial measurement against twenty in-scope questions we wrote ourselves — laundry booking,
the deposit-return system, waste sorting, regional transport, Arrival Day — produced zero
false refusals, and we concluded the guardrails were not over-blocking. That conclusion was
drawn from a corpus we invented. Asked which prompts had actually been blocked, the user named
three: obtaining a personal identity number, opening a bank account, and how to make a
doctor's appointment.

The third was a false refusal of a class we had not thought to test. Every pattern in the
medical category was a bare institution noun — `doctor`, `1177`, `clinic`, `vårdcentral` — so
naming the institution was the trigger. **Asking "what is 1177?" returned a refusal card
instructing the user to contact 1177.** The product refused to explain the service it was
recommending.

That is topic detection, not the restriction the charter describes. What §3.3 forbids is a
determination about someone's health. Explaining how Swedish healthcare works to a newcomer is
this product's stated purpose, and refusing it sends the user to a general-purpose chatbot
that will answer confidently and may be wrong — the exact harm §1.1 identifies. The medical
category was narrowed on 12 August with the Operator's explicit recorded approval, to refuse
symptoms, diagnosis, treatment, urgency and mental-health crisis while answering booking,
registration and orientation questions.

### 3.1 The fix did not reach the user for a day, and the reason is the finding

An earlier version of this section claimed that narrowing as complete and measured. It was
neither, and the correction is more useful than the original claim.

**The rule was written down in three places and we changed one.** On 13 August the deployed
service still refused "what is 1177?". Four separate causes, found in this order:

1. **It was never deployed.** `gcloud builds submit` uploads the working directory, not a
   branch, and the Cloud Shell clone it was run from had not been pulled since 7 August. The
   build succeeded, the deploy succeeded, Cloud Run returned HTTP 200 and the watchdog's
   `deploy_health` check stayed green throughout — none of them look at *what* is running. The
   12 August fix existed in git and not in the product for 25 hours.
2. **The answer was scanned with the question's patterns.** `app/main.py` ran
   `check_query_guardrails` over the model's own output. Defensible while those patterns were
   topic nouns; wrong the moment they stopped being. A correct answer to "what is 1177?" says
   "1177 Vårdguiden provides medical guidance", and that was enough to discard it. Measured
   against production: a residence-permit question refused in 0.21 s with no model call, while
   "what is 1177?" refused in 5.4 s — the sound of an answer being generated, paid for and
   thrown away.
3. **The system instruction still carried the old rule**, listing "calling 1177 or 112" as a
   refusal topic. The model duly opened with "I cannot provide medical advice" and then
   answered the question anyway.
4. **The replacement output check refused the product's own safety boilerplate.** A rule
   matching "you should see or call a doctor / 112" caught the routing advice the system
   instruction *tells* the model to give. The effect was non-deterministic: on 13 August, in
   the same deployment and the same minute, "what's 1177?" was refused in 4.6 s and "what is
   1177" answered in 4.0 s. Which one you got depended on which sentence the model reached for.

Commits `4b0fbec`, `777370c`, `851ea94` and `2744708`, all 13 August, with the deploy
verification in `bin/deploy` added so that a build reporting success can no longer stand in
for the served bytes.

### 3.2 What that taught us about deterministic guardrails

We claim deterministic refusals. That claim is true on the way in and weaker on the way out,
and the two should not be described in the same sentence.

**The input check earns the word.** It runs before any model call, so a refused question costs
nothing, returns in about 0.2 seconds, and cannot be argued out of by rephrasing, retrying or
instructing the model. Everything usually claimed for deterministic guardrails is true of it.

**The output check does not, and cannot.** The question it is asked — is this sentence a
determination about the reader? — frequently has no answer in the sentence:

> "You should call 112 in an emergency."

General information in an answer about how Swedish healthcare works. A determination if it is
the reply to someone describing chest pain. Identical text. What separates them is the user's
question, which the output scan never sees, and even with it the judgement is semantic rather
than lexical. Patterns written to catch the second reading caught the first, and refused the
product's core function at random.

The design is therefore deliberately lopsided, and `app/guardrails.py` says so at the point of
use rather than leaving a pattern list to look more capable than it is:

- the strong deterministic check is on the way in, where a refusal is free and unarguable;
- generation is constrained by the system instruction, which *does* see the question;
- the output scan keeps only what is a determination in every context — a numeric dosage, a
  diagnosis of the reader, an assertion about their eligibility or liability — plus a wider
  rule for images, where nothing else has read the input at all: `check_query_guardrails` reads
  typed text and cannot read a photograph, so a summary of the reader's own permit decision has
  to be caught on the way out or not at all.

The right instrument for the semantic question is a classifier that sees the question and the
answer together — one additional cheap model call returning a yes/no on whether the text
decides something about this person. It costs latency and money, it has not been built, and it
is recorded as the known limitation of this module rather than left for a reader to discover.

### 3.3 The defect in the opposite direction

The same round of measurement found the mirror-image failure. Nine of eleven tenancy questions,
phrased as a person phrases them — "my landlord kept my deposit, what are my rights?" — passed
straight through, because every pattern in that category was a compound phrase written in the
vocabulary of the category name. Nobody in trouble writes "tenancy dispute". The category we
claim as deterministically refused was approximately 18% effective.

Civil registration was deliberately **not** loosened. Wrong guidance about personnummer
registration can affect a person's right to remain, and Skatteverket is genuinely the right
destination. The user's first two blocked topics were correct refusals.

**Current measurement, re-run against the committed code on 13 August**, with every corpus
pinned in `tests/test_app_guardrails.py` so the claim stays true or the build goes red:

| Corpus | Intended | Result |
|---|---|---:|
| Healthcare navigation ("what is 1177?", "how do I register with a vårdcentral?") | answered | 14/14 |
| Health determinations ("should I see a doctor?", "is this infected?") | refused | 16/16 |
| Civil registration | refused | 3/3 |
| Tenancy, as people phrase it | refused | 10/10 |
| Pant and housing (the bottle deposit, which shares vocabulary with the tenancy one) | answered | 13/13 |

**Honest summary:** one user, related to the Operator, less than one day of use, no quotable
testimonial. Her single comment produced five commits, exposed four separate defects, and
corrected one claim in this document that we had already made and believed. The product has
been publicly reachable at `https://undra.nu` since 10 August and at its Cloud Run URL since
7 August, and has not been marketed anywhere.

## 4. Production Operation

The application runs continuously on Cloud Run at `https://undra.nu` (custom domain, TLS
certificate issued 10 August 2026) and at `https://undra-dteqnu36ia-lz.a.run.app`. Both
serve the same service.

It is operated by an autonomous agent, **Coral**, which wakes on a four-hour timer on a home
server, is handed a deterministic situation report, decides what to do, acts through a fixed
tool surface, and writes a handoff for the next cycle — which is a fresh instance with no
memory of the previous one. It commissions its own code through Jules, reviews the resulting
pull requests against a written charter, and merges them when CI passes. It operates under
machine-checked invariants and a watchdog running outside its own process, escalates to the
Operator's phone when it needs a human, and publishes its decision log publicly.

Operating record as of 11 August 2026, read from `ledger.db`:

| | |
|---|---:|
| Cycles run | 43 |
| — productive / idle / halted | 33 / 9 / 1 |
| Decisions logged | 55 (agent 48, operator 7) |
| Events recorded | 1,006 |
| Model calls | 431 |
| Tokens billed | 5,062,817 |
| Actions succeeded / failed | 22 / 2 |
| Approvals requested | 3, all granted |
| Guarantee tests | 83 |
| Agent tools | 22 |

Supporting evidence available: the public operations log, the Google Cloud billing console
export for project `undra-504613`, the Gemini API usage dashboard, and the ledger itself.

---

## 5. Human and AI Contribution

The competition asks entrants to separate human work from AI work. Naming the git authors
answers this badly, because git records who *committed*, not who *wrote*.

### 5.1 By commit

| Attribution | Commits |
|---|---:|
| Operator, genuinely her own typing | 3 |
| Mechanical merges, credited to her | 2 |
| Jules-written, credited to her | 3 |
| Operator-directed, Claude Code-written (carry `Co-Authored-By`) | 92 |
| Coral, autonomous | 12 |
| **Total** | **112** |

**107 of 112 commits were written by software.** Exactly three are the Operator's own typing:
the repository initialisation, a one-line DNS file, and a plugin installation. The git author
field credits her with 100 — roughly thirty-three times what she actually typed.

**These counts are read as of the commit immediately before the one that carries them**, since
a document cannot count the commit that records it. So the repository is always one commit
ahead of this table, and that commit is a change to this table. It is a fitting last word for
a section about how commit counts mislead, and it is disclosed rather than rounded away.

Three commits written by software carry no AI marker at all, because they landed under the
Operator's access token as squash merges: Jules's guardrail fix, the earlier draft of this
document, and a 16 August edit to this section's own money figures. A squash merge keeps the
pull request title and discards the trailer, so the only record of who wrote them is the
commit body — which is why the row above is maintained by hand while the rest are read from
`git log`.

**Every count above begins after the human-only phase ended.** Work started on 3 August; the
first commit is 5 August and the first ledger row 6 August. The design decisions that
determined what this system would be — the charter, the scope of what the product refuses,
the decision to build the operating harness before the product — were taken before anything
was recording. The ratios therefore understate the human contribution, for the same
structural reason the author field overstates it: each instrument counts only what it was
built to see.

### 5.2 Who did what

**The Operator (human)** wrote the charter's intent and every constraint the system runs
under, provisioned every credential, billing account and domain, approved each gated action,
and intervened when the agent deadlocked in a way it could not perceive.

**Claude (chat sessions)** drafted the charter, the machine-checked invariants, the watchdog
and the log generator, before the build began. **Claude Code** wrote the cycle runner, the
ledger, the tool surface and the test suite, and diagnosed the defects. **Coral** set its own
objectives, commissioned the product, reviewed it against the charter, merged it on a genuine
CI pass, then found a defect in the merged code and commissioned the fix. **Jules** wrote the
product codebase to a specification Coral wrote.

No pre-existing generic template, framework boilerplate or code snippet forms the substance
of this project beyond standard open-source libraries declared in `requirements.txt`
(FastAPI, `google-genai`, Pillow).

### 5.3 What went wrong, and why we are telling you

On 9 August 2026 an earlier version of this document was commissioned by Coral from Jules,
reviewed by Coral against the charter, and merged to `main`. It stated
<!-- audit:disclosed -->that the domain cost 100 SEK, that the lab box drew 30 SEK of
electricity, and that total spend was about $15<!-- /audit:disclosed --> — none of which had
a source — along with a model retired before it shipped, and revenue reported as zero in
July "because the project was in a local infrastructure setup and operational charter design
phase". **No ledger row supported any of the figures, and the project did not exist in
July.**

Every mechanism worked as specified. CI passed, because CI verifies that code runs. The task
landed as `ok`, which was true. Coral reviewed the diff and merged it. A completed task was
not something the design knew how to doubt.

The cause was structural and came before the agent's failure. The `spend` table had a reader
and no writer — the watchdog totalled it, but no code path could put a row in it. Asked for a
cost breakdown, the drafter found an empty source and filled it. **And the specific shape of
the fabrication came from this form**, which asks for revenue broken out by May, June and
July — months that predate the project. The zeros were correct; what was invented was a
narrative to make correct zeros look deliberate.

Three things changed as a result: costs can now be recorded with evidence mandatory in the
row; a deterministic auditor checks any prose against the ledger for money, model names,
dates and hosts before it can reach `main`; and this document was rewritten from source.

The uncomfortable part is the detection. It was not caught by a check, a test, or the agent.
It was caught because a human read it. Everything here was instrumented to prove what was
*done*, and nothing to question what was *said*.

---

## 6. Video Script Outline

Split-screen: presenter or clean slides on one side, the mobile web interface running live on
the other. Target 2:30.

**Scene 1 — The pre-arrival problem (0:00–0:30).** An admitted student, still abroad, facing
a lease to sign, a laundry room she has never seen, and a recycling system in a language she
does not read. General chatbots will answer her visa question. That is the danger.

**Scene 2 — Undra and the guides (0:30–1:00).** Live on a phone at `undra.nu`. The guides:
housing and scams, Arrival Day from CPH to Lund C, pant and sorting, tvättstuga etiquette.

**Scene 3 — Multimodal Q&A (1:00–1:30).** Photograph a Swedish laundry booking panel; Gemini
reads the sign and explains the booking rules in context. Note the AI-authorship badge on
every reply.

**Scene 4 — Refuse and route (1:30–2:20).** Two questions, back to back. First "how do I
apply for a residence permit?" — the refusal card returns before any model call is made,
naming Migrationsverket and linking to it. Then "what is 1177?" — which answers, because the
restriction is on determinations about a person's health, not on the topic of healthcare.
Until 12 August that second question was refused with a card instructing the user to contact
1177; the first real user's feedback is what surfaced it (§3). Show that this is 112 regex
patterns in `app/guardrails.py` and not a polite request to the model, and that the same
scanner runs on the model's output, which is how image-only queries are covered.

A practical shot list for recording this is in `docs/video-shot-list.md`. Audio is captions
rather than voiceover, and the video carries no music, per the competition's third-party
content rule.

**Scene 5 — The operator, and the honest ending (2:10–2:30).** Coral's public log ticking
over every four hours. Close on the real numbers: one user, zero revenue, $20.45 spent, and a
document that was rewritten because the agent made one up.

---

*Prepared 11 August 2026. Submission deadline 17 August 2026, 13:00 Pacific
(`2026-08-17T20:00:00Z`). Figures read from `ledger.db`, `git log`, and invoices held by the
Operator; none recalled or estimated except where explicitly marked as an estimate.*
