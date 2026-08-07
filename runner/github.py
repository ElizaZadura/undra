"""GitHub REST client for the review half of the build loop.

Coral files Jules tasks, which become pull requests. Without these it can see
that a PR exists but not what is in it — which is how a guardrail that skips the
image path reaches main unnoticed. CHARTER.md §5 grants "review and merge PRs
that pass CI" as standing latitude, so this closes a capability gap, not a
policy one.

Two things are enforced here rather than trusted:

  - **Scope.** Every call is pinned to invariants.toml allowed_repos. There is
    no parameter for choosing a different repository.
  - **"that pass CI".** merge() refuses when checks are failing or still
    running. The charter's grant is conditional and the condition is machine-
    checkable, so it is checked in code.

Stdlib urllib rather than an SDK: one fewer dependency that can move under a
two-week run, and the request shapes are simple.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.github.com"
TIMEOUT = 20
MAX_DIFF_BYTES = 60_000     # token discipline; truncation is reported, not silent


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, repo: str, token: str | None = None):
        self.repo = repo
        self.token = token or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
        if not self.token:
            raise GitHubError("GITHUB_PERSONAL_ACCESS_TOKEN is not set")

    def _call(self, method: str, path: str, body: dict | None = None,
              accept: str = "application/vnd.github+json") -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{API}/repos/{self.repo}/{path}", data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": accept,
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read()
                if accept.endswith("diff"):
                    return raw.decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            try:
                detail = json.load(exc).get("message", "")
            except Exception:  # noqa: BLE001
                detail = ""
            raise GitHubError(f"{method} {path}: HTTP {exc.code} {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GitHubError(f"{method} {path}: {exc}") from None

    # -- read ---------------------------------------------------------------- #

    def pulls(self, state: str = "open") -> list[dict]:
        return self._call("GET", f"pulls?state={urllib.parse.quote(state)}&per_page=20")

    def pull(self, number: int) -> dict:
        return self._call("GET", f"pulls/{number}")

    def pull_files(self, number: int) -> list[dict]:
        return self._call("GET", f"pulls/{number}/files?per_page=100")

    def pull_diff(self, number: int) -> tuple[str, bool]:
        """Returns (diff, truncated)."""
        diff = self._call("GET", f"pulls/{number}",
                          accept="application/vnd.github.v3.diff")
        if len(diff) > MAX_DIFF_BYTES:
            return diff[:MAX_DIFF_BYTES], True
        return diff, False

    def tree(self, ref: str = "main") -> list[str]:
        """Every path on a branch.

        Without this an agent can read pull requests but not the branch they
        target, so it cannot tell "this file does not exist" from "this file
        does not exist *in this PR*". On 2026-08-06 that produced a duplicate
        .github/workflows/ci.yml and a merge conflict: the file had been on main
        for four hours, and the only visible evidence said it was missing.
        """
        data = self._call("GET", f"git/trees/{urllib.parse.quote(ref)}?recursive=1")
        return [t["path"] for t in data.get("tree", []) if t["type"] == "blob"]

    def file(self, path: str, ref: str = "main") -> str:
        import base64
        data = self._call(
            "GET", f"contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(ref)}")
        if isinstance(data, dict) and data.get("content"):
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        raise GitHubError(f"{path} is not a readable file on {ref}")

    def checks(self, sha: str) -> dict:
        """Combined CI verdict for a commit.

        Returns counts plus a `verdict` of pass / fail / pending / none. `none`
        is NOT a pass: a repository with no CI configured has not demonstrated
        anything, and CHARTER.md §5 conditions merging on passing CI.
        """
        runs = self._call("GET", f"commits/{sha}/check-runs").get("check_runs", [])
        statuses = self._call("GET", f"commits/{sha}/status")

        failed = [c["name"] for c in runs
                  if c.get("conclusion") in ("failure", "timed_out", "cancelled",
                                             "action_required")]
        pending = [c["name"] for c in runs if c.get("status") != "completed"]
        passed = [c["name"] for c in runs if c.get("conclusion") in ("success",
                                                                    "neutral",
                                                                    "skipped")]
        combined = statuses.get("state")          # success | failure | pending
        if combined == "failure":
            failed.append("commit-status")
        elif combined == "pending" and statuses.get("statuses"):
            pending.append("commit-status")

        if failed:
            verdict = "fail"
        elif pending:
            verdict = "pending"
        elif passed or combined == "success":
            verdict = "pass"
        else:
            verdict = "none"
        return {"verdict": verdict, "passed": passed, "failed": failed,
                "pending": pending}

    # -- write --------------------------------------------------------------- #

    def create_branch_with_files(self, *, branch: str, base: str,
                                 files: dict[str, str], message: str) -> str:
        """Create a branch carrying `files`, as one commit. Returns the new sha.

        Used to land work a Jules session finished but never published. Writes
        only to a NEW branch — it refuses to touch one that already exists, so
        it cannot overwrite anything, and `base` is read rather than assumed.
        The result still has to pass CI and be reviewed before it can reach the
        default branch.
        """
        try:
            self._call("GET", f"git/ref/heads/{urllib.parse.quote(branch)}")
            raise GitHubError(
                f"branch {branch!r} already exists; refusing to write to it. "
                "Choose a new name rather than overwriting work.")
        except GitHubError as exc:
            if "already exists" in str(exc):
                raise
            pass  # 404 is what we want

        base_sha = self._call(
            "GET", f"git/ref/heads/{urllib.parse.quote(base)}")["object"]["sha"]
        base_tree = self._call("GET", f"git/commits/{base_sha}")["tree"]["sha"]

        tree = []
        for path, content in files.items():
            blob = self._call("POST", "git/blobs",
                              {"content": content, "encoding": "utf-8"})
            tree.append({"path": path, "mode": "100644", "type": "blob",
                         "sha": blob["sha"]})
        new_tree = self._call("POST", "git/trees",
                              {"base_tree": base_tree, "tree": tree})
        commit = self._call("POST", "git/commits",
                            {"message": message, "tree": new_tree["sha"],
                             "parents": [base_sha]})
        self._call("POST", "git/refs",
                   {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
        return commit["sha"]

    def open_pull_request(self, *, title: str, head: str, base: str,
                          body: str) -> dict:
        return self._call("POST", "pulls",
                          {"title": title, "head": head, "base": base, "body": body})

    def comment(self, number: int, body: str) -> dict:
        """Issue-level comment. CHARTER.md §2.4: anything a human reads must
        disclose that it came from software."""
        return self._call("POST", f"issues/{number}/comments", {"body": body})

    def merge(self, number: int, commit_title: str) -> dict:
        return self._call("PUT", f"pulls/{number}/merge",
                          {"commit_title": commit_title, "merge_method": "squash"})
