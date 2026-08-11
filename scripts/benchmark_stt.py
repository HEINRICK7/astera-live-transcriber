#!/usr/bin/env python3
"""Benchmark a file as a live source without materializing full PCM in memory."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from astera_live_transcriber.domain.audio import (
    AudioSourceMetadata,
    AudioSourceType,
    AudioStreamMode,
)
from astera_live_transcriber.domain.session import TranscriptionSession
from astera_live_transcriber.infrastructure.audio.file_source import FileAudioSource
from astera_live_transcriber.infrastructure.config.settings import Settings
from astera_live_transcriber.infrastructure.engines.runtime import build_engine_runtime
from astera_live_transcriber.infrastructure.observability.metrics import PipelineMetrics
from astera_live_transcriber.presentation.api.dependencies import create_realtime_pipeline


async def run(path: Path, model_path: str, mode: AudioStreamMode) -> None:
    settings = Settings(
        engine="parakeet",
        model_path=model_path,
        partial_interval_ms=5_000,
    )
    runtime = build_engine_runtime(settings)
    await runtime.start()
    metrics = PipelineMetrics()
    session = TranscriptionSession(
        id="benchmark",
        model=settings.default_model,
        language="pt-BR",
        source_metadata=AudioSourceMetadata(
            filename=path.name,
            source_type=AudioSourceType.FILE,
            stream_mode=mode,
        ),
    )
    pipeline = create_realtime_pipeline(session, settings, runtime, metrics)
    source = FileAudioSource(
        path=path,
        sample_rate=settings.audio_sample_rate,
        chunk_ms=settings.audio_chunk_ms,
        mode=mode,
        metrics=metrics,
    )
    started = time.perf_counter()
    event_counts: dict[str, int] = {}
    try:
        async for chunk in source.stream():
            for event in await pipeline.process(chunk):
                event_counts[event.type.value] = event_counts.get(event.type.value, 0) + 1
        for event in await pipeline.flush():
            event_counts[event.type.value] = event_counts.get(event.type.value, 0) + 1
    finally:
        await pipeline.close()
        await source.close()
        await runtime.close()
    snapshot = metrics.snapshot()
    print(
        json.dumps(
            {
                "file": str(path),
                "mode": mode.value,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "metrics": snapshot,
                "events": event_counts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--model-path", default="models/parakeet-tdt-0.6b-v3-int8")
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in AudioStreamMode],
        default="accelerated",
    )
    args = parser.parse_args()
    asyncio.run(run(args.file, args.model_path, AudioStreamMode(args.mode)))


if __name__ == "__main__":
    main()
