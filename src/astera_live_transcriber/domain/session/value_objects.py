from enum import StrEnum


class TurnDecision(StrEnum):
    WAIT = "wait"
    COMMIT = "commit"
    FORCE_COMMIT = "force_commit"

