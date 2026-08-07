---
name: build-record
description: Refresh the Undra build record — the human-readable log of what was built, what broke, and who did which parts. Updates the markdown file at ~/undra-build-record.md and republishes the matching Artifact page. Use when the user asks to update, regenerate or refresh the build record, build log, project record or the XPRIZE narrative source. Not for the public operations log at log.undra.nu, which publish_log.py generates automatically.
---

# Refresh the build record

Two outputs, same content, both kept in step:

| Output | Location |
|---|---|
| Markdown | `/home/elz/undra-build-record.md` |
| Artifact | `https://claude.ai/code/artifact/885d7a1d-529a-4b9d-ac66-22579bce5dfb` |

Neither lives in the repository. That is deliberate — the Operator asked for a record
separate from the project's own artefacts.

## What this document is for

Three uses, in this order of importance:

1. **Source material for the XPRIZE submission**, which requires a 500–1000 word account
   of what the human did versus what the AI did. §2 and §5 exist to be excerpted.
2. **The Operator's own traceability**, since the alternative is scrolling a terminal.
3. **Writing or sharing about the project.**

## Steps

### 1. Read the facts. Do not recall them.

```bash
cd /srv/lab/undra && ./bin/build-record-facts
```

This prints every number the document quotes, read from `git log` and `ledger.db`. Use
`--json` if you want to work with it programmatically.

**Never carry a figure forward from the previous version of the document.** The whole
point of this record is that its numbers are traceable, and a stale count that nobody
re-derived is exactly the drift the project is built to prevent. If a number cannot be
read from a source, say so in the text rather than estimating it.

### 2. Read the existing document

Read `/home/elz/undra-build-record.md` before editing. Preserve its structure and, more
importantly, the honesty conventions in it:

- **The money figure is flagged as an estimate**, not a billed amount, with the reason
  (per-token rates are transcribed, token counts are measured). Keep that framing.
- **The GitHub attribution caveat** — squash merges are credited to the token owner, so
  git history over-counts the human. This matters for the submission and must survive.
- **Defects are attributed honestly.** The table records which were in the scaffolding and
  which were the agent's. Do not soften it; a defect list where the agent looks flawless
  is less credible and less useful than the truth.
- **What the agent got wrong** has its own subsection. Keep it.

### 3. Bring it up to date

Update the figures, extend the timeline with what has happened since, and add any new
defects. Sections to revisit every time:

- §1 the summary figures
- §2 the human/agent split
- §3 the timeline — append, do not rewrite history
- §4 the defect table
- §6 ledger totals
- §7 where it stands — this goes stale fastest

Keep the tone factual and unhurried. It is a record, not a pitch. Specific beats
impressive: "the free tier gave out after thirty calls" is worth more than "we hit
unexpected limits".

### 4. Write both outputs

Update the markdown at `/home/elz/undra-build-record.md` first, then mirror the changes
into the HTML and republish:

```
Artifact(file_path=/home/elz/undra-build-record.html,
         url="https://claude.ai/code/artifact/885d7a1d-529a-4b9d-ac66-22579bce5dfb",
         favicon="🪸")
```

Passing `url` is what keeps the existing page rather than minting a new one — without it,
a session that did not publish the artifact will create a second one. If
`/home/elz/undra-build-record.html` is missing, fetch the published page with WebFetch and
rebuild from that, preserving the existing design rather than restyling.

**Design notes for the HTML**, so a refresh does not drift: teal `#1F5F5B` marks
human-attributed work and coral `#C4614A` marks agent-attributed work, running through
the timeline and the split panel. Monospace carries machine facts, a system serif carries
narration. Both themes are defined at token level. That colour coding does real work —
it is what lets a reader answer "who did this" by scanning — so keep it.

### 5. Tell the user what changed

Briefly: which figures moved, what was added to the timeline, any new defect. If a number
went the wrong way — spend climbing, unproductive cycles rising — say so plainly rather
than burying it.
