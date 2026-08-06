"""Entrypoint. One cycle per invocation, then exit.

The systemd timer on `red` is the scheduler. There is deliberately no loop in
here: a long-lived process would accumulate exactly the state the design
forbids, and a crash-looping agent is what the watchdog exists to catch.

    python3 -m runner                  # a real cycle
    python3 -m runner --stub-model     # exercise the path, no model call
    python3 -m runner --no-telegram    # skip the interrupt channel
"""

from __future__ import annotations

import argparse
import sys

from . import cycle


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stub-model", action="store_true",
                    help="run the full cycle with the model call stubbed")
    ap.add_argument("--no-telegram", action="store_true",
                    help="do not poll or send on the interrupt channel")
    args = ap.parse_args()

    return cycle.run(stub_model=args.stub_model,
                     use_telegram=not args.no_telegram)


if __name__ == "__main__":
    sys.exit(main())
