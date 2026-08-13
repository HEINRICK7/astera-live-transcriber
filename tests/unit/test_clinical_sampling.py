from astera_live_transcriber.infrastructure.transcription_intelligence.evaluation import (
    build_sample_plan,
)


def test_sample_plan_includes_time_buckets_and_clinical_targets() -> None:
    events = [
        {
            "type": "transcript.committed",
            "segment_id": "seg_1",
            "revision": 1,
            "start_ms": 10_000,
            "end_ms": 20_000,
            "text": "Exame físico. O paciente nega uso de ibuprofeno.",
            "projected_text": "Exame físico. O paciente nega uso de ibuprofeno.",
        },
        {
            "type": "transcript.committed",
            "segment_id": "seg_2",
            "revision": 2,
            "start_ms": 700_000,
            "end_ms": 710_000,
            "text": "Cirrose e hipertensão portal. RNI 1,5.",
            "projected_text": "acumulado",
        },
    ]

    samples = build_sample_plan(events, duration_ms=900_000, sample_duration_ms=45_000)

    assert any(sample.reason == "time_bucket" for sample in samples)
    assert any(sample.reason == "clinical_target" for sample in samples)
    assert any("ibuprofeno" in sample.matched_terms for sample in samples)
    assert all(sample.clip_end_ms > sample.clip_start_ms for sample in samples)


def test_sampling_does_not_treat_cumulative_projection_as_local_text() -> None:
    events = [
        {
            "type": "transcript.committed",
            "segment_id": "seg_1",
            "start_ms": 0,
            "end_ms": 1_000,
            "text": "A",
            "projected_text": "A",
        }
    ]

    sample = build_sample_plan(events, duration_ms=60_000, sample_duration_ms=30_000)[0]

    assert sample.raw_text == "A"
    assert sample.structural_snapshot == "A"
