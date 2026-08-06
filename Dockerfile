# Operator-loop container. One cycle per invocation, then exit — the systemd
# timer on `red` is the scheduler, not a loop inside here (AGENTS.md: Coral is
# stateless between cycles).
#
# 3.12 rather than the host's 3.14: tomllib is 3.11+, so the floor is met, and
# 3.12 has settled wheels for the whole dependency tree.
FROM python:3.12-slim

# No .pyc clutter in a container that is rebuilt often; unbuffered so that
# `docker logs` shows a crashing cycle's output instead of losing it.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GOOGLE_GENAI_USE_VERTEXAI=FALSE

# git is needed at runtime, not for the build: situation_report.py shells out to
# it for git_head/git_branch, and the runner commits the redacted ledger dump.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY runner/requirements.txt /app/runner/requirements.txt
RUN pip install --no-cache-dir -r /app/runner/requirements.txt

# Source is bind-mounted in compose so a code change does not need a rebuild.
# Copying it here as well keeps the image runnable on its own.
COPY runner/ /app/runner/
COPY situation_report.py publish_log.py invariants.toml CHARTER.md /app/

# Runs as a non-root user; the bind-mounted ledger must be writable by it, so
# the uid is pinned to match the host account that owns /srv/lab/undra.
RUN useradd --uid 1000 --create-home coral && chown -R coral:coral /app
USER coral

# `git` refuses to operate on a repo owned by a different uid without this.
RUN git config --global --add safe.directory /app

ENTRYPOINT ["python3", "-m", "runner"]
