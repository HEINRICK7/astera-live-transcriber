import struct

import pytest

from astera_live_transcriber.domain.audio import AudioChunk, VadEventType
from astera_live_transcriber.domain.session import TurnDecision, TurnState
from astera_live_transcriber.infrastructure.turn_detection.time_based import TimeBasedTurnDetector
from astera_live_transcriber.infrastructure.vad.rms import RmsVad


def pcm_chunk(sequence: int, timestamp_ms: int, speech: bool) -> AudioChunk:
    amplitude = 20_000 if speech else 0
    data = struct.pack("<1600h", *([amplitude] * 1600))
    return AudioChunk(data=data, sequence=sequence, timestamp_ms=timestamp_ms, duration_ms=100)


@pytest.mark.asyncio
async def test_rms_vad_emits_start_candidate_and_silence() -> None:
    vad = RmsVad(threshold=0.2, silence_candidate_ms=500, min_speech_ms=0)

    started = await vad.process(pcm_chunk(0, 0, True))
    silence = await vad.process(pcm_chunk(1, 100, False))
    candidate = await vad.process(pcm_chunk(6, 600, False))

    assert started.type is VadEventType.SPEECH_STARTED
    assert silence.type is VadEventType.SILENCE
    assert candidate.type is VadEventType.SPEECH_STOP_CANDIDATE
    assert candidate.silence_ms == 600


@pytest.mark.asyncio
async def test_time_detector_waits_for_long_pause() -> None:
    detector = TimeBasedTurnDetector(commit_silence_ms=1500, max_silence_ms=3000)
    state = TurnState(
        active_segment=True,
        segment_started_at_ms=0,
        speech_started_at_ms=0,
        silence_started_at_ms=100,
        current_timestamp_ms=800,
        buffer_duration_ms=800,
        max_segment_duration_ms=30_000,
    )

    assert await detector.evaluate(state) is TurnDecision.WAIT
    assert (
        await detector.evaluate(
            TurnState(
                active_segment=True,
                segment_started_at_ms=0,
                speech_started_at_ms=0,
                silence_started_at_ms=100,
                current_timestamp_ms=1600,
                buffer_duration_ms=1600,
                max_segment_duration_ms=30_000,
            )
        )
        is TurnDecision.COMMIT
    )
