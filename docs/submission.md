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

Each year Lund University admits a large intake of international students to southern
Sweden. For many it is a first time living abroad, and they are immediately confronted with
a set of complex, highly structured and unfamiliar local systems.

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
  never written to persistent disk or object storage. User question text is never logged and
  never persisted.

### 1.5 Refusal Guardrails: Deterministic "Refuse and Route"

In a student-advisory context the most dangerous failure is a confident, incorrect answer
about a visa, a tax obligation or a medical symptom. Undra therefore refuses four categories
outright rather than relying on prompt instructions alone.

**The four restricted categories** (CHARTER §3.3): Immigration and Visas; Taxes and Civil
Registration; Legal Contracts and Tenancy Disputes; Medical and Safety.

**Implementation** — `app/guardrails.py`, 184 lines, **77 regular-expression patterns**
across the four categories, matching Swedish and English regulatory terminology
(*Migrationsverket*, *Skatteverket*, *personnummer*, *uppehållstillstånd*, *residence
permit*, *deposit dispute*, *112*, and so on):

- **Pre-generation**: the query is scanned locally before anything is sent to the Gemini
  API. A match blocks the query deterministically, and no call is made.
- **Post-generation**: the model's own output is passed through the same scanner. This
  covers adversarial phrasing and image-only queries, where there is no input text to match
  against — a defect found and fixed on 7 August after review (§5.2).
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
| Google AI Pro | One month; the agent's coding tool (Jules) | **$6.85** | kr 65.00, promotional rate. Operator-reported from the account billing page; no invoice document held |
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

**A note on the estimate in our own logs.** The ledger's internal estimate of total model
spend is **$10.38**, computed from per-token rates transcribed from Google's pricing page and
applied to metered token counts. That figure covers every call the agent made, on both API
projects. Only one of those projects is billable: `undra-504613` carries the paid key, and
`undra-free` carries a free-tier key whose calls cost nothing.

Split by the key that served each call, **as of 2026-08-12T09:16Z**:

| Key | Calls | Estimated |
|---|---:|---:|
| Paid (`undra-504613`) | 233 | $5.31 |
| Free (`undra-free`) | 123 | $2.51 |
| Recorded before the ledger tracked the key | 75 | $2.56 |
| **Total** | **431** | **$10.38** |

The agent is still running, so this table is a reading rather than a final figure; it is
regenerated from the ledger before submission. Google billed **$3.18** against the paid key
through 10 August, the last day the invoice covers. Measured on 11 August, the ledger's
estimate for that same key and period ran about 7% above the invoice — ordinary estimation
error, in the conservative direction. That comparison is not reproducible from the table
above, which keeps moving as the agent runs; only the billed figure is fixed.
**$3.18 is the billed figure and the one we quote.** The estimate is what the same volume of
work would have cost had none of it run on the free tier, and it is the number our spend
ceiling is enforced against, deliberately, because a budget guard should err toward stopping
early.

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

**Testimonials: none at the time of writing.** She has had the product for less than a day.
We would rather submit no testimonial than one solicited before the user had formed an
opinion. If feedback arrives before the deadline it will be included only with her explicit
informed consent to its being quoted publicly.

**Honest summary:** one user, related to the Operator, less than one day of use, no
feedback yet. The product has been publicly reachable at `https://undra.nu` since 10 August
and at its Cloud Run URL since 7 August, and has not been marketed anywhere.

---

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
| Jules-written, credited to her | 2 |
| Operator-directed, Claude Code-written (carry `Co-Authored-By`) | 59 |
| Coral, autonomous | 12 |
| **Total** | **78** |

**73 of 78 commits were written by software.** Exactly three are the Operator's own typing:
the repository initialisation, a one-line DNS file, and a plugin installation. The git author
field credits her with 66 — roughly twenty-two times what she actually typed.

Two commits written by software carry no AI marker at all, because they landed under the
Operator's access token: Jules's guardrail fix, and the earlier draft of this document.

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

**Scene 4 — Refuse and route (1:30–2:10).** Ask "how do I apply for a residence permit?" The
refusal card returns before any model call is made, naming Migrationsverket and linking to
it. Show that this is 77 regex patterns in `app/guardrails.py` and not a polite request to the
model — and that the same scanner runs on the model's output, which is how image-only queries
are covered.

**Scene 5 — The operator, and the honest ending (2:10–2:30).** Coral's public log ticking
over every four hours. Close on the real numbers: one user, zero revenue, $20.45 spent, and a
document that was rewritten because the agent made one up.

---

*Prepared 11 August 2026. Submission deadline 17 August 2026, 13:00 Pacific
(`2026-08-17T20:00:00Z`). Figures read from `ledger.db`, `git log`, and invoices held by the
Operator; none recalled or estimated except where explicitly marked as an estimate.*
