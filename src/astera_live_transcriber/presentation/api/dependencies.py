from astera_live_transcriber.application.services.audio_pipeline import (
    AudioPipeline,
    AudioPipelineConfig,
)
from astera_live_transcriber.application.services.segment_service import SegmentLifecycleService
from astera_live_transcriber.application.services.transcription_service import TranscriptionService
from astera_live_transcriber.domain.session import TranscriptionSession
from astera_live_transcriber.infrastructure.audio.buffer import RingBuffer
from astera_live_transcriber.infrastructure.audio.normalizer import AudioNormalizer
from astera_live_transcriber.infrastructure.config.settings import Settings, get_settings
from astera_live_transcriber.infrastructure.engines.noop.adapter import NoopTranscriptionEngine
from astera_live_transcriber.infrastructure.engines.runtime import EngineRuntime
from astera_live_transcriber.infrastructure.observability.metrics import PipelineMetrics
from astera_live_transcriber.infrastructure.turn_detection.time_based import TimeBasedTurnDetector
from astera_live_transcriber.infrastructure.vad import PassthroughVad, RmsVad


def create_engine() -> NoopTranscriptionEngine:
    return NoopTranscriptionEngine()


def get_transcription_service() -> TranscriptionService:
    return TranscriptionService(create_engine())


def get_app_settings() -> Settings:
    return get_settings()


def create_realtime_pipeline(
    session: TranscriptionSession,
    settings: Settings,
    runtime: EngineRuntime | None = None,
    metrics: PipelineMetrics | None = None,
) -> AudioPipeline:
    vad = (
        RmsVad(
            threshold=settings.vad_threshold,
            silence_candidate_ms=settings.silence_candidate_ms,
            min_speech_ms=settings.min_speech_ms,
        )
        if settings.vad_enabled
        else PassthroughVad()
    )
    return AudioPipeline(
        session=session,
        normalizer=AudioNormalizer(),
        buffer=RingBuffer(
            max_duration_ms=settings.ring_buffer_max_duration_ms,
            max_chunks=settings.ring_buffer_max_chunks,
        ),
        vad=vad,
        turn_detector=TimeBasedTurnDetector(
            commit_silence_ms=settings.commit_silence_ms,
            max_silence_ms=settings.max_silence_ms,
        ),
        engine=runtime.engine if runtime is not None else create_engine(),
        lifecycle=SegmentLifecycleService(),
        config=AudioPipelineConfig(
            prefix_padding_ms=settings.prefix_padding_ms,
            max_segment_duration_ms=settings.max_segment_duration_ms,
            partial_interval_ms=settings.partial_interval_ms,
            inference_cancel_grace_ms=settings.inference_cancel_grace_ms,
        ),
        metrics=metrics or PipelineMetrics(),
    )
