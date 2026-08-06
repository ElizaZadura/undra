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
#   4. pushed, if UNDRA_PUSH=1
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
  if git push -q origin HEAD 2>/dev/null; then
    log "pushed"
  else
    log "push failed"
    notify "[undra] git push failed. The audit trail is committed locally but not offsite."
  fi
else
  log "push disabled (set UNDRA_PUSH=1 to enable)"
fi

exit "$CYCLE_RC"
