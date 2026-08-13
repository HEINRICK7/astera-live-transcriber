from astera_live_transcriber.application.services.provider_segment_store import (
    ProviderSegmentStore,
)


def test_provider_segment_store_replaces_provisional_with_authoritative_commit() -> None:
    store = ProviderSegmentStore()
    store.upsert_provisional("seg_0009", 18, "fuma ou cons fuma ou consome", 55_700)

    committed = store.commit(
        "seg_0009",
        19,
        "Você fuma ou consome bebidas alcoólicas?",
        55_700,
        81_000,
    )

    assert committed.status == "committed"
    assert committed.provisional_text == ""
    assert committed.committed_text == "Você fuma ou consome bebidas alcoólicas?"
    assert [segment.segment_id for segment in store.ordered_committed()] == ["seg_0009"]


def test_provider_segment_store_replaces_same_segment_commit_without_concatenation() -> None:
    store = ProviderSegmentStore()
    store.commit("seg_0006", 1, "snapshot antigo", 55_500, 55_500)
    store.commit("seg_0006", 2, "snapshot autoritativo", 55_500, 80_000)

    committed = store.ordered_committed()
    assert len(committed) == 1
    assert committed[0].committed_text == "snapshot autoritativo"


def test_provider_segment_store_preserves_immutable_commit_evidence_history() -> None:
    store = ProviderSegmentStore()
    store.commit("seg_1", 1, "snapshot antigo", 100, 200)
    store.commit("seg_1", 2, "snapshot autoritativo", 100, 300)

    history = store.evidence_history()

    assert len(history) == 2
    assert history[0].committed_text == "snapshot antigo"
    assert history[1].committed_text == "snapshot autoritativo"
    assert store.get("seg_1").committed_text == "snapshot autoritativo"
