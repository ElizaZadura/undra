"""Configuration, loaded from invariants.toml.

CHARTER.md §0: where the charter and invariants.toml disagree, invariants.toml
wins. So nothing in this package hardcodes a limit, a model id, or a gated
action kind — they all come from here, and a diff to invariants.toml is a
visible change to what the system may do.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(os.environ.get("INVARIANTS_PATH", "./invariants.toml"))


class ConfigError(RuntimeError):
    """invariants.toml is missing, malformed, or still has a FILL_ME in a
    place that matters. Fail loudly at startup rather than at 3am."""


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]

    # -- sections ---------------------------------------------------------- #
    @property
    def project(self) -> dict:
        return self.raw["project"]

    @property
    def budget(self) -> dict:
        return self.raw["budget"]

    @property
    def rates(self) -> dict:
        return self.raw["rates"]

    @property
    def staleness(self) -> dict:
        return self.raw["staleness"]

    @property
    def scope(self) -> dict:
        return self.raw.get("scope", {})

    @property
    def models(self) -> dict:
        return self.raw["models"]

    @property
    def egress(self) -> dict:
        return self.raw.get("egress", {})

    # -- frequently used --------------------------------------------------- #
    @property
    def codename(self) -> str:
        return self.project["codename"]

    @property
    def ledger_path(self) -> str:
        return self.scope.get("ledger_path", "./ledger.db")

    @property
    def deadline(self) -> datetime:
        return datetime.fromisoformat(self.project["deadline_utc"].replace("Z", "+00:00"))

    @property
    def hours_remaining(self) -> float:
        return (self.deadline - datetime.now(timezone.utc)).total_seconds() / 3600

    @property
    def gated_kinds(self) -> frozenset[str]:
        return frozenset(self.raw.get("gates", {}).get("require_approval", []))

    def timeout_default(self, kind: str) -> str:
        """What to do when an approval request expires unanswered. CHARTER.md §4:
        a request without a default action is malformed."""
        return self.raw.get("gates", {}).get("timeout_defaults", {}).get(kind, "abandon_task")

    def is_gated(self, kind: str) -> bool:
        return kind in self.gated_kinds

    # -- models ------------------------------------------------------------ #
    def model_for(self, role: str) -> str:
        """Model id for a role. Never hardcode one at a call site."""
        try:
            return self.models[role]
        except KeyError:
            raise ConfigError(
                f"no model configured for role {role!r}; "
                f"known roles: {sorted(k for k, v in self.models.items() if isinstance(v, str))}"
            ) from None

    @property
    def max_planning_calls_per_day(self) -> int:
        return int(self.models.get("max_planning_calls_per_day", 1))

    # -- scope -------------------------------------------------------------- #
    @property
    def allowed_repos(self) -> list[str]:
        return list(self.scope.get("allowed_repos", []))

    @property
    def allowed_hosts(self) -> list[str]:
        return [h for h in self.scope.get("allowed_hosts", []) if "FILL_ME" not in h]

    @property
    def publish_dir(self) -> Path:
        return Path(self.scope.get("publish_dir", "./docs"))


def load(path: Path | None = None) -> Config:
    p = path or CONFIG_PATH
    try:
        raw = tomllib.loads(p.read_text())
    except FileNotFoundError:
        raise ConfigError(f"invariants.toml not found at {p}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invariants.toml is malformed: {exc}") from None

    for section in ("project", "budget", "rates", "staleness", "models"):
        if section not in raw:
            raise ConfigError(f"invariants.toml is missing the [{section}] section")

    cfg = Config(raw=raw)

    # allowed_hosts may legitimately still be FILL_ME before the first deploy —
    # collect_deploy() in situation_report.py treats that as "nothing deployed",
    # which is honest. allowed_repos may not: the build loop needs a target.
    if not cfg.allowed_repos or any("FILL_ME" in r for r in cfg.allowed_repos):
        raise ConfigError("scope.allowed_repos is unset or still FILL_ME")

    return cfg
