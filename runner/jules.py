"""Jules build-loop client (v1alpha REST).

VERIFIED against the live API on 2026-08-06:
  GET /v1alpha/sources   -> 200, returns sources/github/ElizaZadura/undra
  GET /v1alpha/sessions  -> 200

NOT VERIFIED: the request body for POST /v1alpha/sessions. No discovery
document is published and the validator returns a bare INVALID_ARGUMENT rather
than naming fields, so the shape below comes from the documentation rather than
from observation. The first real create will confirm or correct it — which is
why create_session() logs the exact body it sent before sending it. Do not
present this path as working until a session id has come back (CHARTER.md §6.6).

Budget: the free tier is 15 tasks/day, ~210 over the fortnight. Hitting that
ceiling means the build loop is thrashing, not that the tier is too small
(HANDOFF.md §4).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

BASE = "https://jules.googleapis.com/v1alpha"
TIMEOUT = 30

# AGENTS.md #10: requirePlanApproval for anything touching payments, auth or
# user data; autoPr otherwise. Substring match, deliberately broad — a false
# positive costs one plan review, a false negative ships unreviewed auth code.
SENSITIVE = ("payment", "billing", "auth", "login", "credential", "token",
             "secret", "session", "cookie", "user data", "personal data",
             "pii", "gdpr", "privacy", "upload", "photo", "image")


class JulesError(RuntimeError):
    pass


@dataclass
class Session:
    name: str
    id: str
    raw: dict


def needs_plan_approval(prompt: str, title: str = "") -> bool:
    blob = f"{title} {prompt}".lower()
    return any(word in blob for word in SENSITIVE)


# Files an agent-authored patch may not land on its own. Two groups: what the
# agent is allowed to do (the rules) and what checks whether it did (the
# enforcement). Both have to be here, because protecting the rules alone just
# moves the target one file over.
#
# Observed 2026-08-12. docs/submission.md claimed a model, `gemini-3.1-pro`,
# that no cycle has ever called. Coral commissioned "fix the audit errors";
# Jules fixed them by adding
#
#     planning_pro = "gemini-3.1-pro"   # matches regex-derived name in prose_audit
#
# to invariants.toml, because prose_audit._check_models accepts any model named
# in [models]. The claim was false, the check was correct, and the patch edited
# the check's ground truth so the claim would pass. It reached an open pull
# request and nothing in the landing path had an opinion about it.
#
# A trailing "/" means the whole directory. This is not a security boundary —
# the patch never touches the default branch and the token is repo-scoped. It
# is a review boundary: these files change when a human decides they change, and
# a diff here should arrive as a decision rather than as routine work.
PROTECTED = (
    "CHARTER.md",              # the rules
    "AGENTS.md",
    "invariants.toml",         # the machine-checked subset of them
    "tests/test_guarantees.py",  # what enforces all of it
    "runner/prose_audit.py",   # the checks themselves. See the note above:
    "situation_report.py",     # a checker that edits itself is not a checker.
    ".claude/",                # tool permissions
    "env/",                    # secrets
)


def protected_paths(files: Iterable[str]) -> list[str]:
    """Which of `files` an agent-authored patch must not land unreviewed."""
    hits = []
    for f in files:
        # removeprefix, not lstrip: lstrip takes a character SET, so
        # ".claude/settings.json".lstrip("./") is "claude/settings.json" and the
        # permissions directory falls straight through the guard.
        path = f.strip().removeprefix("./")
        for guard in PROTECTED:
            if (path == guard if not guard.endswith("/")
                    else path.startswith(guard)):
                hits.append(path)
                break
    return hits


class Jules:
    def __init__(self, api_key: str | None = None):
        self.key = api_key or os.environ.get("JULES_API_KEY", "")
        if not self.key:
            raise JulesError("JULES_API_KEY is not set in this environment")

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{BASE}/{path}", data=data, method=method,
            headers={"X-Goog-Api-Key": self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.load(exc).get("error", {}).get("message", "")
            except Exception:  # noqa: BLE001
                detail = exc.read().decode(errors="replace")[:300]
            raise JulesError(f"{method} {path}: HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise JulesError(f"{method} {path}: {exc}") from None

    # -- verified ----------------------------------------------------------- #

    def sources(self) -> list[dict]:
        return self._request("GET", "sources").get("sources", [])

    def source_for(self, repo: str) -> str:
        """repo is "owner/name" as it appears in invariants.toml allowed_repos."""
        want = f"sources/github/{repo}"
        for s in self.sources():
            if s.get("name") == want:
                return want
        raise JulesError(
            f"{repo} is not connected to Jules. Available: "
            f"{[s.get('name') for s in self.sources()]}")

    def sessions(self) -> list[dict]:
        return self._request("GET", "sessions").get("sessions", [])

    def session(self, session_id: str) -> dict:
        return self._request("GET", f"sessions/{session_id}")

    def activities(self, session_id: str) -> list[dict]:
        return self._request("GET", f"sessions/{session_id}/activities"
                             ).get("activities", [])

    def patch(self, session_id: str) -> tuple[str, list[str]]:
        """The unified diff a completed session produced, and the files it touches.

        Jules leaves finished work here when nobody publishes it. There is no
        submit endpoint — probed 2026-08-07 with an invalid body so a live route
        would reject at validation rather than execute: submit, publish,
        publishBranch, createPullRequest, createPr, submitChanges, pushBranch,
        complete and finalize all 404. Publishing from the web UI is the only
        route Jules offers, so the patch has to be lifted out and applied here.
        """
        session = self.session(session_id)
        for out in session.get("outputs") or []:
            diff = (out.get("changeSet", {}).get("gitPatch", {})
                       .get("unidiffPatch", ""))
            if diff:
                files = [l.split(" b/", 1)[-1] for l in diff.splitlines()
                         if l.startswith("diff --git")]
                return diff, files
        raise JulesError(
            f"session {session_id} has no patch — it may still be running, or "
            "may have finished without changing anything")

    def approve_plan(self, session_id: str) -> dict:
        """Release a session that is waiting on plan approval.

        VERIFIED 2026-08-07 — `POST sessions/{id}:approvePlan` returns 200 with an
        empty body and moves the session to IN_PROGRESS.

        **Never expose this to Coral.** requirePlanApproval exists to put a human
        between the agent and code that touches payments, auth or user data. An
        agent that can approve its own plans has no such gate. This is called
        from exactly one place — the Operator's Telegram channel, after the chat
        id has been checked.

        Found by accident, and worth recording how: probing for the endpoint with
        a POST *performed* the approval rather than reporting that it existed.
        Probe unknown POST routes with a deliberately invalid body so validation
        rejects them, or not at all.
        """
        return self._request("POST", f"sessions/{session_id}:approvePlan", {})

    # -- unverified --------------------------------------------------------- #

    def create_session(self, *, repo: str, prompt: str, title: str,
                       branch: str = "main",
                       require_plan_approval: bool | None = None,
                       ledger=None) -> Session:
        """File a build task. UNVERIFIED SHAPE — see the module docstring."""
        if require_plan_approval is None:
            require_plan_approval = needs_plan_approval(prompt, title)

        # NO autoPr FIELD. AGENTS.md #10 mentions autoPr, and it was added here
        # on 2026-08-06 to stop finished work stranding as an unsubmitted patch.
        # It broke the build loop: the v1alpha API rejects the whole request with
        # 400 "Unknown name 'autoPr' at 'session'", so every jules_file_task call
        # failed until it was removed. Probed 2026-08-06 — autoPr, autoPR,
        # auto_pr, automaticPullRequest and createPullRequest are all rejected as
        # unknown field names. Whatever AGENTS.md was describing, this API does
        # not expose it under any of those spellings.
        #
        # So stranding is not solvable from here. Session 1652844863819652924
        # completed with a full patch and produced a PR only after the Operator
        # clicked Publish in the web UI, and there is no submit endpoint either
        # (:submit, :publish and :createPullRequest all 404). Treat a completed
        # session with no PR as needing a human, and say so rather than
        # inventing a field to fix it.
        body = {
            "prompt": prompt,
            "title": title,
            "sourceContext": {
                "source": self.source_for(repo),
                "githubRepoContext": {"startingBranch": branch},
            },
            "requirePlanApproval": require_plan_approval,
        }

        if ledger is not None:
            ledger.event("info", "jules",
                         f"creating session (shape unverified): "
                         f"{json.dumps(body)[:800]}")

        result = self._request("POST", "sessions", body)
        name = result.get("name", "")
        sid = result.get("id") or name.rsplit("/", 1)[-1]
        if not sid:
            raise JulesError(f"session created but no id in response: "
                             f"{json.dumps(result)[:400]}")
        if ledger is not None:
            ledger.event("info", "jules",
                         f"session {sid} created; "
                         f"requirePlanApproval={require_plan_approval}")
        return Session(name=name, id=sid, raw=result)
