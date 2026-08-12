# Video shot list — what to record on the phone

Companion to `docs/submission.md` §6, which is the narrative version. This one is the
practical one: what to tap, what to type, in what order.

**Rules that constrain this** (Build with Gemini XPRIZE, official rules, read 12 August):

- Under **three minutes**. Judges are not required to watch past it.
- Must show **"the Project functioning on the device for which it was built"** — Undra is
  mobile-first, so this is a phone screen recording. A desktop capture undersells it.
- Uploaded and **publicly visible on YouTube, Vimeo or Youku**, link on the submission form.
- **No third-party trademarks or copyrighted music** without permission. Hence: no music,
  captions instead of voiceover. Migrationsverket appears on screen by the Operator's
  decision — nominative use of a public authority's name, which is the product's function.

**Audio: none.** Record silent or the audio gets discarded. Captions are written afterwards
against the timings below, so leave a beat on each screen rather than scrolling fast.

---

## Before you start

- Phone in portrait. Screen recording on. Notifications off.
- Open `https://undra.nu` in the mobile browser, not an app shell.
- Have one photo ready in the camera roll: **a Swedish laundry booking panel or a
  miljöhus sorting sign.** Scene 3 depends on it and it is the only prop.
- Do a throwaway run first. The two questions in scenes 3 and 4 hit a live service and take
  a few seconds to answer; knowing the pause length makes the real take much easier to cut.

---

## Scene 1 — the problem (0:00–0:25)

No app yet. Two or three still shots, held ~4 seconds each:

1. The Lund University admission page, or an AF Bostäder listing.
2. A Swedish sign the viewer cannot read — the laundry panel photo works.
3. A general-purpose chatbot confidently answering a residence-permit question.

Shot 3 is the argument for the whole product and is worth getting. If it is awkward to
capture, skip it — the caption can carry it.

## Scene 2 — the guides (0:25–0:55)

On `undra.nu`. Scroll the landing page slowly, then open **two** guides, not four:

- housing and scams
- tvättstuga etiquette

Hold each open for ~6 seconds. Slow scrolling reads as confidence; fast scrolling reads as
hiding something.

## Scene 3 — multimodal (0:55–1:35)

The heart of it. This is the Gemini call in the deployed application.

1. Open the chat.
2. Attach the laundry-panel photo.
3. Type: **`what does this sign say and what do I need to do?`**
4. Wait on the response. Do not cut the wait — it is a real API call and the pause is honest.
5. **Scroll to the top of the answer and hold on the `🤖 [AI-Generated Response by Undra
   Assistant]` badge for a full 2 seconds.** This is an EU AI Act Article 50 transparency
   point and a judge will look for it.

## Scene 4 — refuse and route (1:35–2:20)

Two questions, back to back, same screen. The contrast is the point.

**First, the refusal:**

> `how do I apply for a residence permit?`

The card returns effectively instantly — no model call is made. Hold on it. Tap the
**Migrationsverket** link so the viewer sees it actually goes somewhere. Come back.

**Then, immediately, the thing that is not refused:**

> `what is 1177?`

This answers. **As of 12 August it answers — this morning it did not.** The old guardrail
matched the bare word `1177` and returned a card telling the user to contact 1177. Showing
these two side by side is the strongest thirty seconds available: the product refuses the
determination and explains the system, and it does not confuse the two.

If there is time, a third:

> `my landlord kept my deposit, what are my rights?`

Refused as of today; passed straight through yesterday.

## Scene 5 — the operator, and the honest ending (2:20–2:50)

1. `https://log.undra.nu` — Coral's public decision log, updating every four hours.
2. Hold on the closing figures.

Close on the real numbers, which are the ones to end on:

> one user · zero revenue · $20.45 spent · a submission rewritten because the agent
> invented its finances

---

## After you upload

Send me the link and the raw file if you want it cut. What I can do here: trim to under
three minutes, burn in captions against these timings, normalise the framing, and write the
Devpost description around it. What I cannot do is upload it — that is a login, and it stays
with you.
