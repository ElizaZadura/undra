"""Undra operator-loop runner.

One cycle per process. Coral is stateless between cycles: no in-memory state,
no caches, no conversation history survives this process exiting. Each cycle is
a fresh model instance whose entire picture of reality comes from the situation
report (AGENTS.md; HANDOFF.md §3).
"""

__all__ = ["config", "ledger", "llm"]
