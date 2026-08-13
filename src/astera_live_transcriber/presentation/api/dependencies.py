from astera_live_transcriber.application.services.audio_pipeline import (
    AudioPipeline,
    AudioPipelineConfig,
)
from astera_live_transcriber.application.services.segment_service import SegmentLifecycleService
from astera_live_transcriber.application.services.transcription_service import TranscriptionService
from astera_live_transcriber.application.transcription_intelligence.adaptation_engine import (
    AdaptationEngine,
)
from astera_live_transcriber.application.transcription_intelligence.keyterm_selector import (
    KeytermSelector,
)
from astera_live_transcriber.application.transcription_intelligence.memory import (
    InMemoryTranscriptionMemory,
)
from astera_live_transcriber.application.transcription_intelligence.observer import (
    TranscriptionObserver,
)
from astera_live_transcriber.application.transcription_intelligence.safe_normalizer import (
    SafeNormalizer,
)
from astera_live_transcriber.application.transcription_intelligence.vocabulary_matcher import (
    RapidFuzzVocabularyMatcher,
)
from astera_live_transcriber.domain.session import TranscriptionSession
from astera_live_transcriber.domain.transcription_intelligence import RetentionPolicy
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
    streaming_keyterms: tuple[str, ...] | None = None,
    streaming_diarization: bool | None = None,
    intelligence_memory: InMemoryTranscriptionMemory | None = None,
) -> AudioPipeline:
    pipeline_metrics = metrics or PipelineMetrics()
    observer = None
    adaptive_keyterms: tuple[str, ...] = ()
    if settings.intelligence_enabled:
        memory = intelligence_memory or InMemoryTranscriptionMemory(
            policy=RetentionPolicy(settings.intelligence_memory_policy),
            decay_half_life_days=settings.intelligence_decay_half_life_days,
        )
        provider = settings.stt_provider if settings.stt_provider != "local" else settings.engine
        adaptation = AdaptationEngine(
            memory=memory,
            matcher=RapidFuzzVocabularyMatcher(),
            normalizer=SafeNormalizer(settings.intelligence_normalization_enabled),
            keyterm_selector=KeytermSelector(memory, settings.intelligence_max_keyterms),
            mode=settings.intelligence_mode,
        )
        observer = TranscriptionObserver(
            session_id=session.id,
            engine=adaptation,
            queue_size=settings.intelligence_observer_queue_size,
            metrics=pipeline_metrics,
        )
        adaptive_keyterms = adaptation.keyterms_for_session(session.id, provider)
    requested_keyterms = settings.stt_keyterms if streaming_keyterms is None else streaming_keyterms
    combined_keyterms = tuple(dict.fromkeys((*adaptive_keyterms, *requested_keyterms)))
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
        engine=(
            runtime.create_session_engine(pipeline_metrics)
            if runtime is not None
            else create_engine()
        ),
        lifecycle=SegmentLifecycleService(),
        config=AudioPipelineConfig(
            prefix_padding_ms=settings.prefix_padding_ms,
            max_segment_duration_ms=settings.max_segment_duration_ms,
            partial_interval_ms=settings.partial_interval_ms,
            partial_window_ms=settings.partial_window_ms,
            partial_overlap_ms=settings.partial_overlap_ms,
            inference_cancel_grace_ms=settings.inference_cancel_grace_ms,
            streaming_finalize_timeout_ms=settings.stt_finalize_timeout_ms,
            streaming_keyterms=(
                combined_keyterms
            ),
            streaming_diarization=(
                settings.stt_diarization
                if streaming_diarization is None
                else streaming_diarization
            ),
            streaming_debug_trace=settings.stt_debug_trace,
            incremental_projection_enabled=settings.incremental_projection_enabled,
            incremental_projection_working_set_size=settings.incremental_projection_working_set_size,
        ),
        metrics=pipeline_metrics,
        observer=observer,
    )
