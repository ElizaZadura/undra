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
# That is a §3.6 violation upstream — the fix is the writer, not the scrubber —
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

# ----------------------------------------------------------------- 3. commit
# Commits made by the agent loop carry Coral's identity, so that `git log`
# distinguishes human work from agent work (CHARTER.md §2.1).
export GIT_AUTHOR_NAME="Coral (agent)"
export GIT_AUTHOR_EMAIL="coral@undra.nu"
export GIT_COMMITTER_NAME="Coral (agent)"
export GIT_COMMITTER_EMAIL="coral@undra.nu"

git add docs reports 2>/dev/null
if git diff --cached --quiet; then
  log "nothing to commit"
else
  STAMP=$(date -u +%Y-%m-%dT%H:%MZ)
  git commit -q -m "cycle ${STAMP}: operations log and redacted ledger dump" \
             -m "Rendered from the ledger by publish_log.py. Not composed." \
    && log "committed"
fi

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
    # Rebase rather than merge: cycle commits are a deterministic render of the
    # ledger, so replaying them on the new base is exactly right and leaves no
    # merge commits in what should read as a linear operations log.
    # Fetch into refs/remotes/origin/main, not just FETCH_HEAD. The situation
    # report runs inside the container with no token and cannot fetch for
    # itself, so this ref is its only view of the remote; leaving it stale
    # would make collect_git() report a divergence that was already resolved.
    git fetch -q "$REMOTE" "+refs/heads/main:refs/remotes/origin/main" 2>&1 | scrub
    BEHIND=$(git rev-list --count HEAD..refs/remotes/origin/main 2>/dev/null || echo 0)
    if [ "${BEHIND:-0}" -gt 0 ]; then
      log "remote is ${BEHIND} commit(s) ahead; rebasing before push"
      if [ -n "$(git status --porcelain)" ]; then
        # Refuse to rebase over uncommitted work rather than stash it. A stash
        # that fails to pop is a silent data loss, and this runs unattended.
        log "uncommitted changes present; not rebasing"
        ./bin/ledger-note event error git_push \
          "remote is ${BEHIND} commit(s) ahead but the working tree is dirty, so the rebase was skipped and the push will be rejected. Needs a human at the box." >/dev/null
        notify "[undra] Cannot push: remote is ${BEHIND} commit(s) ahead and there are uncommitted changes on the box, so the rebase was skipped. The audit trail is local-only until this is cleared by hand."
        unset REMOTE GITHUB_PERSONAL_ACCESS_TOKEN
        exit "$CYCLE_RC"
      fi
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
      log "pushed"
      ./bin/ledger-note event info git_push "pushed HEAD to origin/main" >/dev/null
    else
      log "push failed"
      ./bin/ledger-note event error git_push \
        "git push to origin/main failed; the audit trail is committed locally but not offsite" >/dev/null
      notify "[undra] git push failed. The audit trail is committed locally but not offsite."
    fi
    unset REMOTE GITHUB_PERSONAL_ACCESS_TOKEN
  fi
else
  log "push disabled (set UNDRA_PUSH=1 to enable)"
fi

exit "$CYCLE_RC"
