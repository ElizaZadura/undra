#!/usr/bin/env bash
#
# One cycle, end to end. Invoked by undra-cycle.timer; safe to run by hand.
#
# The order is AGENTS.md's, and the last three steps are deliberately OUTSIDE
# the container: the agent renders nothing and pushes nothing itself. The log
# generator is a deterministic render of ledger rows, which is the property
# CHARTER.md §5 relies on to authorise publishing without an approval token.
#
#   1. container runs one cycle (situation report -> agent -> handoff)
#   2. publish_log.py renders docs/ from the ledger
#   3. the redacted dump, reports and docs are committed
#   4. rebased onto the remote and pushed, if UNDRA_PUSH=1
#
# Step 4 rebases because this box is not the only writer to main — pull
# requests merge on GitHub — and a bare push deadlocks the moment one does.
#
# Exit codes from publish_log.py matter: 30 means an entry was WITHHELD because
# the scrubber found what may be personal data in a ledger free-text field.
# That is a §3.5 violation upstream — the fix is the writer, not the scrubber —
# so it is surfaced loudly rather than swallowed.

set -uo pipefail

cd /srv/lab/undra || exit 20

LOG_TAG="undra-cycle"
log() { logger -t "$LOG_TAG" -- "$*" 2>/dev/null || true; echo "[$LOG_TAG] $*"; }

notify() {
  # Straight to Telegram, bypassing the ledger's outbound table on purpose:
  # the Operator is not a third party (AGENTS.md #2).
  [ -f env/ops.env ] || return 0
  # shellcheck disable=SC1091
  set -a; . env/ops.env; set +a
  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] || return 0
  curl -sS -m 15 -o /dev/null \
    -d "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=$1" \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" || true
}

# ---------------------------------------------------------------- 1. the cycle
log "starting cycle"
docker compose run --rm --no-deps ops
CYCLE_RC=$?
log "cycle exited $CYCLE_RC"

case "$CYCLE_RC" in
  0)  ;;
  10) log "halted — no model call was made" ;;
  20) log "situation report could not be produced; treating as halt"
      notify "[undra] situation_report.py exit 20 — the report could not be produced. Coral did not run. This needs a human look."
      ;;
  *)  log "unexpected exit $CYCLE_RC"
      notify "[undra] cycle exited unexpectedly with code ${CYCLE_RC}."
      ;;
esac

# ------------------------------------------------------------ 2. render the log
python3 publish_log.py
PUB_RC=$?
if [ "$PUB_RC" -eq 30 ]; then
  log "publish_log withheld an entry: possible personal data in a ledger field"
  notify "[undra] publish_log.py exit 30 — an entry was WITHHELD because the scrubber matched possible personal data in a ledger free-text field. That is a CHARTER.md 3.5 violation upstream. Fix the writer, not the scrubber."
elif [ "$PUB_RC" -ne 0 ]; then
  log "publish_log failed with $PUB_RC"
fi

# ---------------------------------------------------------------- 3. publish
# Commits made by the agent loop carry Coral's identity, so that `git log`
# distinguishes human work from agent work (CHARTER.md §2.1).
export GIT_AUTHOR_NAME="Coral (agent)"
export GIT_AUTHOR_EMAIL="coral@undra.nu"
export GIT_COMMITTER_NAME="Coral (agent)"
export GIT_COMMITTER_EMAIL="coral@undra.nu"

# docs/ and reports/ go to their own branch, not to main.
#
# They are regenerated from the ledger every four hours. While they lived on
# main, every cycle moved main, so any branch that outlived a cycle came back
# to conflicts in files no human had touched — and the natural fix, merging
# main in, dragged the next regeneration along and re-created the conflict.
# Five cycles went into that race on 2026-08-07, and no model was going to win
# it. Splitting the branch removes the class rather than detecting it: main now
# changes only when code changes.
#
# Built with a throwaway index and `commit-tree` rather than a checkout or a
# worktree. This runs unattended every four hours, and nothing here can leave
# the repository on the wrong branch or with a half-applied index: the working
# tree is never touched, and the branch ref moves only after the commit object
# exists.
PUBLISH_BRANCH="${UNDRA_PUBLISH_BRANCH:-$(python3 -c \
  "import tomllib;print(tomllib.load(open('invariants.toml','rb'))['scope'].get('publish_branch','ops-log'))" \
  2>/dev/null || echo ops-log)}"

