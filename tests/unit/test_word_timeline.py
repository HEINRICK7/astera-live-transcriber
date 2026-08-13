from astera_live_transcriber.application.ports.transcription_engine import StreamingWord
from astera_live_transcriber.application.services.word_timeline import WordTimelineStore


def word(text: str, start: int, end: int) -> StreamingWord:
    return StreamingWord(text=text, start_ms=start, end_ms=end)


def test_word_timeline_appends_non_overlapping_temporal_words() -> None:
    timeline = WordTimelineStore()
    timeline.upsert((word("Náusea.", 55_780, 63_420),))
    timeline.upsert((word("Eu", 63_500, 63_800), word("tive", 63_800, 64_200)))

    assert timeline.text() == "Náusea. Eu tive"


def test_word_timeline_replaces_same_temporal_span() -> None:
    timeline = WordTimelineStore()
    timeline.upsert((word("100", 70_000, 70_500),))
    timeline.upsert((word("50", 70_000, 70_500), word("miligramas", 70_500, 71_000)))

    assert timeline.text() == "50 miligramas"
    assert timeline.last_change is not None
    assert timeline.last_change.operation == "replace_overlap"
    assert [item.text for item in timeline.last_change.removed] == ["100"]


def test_word_timeline_replaces_overlapping_word_span() -> None:
    timeline = WordTimelineStore()
    timeline.upsert((word("alguns", 80_000, 80_500), word("pontos", 80_500, 81_000)))
    timeline.upsert((word("alguns", 80_000, 80_500), word("momentos", 80_500, 81_000)))

    assert timeline.text() == "alguns momentos"


def test_word_timeline_does_not_append_repeated_timed_words() -> None:
    timeline = WordTimelineStore()
    words = (word("fuma", 90_000, 90_300), word("ou", 90_300, 90_500))
    timeline.upsert(words)
    timeline.upsert(words + (word("consome", 90_500, 91_000),))

    assert timeline.text() == "fuma ou consome"
    assert timeline.last_change is not None
    assert timeline.last_change.operation == "replace_overlap"
    assert [item.text for item in timeline.last_change.removed] == ["fuma", "ou"]
