from enum import StrEnum


class SessionState(StrEnum):
    ACTIVE = "active"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CLOSED = "closed"


class TurnDecision(StrEnum):
    WAIT = "wait"
    COMMIT = "commit"
    FORCE_COMMIT = "force_commit"