publish_log_branch() {
  local idx tree parent commit stamp
  idx=$(mktemp -u "${TMPDIR:-/tmp}/undra-idx.XXXXXX")

  if ! GIT_INDEX_FILE="$idx" git read-tree --empty \
    || ! GIT_INDEX_FILE="$idx" git add -f docs reports 2>/dev/null; then
    rm -f "$idx"
    log "could not stage docs/reports for ${PUBLISH_BRANCH}"
    return 1
  fi
  tree=$(GIT_INDEX_FILE="$idx" git write-tree)
  rm -f "$idx"
  [ -n "$tree" ] || { log "could not write publish tree"; return 1; }

  parent=$(git rev-parse -q --verify "refs/heads/${PUBLISH_BRANCH}" || true)
  if [ -n "$parent" ] && [ "$tree" = "$(git rev-parse "${parent}^{tree}")" ]; then
    log "operations log unchanged; nothing to publish"
    return 2
  fi

  stamp=$(date -u +%Y-%m-%dT%H:%MZ)
  # Message on stdin: commit-tree's -m is not repeatable everywhere, and this
  # keeps the second paragraph that says where the content came from.
  commit=$(git commit-tree "$tree" ${parent:+-p "$parent"} <<EOF
cycle ${stamp}: operations log and redacted ledger dump

Rendered from the ledger by publish_log.py. Not composed.
EOF
)
  [ -n "$commit" ] || { log "commit-tree failed"; return 1; }
  git update-ref "refs/heads/${PUBLISH_BRANCH}" "$commit" || return 1
  log "published ${PUBLISH_BRANCH} $(git rev-parse --short "$commit")"
}

publish_log_branch
PUBLISH_RC=$?

