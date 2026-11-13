"""The staged rollout of a market (ZID-COM-WF-2026-11 WF-I05 rollout panel). Pure.

A market goes SANDBOX, STAGING, PILOT, CANARY, PRODUCTION, one step at a time, and no step is made by the person who made the one before. Only CANARY and
PRODUCTION count as executed release evidence, so a market in an earlier stage reads as not yet released. It can be PAUSED at any stage and resumed only to the
stage it was paused from, or ROLLED_BACK, after which it starts again from SANDBOX. Pausing and rolling back need no second person: stopping is never held up.
"""

from __future__ import annotations

STAGES = ("SANDBOX", "STAGING", "PILOT", "CANARY", "PRODUCTION")
HOLDS = ("PAUSED", "ROLLED_BACK")
LIVE = ("CANARY", "PRODUCTION")


class RolloutError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def check_move(
    current: str | None,
    resume_to: str | None,
    target: str,
    decider: str,
    last_decider: str | None,
) -> None:
    """Raise unless moving from `current` to `target` is allowed. `resume_to` is the stage a PAUSED market was paused from; `last_decider` made the latest move."""
    if target not in STAGES and target not in HOLDS:
        raise RolloutError("ROLLOUT_STAGE_UNKNOWN", target)
    if target in HOLDS:
        if current is None:
            raise RolloutError("ROLLOUT_NOT_STARTED", "there is nothing to pause or roll back")
        if current == target:
            raise RolloutError("ROLLOUT_ALREADY_IN_STAGE", target)
        if target == "PAUSED" and current == "ROLLED_BACK":
            raise RolloutError("ROLLOUT_NOT_LIVE", "a rolled-back market is not running")
        return
    if current is None or current == "ROLLED_BACK":
        wanted = "SANDBOX"
    elif current == "PAUSED":
        wanted = resume_to or "SANDBOX"
    elif current == "PRODUCTION":
        raise RolloutError("ROLLOUT_AT_END", "already in production")
    else:
        wanted = STAGES[STAGES.index(current) + 1]
    if target != wanted:
        raise RolloutError("ROLLOUT_STAGE_SKIP", f"the next stage is {wanted}")
    if last_decider is not None and decider == last_decider:
        raise RolloutError(
            "ROLLOUT_SAME_PERSON", "a stage is advanced by a different person than the last move"
        )


def is_live(stage: str | None) -> bool:
    """Whether a market in this stage counts as having executed its release evidence."""
    return stage in LIVE
