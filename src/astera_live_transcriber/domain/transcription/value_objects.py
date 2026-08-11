import unicodedata
from enum import StrEnum


class SegmentStatus(StrEnum):
    PARTIAL = "partial"
    REVISED = "revised"
    COMMITTED = "committed"


def is_publishable_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return any(not unicodedata.category(character).startswith("P") for character in stripped)