# ------------------------------------------------------------------- 4. push
if [ "${UNDRA_PUSH:-0}" = "1" ]; then
  # The PAT is read from env/ops.env (mode 600) and used in an ephemeral remote
  # URL. Deliberately NOT stored in .git/config or a credential helper: a token
  # written into the repo's own config is one `git config --list` from a log,
  # and it would outlive the process that needed it.
  # shellcheck disable=SC1091
  set -a; . env/ops.env; set +a
  if [ -z "${GITHUB_PERSONAL_ACCESS_TOKEN:-}" ]; then
    log "push enabled but GITHUB_PERSONAL_ACCESS_TOKEN is unset"
    notify "[undra] push enabled but no GitHub token found. The audit trail is committed locally but not offsite."
  else
    REMOTE="https://x-access-token:${GITHUB_PERSONAL_ACCESS_TOKEN}@github.com/ElizaZadura/undra.git"
    # scrub() exists because git echoes the remote URL back on failure, token
    # and all, and this output goes to journald.
    scrub() { sed -E 's#https://[^@]*@#https://***@#g'; }

    # Catch up with the remote before pushing.
    #
    # A bare `git push HEAD:main` assumes this box is the only writer, and it
    # is not: pull requests are merged on GitHub, by Coral and by hand. On
    # 2026-08-07 PR #6 merged, remote main moved two commits ahead, and the
    # push was rejected as non-fast-forward on four consecutive cycles. The
    # trail sat on one disk for twelve hours. Nothing self-corrected, because
    # nothing was trying to.
    #
    # Fetch into refs/remotes/origin/main, not just FETCH_HEAD. The situation
    # report runs inside the container with no token and cannot fetch for
    # itself, so this ref is its only view of the remote; leaving it stale
    # would make collect_git() report a divergence that was already resolved.
    git fetch -q "$REMOTE" "+refs/heads/main:refs/remotes/origin/main" 2>&1 | scrub
    # Separately, and allowed to fail: a refspec naming a branch the remote does
    # not have aborts the whole fetch, which would leave BEHIND at 0 and skip
    # the rebase without saying anything.
    git fetch -q "$REMOTE" \
      "+refs/heads/${PUBLISH_BRANCH}:refs/remotes/origin/${PUBLISH_BRANCH}" 2>&1 \
      | scrub || log "no ${PUBLISH_BRANCH} on the remote yet"
    BEHIND=$(git rev-list --count HEAD..refs/remotes/origin/main 2>/dev/null || echo 0)
    if [ "${BEHIND:-0}" -gt 0 ]; then
      log "remote is ${BEHIND} commit(s) ahead; rebasing before push"
      # What counts as "dirty" here is narrower than it looks, twice over.
      #
      # --untracked-files=no: rebase steps over untracked files without touching
      # them, and step 2 routinely leaves some behind. Counting those would skip
      # the rebase and exit before the push — the very failure this block exists
      # to prevent.
      #
      # docs/ and reports/ excluded: they are machine output, regenerated from
      # the ledger every cycle and never hand-edited, so there is no work in
      # them to protect. They also stay tracked-and-modified on main until the
      # Pages source is moved to the publish branch, which would otherwise mean
      # a permanently dirty tree and a rebase that never runs.
      #
      # Modifications to actual source still stop us.
      if [ -n "$(git status --porcelain --untracked-files=no -- . \
                   ':(exclude)docs' ':(exclude)reports')" ]; then
        # Refuse to rebase over uncommitted work rather than stash it. A stash
        # that fails to pop is a silent data loss, and this runs unattended.
        log "uncommitted tracked changes present; not rebasing"
        ./bin/ledger-note event error git_push \
          "remote is ${BEHIND} commit(s) ahead but the working tree is dirty, so the rebase was skipped and the push will be rejected. Needs a human at the box." >/dev/null
        notify "[undra] Cannot push: remote is ${BEHIND} commit(s) ahead and there are uncommitted changes on the box, so the rebase was skipped. The audit trail is local-only until this is cleared by hand."
        unset REMOTE GITHUB_PERSONAL_ACCESS_TOKEN
        exit "$CYCLE_RC"
      fi
      # Rebase rather than merge: cycle commits are a deterministic render of
      # the ledger, so replaying them on the new base is exactly right, and it
      # leaves no merge commits in what should read as a linear operations log.
      if git rebase -q refs/remotes/origin/main 2>&1 | scrub; then
        log "rebased onto remote"
        ./bin/ledger-note event info git_push \
          "rebased ${BEHIND} commit(s) from the remote before pushing" >/dev/null
      else
        # A real conflict. Do not fight it unattended — that is the loop that
        # burned five cycles on 2026-08-07. Leave the tree exactly as it was
        # and hand it over.
        git rebase --abort 2>/dev/null
        log "rebase conflicted; aborted"
        PAYLOAD="git rebase onto origin/main conflicts; the audit trail cannot be pushed until it is resolved by hand at /srv/lab/undra"
        ./bin/ledger-note request PUSH_BLOCKED "$PAYLOAD" \
          --priority high --default-action keep_trail_local_and_retry_next_cycle >/dev/null
        RQ=$?
        [ "$RQ" -ne 3 ] && notify "[undra] git rebase onto origin/main conflicts and was aborted. The tree is untouched and the audit trail is committed locally but not offsite. This needs a human at the box; Coral cannot resolve it."
        unset REMOTE GITHUB_PERSONAL_ACCESS_TOKEN
        exit "$CYCLE_RC"
      fi
    fi

    if git push -q "$REMOTE" HEAD:main 2>&1 | scrub; then
      log "pushed main"
      ./bin/ledger-note event info git_push "pushed HEAD to origin/main" >/dev/null
    else
      log "push failed"
      ./bin/ledger-note event error git_push \
        "git push to origin/main failed; the audit trail is committed locally but not offsite" >/dev/null
      notify "[undra] git push failed. The audit trail is committed locally but not offsite."
    fi

    # The operations log branch, pushed separately so a failure names which of
    # the two is stuck. This box is its only writer, so a rejection here means
    # something else wrote to it — which is worth a human look, not a force
    # push. Nothing on this branch is ever rewritten.
    if [ "$PUBLISH_RC" -eq 0 ]; then
      if git push -q "$REMOTE" \
           "refs/heads/${PUBLISH_BRANCH}:refs/heads/${PUBLISH_BRANCH}" 2>&1 | scrub; then
        log "pushed ${PUBLISH_BRANCH}"
        ./bin/ledger-note event info git_push \
          "published the operations log to origin/${PUBLISH_BRANCH}" >/dev/null
      else
        log "${PUBLISH_BRANCH} push failed"
        ./bin/ledger-note event error git_push \
          "push to origin/${PUBLISH_BRANCH} was rejected; this box should be its only writer, so the branch has diverged and the public log is no longer updating" >/dev/null
        notify "[undra] Could not push ${PUBLISH_BRANCH}. It was rejected, and this box should be its only writer — so something else has written to that branch. log.undra.nu has stopped updating until this is looked at. Not force-pushing."
      fi
    fi
    unset REMOTE GITHUB_PERSONAL_ACCESS_TOKEN
  fi
else
  log "push disabled (set UNDRA_PUSH=1 to enable)"
fi

exit "$CYCLE_RC"
