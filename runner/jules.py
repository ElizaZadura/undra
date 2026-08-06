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

    # -- unverified --------------------------------------------------------- #

    def create_session(self, *, repo: str, prompt: str, title: str,
                       branch: str = "main",
                       require_plan_approval: bool | None = None,
                       ledger=None) -> Session:
        """File a build task. UNVERIFIED SHAPE — see the module docstring."""
        if require_plan_approval is None:
            require_plan_approval = needs_plan_approval(prompt, title)

        # autoPr is set REGARDLESS of require_plan_approval, which is a
        # correction (2026-08-06). AGENTS.md #10 reads "requirePlanApproval for
        # anything touching payments, auth or user data; autoPr otherwise", and
        # that was implemented as mutually exclusive. Session
        # 1652844863819652924 then built the entire application — code, tests,
        # code review, XSS fix, README — reported "All plan steps completed.
        # Ready for submission", and stopped there. No branch, no PR, and no API
        # endpoint to submit it: :submit, :publish and :createPullRequest all
        # return 404, so the work was only reachable by a human clicking
        # Publish in the web UI.
        #
        # The two flags gate different things. requirePlanApproval gates whether
        # Jules may START, which is the control the charter wants for anything
        # touching user data. autoPr gates whether finished work becomes a PR,
        # which is not a safety boundary at all — a PR still has to be reviewed
        # and merged. Withholding it just strands completed work.
        body = {
            "prompt": prompt,
            "title": title,
            "sourceContext": {
                "source": self.source_for(repo),
                "githubRepoContext": {"startingBranch": branch},
            },
            "requirePlanApproval": require_plan_approval,
            "autoPr": True,
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
